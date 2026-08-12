import unittest
from dotmd_parser import audit as A


def _ir(**over):
    base = {"meta": {}, "classes": [], "datatype_properties": [], "object_properties": [],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}
    base.update(over)
    return base


class TestNameMatch(unittest.TestCase):
    def test_similar_pair_candidate_unrelated_excluded(self):
        ir = _ir(vocabularies=[{"name": "media",
                                "values": ["Google 指名", "GSA指名 即日", "買取"],
                                "provenance": ["a.md"]}])
        cands = A.namematch_candidates(ir)
        pairs = {frozenset((c["a"], c["b"])) for c in cands}
        self.assertIn(frozenset(("Google 指名", "GSA指名 即日")), pairs)  # surfaced via shared "指名" substring
        self.assertNotIn(frozenset(("Google 指名", "買取")), pairs)       # unrelated excluded (no significant overlap)

    def test_deterministic(self):
        ir = _ir(vocabularies=[{"name": "m", "values": ["Google 指名", "GSA指名 即日"],
                                "provenance": ["a.md"]}])
        self.assertEqual(A.namematch_candidates(ir), A.namematch_candidates(ir))


class TestStructural(unittest.TestCase):
    def test_merge_conflict_promoted(self):
        ir = _ir(conflicts=[{"kind": "naming", "detail": "feeRate.type: decimal vs string",
                             "provenance": ["a.md", "b.md"]}])
        out = A.structural_findings(ir)
        self.assertTrue(any(f["kind"] == "merge-conflict" and "feeRate" in f["detail"] for f in out))

    def test_vocab_overlap(self):
        ir = _ir(vocabularies=[{"name": "v1", "values": ["謝絶", "承認"], "provenance": ["a.md"]},
                               {"name": "v2", "values": ["謝絶", "却下"], "provenance": ["b.md"]}])
        out = A.structural_findings(ir)
        self.assertTrue(any(f["kind"] == "vocab-overlap" and "謝絶" in f["detail"] for f in out))

    def test_dangling_invariant_ref(self):
        # invariant mentions no known ontology term at all
        ir = _ir(classes=[{"name": "Application", "label_ja": "申請", "domain_group": "取引",
                           "provenance": ["a.md"]}],
                 invariants=[{"id": "x", "statement": "Zzz relates to Qqq", "kind": "constraint",
                              "provenance": ["a.md"]}])
        out = A.structural_findings(ir)
        self.assertTrue(any(f["kind"] == "dangling-invariant-ref" for f in out))

    def test_deterministic(self):
        ir = _ir(vocabularies=[{"name": "v1", "values": ["謝絶"], "provenance": ["a.md"]},
                               {"name": "v2", "values": ["謝絶"], "provenance": ["b.md"]}])
        self.assertEqual(A.structural_findings(ir), A.structural_findings(ir))


import json as _json


def _resp(obj):
    return "```json\n" + _json.dumps(obj) + "\n```"


class TestContradiction(unittest.TestCase):
    def _ir_funnel(self):
        return _ir(
            classes=[{"name": "ConversionEvent", "label_ja": "CVイベント", "domain_group": "マーケ",
                      "provenance": ["f.md"]}],
            invariants=[{"id": "review-ge-application",
                         "statement": "審査は申請ごと→審査CV≥申請CV", "kind": "constraint",
                         "provenance": ["f.md"]}])

    def test_detect_returns_candidates_and_open_questions(self):
        def caller(prompt, system, model):
            return _resp({"candidates": [
                {"claim": "審査CV(681) < 申請CV(1290)", "violates": "審査CV≥申請CV",
                 "severity": "high", "evidence": ["B-1c.md"]}],
                "open_questions": [{"text": "審査CVの正確な定義", "provenance": ["B-1c.md"]}]})
        det = A.detect_contradictions(self._ir_funnel(),
                                      [{"path": "B-1c.md", "content": "審査681 申請1290"}],
                                      caller=caller)
        self.assertEqual(len(det["candidates"]), 1)
        self.assertEqual(len(det["open_questions"]), 1)

    def test_verify_confirms_real_and_drops_structurally_normal(self):
        real = {"claim": "審査CV(681) < 申請CV(1290)", "violates": "審査CV≥申請CV",
                "severity": "high", "evidence": ["B-1c.md"]}
        false = {"claim": "メアド登録CV(957) < 申請CV(1290)", "violates": "?",
                 "severity": "medium", "evidence": ["f.md"]}

        def caller(prompt, system, model):
            # skeptic: refute the structurally-normal one, keep the real one
            if "メアド" in prompt:
                return _resp({"refuted": True, "reason": "stageScope 初回のみ vs 初回+リピで正常"})
            return _resp({"refuted": False, "reason": "構造差で説明不可"})

        survivors = A.verify_contradictions([real, false], self._ir_funnel(), caller=caller)
        claims = [s["claim"] for s in survivors]
        self.assertIn(real["claim"], claims)          # real contradiction CONFIRMED
        self.assertNotIn(false["claim"], claims)      # structurally-normal dropped
        self.assertTrue(all(s["verdict"] == "CONFIRMED" for s in survivors))


