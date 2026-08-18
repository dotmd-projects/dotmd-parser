import csv
import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser.cli import run as cli_run


def _mk(root: Path, status_values):
    onto = root / "ontology"; onto.mkdir()
    ir = {"vocabularies": [{"name": "applicationStatus",
                            "values": ["買取成立", "謝絶", "キャンセル", "未処理"]}]}
    (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
    csvp = root / "t.csv"
    with csvp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["status"]); w.writeheader()
        for v in status_values:
            w.writerow({"status": v})
    return csvp


class TestCliEnums(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_exit_1_on_dead_enum(self):
        # data lacks 未処理 -> dead enum -> --check exits 1
        csvp = _mk(self.root, ["買取成立", "謝絶", "キャンセル", "却下"])
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-verify-enums", str(self.root), "--data", str(csvp), "--check"])
        self.assertEqual(cm.exception.code, 1)

    def test_clean_exit_0(self):
        # data has all four enum values -> no dead enum -> 0
        csvp = _mk(self.root, ["買取成立", "謝絶", "キャンセル", "未処理"])
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-verify-enums", str(self.root), "--data", str(csvp), "--check"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue((self.root / "ontology" / "ontology-enum-report.json").exists())

    def test_missing_json_exit_2(self):
        csvp = self.root / "t.csv"
        csvp.write_text("status\n買取成立\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-verify-enums", str(self.root), "--data", str(csvp)])
        self.assertEqual(cm.exception.code, 2)

    def test_no_data_exit_2(self):
        _mk(self.root, ["買取成立"])
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology-verify-enums", str(self.root)])   # no --data
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
