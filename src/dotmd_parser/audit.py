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
from pathlib import Path

from dotmd_parser import llm
from dotmd_parser.analyze import scan_documents
from dotmd_parser.ontology import _norm


def load_ir(directory: str | Path) -> dict:
    path = Path(directory).resolve() / "ontology" / "ontology.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build the ontology first: "
            f"dotmd-parser ontology \"{directory}\" (emits ontology.json)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def structural_findings(ir: dict) -> list[dict]:
    """Deterministic integrity findings over the IR (no LLM)."""
    findings: list[dict] = []

    # promote recorded conflicts: naming conflicts (from merge) vs text-extracted contradictions
    for c in ir.get("conflicts", []):
        findings.append({
            "kind": "merge-conflict" if c.get("kind") == "naming" else "recorded-conflict",
            "detail": c.get("detail", ""),
            "provenance": list(c.get("provenance") or []),
        })

    # vocab-overlap: same value string across two different vocabularies
    seen: dict[str, str] = {}
    for v in ir.get("vocabularies", []):
        for val in v.get("values", []):
            if val in seen and seen[val] != v["name"]:
                findings.append({
                    "kind": "vocab-overlap",
                    "detail": f'value "{val}" appears in both {seen[val]} and {v["name"]}',
                    "provenance": list(v.get("provenance") or []),
                })
            else:
                seen[val] = v["name"]

    return findings


NAMEMATCH_THRESHOLD = 0.6


def _surface_terms(ir: dict) -> list[str]:
    terms: list[str] = []
    for v in ir.get("vocabularies", []):
        terms.extend(v.get("values", []))
    for c in ir.get("classes", []):
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
    inv = "\n".join(f"- {i.get('id','?')}: {i.get('statement','')}" for i in ir.get("invariants", []))
    classes = ", ".join(c["name"] for c in ir.get("classes", []))
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


_AUDIT_JSON = "ontology-audit.json"
_AUDIT_MD = "ontology-audit.md"


def _corpus(directory) -> list[dict]:
    docs = scan_documents(directory, extensions=[".md", ".txt"])
    # exclude the ontology output dir
    return [{"path": d["path"], "content": d["content"]}
            for d in docs if not d["path"].startswith("ontology/")]


def run_audit(directory, out_dir=None, structural_only=False, model=None, api_key=None,
              *, caller=None) -> dict:
    ir = load_ir(directory)
    corpus = _corpus(directory)
    known_paths = {d["path"] for d in corpus}
    warnings: list[str] = []
    structural = structural_findings(ir)

    contradictions: list[dict] = []
    name_matches: list[dict] = []
    open_qs: list[dict] = []

    if not structural_only:
        det = detect_contradictions(ir, corpus, model=model, api_key=api_key, caller=caller)
        contradictions = verify_contradictions(det["candidates"], ir, model=model,
                                                api_key=api_key, caller=caller)
        open_qs = det["open_questions"]
        for c in contradictions:
            for ev in c.get("evidence", []):
                if ev not in known_paths:
                    warnings.append(f"contradiction evidence path not in corpus: {ev}")
        cands = namematch_candidates(ir)
        name_matches = adjudicate_namematches(cands, ir, corpus, model=model,
                                              api_key=api_key, caller=caller)

    findings = {
        "meta": {"audited": "ontology/ontology.json", "corpus": str(directory),
                 "generated_by": "dotmd-parser ontology-audit v2"},
        "structural": structural,
        "contradictions": sorted(contradictions, key=_severity_key),
        "name_matches": name_matches,
        "open_questions": open_qs,
        "warnings": warnings,
    }
    out = Path(out_dir) if out_dir else (Path(directory).resolve() / "ontology")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    (out / _AUDIT_JSON).write_text(emit_audit_json(findings), encoding="utf-8")
    written.append(str(out / _AUDIT_JSON))
    (out / _AUDIT_MD).write_text(emit_audit_md(findings), encoding="utf-8")
    written.append(str(out / _AUDIT_MD))
    return {"findings": findings, "written": written, "summary": format_audit_summary(findings)}


_SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _severity_key(c: dict):
    return (_SEV_ORDER.get(c.get("severity", "low"), 3), c.get("claim", ""))


