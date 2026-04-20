"""
dotmd-parser — empty/non-markdown folder warning tests.

Covers `suggest_next_command()` helper and CLI behaviour on folders that
contain zero .md files, ensuring users are pointed to `inventory` /
`analyze --apply` instead of silently getting an empty graph.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from dotmd_parser.inventory import suggest_next_command, inventory


class TestSuggestNextCommand(unittest.TestCase):
    """Pure helper: given an inventory dict, suggest the next command."""

    def _inv(self, **overrides):
        base = {
            "total_files": 0,
            "markdown_count": 0,
            "binary_count": 0,
            "markdown_ratio": 0.0,
            "binary_ratio": 0.0,
        }
        base.update(overrides)
        return base

    def test_empty_folder_returns_none(self):
        """Nothing to warn about in a truly empty directory."""
        self.assertIsNone(suggest_next_command(self._inv()))

    def test_markdown_dominant_returns_none(self):
        """Normal .md-first repo — no special action needed."""
        self.assertIsNone(
            suggest_next_command(
                self._inv(total_files=10, markdown_count=10, markdown_ratio=1.0)
            )
        )

    def test_zero_md_with_binary_files_suggests_analyze(self):
        suggestion = suggest_next_command(
            self._inv(total_files=50, binary_count=50, binary_ratio=1.0)
        )
        self.assertIsNotNone(suggestion)
        self.assertIn("analyze", suggestion)

    def test_zero_md_with_text_files_suggests_manual(self):
        """Text files but no .md — user likely wants to add directives themselves."""
        suggestion = suggest_next_command(
            self._inv(total_files=5, markdown_count=0, binary_count=0)
        )
        self.assertIsNotNone(suggestion)
        self.assertIn("@include", suggestion)

    def test_mostly_binary_suggests_analyze(self):
        """Few .md files drowning in binaries — analyze helps seed deps.yml."""
        suggestion = suggest_next_command(
            self._inv(
                total_files=100,
                markdown_count=2,
                binary_count=95,
                markdown_ratio=0.02,
                binary_ratio=0.95,
            )
        )
        self.assertIsNotNone(suggestion)
        self.assertIn("analyze", suggestion)


class TestIndexCommandWarning(unittest.TestCase):
    """End-to-end: `dotmd-parser index` on a .md-less folder emits a warning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cli(self, argv):
        """Invoke the CLI as a function and capture stdout + stderr + return code."""
        from dotmd_parser.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = args.func(args)
        return rc, out.getvalue(), err.getvalue()

    def test_index_on_binary_only_warns(self):
        (self.root / "a.pdf").write_bytes(b"x")
        (self.root / "b.docx").write_bytes(b"y")
        rc, _stdout, stderr = self._run_cli(["index", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("inventory", stderr)

    def test_index_on_markdown_folder_no_warning(self):
        (self.root / "README.md").write_text("# hello\n", encoding="utf-8")
        rc, _stdout, stderr = self._run_cli(["index", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertNotIn("inventory", stderr)

    def test_digest_on_binary_only_warns(self):
        (self.root / "a.pdf").write_bytes(b"x")
        rc, _stdout, stderr = self._run_cli(["digest", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("inventory", stderr)


if __name__ == "__main__":
    unittest.main()
