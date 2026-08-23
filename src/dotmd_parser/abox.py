"""dotmd-parser — ABox generation: materialize class instances from CSV data (v4c).

Maps each CSV (via explicit --map Class=file) to an ontology class, auto-matches
CSV columns to the class's datatype properties by name similarity, and emits an
instances TTL (datatype-property literals only). stdlib generation, deterministic;
reads the ontology, writes only ontology-abox.* — never mutates the ontology.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
from pathlib import Path

from dotmd_parser.abox_links import subject_uri
from dotmd_parser.enums import load_vocabularies
from dotmd_parser.ontology import XSD_MAP

# Minimum shared enum-value count to accept a value-overlap column match;
# mirrors the value-match convention used in enums.py.
_MIN_VALUE_OVERLAP = 2


def load_class_dprops(directory) -> tuple[dict, set, dict]:
    path = Path(directory).resolve() / "ontology" / "ontology.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build the ontology first: dotmd-parser ontology \"{directory}\"."
        )
    ir = json.loads(path.read_text(encoding="utf-8"))
    meta = (ir.get("meta") or {}) if isinstance(ir, dict) else {}
    if not (meta.get("namespace") and meta.get("prefix")):
        raise RuntimeError(
            "ontology.json is missing meta.namespace/prefix. Rebuild with dotmd-parser ontology."
        )
    class_names = {c["name"] for c in ir.get("classes", [])}
    dprops_by_class: dict[str, list[dict]] = {}
    for dp in ir.get("datatype_properties", []):
        dprops_by_class.setdefault(dp.get("domain"), []).append(dp)
    return {"namespace": meta["namespace"], "prefix": meta["prefix"]}, class_names, dprops_by_class


def load_object_props(directory) -> list[dict]:
    """Return the IR's object_properties list (empty if absent)."""
    path = Path(directory).resolve() / "ontology" / "ontology.json"
    ir = json.loads(path.read_text(encoding="utf-8"))
    return ir.get("object_properties", []) if isinstance(ir, dict) else []


def parse_maps(map_args, class_names) -> dict[str, str]:
    maps: dict[str, str] = {}
    for arg in map_args:
        if "=" not in arg:
            raise ValueError(f"bad --map (expected Class=file): {arg!r}")
        cls, _, file = arg.partition("=")
        cls = cls.strip()
        if cls not in class_names:
            raise ValueError(f"unknown class in --map: {cls!r}")
        if cls in maps:
            raise ValueError(f"duplicate class in --map: {cls!r}")
        maps[cls] = file.strip()
    return maps


def parse_map_cols(mapcol_args, dprops_by_class) -> dict:
    """Parse ["Class.prop=column", ...] -> {Class: {prop: column}}. Validate class+prop."""
    out: dict = {}
    for arg in mapcol_args or []:
        if "=" not in arg or "." not in arg.split("=", 1)[0]:
            raise ValueError(f"bad --map-col (expected Class.prop=column): {arg!r}")
        lhs, _, col = arg.partition("=")
        cls, _, prop = lhs.strip().partition(".")
        cls, prop, col = cls.strip(), prop.strip(), col.strip()
        if cls not in dprops_by_class:
            raise ValueError(f"unknown class in --map-col: {cls!r}")
        if prop not in {dp["name"] for dp in dprops_by_class[cls]}:
            raise ValueError(f"{prop!r} is not a datatype property of {cls!r}")
        out.setdefault(cls, {})[prop] = col
    return out


