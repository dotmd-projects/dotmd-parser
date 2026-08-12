import json, tempfile, unittest
from pathlib import Path
from dotmd_parser.cli import run as cli_run


def _mk(root: Path):
    (root / "B-1c.md").write_text("審査681 申請1290", encoding="utf-8")
    onto = root / "ontology"; onto.mkdir()
    ir = {"meta": {}, "classes": [], "datatype_properties": [], "object_properties": [],
          "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}
    (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")


class TestCliAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_ontology_json_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-audit", str(self.root)])   # no ontology/ontology.json
        self.assertEqual(cm.exception.code, 2)

    def test_structural_only_exits_0_no_api(self):
        _mk(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-audit", str(self.root), "--structural-only"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue((self.root / "ontology" / "ontology-audit.md").exists())

    def test_check_exits_1_on_confirmed_via_apply_from(self):
        _mk(self.root)
        payload = {"contradictions": [{"claim": "x", "violates": "y", "severity": "high",
                   "evidence": ["B-1c.md"]}], "name_matches": [], "open_questions": []}
        jp = self.root / "in.json"; jp.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-audit", str(self.root), "--apply-from", str(jp), "--check"])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