class TestAdjudicate(unittest.TestCase):
    def test_same_included_uncertain_excluded(self):
        cands = [{"a": "Google 指名", "b": "GSA指名 即日", "score": 0.7},
                 {"a": "Meta", "b": "Yahoo!", "score": 0.61}]

        def caller(prompt, system, model):
            if "GSA" in prompt:
                return _resp({"decision": "same", "canonical": "Google 指名",
                              "confidence": 0.86, "rationale": "同一媒体の別表記"})
            return _resp({"decision": "different", "canonical": "", "confidence": 0.2, "rationale": "別媒体"})

        out = A.adjudicate_namematches(cands, _ir(), [], caller=caller)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["canonical"], "Google 指名")
        self.assertEqual(out[0]["to"], "Google 指名")


import tempfile, json as _json2
from pathlib import Path


class TestRunAudit(unittest.TestCase):
    def _prep(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "B-1c.md").write_text("審査681 申請1290", encoding="utf-8")
        onto = root / "ontology"
        onto.mkdir()
        ir = {"meta": {}, "classes": [{"name": "ConversionEvent", "label_ja": "CV",
              "domain_group": "マーケ", "provenance": ["B-1c.md"]}],
              "datatype_properties": [], "object_properties": [],
              "vocabularies": [], "invariants": [{"id": "r", "statement": "審査CV≥申請CV",
              "kind": "constraint", "provenance": ["B-1c.md"]}],
              "conflicts": [], "open_questions": []}
        (onto / "ontology.json").write_text(_json2.dumps(ir), encoding="utf-8")
        return tmp, root

    def test_structural_only_no_llm(self):
        tmp, root = self._prep()
        res = A.run_audit(root, structural_only=True)          # no caller, no API — must not raise
        self.assertEqual(res["findings"]["contradictions"], [])
        self.assertTrue(any(Path(p).name == "ontology-audit.json" for p in res["written"]))
        tmp.cleanup()

    def test_full_audit_with_caller_and_hallucination_guard(self):
        tmp, root = self._prep()

        def caller(prompt, system, model):
            if "skeptic" in system.lower() or "refute" in prompt.lower():
                return _resp({"refuted": False, "reason": "ok"})
            if "adjudicate" in system.lower() or "same domain entity" in prompt.lower():
                return _resp({"decision": "different"})
            return _resp({"candidates": [{"claim": "審査<申請", "violates": "審査CV≥申請CV",
                          "severity": "high", "evidence": ["B-1c.md", "ghost.md"]}],
                          "open_questions": []})

        res = A.run_audit(root, caller=caller)
        f = res["findings"]
        self.assertEqual(len(f["contradictions"]), 1)
        self.assertEqual(f["contradictions"][0]["verdict"], "CONFIRMED")
        self.assertTrue(any("ghost.md" in w for w in f["warnings"]))   # hallucinated evidence path flagged
        tmp.cleanup()


class TestHostAgent(unittest.TestCase):
    def test_plan_mentions_apply_from(self):
        tmp, root = TestRunAudit()._prep()
        plan = A.format_host_agent_plan(root)
        self.assertIn("--apply-from", plan)
        tmp.cleanup()

    def test_apply_from_builds_report(self):
        tmp, root = TestRunAudit()._prep()
        payload = {"contradictions": [{"claim": "審査<申請", "violates": "審査CV≥申請CV",
                   "severity": "high", "verdict": "CONFIRMED", "refutation_checked": "ok",
                   "evidence": ["B-1c.md"]}],
                   "name_matches": [], "open_questions": []}
        jp = root / "audit-in.json"
        jp.write_text(_json2.dumps(payload), encoding="utf-8")
        res = A.apply_audit_from_file(root, jp)
        self.assertEqual(len(res["findings"]["contradictions"]), 1)
        self.assertTrue(any(Path(p).name == "ontology-audit.md" for p in res["written"]))
        tmp.cleanup()


class TestAuditDeterminism(unittest.TestCase):
    def test_structural_and_emit_deterministic(self):
        findings = {"meta": {"audited": "ontology/ontology.json", "corpus": "c",
                    "generated_by": "t"},
                    "structural": [{"kind": "vocab-overlap", "detail": "d", "provenance": ["a.md"]}],
                    "contradictions": [], "name_matches": [], "open_questions": [], "warnings": []}
        self.assertEqual(A.emit_audit_json(findings), A.emit_audit_json(findings))
        self.assertEqual(A.emit_audit_md(findings), A.emit_audit_md(findings))

    def test_namematch_candidates_stable_order(self):
        ir = _ir(vocabularies=[{"name": "m", "values": ["Google 指名", "GSA指名 即日", "Google 一般"],
                                "provenance": ["a.md"]}])
        self.assertEqual(A.namematch_candidates(ir), A.namematch_candidates(ir))


if __name__ == "__main__":
    unittest.main()
