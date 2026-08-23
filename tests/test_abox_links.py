import unittest

from dotmd_parser import abox_links as L


class TestSlugKey(unittest.TestCase):
    def test_numeric_key_unchanged(self):
        self.assertEqual(L.slug_key("7834"), "7834")

    def test_hyphen_and_underscore_preserved(self):
        self.assertEqual(L.slug_key("D-123_A"), "D-123_A")

    def test_cjk_and_spaces_collapse_to_x(self):
        # every char is outside [A-Za-z0-9_-]; the whole run collapses to one
        # dash and strips to empty -> "x".
        self.assertEqual(L.slug_key(" 建設 業 "), "x")
        self.assertEqual(L.slug_key("建設業"), "x")

    def test_mixed_safe_and_unsafe(self):
        self.assertEqual(L.slug_key("a b/c"), "a-b-c")   # unsafe runs -> single dash
        self.assertEqual(L.slug_key("D#1"), "D-1")       # interior unsafe -> dash

    def test_empty_becomes_x(self):
        self.assertEqual(L.slug_key(""), "x")
        self.assertEqual(L.slug_key("！！"), "x")

    def test_result_is_valid_local_suffix(self):
        # prefix:Class_<slug> must satisfy ^[A-Za-z_][A-Za-z0-9_-]*$; the Class_
        # prefix guarantees a leading letter, so slug only needs the safe charset.
        for v in ["7834", "建設業", "a b/c", "D#1", "  "]:
            self.assertRegex(L.slug_key(v), r"^[A-Za-z0-9_-]+$")


class TestSubjectUri(unittest.TestCase):
    def test_unkeyed_uses_rowindex(self):
        uri, key = L.subject_uri("ex", "Member", {"user_id": "7834"}, 3, None)
        self.assertEqual(uri, "ex:Member_3")
        self.assertIsNone(key)

    def test_keyed_uses_slugged_value(self):
        uri, key = L.subject_uri("ex", "Member", {"user_id": "7834"}, 3, "user_id")
        self.assertEqual(uri, "ex:Member_7834")
        self.assertEqual(key, "7834")

    def test_keyed_missing_value_falls_back_to_row(self):
        uri, key = L.subject_uri("ex", "Member", {"user_id": ""}, 5, "user_id")
        self.assertEqual(uri, "ex:Member__row5")
        self.assertIsNone(key)


_OPROPS = [
    {"name": "submittedBy", "from": "Application", "to": "Member"},
    {"name": "hasOccupation", "from": "Member", "to": "Occupation"},
]


