import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dotmd_parser.cli import run as cli_run


class TestCliOntology(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "d.md").write_text("申請→審査。", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_path_no_api(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--plan"])
        self.assertEqual(cm.exception.code, 0)

    def test_apply_from_and_check(self):
        partials = [{"source": "d.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                        {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
            "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}]
        jp = self.root / "ontology.json"
        jp.write_text(json.dumps(partials), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--apply-from", str(jp), "--check"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue((self.root / "ontology" / "ontology.ttl").exists())

    def test_check_fails_on_dangling(self):
        partials = [{"source": "d.md", "elements": {
            "classes": [], "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "Missing", "cardinality": "1:1",
                                   "characteristics": [], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}]
        jp = self.root / "ontology.json"
        jp.write_text(json.dumps(partials), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--apply-from", str(jp), "--check"])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
