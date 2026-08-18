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

from dotmd_parser.ontology import XSD_MAP


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


def _norm_ident(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_columns_to_props(dprops, fieldnames, threshold: float) -> dict[str, str]:
    matches: dict[str, str] = {}
    norm_cols = [(col, _norm_ident(col)) for col in fieldnames]
    for dp in dprops:
        pn = _norm_ident(dp["name"])
        best = None  # (ratio, column)
        for col, nc in norm_cols:
            if not nc:
                continue
            ratio = difflib.SequenceMatcher(None, pn, nc).ratio()
            if best is None or ratio > best[0] or (ratio == best[0] and col < best[1]):
                best = (ratio, col)
        if best and best[0] >= threshold:
            matches[dp["name"]] = best[1]
    return matches


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _lit(value: str, xsd_type: str) -> str:
    return f'"{_esc(value)}"^^{XSD_MAP.get(xsd_type, "xsd:string")}'


def materialize_class(class_name, dprops, prop_col_map, rows, prefix) -> tuple[list[str], int]:
    lines: list[str] = []
    count = 0
    for i, row in enumerate(rows, start=1):
        count += 1
        subj = f"{prefix}:{class_name}_{i}"
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
    return lines, count
