import csv
import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser.cli import run as cli_run


def _mk(root: Path):
    onto = root / "ontology"; onto.mkdir()
    ir = {"meta": {"namespace": "https://ex.org/o#", "prefix": "ex"},
          "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
          "datatype_properties": [
              {"name": "feeRate", "domain": "Application", "type": "decimal", "label_ja": "率"}]}
    (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
    csvp = root / "app.csv"
    with csvp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fee_rate"]); w.writeheader()
        w.writerow({"fee_rate": "0.05"})
    return csvp


class TestCliAbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ok_exit_0(self):
        csvp = _mk(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Application={csvp}"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue((self.root / "ontology" / "ontology-abox.ttl").exists())

    def test_unknown_class_exit_2(self):
        csvp = _mk(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Bogus={csvp}"])
        self.assertEqual(cm.exception.code, 2)

    def test_missing_json_exit_2(self):
        csvp = self.root / "app.csv"; csvp.write_text("fee_rate\n0.05\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Application={csvp}"])
        self.assertEqual(cm.exception.code, 2)

    def test_no_map_exit_2(self):
        _mk(self.root)
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root)])
        self.assertEqual(cm.exception.code, 2)


class TestCliAboxMapCol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"meta": {"namespace": "https://ex.org/o#", "prefix": "ex"},
              "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
              "datatype_properties": [
                  {"name": "requestedAmount", "domain": "Application", "type": "decimal", "enum": None}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        self.csv = self.root / "d.csv"
        with self.csv.open("w", encoding="utf-8", newline="") as f:
            import csv as _c
            w = _c.DictWriter(f, fieldnames=["offer_price"]); w.writeheader()
            w.writerow({"offer_price": "1000"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_map_col_ok_exit_0(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Application={self.csv}",
                     "--map-col", "Application.requestedAmount=offer_price"])
        self.assertEqual(cm.exception.code, 0)

    def test_map_col_unknown_prop_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Application={self.csv}",
                     "--map-col", "Application.nope=offer_price"])
        self.assertEqual(cm.exception.code, 2)

    def test_map_col_unknown_column_exit_2(self):
        # requestedAmount is a real dprop, but the pinned column doesn't exist
        # in the CSV -> hard error (no silent fallback to auto-match).
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root), "--map", f"Application={self.csv}",
                     "--map-col", "Application.requestedAmount=nonexistent_col"])
        self.assertEqual(cm.exception.code, 2)


class TestCliAboxLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"meta": {"namespace": "https://ex.org/o#", "prefix": "ex"},
              "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                          {"name": "Member", "label_ja": "会員", "domain_group": "顧客"}],
              "datatype_properties": [
                  {"name": "feeRate", "domain": "Application", "type": "decimal", "enum": None},
                  {"name": "entityType", "domain": "Member", "type": "string", "enum": None}],
              "object_properties": [
                  {"name": "submittedBy", "from": "Application", "to": "Member",
                   "cardinality": "多:1", "characteristics": ["Functional"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        self.app = self.root / "app.csv"
        with self.app.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["deal_id", "user_id", "feeRate"]); w.writeheader()
            w.writerow({"deal_id": "D1", "user_id": "7834", "feeRate": "0.03"})
            w.writerow({"deal_id": "D2", "user_id": "9001", "feeRate": "0.05"})
        self.mem = self.root / "mem.csv"
        with self.mem.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["user_id", "entityType"]); w.writeheader()
            w.writerow({"user_id": "7834", "entityType": "a_個人"})
            w.writerow({"user_id": "9001", "entityType": "b_法人"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_idcol_autolinks_exit_0(self):
        # --id-col keys both classes; submittedBy auto-detects user_id by value overlap.
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root),
                     "--map", f"Application={self.app}", "--map", f"Member={self.mem}",
                     "--id-col", "Application=deal_id", "--id-col", "Member=user_id"])
        self.assertEqual(cm.exception.code, 0)
        ttl = (self.root / "ontology" / "ontology-abox.ttl").read_text(encoding="utf-8")
        self.assertIn("ex:submittedBy", ttl)
        self.assertIn("ex:Application_D1 ex:submittedBy ex:Member_7834 .", ttl)

    def test_explicit_link_exit_0(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root),
                     "--map", f"Application={self.app}", "--map", f"Member={self.mem}",
                     "--id-col", "Member=user_id",
                     "--link", "Application.submittedBy=user_id"])
        self.assertEqual(cm.exception.code, 0)

    def test_bad_idcol_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root),
                     "--map", f"Application={self.app}",
                     "--id-col", "Ghost=deal_id"])
        self.assertEqual(cm.exception.code, 2)

    def test_bad_link_column_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-abox", str(self.root),
                     "--map", f"Application={self.app}", "--map", f"Member={self.mem}",
                     "--id-col", "Member=user_id",
                     "--link", "Application.submittedBy=nonexistent_col"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
