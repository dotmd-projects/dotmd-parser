import csv
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from dotmd_parser import enums as E


def _write_csv(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


class TestLoadAndCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_vocabularies(self):
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"vocabularies": [{"name": "applicationStatus",
                                "values": ["買取成立", "謝絶"], "provenance": ["a.md"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        vocabs = E.load_vocabularies(self.root)
        self.assertEqual(vocabs[0]["name"], "applicationStatus")

    def test_load_missing_json_raises(self):
        (self.root / "ontology").mkdir()
        with self.assertRaises(FileNotFoundError):
            E.load_vocabularies(self.root)

    def test_column_counts(self):
        csvp = self.root / "d.csv"
        _write_csv(csvp, [{"status": "買取成立", "seg": "a"},
                          {"status": "買取成立", "seg": "b"},
                          {"status": "謝絶", "seg": "a"}])
        cols, warnings = E.column_counts([str(csvp)])
        self.assertEqual(cols[(str(csvp), "status")], Counter({"買取成立": 2, "謝絶": 1}))
        self.assertEqual(warnings, [])

    def test_column_counts_unreadable_warns(self):
        cols, warnings = E.column_counts([str(self.root / "nope.csv")])
        self.assertEqual(cols, {})
        self.assertTrue(warnings and "nope.csv" in warnings[0])


if __name__ == "__main__":
    unittest.main()
