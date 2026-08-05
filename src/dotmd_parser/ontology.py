"""dotmd-parser — text-first domain ontology construction (v1: extraction core).

Scans a folder of markdown, asks Claude to extract ontology elements per
document (map), merges them by normalized name (reduce), and emits a canonical
IR (ontology.yml) plus Turtle (.ttl) and a §5-style design markdown.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotmd_parser.analyze import scan_documents
from dotmd_parser import llm

ELEMENT_KEYS = (
    "classes", "datatype_properties", "object_properties",
    "vocabularies", "invariants", "conflicts", "open_questions",
)
EMPTY_ELEMENTS: dict = {k: [] for k in ELEMENT_KEYS}

VALID_TYPES = {"string", "decimal", "integer", "date", "dateTime", "boolean"}


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _add_prov(item: dict, source: str) -> dict:
    prov = list(item.get("provenance") or [])
    if source and source not in prov:
        prov.append(source)
    out = dict(item)
    out["provenance"] = prov
    return out


def _merge_named(bucket: dict, items: list[dict], source: str,
                 key_fields: tuple[str, ...], conflicts: list[dict]) -> None:
    """Merge `items` into `bucket` keyed by normalized name; flag field conflicts."""
    for raw in items:
        key = _norm(raw.get("name", ""))
        if not key:
            continue
        if key not in bucket:
            bucket[key] = _add_prov(raw, source)
            continue
        existing = bucket[key]
        for f in key_fields:
            if raw.get(f) is not None and existing.get(f) is not None and raw[f] != existing[f]:
                conflicts.append({
                    "kind": "naming",
                    "detail": f'{raw["name"]}.{f}: "{existing[f]}" vs "{raw[f]}"',
                    "provenance": sorted({*existing.get("provenance", []), source}),
                })
        # first-seen wins for representative values; accumulate provenance
        prov = existing["provenance"]
        if source not in prov:
            prov.append(source)


def merge_ontology(partials: list[dict], meta: dict) -> dict:
    classes: dict[str, dict] = {}
    dprops: dict[str, dict] = {}
    oprops: dict[str, dict] = {}
    vocabs: dict[str, dict] = {}
    invariants: list[dict] = []
    conflicts: list[dict] = []
    open_qs: list[dict] = []

    for part in partials:
        source = part.get("source", "")
        el = {**EMPTY_ELEMENTS, **(part.get("elements") or {})}
        _merge_named(classes, el["classes"], source, ("label_ja", "domain_group"), conflicts)
        _merge_named(dprops, el["datatype_properties"], source,
                     ("domain", "type", "enum"), conflicts)
        _merge_named(oprops, el["object_properties"], source,
                     ("from", "to", "cardinality"), conflicts)
        _merge_named(vocabs, el["vocabularies"], source, ("values",), conflicts)
        for inv in el["invariants"]:
            invariants.append(_add_prov(inv, source))
        for c in el["conflicts"]:
            conflicts.append(_add_prov(c, source))
        for q in el["open_questions"]:
            open_qs.append(_add_prov(q, source))

    return {
        "meta": meta,
        "classes": sorted(classes.values(), key=lambda x: x["name"]),
        "datatype_properties": sorted(dprops.values(), key=lambda x: (x.get("domain", ""), x["name"])),
        "object_properties": sorted(oprops.values(), key=lambda x: x["name"]),
        "vocabularies": sorted(vocabs.values(), key=lambda x: x["name"]),
        "invariants": invariants,
        "conflicts": conflicts,
        "open_questions": open_qs,
    }


def _normalize_types(elements: dict) -> dict:
    """Normalize unknown datatype types to 'string', stashing original in _type_warning."""
    out = {**EMPTY_ELEMENTS, **(elements or {})}
    for dp in out["datatype_properties"]:
        if dp.get("type") not in VALID_TYPES:
            dp["_type_warning"] = dp.get("type")
            dp["type"] = "string"
    return out


def extract_ontology(directory, api_key=None, extensions=None, model=None, *, caller=None):
    """Map step: per-document LLM extraction. Returns partials with source paths."""
    if extensions is None:
        extensions = [".md", ".txt"]
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or export it, "
            "or use --plan for the no-API-key host-agent path."
        )
    resolved_model = model or os.environ.get("CLAUDE_MODEL", llm.DEFAULT_MODEL)
    template = llm.load_prompt_template("extract-ontology")

    partials: list[dict] = []
    for doc in scan_documents(directory, extensions=extensions):
        prompt = (template
                  .replace("{{doc_path}}", doc["path"])
                  .replace("{{doc_content}}", doc["content"]))
        first, _, rest = prompt.partition("\n")
        system = first.strip() or "You extract a domain ontology."
        user = rest.strip() or prompt
        raw = caller(user, system, resolved_model) if caller else llm.call_claude(
            user, system, api_key, resolved_model)
        elements = _normalize_types(llm.extract_json(raw))
        partials.append({"source": doc["path"], "elements": elements})
    return partials
