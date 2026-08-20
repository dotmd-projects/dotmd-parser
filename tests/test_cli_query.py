import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser import ontology as O
from dotmd_parser.cli import run as cli_run


def _make(root: Path):
    ir = O.merge_ontology([{"source": "src.md", "elements": {**O.EMPTY_ELEMENTS,
        "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
        "datatype_properties": [{"name": "feeRate", "domain": "Application", "type": "decimal",
                                 "label_ja": "手数料率", "enum": None}]}}],
        {"namespace": "https://ex.org/o#", "prefix": "ex", "domain": "d",
         "built_from": "c", "source_docs": ["src.md"], "generated_by": "t"})
    onto = root / "ontology"; onto.mkdir(parents=True)
    (onto / "ontology.ttl").write_text(O.emit_ttl(ir), encoding="utf-8")
    (onto / "ontology.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")


class TestCliQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_exit_0(self):
        _make(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-query", str(self.root), "list", "classes"])
        self.assertEqual(cm.exception.code, 0)

    def test_missing_ttl_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-query", str(self.root), "list", "classes"])
        self.assertEqual(cm.exception.code, 2)

    def test_exclusive_violation_exit_2(self):
        _make(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-query", str(self.root), "list", "classes", "--sparql", "ASK {}"])
        self.assertEqual(cm.exception.code, 2)

    def test_malformed_sparql_exit_1(self):
        _make(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-query", str(self.root), "--sparql", "NOT SPARQL"])
        self.assertEqual(cm.exception.code, 1)

    def test_with_abox_flag_exits_0_when_abox_present(self):
        _make(self.root)
        abox_ttl = (
            "@prefix ex: <https://ex.org/o#> .\n"
            "ex:Application_1 a ex:Application .\n"
        )
        (self.root / "ontology" / "ontology-abox.ttl").write_text(
            abox_ttl, encoding="utf-8"
        )
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-query", str(self.root), "list", "classes", "--with-abox"])
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