def _norm_ident(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_columns_to_props(dprops, fieldnames, threshold: float, *,
                           columns_values=None, vocab_values=None, explicit=None) -> tuple[dict, dict]:
    """Match each datatype property to a CSV column.

    Precedence: explicit (--map-col) > value-overlap (enum props) > difflib name similarity.
    Returns (matches, method) where method[prop] in {"explicit","value","name"}.
    """
    explicit = explicit or {}
    vocab_values = vocab_values or {}
    columns_values = columns_values or {}
    fieldset = set(fieldnames)
    norm_cols = [(col, _norm_ident(col)) for col in fieldnames]

    matches: dict[str, str] = {}
    method: dict[str, str] = {}
    # Note: a single column may be selected by more than one property (allowed by design).
    for dp in dprops:
        name = dp["name"]
        # 1) explicit override
        if name in explicit and explicit[name] in fieldset:
            matches[name] = explicit[name]
            method[name] = "explicit"
            continue
        # 2) value-overlap for enum-typed props
        enum_name = dp.get("enum")
        E = vocab_values.get(enum_name) if enum_name else None
        if E:
            best = None  # (shared, column)
            for col in fieldnames:
                shared = len(E & columns_values.get(col, set()))
                if shared == 0:
                    continue
                if best is None or shared > best[0] or (shared == best[0] and col < best[1]):
                    best = (shared, col)
            if best and best[0] >= _MIN_VALUE_OVERLAP and best[0] / len(E) >= threshold:
                matches[name] = best[1]
                method[name] = "value"
                continue
        # 3) name similarity (difflib)
        pn = _norm_ident(name)
        best = None  # (ratio, column)
        for col, nc in norm_cols:
            if not nc:
                continue
            ratio = difflib.SequenceMatcher(None, pn, nc).ratio()
            if best is None or ratio > best[0] or (ratio == best[0] and col < best[1]):
                best = (ratio, col)
        if best and best[0] >= threshold:
            matches[name] = best[1]
            method[name] = "name"
    return matches, method


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _lit(value: str, xsd_type: str) -> str:
    return f'"{_esc(value)}"^^{XSD_MAP.get(xsd_type, "xsd:string")}'


def materialize_class(class_name, dprops, prop_col_map, rows, prefix,
                      id_col=None) -> tuple[list[str], int, dict]:
    lines: list[str] = []
    count = 0
    keys: set[str] = set()
    duplicate_ids = 0
    missing_id_rows = 0
    for i, row in enumerate(rows, start=1):
        count += 1
        subj, key = subject_uri(prefix, class_name, row, i, id_col)
        if id_col is not None:
            if key is None:
                missing_id_rows += 1
            else:
                if key in keys:
                    duplicate_ids += 1
                keys.add(key)
        lines.append(f"{subj} a {prefix}:{class_name} ;")
        for dp in dprops:
            col = prop_col_map.get(dp["name"])
            if not col:
                continue
            val = (row.get(col) or "").strip()
            if val:
                lines.append(f"    {prefix}:{dp['name']} {_lit(val, dp.get('type'))} ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."   # terminate the subject block
        lines.append("")
    keyinfo = {"keys": keys, "duplicate_ids": duplicate_ids,
               "missing_id_rows": missing_id_rows}
    return lines, count, keyinfo


_ABOX_TTL = "ontology-abox.ttl"
_ABOX_REPORT = "ontology-abox-report.json"


def emit_abox_ttl(meta: dict, class_blocks: list) -> str:
    p = meta["prefix"]
    lines = [f"@prefix {p}: <{meta['namespace']}> .",
             "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .", ""]
    for block in class_blocks:
        lines.extend(block)
    return "\n".join(lines).rstrip("\n") + "\n"


def format_abox_summary(findings: dict) -> str:
    total = sum(c["instances"] for c in findings["classes"])
    return (f"classes={len(findings['classes'])} instances={total} "
            f"warnings={len(findings['warnings'])}")


def run_abox(directory, maps, threshold=0.6, out_dir=None, map_cols=None) -> dict:
    meta, class_names, dprops_by_class = load_class_dprops(directory)
    prefix = meta["prefix"]
    vocab_values = {v["name"]: {x.strip() for x in (v.get("values") or []) if x and x.strip()}
                    for v in load_vocabularies(directory)}
    map_cols = map_cols or {}
    class_reports = []
    warnings: list[str] = []
    blocks: list[list[str]] = []
    for cls, file in maps.items():
        try:
            with open(file, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                rows = list(reader)
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            raise ValueError(f"could not read {file}: {e}") from e
        dprops = dprops_by_class.get(cls, [])
        explicit = (map_cols or {}).get(cls, {})
        for prop, col in explicit.items():
            if col not in fieldnames:
                raise ValueError(
                    f"--map-col {cls}.{prop}: column {col!r} not found in {file}"
                )
        columns_values = {col: {(r.get(col) or "").strip() for r in rows if (r.get(col) or "").strip()}
                          for col in fieldnames}
        prop_col, method = match_columns_to_props(
            dprops, fieldnames, threshold,
            columns_values=columns_values, vocab_values=vocab_values,
            explicit=explicit)
        lines, count, _keyinfo = materialize_class(cls, dprops, prop_col, rows, prefix)
        blocks.append(lines)
        unmatched = [dp["name"] for dp in dprops if dp["name"] not in prop_col]
        if not prop_col:
            warnings.append(f"class {cls} matched no columns; only rdf:type materialized")
        class_reports.append({"class": cls, "file": str(file), "instances": count,
                              "matched": prop_col, "match_method": method,
                              "unmatched_props": unmatched})

    findings = {
        "meta": {"audited": "ontology/ontology.json", "namespace": meta["namespace"],
                 "prefix": prefix, "threshold": threshold,
                 "generated_by": "dotmd-parser ontology-abox v4c"},
        "classes": class_reports,
        "warnings": warnings,
    }
    out = Path(out_dir) if out_dir else (Path(directory).resolve() / "ontology")
    out.mkdir(parents=True, exist_ok=True)
    (out / _ABOX_TTL).write_text(emit_abox_ttl(meta, blocks), encoding="utf-8")
    (out / _ABOX_REPORT).write_text(
        json.dumps(findings, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"findings": findings,
            "written": [str(out / _ABOX_TTL), str(out / _ABOX_REPORT)],
            "summary": format_abox_summary(findings)}
