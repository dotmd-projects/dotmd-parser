"""dotmd-parser — text-first domain ontology construction (v1: extraction core).

Scans a folder of markdown, asks Claude to extract ontology elements per
document (map), merges them by normalized name (reduce), and emits a canonical
IR (ontology.yml) plus Turtle (.ttl) and a §5-style design markdown.
"""
from __future__ import annotations

from pathlib import Path

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
