import unittest
from dotmd_parser import audit as A


def _ir(**over):
    base = {"meta": {}, "classes": [], "datatype_properties": [], "object_properties": [],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}
    base.update(over)
    return base


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


if __name__ == "__main__":
    unittest.main()
