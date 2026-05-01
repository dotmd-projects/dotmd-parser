"""
dotmd-parser — CLI `dotmd-index` subcommand tests.

The subcommand wraps `write_index_md` for the on-disk path and
`generate_index_md` for the `--stdout` path.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from dotmd_parser.cli import run as cli_run
from dotmd_parser.index_md import DEFAULT_INDEX_FILENAME, extract_frontmatter


def _w(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI and capture (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cli_run(argv)
            rc = 0
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
    return rc, out.getvalue(), err.getvalue()


class TestDotmdIndexCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _w(self.root / "SKILL.md", "# Demo\n@include shared.md\n")
        _w(self.root / "shared.md", "# Shared\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_dotmd_index_md_to_root(self):
        rc, _, _ = _invoke(["dotmd-index", str(self.root)])
        self.assertEqual(rc, 0)
        target = self.root / DEFAULT_INDEX_FILENAME
        self.assertTrue(target.exists())
        fm = extract_frontmatter(target.read_text(encoding="utf-8"))
        self.assertEqual(fm["generated_by"], "dotmd-parser")

    def test_stdout_flag_prints_without_writing(self):
        rc, out, _ = _invoke(["dotmd-index", str(self.root), "--stdout"])
        self.assertEqual(rc, 0)
        self.assertIn("# Folder Index", out)
        self.assertFalse((self.root / DEFAULT_INDEX_FILENAME).exists())

    def test_skips_when_unchanged(self):
        _invoke(["dotmd-index", str(self.root)])
        rc, out, _ = _invoke(["dotmd-index", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("unchanged", out.lower())

    def test_force_overwrites_user_file(self):
        target = self.root / DEFAULT_INDEX_FILENAME
        target.write_text("# user-written\n", encoding="utf-8")
        # Without --force: refused
        rc, _, err = _invoke(["dotmd-index", str(self.root)])
        self.assertNotEqual(rc, 0)
        self.assertIn("refus", err.lower())
        # With --force: overwritten
        rc, _, _ = _invoke(["dotmd-index", str(self.root), "--force"])
        self.assertEqual(rc, 0)
        fm = extract_frontmatter(target.read_text(encoding="utf-8"))
        self.assertEqual(fm["generated_by"], "dotmd-parser")

    def test_no_folder_map_flag(self):
        rc, out, _ = _invoke(["dotmd-index", str(self.root), "--stdout", "--no-folder-map"])
        self.assertEqual(rc, 0)
        self.assertNotIn("## Folder Map", out)

    def test_no_deps_flag(self):
        rc, out, _ = _invoke(["dotmd-index", str(self.root), "--stdout", "--no-deps"])
        self.assertEqual(rc, 0)
        self.assertNotIn("## Dependency Tree", out)

    def test_max_files_clip(self):
        for i in range(50):
            _w(self.root / "many" / f"f{i:03d}.md", f"# f{i}\n")
        rc, out, _ = _invoke(["dotmd-index", str(self.root), "--stdout", "--max-files", "10"])
        self.assertEqual(rc, 0)
        self.assertIn("files omitted", out.lower())

    def test_nonexistent_path_returns_error(self):
        rc, _, err = _invoke(["dotmd-index", "/no/such/folder/exists/here"])
        self.assertEqual(rc, 2)
        self.assertIn("does not exist", err)


class TestInitSkillFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_default_installs_parent_skill(self):
        rc, out, _ = _invoke(["init", str(self.root)])
        self.assertEqual(rc, 0)
        target = self.root / ".claude" / "skills" / "dotmd-parser" / "SKILL.md"
        self.assertTrue(target.exists())
        body = target.read_text(encoding="utf-8")
        self.assertIn("dotmd-parser", body)

    def test_init_skill_dotmd_index(self):
        rc, out, _ = _invoke(["init", str(self.root), "--skill", "dotmd-index"])
        self.assertEqual(rc, 0)
        target = self.root / ".claude" / "skills" / "dotmd-index" / "SKILL.md"
        self.assertTrue(target.exists())
        body = target.read_text(encoding="utf-8")
        self.assertIn("name: dotmd-index", body)
        self.assertIn("dotmd-index.md", body)

    def test_init_unknown_skill_id_fails(self):
        rc, _, err = _invoke(["init", str(self.root), "--skill", "no-such-skill"])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
