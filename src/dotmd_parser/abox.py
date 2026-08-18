"""dotmd-parser — ABox generation: materialize class instances from CSV data (v4c).

Maps each CSV (via explicit --map Class=file) to an ontology class, auto-matches
CSV columns to the class's datatype properties by name similarity, and emits an
instances TTL (datatype-property literals only). stdlib generation, deterministic;
reads the ontology, writes only ontology-abox.* — never mutates the ontology.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


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
