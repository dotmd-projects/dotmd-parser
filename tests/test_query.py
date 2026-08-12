import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser import ontology as O
from dotmd_parser import query as Q


def _make_ontology(root: Path) -> dict:
    """Emit ontology.ttl + ontology.json for a tiny fixture ontology."""
    ir = O.merge_ontology([{"source": "src.md", "elements": {**O.EMPTY_ELEMENTS,
        "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                    {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
        "datatype_properties": [{"name": "feeRate", "domain": "Application", "type": "decimal",
                                 "label_ja": "手数料率", "enum": None}],
        "object_properties": [{"name": "evaluatedBy", "from": "Application",
                               "to": "CreditAssessment", "cardinality": "1:1",
                               "characteristics": ["Functional"], "note": ""}],
        "vocabularies": [{"name": "applicationStatus", "values": ["買取成立", "謝絶"]}]}}],
        {"namespace": "https://ex.org/o#", "prefix": "ex", "domain": "d",
         "built_from": "c", "source_docs": ["src.md"], "generated_by": "t"})
    onto = root / "ontology"
    onto.mkdir(parents=True, exist_ok=True)
    (onto / "ontology.ttl").write_text(O.emit_ttl(ir), encoding="utf-8")
    (onto / "ontology.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    return ir


class TestLoadGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_graph_and_meta(self):
        _make_ontology(self.root)
        graph, meta = Q.load_graph(self.root)
        self.assertEqual(meta["namespace"], "https://ex.org/o#")
        self.assertEqual(meta["prefix"], "ex")
        self.assertGreater(len(graph), 0)          # non-empty rdflib graph

    def test_missing_ttl_raises(self):
        (self.root / "ontology").mkdir()
        with self.assertRaises(FileNotFoundError):
            Q.load_graph(self.root)


if __name__ == "__main__":
    unittest.main()
