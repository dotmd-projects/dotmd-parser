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


class TestEmitYamlEscaping(unittest.TestCase):
    def test_escaping_and_empty_and_null(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Quote", "label_ja": 'a"b\\c\nd', "domain_group": "g"}],
            "datatype_properties": [{"name": "p", "domain": "Quote", "type": "string",
                                     "label_ja": "x", "enum": None}],
            "object_properties": [], "vocabularies": [],
            "invariants": [], "conflicts": [], "open_questions": []}}], _meta())
        y = O.emit_yaml(ir)
        # escaping: backslash, double-quote, and newline are escaped inside the scalar
        self.assertIn('label_ja: "a\\"b\\\\c\\nd"', y)
        # empty section renders as [] on one line, not a dangling header
        self.assertIn("object_properties: []", y)
        self.assertIn("vocabularies: []", y)
        # null enum renders as the bareword null
        self.assertIn("enum: null", y)


class TestEmitTtl(unittest.TestCase):
    def _ir(self):
        return O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                        {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
            "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                     "type": "decimal", "label_ja": "手数料率", "enum": None}],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": "鎖"}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())

    def test_parses_with_rdflib(self):
        try:
            import rdflib  # noqa: F401
        except ImportError:
            self.skipTest("rdflib not installed")
        from rdflib import Graph
        g = Graph()
        g.parse(data=O.emit_ttl(self._ir()), format="turtle")
        # 2 classes present as owl:Class
        from rdflib.namespace import OWL, RDF
        classes = set(g.subjects(RDF.type, OWL.Class))
        self.assertEqual(len(classes), 2)

    def test_cardinality_annotation_present(self):
        self.assertIn('ex:cardinality "1:1"', O.emit_ttl(self._ir()))

    def test_null_characteristics_does_not_crash(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {**O.EMPTY_ELEMENTS,
            "classes": [{"name": "A", "label_ja": "a", "domain_group": "g"},
                        {"name": "B", "label_ja": "b", "domain_group": "g"}],
            "object_properties": [{"name": "rel", "from": "A", "to": "B",
                                   "cardinality": "1:1", "characteristics": None, "note": ""}]}}], _meta())
        ttl = O.emit_ttl(ir)               # must not raise
        self.assertIn("ex:rel a owl:ObjectProperty", ttl)

    def test_vocabulary_emits_provenance(self):
        ir = O.merge_ontology([{"source": "src.md", "elements": {**O.EMPTY_ELEMENTS,
            "vocabularies": [{"name": "status", "values": ["x", "y"]}]}}], _meta())
        ttl = O.emit_ttl(ir)
        self.assertIn('ex:sourceDoc "src.md"', ttl)


class TestEmitDesignMd(unittest.TestCase):
    def test_tables_present(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
            "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                     "type": "decimal", "label_ja": "手数料率", "enum": None}],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": "鎖"}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())
        md = O.emit_design_md(ir)
        self.assertIn("## クラス", md)
        self.assertIn("Application", md)
        self.assertIn("evaluatedBy", md)
        self.assertIn("| Application → CreditAssessment | 1:1 |", md)


if __name__ == "__main__":
    unittest.main()
