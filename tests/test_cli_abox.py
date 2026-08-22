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


if __name__ == "__main__":
    unittest.main()