def emit_audit_json(findings: dict) -> str:
    return json.dumps(findings, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def emit_audit_md(findings: dict) -> str:
    out = ["# オントロジー整合性監査レポート", "",
           "> LLM 由来の矛盾・名寄せ所見は**非決定的**です。人間の確認を前提にしてください。", ""]
    out += ["## 矛盾 (CONFIRMED)", ""]
    if findings["contradictions"]:
        for c in findings["contradictions"]:
            out.append(f"### [{c.get('severity','')}] {c.get('claim','')}")
            out.append(f"- violates: {c.get('violates','')}")
            out.append(f"- evidence: {', '.join(c.get('evidence', []))}")
            out.append(f"- refutation_checked: {c.get('refutation_checked','')}")
            out.append("")
    else:
        out += ["（CONFIRMED な矛盾なし）", ""]

    out += ["## 名寄せ提案", "", "| from | → to | canonical | confidence | rationale |",
            "|---|---|---|---|---|"]
    for n in findings["name_matches"]:
        out.append(f"| {n['from']} | {n['to']} | {n['canonical']} | "
                   f"{n.get('confidence','')} | {n.get('rationale','')} |")
    out.append("")

    out += ["## 構造所見", ""]
    for s in findings["structural"]:
        out.append(f"- [{s['kind']}] {s['detail']}  ({', '.join(s.get('provenance', []))})")
    out.append("")

    if findings["open_questions"]:
        out += ["## 未解決質問", ""]
        for q in findings["open_questions"]:
            out.append(f"- {q.get('text','')}")
        out.append("")

    if findings["warnings"]:
        out += ["## 警告", ""]
        for w in findings["warnings"]:
            out.append(f"- {w}")
        out.append("")
    return "\n".join(out) + "\n"


def format_audit_summary(findings: dict) -> str:
    return (f"structural={len(findings['structural'])} "
            f"contradictions={len(findings['contradictions'])} "
            f"name_matches={len(findings['name_matches'])} "
            f"open_questions={len(findings['open_questions'])} "
            f"warnings={len(findings['warnings'])}")


def format_host_agent_plan(directory) -> str:
    """Pack a no-API-key host-agent plan with embedded prompts and --apply-from reference."""
    root = Path(directory).resolve()
    det = llm.load_prompt_template("detect-contradictions")
    ver = llm.load_prompt_template("verify-contradiction")
    adj = llm.load_prompt_template("adjudicate-namematch")
    return (
        "# dotmd-parser — ontology-audit host-agent plan\n\n"
        f"Target: `{root}`  (reads `ontology/ontology.json`, scans corpus)\n\n"
        "Run the three tasks below yourself, then assemble a JSON object "
        '`{"contradictions": [...verified CONFIRMED...], "name_matches": [...same only...], '
        '"open_questions": [...]}`  and apply it:\n\n'
        "```bash\n"
        f'dotmd-parser ontology-audit "{root}" --apply-from audit.json\n'
        "```\n\n"
        "## 1. Detect contradictions\n\n```\n" + det.strip() + "\n```\n\n"
        "## 2. Verify each (skeptic; default refuted=true)\n\n```\n" + ver.strip() + "\n```\n\n"
        "## 3. Adjudicate name matches (keep only decision=same)\n\n```\n" + adj.strip() + "\n```\n"
    )


def apply_audit_from_file(directory, json_path, out_dir=None) -> dict:
    """Load pre-computed findings from JSON and generate audit report."""
    ir = load_ir(directory)
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"audit JSON not found: {json_path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {json_path}: {e}") from e

    contradictions = [{**c, "verdict": "CONFIRMED"} for c in payload.get("contradictions", [])]
    findings = {
        "meta": {"audited": "ontology/ontology.json", "corpus": str(directory),
                 "generated_by": "dotmd-parser ontology-audit v2"},
        "structural": structural_findings(ir),
        "contradictions": sorted(contradictions, key=_severity_key),
        "name_matches": payload.get("name_matches", []),
        "open_questions": payload.get("open_questions", []),
        "warnings": [],
    }
    out = Path(out_dir) if out_dir else (Path(directory).resolve() / "ontology")
    out.mkdir(parents=True, exist_ok=True)
    (out / _AUDIT_JSON).write_text(emit_audit_json(findings), encoding="utf-8")
    (out / _AUDIT_MD).write_text(emit_audit_md(findings), encoding="utf-8")
    return {"findings": findings,
            "written": [str(out / _AUDIT_JSON), str(out / _AUDIT_MD)],
            "summary": format_audit_summary(findings)}
