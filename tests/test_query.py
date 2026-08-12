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


class TestRunSparql(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _make_ontology(self.root)
        self.graph, self.meta = Q.load_graph(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_select(self):
        res = Q.run_sparql(self.graph, "SELECT ?c WHERE { ?c a "
                           "<http://www.w3.org/2002/07/owl#Class> }")
        self.assertEqual(res["type"], "select")
        self.assertEqual(res["columns"], ["c"])
        flat = [cell for row in res["rows"] for cell in row]
        self.assertIn("https://ex.org/o#Application", flat)

    def test_ask(self):
        res = Q.run_sparql(self.graph,
                           "ASK { <https://ex.org/o#feeRate> a "
                           "<http://www.w3.org/2002/07/owl#DatatypeProperty> }")
        self.assertEqual(res["type"], "ask")
        self.assertTrue(res["boolean"])

    def test_select_deterministic(self):
        q = ("SELECT ?p WHERE { ?p a "
             "<http://www.w3.org/2002/07/owl#ObjectProperty> }")
        self.assertEqual(Q.run_sparql(self.graph, q)["rows"],
                         Q.run_sparql(self.graph, q)["rows"])


class TestNamedQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _make_ontology(self.root)
        self.graph, self.meta = Q.load_graph(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _flat(self, res):
        return [cell for row in res["rows"] for cell in row]

    def test_properties(self):
        res = Q.named_query(self.graph, self.meta, "properties", "Application")
        flat = self._flat(res)
        self.assertTrue(any("feeRate" in c for c in flat))
        self.assertTrue(any("evaluatedBy" in c for c in flat))

    def test_relations(self):
        res = Q.named_query(self.graph, self.meta, "relations", "Application")
        self.assertTrue(any("evaluatedBy" in c for c in self._flat(res)))

    def test_defines(self):
        res = Q.named_query(self.graph, self.meta, "defines", "feeRate")
        self.assertTrue(any("src.md" in c for c in self._flat(res)))

    def test_list_classes(self):
        res = Q.named_query(self.graph, self.meta, "list", "classes")
        flat = self._flat(res)
        self.assertTrue(any("Application" in c for c in flat))
        self.assertTrue(any("CreditAssessment" in c for c in flat))

    def test_undefined_class_empty(self):
        res = Q.named_query(self.graph, self.meta, "properties", "Nonexistent")
        self.assertEqual(res["rows"], [])          # zero rows, no error

    def test_bad_arg_raises(self):
        with self.assertRaises(ValueError):
            Q.named_query(self.graph, self.meta, "properties", "Bad Name")

    def test_injection_chars_raise(self):
        for bad in ("App#x", "App;DROP", "App(", "a/b", "a.b"):
            with self.assertRaises(ValueError):
                Q.named_query(self.graph, self.meta, "properties", bad)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            Q.named_query(self.graph, self.meta, "bogus", "x")


class TestRunQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _make_ontology(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_table(self):
        out = Q.run_query(self.root, kind="list", arg="classes", fmt="table")
        self.assertIn("Application", out)

    def test_sparql_json(self):
        out = Q.run_query(self.root, sparql="SELECT ?c WHERE { ?c a "
                          "<http://www.w3.org/2002/07/owl#Class> }", fmt="json")
        data = json.loads(out)
        self.assertTrue(any("Application" in str(row) for row in data))

    def test_exclusive_both_raises(self):
        with self.assertRaises(ValueError):
            Q.run_query(self.root, sparql="ASK {}", kind="list", arg="classes")

    def test_exclusive_neither_raises(self):
        with self.assertRaises(ValueError):
            Q.run_query(self.root)


import builtins


class TestRdflibMissing(unittest.TestCase):
    def test_require_rdflib_raises_runtimeerror(self):
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "rdflib" or name.startswith("rdflib."):
                raise ImportError("no rdflib")
            return real_import(name, *a, **k)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(RuntimeError) as cm:
                Q._require_rdflib()
            self.assertIn("dotmd-parser[rdf]", str(cm.exception))
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
