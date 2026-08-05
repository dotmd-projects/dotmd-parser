import unittest
from dotmd_parser import ontology as O


def _meta():
    return {"namespace": "https://ex.org/o#", "prefix": "ex", "domain": "demo",
            "built_from": "corpus/", "source_docs": [], "generated_by": "test"}


class TestMerge(unittest.TestCase):
    def test_dedup_and_provenance(self):
        partials = [
            {"source": "a.md", "elements": {
                "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
                "object_properties": [], "datatype_properties": [], "vocabularies": [],
                "invariants": [], "conflicts": [], "open_questions": []}},
            {"source": "b.md", "elements": {
                "classes": [{"name": "application", "label_ja": "申請", "domain_group": "取引"}],
                "object_properties": [], "datatype_properties": [], "vocabularies": [],
                "invariants": [], "conflicts": [], "open_questions": []}},
        ]
        ir = O.merge_ontology(partials, _meta())
        self.assertEqual(len(ir["classes"]), 1)                      # deduped by normalized name
        self.assertEqual(ir["classes"][0]["provenance"], ["a.md", "b.md"])

    def test_conflict_flagged_not_resolved(self):
        partials = [
            {"source": "a.md", "elements": {"classes": [], "datatype_properties": [
                {"name": "feeRate", "domain": "Application", "type": "decimal",
                 "label_ja": "手数料率", "enum": None}],
                "object_properties": [], "vocabularies": [], "invariants": [],
                "conflicts": [], "open_questions": []}},
            {"source": "b.md", "elements": {"classes": [], "datatype_properties": [
                {"name": "feeRate", "domain": "Application", "type": "string",
                 "label_ja": "手数料率", "enum": None}],
                "object_properties": [], "vocabularies": [], "invariants": [],
                "conflicts": [], "open_questions": []}},
        ]
        ir = O.merge_ontology(partials, _meta())
        self.assertEqual(len(ir["datatype_properties"]), 1)
        self.assertEqual(ir["datatype_properties"][0]["type"], "decimal")  # first-seen wins
        self.assertTrue(any(c["kind"] == "naming" for c in ir["conflicts"]))


if __name__ == "__main__":
    unittest.main()
