"""dotmd-parser — ontology integrity audit (v2).

Active contradiction detection (detect -> adversarial verify) and name-match
support (difflib candidates -> LLM adjudication) over a built ontology.json +
its source corpus. Emits a separate ontology-audit.{md,json}; never mutates
ontology.yml.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from dotmd_parser import llm
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


def _resolve_model(model: str | None) -> str:
    return model or os.environ.get("CLAUDE_MODEL", llm.DEFAULT_MODEL)


def _require_key(api_key: str | None, caller) -> str | None:
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Export it, or use --plan / --structural-only."
        )
    return api_key


def _call(prompt: str, system: str, model: str, api_key, caller):
    return caller(prompt, system, model) if caller else llm.call_claude(prompt, system, api_key, model)


def _ontology_summary(ir: dict) -> str:
    inv = "\n".join(f"- {i.get('id','?')}: {i.get('statement','')}" for i in ir["invariants"])
    classes = ", ".join(c["name"] for c in ir["classes"])
    return f"classes: {classes}\ninvariants:\n{inv}"


def detect_contradictions(ir, corpus, model=None, api_key=None, *, caller=None) -> dict:
    """Stage 1: propose candidate contradictions between ontology and corpus."""
    api_key = _require_key(api_key, caller)
    m = _resolve_model(model)
    template = llm.load_prompt_template("detect-contradictions")
    corpus_block = "\n\n".join(f"### {d['path']}\n{d['content']}" for d in corpus)
    prompt = (template.replace("{{ontology_summary}}", _ontology_summary(ir))
                      .replace("{{corpus}}", corpus_block))
    first, _, rest = prompt.partition("\n")
    raw = _call(rest.strip() or prompt, first.strip() or "You detect contradictions.", m, api_key, caller)
    parsed = llm.extract_json(raw)
    return {"candidates": parsed.get("candidates", []),
            "open_questions": parsed.get("open_questions", [])}


def verify_contradictions(candidates, ir, model=None, api_key=None, *, caller=None) -> list[dict]:
    """Stage 2: adversarial skeptic — refute what's explainable by ontology structure.

    Only refuted=False survivors are kept; a missing/omitted refuted key
    defaults to True (dropped) so an ambiguous verdict never false-positives.
    """
    api_key = _require_key(api_key, caller)
    m = _resolve_model(model)
    template = llm.load_prompt_template("verify-contradiction")
    context = _ontology_summary(ir)
    survivors: list[dict] = []
    for cand in candidates:
        prompt = (template.replace("{{candidate}}", json.dumps(cand, ensure_ascii=False))
                          .replace("{{context}}", context))
        first, _, rest = prompt.partition("\n")
        raw = _call(rest.strip() or prompt, first.strip() or "You are a skeptic.", m, api_key, caller)
        verdict = llm.extract_json(raw)
        if not verdict.get("refuted", True):   # default refuted=True when missing
            survivors.append({**cand, "verdict": "CONFIRMED",
                              "refutation_checked": verdict.get("reason", "")})
    return survivors


def adjudicate_namematches(candidates, ir, corpus, model=None, api_key=None, *, caller=None) -> list[dict]:
    """Stage 2: LLM name-match adjudication (propose-only).

    For each candidate pair, calls an LLM to decide if they refer to the same entity.
    Only pairs with decision == "same" are emitted. from/to are set such that:
    - to = canonical (or cand["a"] if canonical is empty)
    - from = the other term (whichever is NOT canonical)
    """
    api_key = _require_key(api_key, caller)
    m = _resolve_model(model)
    template = llm.load_prompt_template("adjudicate-namematch")
    matches: list[dict] = []
    for cand in candidates:
        prompt = (template.replace("{{a}}", str(cand["a"])).replace("{{b}}", str(cand["b"])))
        first, _, rest = prompt.partition("\n")
        raw = _call(rest.strip() or prompt, first.strip() or "You adjudicate name matches.",
                    m, api_key, caller)
        v = llm.extract_json(raw)
        if v.get("decision") == "same":
            canonical = v.get("canonical") or cand["a"]
            other = cand["b"] if canonical == cand["a"] else cand["a"]
            matches.append({
                "from": other, "to": canonical, "canonical": canonical,
                "confidence": v.get("confidence", 0.0), "rationale": v.get("rationale", ""),
                "provenance": [],
            })
    return matches
