"""dotmd-parser — enum grounding: verify ontology controlled vocabularies against CSV data (v4a).

Auto-matches each vocabulary to the CSV column whose distinct values best
overlap the enum, then reports enum values absent from data (dead) and data
values absent from the enum (coverage gaps). stdlib-only, deterministic;
reads only, never mutates the ontology.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


def load_vocabularies(directory) -> list[dict]:
    path = Path(directory).resolve() / "ontology" / "ontology.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build the ontology first: dotmd-parser ontology \"{directory}\"."
        )
    ir = json.loads(path.read_text(encoding="utf-8"))
    return ir.get("vocabularies", []) if isinstance(ir, dict) else []


def column_counts(csv_paths) -> tuple[dict, list[str]]:
    """Return ({(file, column): Counter(value->count)}, warnings). Stripped, empties dropped."""
    columns: dict = {}
    warnings: list[str] = []
    for p in csv_paths:
        try:
            with open(p, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                counters = {col: Counter() for col in fieldnames}
                for row in reader:
                    for col in fieldnames:
                        val = (row.get(col) or "").strip()
                        if val:
                            counters[col][val] += 1
            for col, counter in counters.items():
                if counter:
                    columns[(str(p), col)] = counter
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            warnings.append(f"could not read {p}: {e}")
    return columns, warnings
