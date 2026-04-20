"""
dotmd-parser — index --scope (subfolder incremental) tests.

`--scope` re-indexes a subdirectory that has its own SKILL.md entry and
merges the result into any existing root-level index.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from dotmd_parser.index import (
    build_scoped_index,
    merge_index,
    build_index,
    load_index,
    default_index_path,
)


def _write_skill(dir_path: Path, body: str) -> None:
    (dir_path / "SKILL.md").write_text(body, encoding="utf-8")


class TestBuildScopedIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # root SKILL pulls in files from two subfolders
        _write_skill(
            self.root,
            "# root\n@include a/SKILL.md\n@include b/SKILL.md\n",
        )
        (self.root / "a").mkdir()
        _write_skill(self.root / "a", "# a\n@include shared.md\n")
        (self.root / "a" / "shared.md").write_text("# a-shared\n", encoding="utf-8")
        (self.root / "b").mkdir()
        _write_skill(self.root / "b", "# b\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_are_prefixed_with_scope(self):
        idx = build_scoped_index(str(self.root), "a")
        self.assertIn("a/SKILL.md", idx["files"])
        self.assertIn("a/shared.md", idx["files"])

    def test_scoped_index_omits_outside_files(self):
        idx = build_scoped_index(str(self.root), "a")
        self.assertNotIn("b/SKILL.md", idx["files"])
        self.assertNotIn("SKILL.md", idx["files"])

    def test_scoped_deps_also_prefixed(self):
        idx = build_scoped_index(str(self.root), "a")
        entry = idx["files"]["a/SKILL.md"]
        targets = [d["to"] for d in entry.get("deps", [])]
        self.assertIn("a/shared.md", targets)

    def test_nonexistent_scope_raises(self):
        with self.assertRaises(ValueError):
            build_scoped_index(str(self.root), "does-not-exist")

    def test_file_as_scope_raises(self):
        with self.assertRaises(ValueError):
            build_scoped_index(str(self.root), "SKILL.md")


class TestMergeIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_skill(
            self.root,
            "# root\n@include a/SKILL.md\n@include b/SKILL.md\n",
        )
        (self.root / "a").mkdir()
        _write_skill(self.root / "a", "# a\n@include shared.md\n")
        (self.root / "a" / "shared.md").write_text("# a-shared\n", encoding="utf-8")
        (self.root / "b").mkdir()
        _write_skill(self.root / "b", "# b\n@include intro.md\n")
        (self.root / "b" / "intro.md").write_text("# b-intro\n", encoding="utf-8")

        self.full = build_index(str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_has_both_subfolders(self):
        """Sanity: the baseline full index must contain files from a/ and b/."""
        self.assertIn("a/SKILL.md", self.full["files"])
        self.assertIn("b/SKILL.md", self.full["files"])

    def test_merge_adds_new_file_in_scope(self):
        (self.root / "a" / "extra.md").write_text("# extra\n", encoding="utf-8")
        _write_skill(
            self.root / "a",
            "# a\n@include shared.md\n@include extra.md\n",
        )
        scoped = build_scoped_index(str(self.root), "a")
        merged = merge_index(self.full, scoped, "a")
        self.assertIn("a/extra.md", merged["files"])
        self.assertIn("a/shared.md", merged["files"])

    def test_merge_preserves_non_scope_files(self):
        scoped = build_scoped_index(str(self.root), "a")
        merged = merge_index(self.full, scoped, "a")
        self.assertIn("SKILL.md", merged["files"])
        self.assertIn("b/SKILL.md", merged["files"])
        self.assertIn("b/intro.md", merged["files"])

    def test_merge_removes_files_missing_from_scope(self):
        (self.root / "a" / "shared.md").unlink()
        _write_skill(self.root / "a", "# a\n")  # no more @include shared.md
        scoped = build_scoped_index(str(self.root), "a")
        merged = merge_index(self.full, scoped, "a")
        self.assertNotIn("a/shared.md", merged["files"])

    def test_merge_recomputes_stats(self):
        scoped = build_scoped_index(str(self.root), "a")
        merged = merge_index(self.full, scoped, "a")
        self.assertEqual(merged["stats"]["files"], len(merged["files"]))


class TestIndexScopeCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_skill(
            self.root,
            "# root\n@include a/SKILL.md\n@include b/SKILL.md\n",
        )
        (self.root / "a").mkdir()
        _write_skill(self.root / "a", "# a\n@include one.md\n")
        (self.root / "a" / "one.md").write_text("# a-one\n", encoding="utf-8")
        (self.root / "b").mkdir()
        _write_skill(self.root / "b", "# b\n@include two.md\n")
        (self.root / "b" / "two.md").write_text("# b-two\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cli(self, argv):
        from dotmd_parser.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = args.func(args)
        return rc, out.getvalue(), err.getvalue()

    def test_scope_preserves_existing_outside_entries(self):
        rc, _o, _e = self._run_cli(["index", str(self.root)])
        self.assertEqual(rc, 0)
        idx_path = default_index_path(self.root)
        full = load_index(idx_path)
        self.assertIn("b/two.md", full["files"])

        # Mutate a/: add three.md and include it from a/SKILL.md
        (self.root / "a" / "three.md").write_text("# a-three\n", encoding="utf-8")
        _write_skill(
            self.root / "a", "# a\n@include one.md\n@include three.md\n"
        )
        rc, _o, _e = self._run_cli(["index", str(self.root), "--scope", "a"])
        self.assertEqual(rc, 0)

        merged = load_index(idx_path)
        self.assertIn("a/three.md", merged["files"])
        self.assertIn("a/one.md", merged["files"])
        self.assertIn("b/two.md", merged["files"])
        self.assertIn("SKILL.md", merged["files"])

    def test_scope_without_prior_index(self):
        rc, _o, _e = self._run_cli(["index", str(self.root), "--scope", "a"])
        self.assertEqual(rc, 0)
        merged = load_index(default_index_path(self.root))
        self.assertIn("a/one.md", merged["files"])
        self.assertNotIn("b/two.md", merged["files"])

    def test_scope_reports_scope_in_output(self):
        rc, stdout, _err = self._run_cli(["index", str(self.root), "--scope", "a"])
        self.assertEqual(rc, 0)
        self.assertIn("'a'", stdout)

    def test_scope_error_for_missing_dir(self):
        rc, _stdout, stderr = self._run_cli(
            ["index", str(self.root), "--scope", "nosuch"]
        )
        self.assertEqual(rc, 2)
        self.assertIn("nosuch", stderr)


if __name__ == "__main__":
    unittest.main()
