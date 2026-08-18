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


def _enum_set(vocab: dict) -> set:
    """Extract unique stripped values from a vocab's enum."""
    return {v.strip() for v in (vocab.get("values") or []) if v and v.strip()}


def match_vocabulary(vocab: dict, columns: dict, threshold: float) -> dict | None:
    """Pick the column whose values best cover this vocab's enum; None if below bar.

    Returns: {"file", "column", "score": round(.,3), "shared", "counter": Counter} or None
    Condition: shared >= 2 and score >= threshold
    Tie-break: -shared, -score, file asc, column asc (deterministic)
    """
    E = _enum_set(vocab)
    if not E:
        return None
    candidates = []
    for (file, col), counter in columns.items():
        shared = len(E & set(counter))
        if shared == 0:
            continue
        candidates.append((shared, shared / len(E), file, col, counter))
    if not candidates:
        return None
    # deterministic: max shared, then max score, then file asc, then column asc
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    shared, score, file, col, counter = candidates[0]
    if shared < 2 or score < threshold:
        return None
    return {
        "file": file,
        "column": col,
        "score": round(score, 3),
        "shared": shared,
        "counter": counter,
    }


def verify_enums(
    vocabs: list[dict], columns: dict, threshold: float = 0.5, top: int = 20
) -> dict:
    """Build per-vocab findings: enum_not_in_data, data_not_in_enum top-N, coverage.

    Returns: {"vocabularies": [...], "unmatched": [...]}
    Strips internal `counter` from emitted `matched`.
    """
    vocabularies = []
    unmatched = []
    for vocab in vocabs:
        name = vocab.get("name", "")
        E = _enum_set(vocab)
        m = match_vocabulary(vocab, columns, threshold)
        if m is None:
            unmatched.append(name)
            vocabularies.append(
                {
                    "name": name,
                    "enum_count": len(E),
                    "matched": None,
                    "enum_not_in_data": [],
                    "data_not_in_enum": [],
                    "data_not_in_enum_omitted": 0,
                    "coverage": 0.0,
                }
            )
            continue
        counter = m["counter"]
        D = set(counter)
        enum_not = sorted(E - D)
        extra = sorted(
            ((val, counter[val]) for val in D - E), key=lambda x: (-x[1], x[0])
        )
        data_not = [{"value": v, "count": c} for v, c in extra[:top]]
        omitted = max(0, len(extra) - top)
        vocabularies.append(
            {
                "name": name,
                "enum_count": len(E),
                "matched": {
                    "file": m["file"],
                    "column": m["column"],
                    "score": m["score"],
                    "shared": m["shared"],
                },
                "enum_not_in_data": enum_not,
                "data_not_in_enum": data_not,
                "data_not_in_enum_omitted": omitted,
                "coverage": m["score"],
            }
        )
    return {"vocabularies": vocabularies, "unmatched": unmatched}
