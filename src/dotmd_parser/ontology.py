"""dotmd-parser — text-first domain ontology construction (v1: extraction core).

Scans a folder of markdown, asks Claude to extract ontology elements per
document (map), merges them by normalized name (reduce), and emits a canonical
IR (ontology.yml) plus Turtle (.ttl) and a §5-style design markdown.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotmd_parser.analyze import estimate_cost, scan_documents
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
        "datatype_properties": sorted(dprops.values(), key=lambda x: (x.get("domain") or "", x["name"])),
        "object_properties": sorted(oprops.values(), key=lambda x: x["name"]),
        "vocabularies": sorted(vocabs.values(), key=lambda x: x["name"]),
        "invariants": invariants,
        "conflicts": conflicts,
        "open_questions": open_qs,
    }


def _normalize_types(elements: dict) -> dict:
    """Normalize unknown datatype types to 'string', stashing original in _type_warning."""
    out = {**EMPTY_ELEMENTS, **(elements or {})}
    normalized = []
    for dp in out["datatype_properties"]:
        if dp.get("type") not in VALID_TYPES:
            dp = {**dp, "_type_warning": dp.get("type"), "type": "string"}
        normalized.append(dp)
    out["datatype_properties"] = normalized
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


def _escape_scalar(s: str) -> str:
    """Escape backslash, double-quote, newline, and carriage return for a quoted scalar."""
    return (str(s).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r"))


def _yq(value) -> str:
    """Quote a scalar for YAML (double-quoted, escaped). None -> null."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{_escape_scalar(value)}"'


def _yaml_list_field(values: list) -> str:
    return "[" + ", ".join(_yq(v) for v in values) + "]"


def emit_yaml(ir: dict) -> str:
    lines: list[str] = ["# ontology.yml — dotmd auto-constructed (v1, text-first)"]
    m = ir["meta"]
    lines.append("meta:")
    for k in ("namespace", "prefix", "domain", "built_from", "generated_by"):
        lines.append(f"  {k}: {_yq(m.get(k))}")
    lines.append(f"  source_docs: {_yaml_list_field(m.get('source_docs', []))}")

    def block(section: str, rows: list[dict], fields: list[str]) -> None:
        lines.append(f"{section}:")
        if not rows:
            lines[-1] = f"{section}: []"
            return
        for row in rows:
            first = True
            for f in fields:
                if f not in row:
                    continue
                val = row[f]
                rendered = _yaml_list_field(val) if isinstance(val, list) else _yq(val)
                prefix = "  - " if first else "    "
                lines.append(f"{prefix}{f}: {rendered}")
                first = False

    block("classes", ir["classes"], ["name", "label_ja", "domain_group", "provenance"])
    block("datatype_properties", ir["datatype_properties"],
          ["name", "domain", "type", "label_ja", "enum", "provenance"])
    block("object_properties", ir["object_properties"],
          ["name", "from", "to", "cardinality", "characteristics", "note", "provenance"])
    block("vocabularies", ir["vocabularies"], ["name", "values", "provenance"])
    block("invariants", ir["invariants"], ["id", "statement", "kind", "provenance"])
    block("conflicts", ir["conflicts"], ["kind", "detail", "provenance"])
    block("open_questions", ir["open_questions"], ["text", "provenance"])
    return "\n".join(lines) + "\n"


XSD_MAP = {"string": "xsd:string", "decimal": "xsd:decimal", "integer": "xsd:integer",
           "date": "xsd:date", "dateTime": "xsd:dateTime", "boolean": "xsd:boolean"}
CHAR_MAP = {"Functional": "owl:FunctionalProperty",
            "InverseFunctional": "owl:InverseFunctionalProperty",
            "Symmetric": "owl:SymmetricProperty", "Transitive": "owl:TransitiveProperty"}


def _ttl_str(s: str) -> str:
    return '"' + _escape_scalar(s) + '"'


def _terminate(lines: list) -> None:
    lines[-1] = lines[-1].rstrip(" ;") + " ."


