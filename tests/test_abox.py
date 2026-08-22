import csv
import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser import abox as A


def _write_ontology(root: Path):
    onto = root / "ontology"; onto.mkdir()
    ir = {"meta": {"namespace": "https://ex.org/o#", "prefix": "ex"},
          "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
          "datatype_properties": [
              {"name": "feeRate", "domain": "Application", "type": "decimal", "label_ja": "手数料率"},
              {"name": "applicationStatus", "domain": "Application", "type": "string", "label_ja": "状態"},
              {"name": "requestedAmount", "domain": "Application", "type": "decimal", "label_ja": "申請額"}]}
    (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


class TestLoadAndMaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_class_dprops(self):
        _write_ontology(self.root)
        meta, classes, dprops = A.load_class_dprops(self.root)
        self.assertEqual(meta["prefix"], "ex")
        self.assertIn("Application", classes)
        self.assertEqual([d["name"] for d in dprops["Application"]],
                         ["feeRate", "applicationStatus", "requestedAmount"])

    def test_load_missing_json_raises(self):
        (self.root / "ontology").mkdir()
        with self.assertRaises(FileNotFoundError):
            A.load_class_dprops(self.root)

    def test_load_missing_meta_raises(self):
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"meta": {}, "classes": [], "datatype_properties": []}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            A.load_class_dprops(self.root)

    def test_parse_maps_ok(self):
        self.assertEqual(A.parse_maps(["Application=app.csv"], {"Application"}),
                         {"Application": "app.csv"})

    def test_parse_maps_errors(self):
        with self.assertRaises(ValueError):
            A.parse_maps(["Application"], {"Application"})            # no '='
        with self.assertRaises(ValueError):
            A.parse_maps(["Foo=x.csv"], {"Application"})              # unknown class
        with self.assertRaises(ValueError):
            A.parse_maps(["Application=a.csv", "Application=b.csv"], {"Application"})  # dup


class TestMatchMaterialize(unittest.TestCase):
    def _dprops(self):
        return [{"name": "feeRate", "domain": "Application", "type": "decimal"},
                {"name": "applicationStatus", "domain": "Application", "type": "string"},
                {"name": "requestedAmount", "domain": "Application", "type": "decimal"}]

    def test_match_columns(self):
        m, _method = A.match_columns_to_props(self._dprops(),
                                              ["fee_rate", "applicationStatus", "note"], 0.6)
        self.assertEqual(m["feeRate"], "fee_rate")
        self.assertEqual(m["applicationStatus"], "applicationStatus")
        self.assertNotIn("requestedAmount", m)          # no column matches

    def test_lit(self):
        self.assertEqual(A._lit("0.05", "decimal"), '"0.05"^^xsd:decimal')
        self.assertEqual(A._lit('a"b', "string"), '"a\\"b"^^xsd:string')

    def test_materialize(self):
        rows = [{"fee_rate": "0.05", "applicationStatus": "買取成立", "note": "x"},
                {"fee_rate": "", "applicationStatus": "謝絶", "note": ""}]
        prop_col = {"feeRate": "fee_rate", "applicationStatus": "applicationStatus"}
        lines, count = A.materialize_class("Application", self._dprops(), prop_col, rows, "ex")
        text = "\n".join(lines)
        self.assertEqual(count, 2)
        self.assertIn("ex:Application_1 a ex:Application ;", text)
        self.assertIn('ex:feeRate "0.05"^^xsd:decimal', text)
        self.assertIn('ex:applicationStatus "買取成立"^^xsd:string', text)
        # row 2 has empty fee_rate -> only applicationStatus emitted, block still valid
        self.assertIn('ex:Application_2 a ex:Application ;', text)
        self.assertIn('ex:applicationStatus "謝絶"^^xsd:string .', text)  # terminated with .

    def test_materialize_zero_matched_cells_terminates(self):
        # row where the only matched column is empty -> block is just the type line, terminated with .
        rows = [{"fee_rate": "", "applicationStatus": "", "note": "x"}]
        prop_col = {"feeRate": "fee_rate", "applicationStatus": "applicationStatus"}
        lines, count = A.materialize_class("Application", self._dprops(), prop_col, rows, "ex")
        text = "\n".join(lines)
        self.assertEqual(count, 1)
        self.assertIn("ex:Application_1 a ex:Application .", text)   # terminated on type line
        self.assertNotIn("ex:feeRate", text)                          # no property lines


class TestRunAbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_ontology(self.root)
        self.csv = self.root / "app.csv"
        _write_csv(self.csv, [{"fee_rate": "0.05", "applicationStatus": "買取成立", "note": "x"},
                              {"fee_rate": "0.10", "applicationStatus": "謝絶", "note": "y"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_writes_ttl_and_report(self):
        res = A.run_abox(self.root, {"Application": str(self.csv)})
        c = res["findings"]["classes"][0]
        self.assertEqual(c["instances"], 2)
        self.assertEqual(c["matched"]["feeRate"], "fee_rate")
        self.assertIn("requestedAmount", c["unmatched_props"])
        self.assertTrue((self.root / "ontology" / "ontology-abox.ttl").exists())
        self.assertTrue((self.root / "ontology" / "ontology-abox-report.json").exists())

    def test_ttl_parses_with_rdflib(self):
        try:
            import rdflib  # noqa: F401
        except ImportError:
            self.skipTest("rdflib not installed")
        from rdflib import Graph, RDF, URIRef
        A.run_abox(self.root, {"Application": str(self.csv)})
        g = Graph()
        g.parse(self.root / "ontology" / "ontology-abox.ttl", format="turtle")
        app1 = URIRef("https://ex.org/o#Application_1")
        self.assertIn((app1, RDF.type, URIRef("https://ex.org/o#Application")), g)

    def test_deterministic(self):
        out1 = self.root / "o1"; out2 = self.root / "o2"
        A.run_abox(self.root, {"Application": str(self.csv)}, out_dir=str(out1))
        A.run_abox(self.root, {"Application": str(self.csv)}, out_dir=str(out2))
        self.assertEqual((out1 / "ontology-abox.ttl").read_text(encoding="utf-8"),
                         (out2 / "ontology-abox.ttl").read_text(encoding="utf-8"))

    def test_unreadable_csv_raises(self):
        with self.assertRaises(ValueError):
            A.run_abox(self.root, {"Application": str(self.root / "nope.csv")})


class TestAboxDeterminism(unittest.TestCase):
    def test_emit_deterministic(self):
        meta = {"namespace": "https://ex.org/o#", "prefix": "ex"}
        dprops = [{"name": "feeRate", "domain": "Application", "type": "decimal"}]
        rows = [{"fee_rate": "0.05"}, {"fee_rate": "0.10"}]
        pc, _method = A.match_columns_to_props(dprops, ["fee_rate"], 0.6)
        lines, _ = A.materialize_class("Application", dprops, pc, rows, "ex")
        self.assertEqual(A.emit_abox_ttl(meta, [lines]), A.emit_abox_ttl(meta, [lines]))


class TestMatchPrecedence(unittest.TestCase):
    def _dprops(self):
        return [
            {"name": "applicationStatus", "domain": "Application", "type": "string", "enum": "applicationStatus"},
            {"name": "customerSegment", "domain": "Application", "type": "string", "enum": "customerSegment"},
            {"name": "requestedAmount", "domain": "Application", "type": "decimal", "enum": None},
        ]

    def test_value_match_for_enum_props(self):
        # column names are NAME-dissimilar, but VALUES overlap the enum -> value match
        fieldnames = ["deal_status_name", "app_segment", "collected_amount"]
        columns_values = {
            "deal_status_name": {"完了", "謝絶", "キャンセル", "入金待ち"},
            "app_segment": {"a_初回", "b_リピーター"},
            "collected_amount": {"76639", "12000"},
        }
        vocab_values = {
            "applicationStatus": {"買取成立", "謝絶", "キャンセル", "未処理"},
            "customerSegment": {"a_初回", "b_リピーター"},
        }
        matches, method = A.match_columns_to_props(self._dprops(), fieldnames, 0.5,
                                                   columns_values=columns_values, vocab_values=vocab_values)
        self.assertEqual(matches["applicationStatus"], "deal_status_name")  # value match (shared 2)
        self.assertEqual(method["applicationStatus"], "value")
        self.assertEqual(matches["customerSegment"], "app_segment")         # value match (shared 2)
        self.assertEqual(method["customerSegment"], "value")

    def test_explicit_overrides(self):
        fieldnames = ["deal_status_name", "offer_price", "collected_amount"]
        matches, method = A.match_columns_to_props(
            self._dprops(), fieldnames, 0.5,
            columns_values={c: set() for c in fieldnames}, vocab_values={},
            explicit={"requestedAmount": "offer_price"})
        self.assertEqual(matches["requestedAmount"], "offer_price")
        self.assertEqual(method["requestedAmount"], "explicit")

    def test_name_match_fallback_for_non_enum(self):
        # non-enum prop, no explicit -> name similarity (registeredDate vs registered_at)
        dprops = [{"name": "registeredDate", "domain": "Member", "type": "date", "enum": None}]
        matches, method = A.match_columns_to_props(dprops, ["registered_at", "x"], 0.6,
                                                   columns_values={"registered_at": set(), "x": set()},
                                                   vocab_values={})
        self.assertEqual(matches["registeredDate"], "registered_at")
        self.assertEqual(method["registeredDate"], "name")


class TestRunAboxValueMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"meta": {"namespace": "https://ex.org/o#", "prefix": "ex"},
              "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
              "datatype_properties": [
                  {"name": "applicationStatus", "domain": "Application", "type": "string", "enum": "applicationStatus"},
                  {"name": "requestedAmount", "domain": "Application", "type": "decimal", "enum": None}],
              "vocabularies": [{"name": "applicationStatus", "values": ["謝絶", "キャンセル", "未処理"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        self.csv = self.root / "deal.csv"
        _write_csv(self.csv, [{"deal_status_name": "謝絶", "offer_price": "1000", "collected_amount": "900"},
                              {"deal_status_name": "キャンセル", "offer_price": "2000", "collected_amount": "0"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_enum_prop_value_matched(self):
        res = A.run_abox(self.root, {"Application": str(self.csv)})
        c = res["findings"]["classes"][0]
        # applicationStatus (enum) value-matches deal_status_name despite name dissimilarity
        self.assertEqual(c["matched"]["applicationStatus"], "deal_status_name")
        self.assertEqual(c["match_method"]["applicationStatus"], "value")

    def test_map_col_override(self):
        res = A.run_abox(self.root, {"Application": str(self.csv)},
                         map_cols={"Application": {"requestedAmount": "offer_price"}})
        c = res["findings"]["classes"][0]
        self.assertEqual(c["matched"]["requestedAmount"], "offer_price")
        self.assertEqual(c["match_method"]["requestedAmount"], "explicit")

    def test_map_col_unknown_column_raises(self):
        # --map-col column doesn't exist in the CSV's fieldnames -> hard error,
        # not a silent fallback to auto-match (parse_map_cols can't validate
        # the column since it doesn't see fieldnames).
        with self.assertRaises(ValueError):
            A.run_abox(self.root, {"Application": str(self.csv)},
                      map_cols={"Application": {"requestedAmount": "nonexistent_col"}})

    def test_parse_map_cols_errors(self):
        dpc = {"Application": [{"name": "requestedAmount"}]}
        with self.assertRaises(ValueError):
            A.parse_map_cols(["Application.requestedAmount"], dpc)         # no '='
        with self.assertRaises(ValueError):
            A.parse_map_cols(["Bogus.x=col"], dpc)                        # unknown class
        with self.assertRaises(ValueError):
            A.parse_map_cols(["Application.nope=col"], dpc)               # prop not a dprop


if __name__ == "__main__":
    unittest.main()
