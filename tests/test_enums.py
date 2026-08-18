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


class TestMatchAndVerify(unittest.TestCase):
    def _columns(self):
        return {
            ("t.csv", "status"): Counter({"買取成立": 500, "謝絶": 120, "キャンセル": 30, "却下": 12}),
            ("t.csv", "seg"): Counter({"a_初回": 300, "b_リピーター": 700}),
        }

    def test_match_picks_best_column(self):
        vocab = {"name": "applicationStatus",
                 "values": ["買取成立", "謝絶", "キャンセル", "未処理"]}
        m = E.match_vocabulary(vocab, self._columns(), 0.5)
        self.assertEqual((m["file"], m["column"]), ("t.csv", "status"))
        self.assertEqual(m["shared"], 3)
        self.assertEqual(m["score"], 0.75)

    def test_match_none_when_no_overlap(self):
        vocab = {"name": "foo", "values": ["x", "y", "z"]}
        self.assertIsNone(E.match_vocabulary(vocab, self._columns(), 0.5))

    def test_match_none_below_threshold(self):
        vocab = {"name": "applicationStatus",
                 "values": ["買取成立", "謝絶", "キャンセル", "未処理"]}
        self.assertIsNone(E.match_vocabulary(vocab, self._columns(), 0.9))  # 0.75 < 0.9

    def test_verify_enums(self):
        vocabs = [{"name": "applicationStatus",
                   "values": ["買取成立", "謝絶", "キャンセル", "未処理"]},
                  {"name": "foo", "values": ["x", "y", "z"]}]
        out = E.verify_enums(vocabs, self._columns(), threshold=0.5, top=20)
        app = next(v for v in out["vocabularies"] if v["name"] == "applicationStatus")
        self.assertEqual(app["matched"]["column"], "status")
        self.assertNotIn("counter", app["matched"])            # internal counter stripped
        self.assertEqual(app["enum_not_in_data"], ["未処理"])
        self.assertEqual(app["data_not_in_enum"], [{"value": "却下", "count": 12}])
        self.assertEqual(app["coverage"], 0.75)
        self.assertIn("foo", out["unmatched"])

    def test_top_n_cap(self):
        cols = {("t.csv", "c"): Counter({"買取成立": 1, "謝絶": 1,
                                         "e1": 9, "e2": 8, "e3": 7})}
        vocabs = [{"name": "v", "values": ["買取成立", "謝絶"]}]
        out = E.verify_enums(vocabs, cols, threshold=0.5, top=2)
        v = out["vocabularies"][0]
        self.assertEqual([d["value"] for d in v["data_not_in_enum"]], ["e1", "e2"])  # count desc
        self.assertEqual(v["data_not_in_enum_omitted"], 1)                            # e3 omitted


class TestRunEnumVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"vocabularies": [{"name": "applicationStatus",
                                "values": ["買取成立", "謝絶", "キャンセル", "未処理"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        self.csv = self.root / "t.csv"
        _write_csv(self.csv, [{"status": "買取成立"}, {"status": "謝絶"},
                              {"status": "キャンセル"}, {"status": "却下"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_writes_report_and_findings(self):
        res = E.run_enum_verify(self.root, [str(self.csv)])
        f = res["findings"]
        app = f["vocabularies"][0]
        self.assertEqual(app["enum_not_in_data"], ["未処理"])
        self.assertTrue(any(Path(p).name == "ontology-enum-report.json" for p in res["written"]))
        self.assertTrue((self.root / "ontology" / "ontology-enum-report.md").exists())

    def test_deterministic_json(self):
        r1 = E.run_enum_verify(self.root, [str(self.csv)])
        r2 = E.run_enum_verify(self.root, [str(self.csv)])
        self.assertEqual(E.emit_enum_report_json(r1["findings"]),
                         E.emit_enum_report_json(r2["findings"]))

    def test_all_unreadable_raises(self):
        with self.assertRaises(ValueError):
            E.run_enum_verify(self.root, [str(self.root / "nope.csv")])


class TestEnumRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"vocabularies": [{"name": "applicationStatus",
                                "values": ["買取成立", "謝絶", "キャンセル", "未処理"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")
        self.good = self.root / "good.csv"
        _write_csv(self.good, [{"status": "買取成立"}, {"status": "謝絶"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_bad_csv_skipped_with_warning(self):
        bad = self.root / "bad.csv"  # does not exist -> unreadable
        res = E.run_enum_verify(self.root, [str(self.good), str(bad)])
        self.assertTrue(any("bad.csv" in w for w in res["findings"]["warnings"]))
        # good.csv still produced a match
        self.assertIsNotNone(res["findings"]["vocabularies"][0]["matched"])

    def test_report_md_deterministic(self):
        r1 = E.run_enum_verify(self.root, [str(self.good)])
        r2 = E.run_enum_verify(self.root, [str(self.good)])
        self.assertEqual(E.emit_enum_report_md(r1["findings"]),
                         E.emit_enum_report_md(r2["findings"]))


class TestEnumCsvHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        onto = self.root / "ontology"; onto.mkdir()
        ir = {"vocabularies": [{"name": "v", "values": ["買取成立", "謝絶"]}]}
        (onto / "ontology.json").write_text(json.dumps(ir), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bom_stripped_from_column_name(self):
        p = self.root / "bom.csv"
        p.write_text("﻿status\n買取成立\n謝絶\n", encoding="utf-8")
        cols, _ = E.column_counts([str(p)])
        # column name must be "status", not "﻿status"
        self.assertIn((str(p), "status"), cols)

    def test_md_escapes_pipe_in_value(self):
        p = self.root / "d.csv"
        p.write_text("status\n買取成立\n謝絶\na|b\n", encoding="utf-8")
        res = E.run_enum_verify(self.root, [str(p)])
        md = E.emit_enum_report_md(res["findings"])
        self.assertNotIn("| a|b |", md)      # raw unescaped cell must not appear
        self.assertIn("a\\|b", md)           # escaped form present


if __name__ == "__main__":
    unittest.main()
