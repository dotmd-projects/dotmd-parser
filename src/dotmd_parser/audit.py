"""dotmd-parser — ontology integrity audit (v2).

Active contradiction detection (detect -> adversarial verify) and name-match
support (difflib candidates -> LLM adjudication) over a built ontology.json +
its source corpus. Emits a separate ontology-audit.{md,json}; never mutates
ontology.yml.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from dotmd_parser.ontology import _norm

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def load_ir(directory: str | Path) -> dict:
    path = Path(directory).resolve() / "ontology" / "ontology.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build the ontology first: "
            f"dotmd-parser ontology \"{directory}\" (emits ontology.json)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _known_terms(ir: dict) -> set[str]:
    names: set[str] = set()
    for c in ir["classes"]:
        names.add(_norm(c["name"]))
    for dp in ir["datatype_properties"]:
        names.add(_norm(dp["name"]))
    for op in ir["object_properties"]:
        names.add(_norm(op["name"]))
    for v in ir["vocabularies"]:
        names.add(_norm(v["name"]))
    return names


def structural_findings(ir: dict) -> list[dict]:
    """Deterministic integrity findings over the IR (no LLM)."""
    findings: list[dict] = []

    # merge-conflict promotion (v1 merge_ontology already flags naming conflicts)
    for c in ir.get("conflicts", []):
        findings.append({
            "kind": "merge-conflict",
            "detail": c.get("detail", ""),
            "provenance": list(c.get("provenance", [])),
        })

    # vocab-overlap: same value string across two different vocabularies
    seen: dict[str, str] = {}
    for v in ir["vocabularies"]:
        for val in v.get("values", []):
            if val in seen and seen[val] != v["name"]:
                findings.append({
                    "kind": "vocab-overlap",
                    "detail": f'value "{val}" appears in both {seen[val]} and {v["name"]}',
                    "provenance": list(v.get("provenance", [])),
                })
            else:
                seen[val] = v["name"]

    # dangling-invariant-ref: an invariant whose ASCII word tokens match no known term
    known = _known_terms(ir)
    for inv in ir["invariants"]:
        tokens = {_norm(t) for t in _WORD_RE.findall(inv.get("statement", ""))}
        if tokens and not (tokens & known):
            findings.append({
                "kind": "dangling-invariant-ref",
                "detail": f'invariant {inv.get("id","?")} references no known ontology term',
                "provenance": list(inv.get("provenance", [])),
            })

    return findings


NAMEMATCH_THRESHOLD = 0.6


def _surface_terms(ir: dict) -> list[str]:
    terms: list[str] = []
    for v in ir["vocabularies"]:
        terms.extend(v.get("values", []))
    for c in ir["classes"]:
        terms.append(c["name"])
        if c.get("label_ja"):
            terms.append(c["label_ja"])
    # dedup, stable order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _significant_common_substring(a: str, b: str) -> bool:
    """True if a and b share a meaningful contiguous substring (>=2 CJK chars
    or >=4 ASCII chars), so CJK label variants surface even at low difflib ratio."""
    na, nb = _norm(a), _norm(b)
    match = difflib.SequenceMatcher(None, na, nb).find_longest_match(0, len(na), 0, len(nb))
    sub = na[match.a:match.a + match.size].strip()
    if not sub:
        return False
    cjk = sum(1 for ch in sub if ord(ch) >= 0x3040)
    if cjk >= 2:
        return True
    return len(sub) >= 4 and sub.isascii()


def namematch_candidates(ir: dict, threshold: float = NAMEMATCH_THRESHOLD) -> list[dict]:
    """Deterministic near-duplicate term pairs via difflib ratio on normalized text."""
    terms = _surface_terms(ir)
    cands: list[dict] = []
    for i in range(len(terms)):
        for j in range(i + 1, len(terms)):
            a, b = terms[i], terms[j]
            if _norm(a) == _norm(b):
                continue
            score = difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
            if score >= threshold or _significant_common_substring(a, b):
                cands.append({"a": a, "b": b, "score": round(score, 3)})
    cands.sort(key=lambda c: (-c["score"], c["a"], c["b"]))
    return cands