def emit_ttl(ir: dict) -> str:
    m = ir["meta"]
    p = m["prefix"]
    lines = [
        f"@prefix {p}: <{m['namespace']}> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]

    def prov(item, indent="    "):
        for src in item.get("provenance", []):
            lines.append(f"{indent}{p}:sourceDoc {_ttl_str(src)} ;")

    for c in ir["classes"]:
        lines.append(f"{p}:{c['name']} a owl:Class ;")
        if c.get("label_ja"):
            lines.append(f'    rdfs:label {_ttl_str(c["label_ja"])}@ja ;')
        prov(c)
        _terminate(lines)
        lines.append("")

    for dp in ir["datatype_properties"]:
        lines.append(f"{p}:{dp['name']} a owl:DatatypeProperty ;")
        if dp.get("domain"):
            lines.append(f"    rdfs:domain {p}:{dp['domain']} ;")
        lines.append(f"    rdfs:range {XSD_MAP.get(dp.get('type'), 'xsd:string')} ;")
        if dp.get("label_ja"):
            lines.append(f'    rdfs:label {_ttl_str(dp["label_ja"])}@ja ;')
        if dp.get("enum"):
            lines.append(f"    {p}:usesVocabulary {p}:{dp['enum']} ;")
        prov(dp)
        _terminate(lines)
        lines.append("")

    for op in ir["object_properties"]:
        types = ["owl:ObjectProperty"] + [CHAR_MAP[c] for c in (op.get("characteristics") or [])
                                          if c in CHAR_MAP]
        lines.append(f"{p}:{op['name']} a {', '.join(types)} ;")
        if op.get("from"):
            lines.append(f"    rdfs:domain {p}:{op['from']} ;")
        if op.get("to"):
            lines.append(f"    rdfs:range {p}:{op['to']} ;")
        if op.get("cardinality"):
            lines.append(f"    {p}:cardinality {_ttl_str(op['cardinality'])} ;")
        prov(op)
        _terminate(lines)
        lines.append("")

    for v in ir["vocabularies"]:
        lines.append(f"{p}:{v['name']} a skos:ConceptScheme ;")
        prov(v)
        _terminate(lines)
        for i, val in enumerate(v.get("values", [])):
            lines.append(f"{p}:{v['name']}_{i} a skos:Concept ; "
                         f"skos:prefLabel {_ttl_str(val)} ; skos:inScheme {p}:{v['name']} .")
        lines.append("")

    for inv in ir["invariants"]:
        lines.append(f"{p}:{inv['id']} a {p}:Invariant ;")
        lines.append(f"    rdfs:comment {_ttl_str(inv['statement'])} ;")
        prov(inv)
        _terminate(lines)
        lines.append("")

    return "\n".join(lines) + "\n"


def emit_design_md(ir: dict) -> str:
    m = ir["meta"]
    out = [f"# {m.get('domain','')} ドメインオントロジー（自動生成）", "",
           f"- namespace: `{m['namespace']}` / prefix: `{m['prefix']}`",
           f"- built_from: `{m.get('built_from','')}`",
           f"- source_docs: {len(m.get('source_docs', []))} 件", ""]

    # classes grouped by domain_group
    out += ["## クラス", "", "| ドメイン | クラス | 日本語 |", "|---|---|---|"]
    for c in ir["classes"]:
        out.append(f"| {c.get('domain_group','')} | {c['name']} | {c.get('label_ja','')} |")
    out.append("")

    # datatype properties by class
    out += ["## データ型プロパティ", "", "| クラス | プロパティ | 型 | 日本語 | enum |", "|---|---|---|---|---|"]
    for dp in ir["datatype_properties"]:
        out.append(f"| {dp.get('domain','')} | {dp['name']} | {dp.get('type','')} | "
                   f"{dp.get('label_ja','')} | {dp.get('enum') or ''} |")
    out.append("")

    # object properties
    out += ["## オブジェクトプロパティ", "",
            "| 関係 | From → To | カーディナリティ | characteristics | 備考 |",
            "|---|---|---|---|---|"]
    for op in ir["object_properties"]:
        out.append(f"| {op['name']} | {op.get('from','')} → {op.get('to','')} | "
                   f"{op.get('cardinality','')} | {', '.join(op.get('characteristics') or [])} | "
                   f"{op.get('note','')} |")
    out.append("")

    if ir["vocabularies"]:
        out += ["## 統制語彙", ""]
        for v in ir["vocabularies"]:
            out.append(f"- **{v['name']}**: {' | '.join(v.get('values', []))}")
        out.append("")

    if ir["invariants"]:
        out += ["## 不変条件", ""]
        for inv in ir["invariants"]:
            out.append(f"- `{inv['id']}` ({inv.get('kind','')}): {inv['statement']}")
        out.append("")

    if ir["conflicts"] or ir["open_questions"]:
        out += ["## 矛盾・未解決質問", ""]
        for c in ir["conflicts"]:
            out.append(f"- ⚠️ [{c.get('kind','')}] {c.get('detail','')}")
        for q in ir["open_questions"]:
            out.append(f"- ❓ {q.get('text','')}")
        out.append("")

    return "\n".join(out) + "\n"


_CARD_RE = re.compile(r"^(1|多|N|M|\d+)(:(0\.\.1|0\.\.\*|1|多|N|M|\d+))?$")
_LOCAL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def validate_ontology(ir: dict, ttl: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    class_names = {_norm(c["name"]) for c in ir["classes"]}
    vocab_names = {_norm(v["name"]) for v in ir["vocabularies"]}

    # duplicate names
    def dup_check(rows, kind):
        seen = set()
        for r in rows:
            if r["name"] in seen:
                errors.append(f"duplicate {kind} name: {r['name']}")
            seen.add(r["name"])
    dup_check(ir["classes"], "class")
    dup_check(ir["datatype_properties"], "datatype property")
    dup_check(ir["object_properties"], "object property")

    # always-on structural check: Turtle local names must be safe even when
    # rdflib is unavailable to catch broken names via the parse below.
    def local_name_check(rows, name_field="name"):
        for r in rows:
            name = r.get(name_field)
            if name and not _LOCAL_NAME_RE.match(name):
                errors.append(f"unsafe Turtle local name: {name}")
    local_name_check(ir["classes"])
    local_name_check(ir["datatype_properties"])
    local_name_check(ir["object_properties"])
    local_name_check(ir["vocabularies"])
    local_name_check(ir["invariants"], name_field="id")

    for dp in ir["datatype_properties"]:
        if dp.get("domain") and _norm(dp["domain"]) not in class_names:
            errors.append(f"datatype property {dp['name']} has dangling domain: {dp['domain']}")
        if dp.get("enum") and _norm(dp["enum"]) not in vocab_names:
            errors.append(f"datatype property {dp['name']} references unknown enum: {dp['enum']}")
        if dp.get("_type_warning"):
            warnings.append(f"datatype property {dp['name']} had unknown type "
                            f"'{dp['_type_warning']}', coerced to string")

    for op in ir["object_properties"]:
        for side in ("from", "to"):
            if op.get(side) and _norm(op[side]) not in class_names:
                errors.append(f"object property {op['name']} has dangling {side}: {op[side]}")
        if op.get("cardinality") and not _CARD_RE.match(op["cardinality"]):
            errors.append(f"object property {op['name']} has bad cardinality: {op['cardinality']}")
        for ch in (op.get("characteristics") or []):
            if ch not in CHAR_MAP:
                errors.append(f"object property {op['name']} has unknown characteristic: {ch}")

    for row in [*ir["classes"], *ir["datatype_properties"], *ir["object_properties"]]:
        if not row.get("provenance"):
            warnings.append(f"{row['name']} has no provenance")

    if ttl is not None:
        try:
            from rdflib import Graph
            Graph().parse(data=ttl, format="turtle")
        except ImportError:
            warnings.append("rdflib not installed; skipped Turtle syntax validation")
        except Exception as e:  # noqa: BLE001 — surface any parse error as a gate failure
            errors.append(f"Turtle parse error: {e}")

    return {"errors": errors, "warnings": warnings}


_EMIT_FILE = {"yml": "ontology.yml", "ttl": "ontology.ttl", "md": "ontology-design.md"}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ontology"


def infer_meta(directory, namespace=None, prefix=None, domain=None, source_docs=None):
    warnings: list[str] = []
    base = Path(directory).resolve()
    slug = _slug(base.name)
    if not namespace:
        namespace = f"https://example.org/{slug}#"
        warnings.append(f"no --namespace given; using placeholder {namespace}")
    if not prefix:
        prefix = slug.replace("-", "")[:8] or "onto"
    meta = {"namespace": namespace, "prefix": prefix, "domain": domain or base.name,
            "built_from": base.name, "source_docs": source_docs or [],
            "generated_by": "dotmd-parser ontology v1"}
    return meta, warnings


def write_ontology(ir, out_dir, emit=("yml", "ttl", "md")) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderers = {"yml": emit_yaml, "ttl": emit_ttl, "md": emit_design_md}
    written: list[str] = []
    for kind in ("yml", "ttl", "md"):
        if kind in emit:
            path = out / _EMIT_FILE[kind]
            path.write_text(renderers[kind](ir), encoding="utf-8")
            written.append(str(path))
    return written


def build_ontology(directory, out_dir=None, emit=("yml", "ttl", "md"), *,
                   namespace=None, prefix=None, domain=None,
                   api_key=None, extensions=None, model=None, caller=None) -> dict:
    partials = extract_ontology(directory, api_key=api_key, extensions=extensions,
                                model=model, caller=caller)
    source_docs = [p["source"] for p in partials]
    meta, meta_warnings = infer_meta(directory, namespace, prefix, domain, source_docs)
    ir = merge_ontology(partials, meta)
    ttl = emit_ttl(ir)
    report = validate_ontology(ir, ttl=ttl)
    out_dir = out_dir or (Path(directory).resolve() / "ontology")
    written = write_ontology(ir, out_dir, emit=emit)
    return {"ir": ir, "report": report, "written": written, "meta_warnings": meta_warnings}


def format_host_agent_plan(directory, extensions=None) -> str:
    if extensions is None:
        extensions = [".md", ".txt"]
    root = Path(directory).resolve()
    docs = [fp.relative_to(root).as_posix()
            for ext in extensions for fp in sorted(root.rglob(f"*{ext}"))
            if not any(part.startswith(".") for part in fp.relative_to(root).parts)]
    template = llm.load_prompt_template("extract-ontology")
    files = "\n".join(f"- `{d}`" for d in docs)
    return (
        "# dotmd-parser — ontology host-agent plan\n\n"
        f"Target: `{root}`  ({len(docs)} documents)\n\n"
        "For EACH document below, run the extraction task and collect the JSON "
        "objects into a list of `{\"source\": <path>, \"elements\": <json>}`. "
        "Save that list to `ontology.json`, then apply:\n\n"
        "```bash\n"
        f'dotmd-parser ontology "{root}" --apply-from ontology.json\n'
        "```\n\n"
        "## Documents\n\n" + files + "\n\n"
        "## Extraction task (per document)\n\n```\n" + template.strip() + "\n```\n"
    )


def apply_ontology_from_file(directory, json_path, out_dir=None,
                             emit=("yml", "ttl", "md"),
                             namespace=None, prefix=None, domain=None) -> dict:
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"ontology JSON not found: {json_path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {json_path}: {e}") from e
    # accept either a list of partials, or a single {"source","elements"} object
    partials = data if isinstance(data, list) else [data]
    partials = [{"source": p.get("source", "unknown"),
                 "elements": _normalize_types(p.get("elements", p))} for p in partials]
    source_docs = [p["source"] for p in partials]
    meta, meta_warnings = infer_meta(directory, namespace, prefix, domain, source_docs)
    ir = merge_ontology(partials, meta)
    report = validate_ontology(ir, ttl=emit_ttl(ir))
    out_dir = out_dir or (Path(directory).resolve() / "ontology")
    written = write_ontology(ir, out_dir, emit=emit)
    return {"ir": ir, "report": report, "written": written, "meta_warnings": meta_warnings}


def eval_ontology(ir, corpus_summary, model=None, api_key=None, *, caller=None) -> dict:
    """Opt-in LLM rubric score: how well `ir` covers/faithfully reflects the corpus."""
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError("ANTHROPIC_API_KEY required for --eval (or inject a caller).")
    resolved_model = model or os.environ.get("CLAUDE_MODEL", llm.DEFAULT_MODEL)
    summary = (f"classes={[c['name'] for c in ir['classes']]}\n"
               f"object_properties={[o['name'] for o in ir['object_properties']]}\n"
               f"datatype_properties={[d['name'] for d in ir['datatype_properties']]}")
    template = llm.load_prompt_template("eval-ontology")
    prompt = (template.replace("{{ontology_summary}}", summary)
                      .replace("{{corpus_summary}}", corpus_summary))
    first, _, rest = prompt.partition("\n")
    raw = (caller(rest.strip(), first.strip(), resolved_model) if caller
           else llm.call_claude(rest.strip(), first.strip(), api_key, resolved_model))
    return llm.extract_json(raw)
