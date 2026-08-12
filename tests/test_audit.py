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


if __name__ == "__main__":
    unittest.main()
