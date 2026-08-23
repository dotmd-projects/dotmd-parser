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


if __name__ == "__main__":
    unittest.main()