class TestParseIdCols(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(
            L.parse_id_cols(["Member=user_id", "Application=deal_id"],
                            {"Member", "Application"}),
            {"Member": "user_id", "Application": "deal_id"})

    def test_none_returns_empty(self):
        self.assertEqual(L.parse_id_cols(None, {"Member"}), {})

    def test_bad_format_raises(self):
        with self.assertRaises(ValueError):
            L.parse_id_cols(["Member"], {"Member"})

    def test_empty_column_raises(self):
        with self.assertRaises(ValueError):
            L.parse_id_cols(["Member="], {"Member"})

    def test_unmapped_class_raises(self):
        with self.assertRaises(ValueError):
            L.parse_id_cols(["Ghost=x"], {"Member"})

    def test_duplicate_class_raises(self):
        with self.assertRaises(ValueError):
            L.parse_id_cols(["Member=a", "Member=b"], {"Member"})


class TestParseLinks(unittest.TestCase):
    def _id_cols(self):
        return {"Member": "user_id"}

    def test_ok(self):
        out = L.parse_links(["Application.submittedBy=user_id"], _OPROPS,
                            self._id_cols(), {"Application", "Member"})
        self.assertEqual(out, {("Application", "submittedBy"): "user_id"})

    def test_none_returns_empty(self):
        self.assertEqual(L.parse_links(None, _OPROPS, self._id_cols(),
                                       {"Application", "Member"}), {})

    def test_bad_format_raises(self):
        with self.assertRaises(ValueError):
            L.parse_links(["submittedBy=user_id"], _OPROPS, self._id_cols(),
                          {"Application", "Member"})

    def test_unknown_property_raises(self):
        with self.assertRaises(ValueError):
            L.parse_links(["Application.ghost=user_id"], _OPROPS,
                          self._id_cols(), {"Application", "Member"})

    def test_from_mismatch_raises(self):
        # submittedBy is from Application, not Member
        with self.assertRaises(ValueError):
            L.parse_links(["Member.submittedBy=user_id"], _OPROPS,
                          self._id_cols(), {"Application", "Member"})

    def test_target_not_mapped_raises(self):
        with self.assertRaises(ValueError):
            L.parse_links(["Application.submittedBy=user_id"], _OPROPS,
                          self._id_cols(), {"Application"})

    def test_target_no_idcol_raises(self):
        with self.assertRaises(ValueError):
            L.parse_links(["Application.submittedBy=user_id"], _OPROPS,
                          {}, {"Application", "Member"})

    def test_duplicate_link_raises(self):
        with self.assertRaises(ValueError):
            L.parse_links(["Application.submittedBy=user_id",
                           "Application.submittedBy=uid2"], _OPROPS,
                          self._id_cols(), {"Application", "Member"})


class TestResolveFkColumn(unittest.TestCase):
    def test_picks_overlapping_column(self):
        cols = {"user_id": {"7834", "9001", "5"}, "amount": {"100", "200"}}
        col, method = L.resolve_fk_column(cols, {"7834", "9001", "5", "42"}, 0.6)
        self.assertEqual(col, "user_id")
        self.assertEqual(method, "value")

    def test_below_threshold_rejected(self):
        # only 1 of 3 src values overlaps -> coverage 0.33 < 0.6, and shared 1 < 2
        cols = {"user_id": {"7834", "x", "y"}}
        col, method = L.resolve_fk_column(cols, {"7834"}, 0.6)
        self.assertIsNone(col)
        self.assertIsNone(method)

    def test_shared_below_min_rejected(self):
        # coverage passes (1/1) but shared count 1 < _MIN_VALUE_OVERLAP
        cols = {"user_id": {"7834"}}
        col, method = L.resolve_fk_column(cols, {"7834", "z"}, 0.5)
        self.assertIsNone(col)

    def test_tie_breaks_by_column_order(self):
        cols = {"a": {"1", "2"}, "b": {"1", "2"}}
        col, _ = L.resolve_fk_column(cols, {"1", "2"}, 0.6)
        self.assertEqual(col, "a")


class TestMaterializeLinks(unittest.TestCase):
    def _ctx(self):
        return {
            "Application": {
                "keyed": True, "id_col": "deal_id",
                "keys": {"D1", "D2", "D3"},
                "rows": [{"deal_id": "D1", "user_id": "7834"},
                         {"deal_id": "D2", "user_id": "9001"},
                         {"deal_id": "D3", "user_id": "9999"}],  # 9999 dangling
                "columns_values": {"deal_id": {"D1", "D2", "D3"},
                                   "user_id": {"7834", "9001", "9999"}},
            },
            "Member": {
                "keyed": True, "id_col": "user_id",
                "keys": {"7834", "9001"},
                "rows": [{"user_id": "7834"}, {"user_id": "9001"}],
                "columns_values": {"user_id": {"7834", "9001"}},
            },
        }

    def test_explicit_link_emits_and_skips_dangling(self):
        oprops = [{"name": "submittedBy", "from": "Application", "to": "Member"}]
        lines, reports = L.materialize_links(
            oprops, self._ctx(),
            {("Application", "submittedBy"): "user_id"}, 0.6, "ex")
        self.assertIn("ex:Application_D1 ex:submittedBy ex:Member_7834 .", lines)
        self.assertIn("ex:Application_D2 ex:submittedBy ex:Member_9001 .", lines)
        # D3 -> user_id 9999 not in Member keys -> skipped
        self.assertNotIn("ex:Member_9999", "".join(lines))
        r = reports[0]
        self.assertEqual(r["method"], "explicit")
        self.assertEqual(r["fk_column"], "user_id")
        self.assertEqual(r["emitted"], 2)
        self.assertEqual(r["dangling"], 1)
        self.assertEqual(r["dangling_examples"], ["9999"])

    def test_autodetect_when_no_explicit(self):
        oprops = [{"name": "submittedBy", "from": "Application", "to": "Member"}]
        lines, reports = L.materialize_links(oprops, self._ctx(), {}, 0.6, "ex")
        self.assertEqual(reports[0]["method"], "value")
        self.assertEqual(reports[0]["fk_column"], "user_id")
        self.assertEqual(reports[0]["emitted"], 2)

    def test_target_not_mapped(self):
        oprops = [{"name": "runsOn", "from": "Application", "to": "AdPlatform"}]
        lines, reports = L.materialize_links(oprops, self._ctx(), {}, 0.6, "ex")
        self.assertEqual(lines, [])
        self.assertEqual(reports[0]["method"], "skipped-not-mapped")

    def test_target_unkeyed(self):
        ctx = self._ctx()
        ctx["Member"]["keyed"] = False
        oprops = [{"name": "submittedBy", "from": "Application", "to": "Member"}]
        _, reports = L.materialize_links(oprops, ctx, {}, 0.6, "ex")
        self.assertEqual(reports[0]["method"], "skipped-no-id")

    def test_no_fk_column_found(self):
        ctx = self._ctx()
        # wipe the overlapping column so nothing auto-detects
        ctx["Application"]["columns_values"] = {"deal_id": {"D1", "D2", "D3"}}
        oprops = [{"name": "submittedBy", "from": "Application", "to": "Member"}]
        _, reports = L.materialize_links(oprops, ctx, {}, 0.6, "ex")
        self.assertEqual(reports[0]["method"], "skipped-no-fk")

    def test_from_not_materialized_is_ignored(self):
        oprops = [{"name": "x", "from": "Ghost", "to": "Member"}]
        lines, reports = L.materialize_links(oprops, self._ctx(), {}, 0.6, "ex")
        self.assertEqual(reports, [])

    def test_lines_sorted_and_headered(self):
        oprops = [{"name": "submittedBy", "from": "Application", "to": "Member"}]
        lines, _ = L.materialize_links(
            oprops, self._ctx(), {("Application", "submittedBy"): "user_id"},
            0.6, "ex")
        self.assertEqual(lines[0], "# object-property links")
        body = [ln for ln in lines if ln and not ln.startswith("#")]
        self.assertEqual(body, sorted(body))


if __name__ == "__main__":
    unittest.main()
