import unittest
import json
import tempfile
from pathlib import Path
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


class TestExtract(unittest.TestCase):
    def test_extract_with_caller(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "d.md").write_text("申請は会員が出す。", encoding="utf-8")

        def fake(prompt, system, model):
            body = {"classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
                    "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                             "type": "money", "label_ja": "手数料率", "enum": None}],
                    "object_properties": [], "vocabularies": [], "invariants": [],
                    "conflicts": [], "open_questions": []}
            return "```json\n" + json.dumps(body) + "\n```"

        partials = O.extract_ontology(root, caller=fake)
        self.assertEqual(partials[0]["source"], "d.md")
        # unknown type normalized to string
        self.assertEqual(partials[0]["elements"]["datatype_properties"][0]["type"], "string")
        tmp.cleanup()


class TestEmitYaml(unittest.TestCase):
    def _ir(self):
        return O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
            "datatype_properties": [], "object_properties": [], "vocabularies": [],
            "invariants": [], "conflicts": [], "open_questions": []}}], _meta())

    def test_deterministic_and_contains_class(self):
        y1 = O.emit_yaml(self._ir())
        y2 = O.emit_yaml(self._ir())
        self.assertEqual(y1, y2)                 # deterministic
        self.assertIn('name: "Application"', y1)
        self.assertIn("classes:", y1)


if __name__ == "__main__":
    unittest.main()
