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


_REPORT_JSON = "ontology-enum-report.json"
_REPORT_MD = "ontology-enum-report.md"


def emit_enum_report_json(findings: dict) -> str:
    """Emit deterministic JSON report with sorted keys."""
    return json.dumps(findings, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def emit_enum_report_md(findings: dict) -> str:
    """Emit markdown report from findings."""
    m = findings["meta"]
    out = ["# オントロジー enum 実在検証レポート", "",
           f"- threshold: {m.get('threshold')} / top: {m.get('top')}",
           f"- data: {', '.join(m.get('data', []))}",
           ""]
    for v in findings["vocabularies"]:
        out.append(f"## {v['name']}  (enum {v['enum_count']} 値, coverage {v['coverage']})")
        if v["matched"]:
            mm = v["matched"]
            out.append(f"- matched: `{mm['file']}:{mm['column']}` (score {mm['score']}, shared {mm['shared']})")
        else:
            out.append("- matched: (対応列なし)")
        if v["enum_not_in_data"]:
            out.append(f"- ⚠️ enum にあるがデータに無い: {', '.join(v['enum_not_in_data'])}")
        if v["data_not_in_enum"]:
            out.append("- データにあるが enum に無い (頻度順):")
            out.append("")
            out.append("  | value | count |")
            out.append("  |---|---|")
            for d in v["data_not_in_enum"]:
                out.append(f"  | {d['value']} | {d['count']} |")
            if v["data_not_in_enum_omitted"]:
                out.append(f"  | … +{v['data_not_in_enum_omitted']} more | |")
        out.append("")
    if findings["unmatched"]:
        out += ["## 対応列なし (unmatched)", "", ", ".join(findings["unmatched"]), ""]
    if findings["warnings"]:
        out += ["## 警告", ""] + [f"- {w}" for w in findings["warnings"]] + [""]
    return "\n".join(out) + "\n"


def format_enum_summary(findings: dict) -> str:
    """Format a brief summary of findings."""
    dead = sum(1 for v in findings["vocabularies"] if v["enum_not_in_data"])
    return (f"vocabularies={len(findings['vocabularies'])} "
            f"with_dead_enum={dead} unmatched={len(findings['unmatched'])} "
            f"warnings={len(findings['warnings'])}")


def run_enum_verify(directory, data_paths, threshold=0.5, top=20, out_dir=None) -> dict:
    """Orchestrate enum verification: load vocabs, read data, verify, emit reports.

    Args:
        directory: Path to project containing ontology/ontology.json
        data_paths: List of CSV paths to verify against
        threshold: Min vocab->column match score (0-1)
        top: Max data_not_in_enum entries to report per vocab
        out_dir: Output directory for reports (default: directory/ontology)

    Returns:
        {"findings": {...}, "written": [json_path, md_path], "summary": "..."}

    Raises:
        ValueError: If data_paths is non-empty but all CSV files are unreadable.
    """
    vocabs = load_vocabularies(directory)
    columns, warnings = column_counts(data_paths)
    if data_paths and not columns:
        raise ValueError("no readable data columns from --data files: " + "; ".join(warnings))
    result = verify_enums(vocabs, columns, threshold=threshold, top=top)
    findings = {
        "meta": {"audited": "ontology/ontology.json", "data": [str(p) for p in data_paths],
                 "threshold": threshold, "top": top,
                 "generated_by": "dotmd-parser ontology-verify-enums v4a"},
        "vocabularies": result["vocabularies"],
        "unmatched": result["unmatched"],
        "warnings": warnings,
    }
    out = Path(out_dir) if out_dir else (Path(directory).resolve() / "ontology")
    out.mkdir(parents=True, exist_ok=True)
    (out / _REPORT_JSON).write_text(emit_enum_report_json(findings), encoding="utf-8")
    (out / _REPORT_MD).write_text(emit_enum_report_md(findings), encoding="utf-8")
    return {"findings": findings,
            "written": [str(out / _REPORT_JSON), str(out / _REPORT_MD)],
            "summary": format_enum_summary(findings)}
