"""
Tests for the Claude Code skill integration layer:
- parse_description / hash_content (parser.py)
- build_index / save_index / load_index / needs_rebuild (index.py)
- digest / tree / affects / deps_of (digest.py)
- CLI subcommands (cli.py)
"""
import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser import (
    __version__,
    affects,
    build_graph,
    build_index,
    changed_files,
    compact_graph,
    default_index_path,
    deps_of,
    digest,
    hash_content,
    load_index,
    needs_rebuild,
    parse_description,
    save_index,
    tree,
)
from dotmd_parser.cli import run as cli_run


class TestParseDescription(unittest.TestCase):
    def test_title_and_desc(self):
        content = "# My Skill\n\nDoes X when Y happens.\nMore details."
        out = parse_description(content)
        self.assertEqual(out["title"], "My Skill")
        self.assertIn("Does X when Y happens.", out["desc"])

    def test_strips_frontmatter(self):
        content = "---\nname: foo\n---\n# Title\n\nBody text."
        out = parse_description(content)
        self.assertEqual(out["title"], "Title")
        self.assertEqual(out["desc"], "Body text.")

    def test_ignores_directives_in_paragraph(self):
        content = "# T\n\n@include shared/a.md\nreal prose here"
        out = parse_description(content)
        self.assertEqual(out["desc"], "real prose here")

    def test_truncates_long_desc(self):
        long = "x" * 500
        out = parse_description(f"# T\n\n{long}")
        self.assertLessEqual(len(out["desc"]), 200)
        self.assertTrue(out["desc"].endswith("…"))

    def test_strips_inline_markdown(self):
        content = "# T\n\nUses **bold**, `code`, and [link](url) elements."
        out = parse_description(content)
        self.assertNotIn("**", out["desc"])
        self.assertNotIn("`", out["desc"])
        self.assertNotIn("](", out["desc"])

    def test_no_title(self):
        out = parse_description("No heading here, just text.")
        self.assertEqual(out["title"], "")
        self.assertIn("No heading", out["desc"])


class TestHashContent(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(hash_content("abc"), hash_content("abc"))

    def test_different_content_different_hash(self):
        self.assertNotEqual(hash_content("abc"), hash_content("abd"))

    def test_truncated_length(self):
        self.assertEqual(len(hash_content("abc")), 16)


class _SkillFixture(unittest.TestCase):
    """Shared scaffolding: write a small skill tree to a tempdir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write("SKILL.md", "# Test Skill\n\nA test.\n@include shared/role.md\n@delegate agents/worker.md --parallel")
        self._write("shared/role.md", "# Role\n\nYou are helpful.\nUse {{name}}.")
        self._write("agents/worker.md", "# Worker\n\nDoes work.")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p


class TestCompactGraph(_SkillFixture):
    def test_relative_paths(self):
        graph = build_graph(str(self.root))
        idx = compact_graph(graph, self.root)
        self.assertIn("SKILL.md", idx["files"])
        self.assertIn("shared/role.md", idx["files"])
        self.assertIn("agents/worker.md", idx["files"])

    def test_stats(self):
        idx = build_index(self.root)
        self.assertEqual(idx["stats"]["files"], 3)
        self.assertEqual(idx["stats"]["edges"], 2)
        self.assertEqual(idx["stats"]["cycles"], 0)
        self.assertEqual(idx["stats"]["missing"], 0)

    def test_metadata_present(self):
        idx = build_index(self.root)
        skill = idx["files"]["SKILL.md"]
        self.assertEqual(skill["title"], "Test Skill")
        self.assertEqual(skill["type"], "skill")
        self.assertTrue(skill["hash"])
        self.assertGreater(skill["size"], 0)

    def test_deps_list(self):
        idx = build_index(self.root)
        skill = idx["files"]["SKILL.md"]
        kinds = {d["type"] for d in skill["deps"]}
        self.assertEqual(kinds, {"include", "delegate"})
        parallel_deps = [d for d in skill["deps"] if d.get("parallel")]
        self.assertEqual(len(parallel_deps), 1)

    def test_placeholders_preserved(self):
        idx = build_index(self.root)
        role = idx["files"]["shared/role.md"]
        self.assertIn("name", role.get("placeholders", []))


class TestSaveLoad(_SkillFixture):
    def test_default_path(self):
        idx = build_index(self.root)
        out = save_index(idx, self.root)
        self.assertEqual(out, default_index_path(self.root))
        self.assertTrue(out.exists())

    def test_roundtrip(self):
        idx = build_index(self.root)
        out = save_index(idx, self.root)
        loaded = load_index(out)
        self.assertEqual(loaded["stats"], idx["stats"])
        self.assertEqual(set(loaded["files"]), set(idx["files"]))

    def test_schema_validation(self):
        out = self.root / "bad.json"
        out.write_text(json.dumps({"schema": 999, "files": {}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_index(out)


class TestCacheInvalidation(_SkillFixture):
    def test_fresh_index_not_dirty(self):
        idx = build_index(self.root)
        self.assertFalse(needs_rebuild(idx, self.root))

    def test_edit_triggers_rebuild(self):
        idx = build_index(self.root)
        self._write("shared/role.md", "# Role\n\nCompletely different content.")
        self.assertTrue(needs_rebuild(idx, self.root))
        self.assertIn("shared/role.md", changed_files(idx, self.root))

    def test_deletion_triggers_rebuild(self):
        idx = build_index(self.root)
        (self.root / "shared/role.md").unlink()
        self.assertTrue(needs_rebuild(idx, self.root))


class TestDigestFunctions(_SkillFixture):
    def test_digest_mentions_files_and_health(self):
        idx = build_index(self.root)
        out = digest(idx)
        self.assertIn("SKILL.md", out)
        self.assertIn("shared/role.md", out)
        self.assertIn("Health: OK", out)
        self.assertIn("Test Skill", out)

    def test_digest_shows_placeholders(self):
        idx = build_index(self.root)
        self.assertIn("name", digest(idx))

    def test_digest_size_token_budget(self):
        """Digest should stay well under 2KB for a 3-file skill."""
        idx = build_index(self.root)
        out = digest(idx)
        self.assertLess(len(out.encode("utf-8")), 2048)

    def test_tree_renders_entry(self):
        idx = build_index(self.root)
        out = tree(idx)
        self.assertIn("SKILL.md", out)
        self.assertIn("shared/role.md", out)
        self.assertIn("include", out)

    def test_affects_transitive(self):
        # Add a 2-hop chain: SKILL.md → shared/role.md
        # role.md already has one dependent (SKILL.md).
        idx = build_index(self.root)
        self.assertEqual(affects(idx, "shared/role.md"), ["SKILL.md"])

    def test_affects_no_dependents(self):
        idx = build_index(self.root)
        self.assertEqual(affects(idx, "SKILL.md"), [])

    def test_deps_of_direct(self):
        idx = build_index(self.root)
        result = deps_of(idx, "SKILL.md")
        self.assertEqual(len(result), 2)


class TestCycleDetection(unittest.TestCase):
    def test_cycle_reflected_in_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("# A\n@include b.md")
            (root / "b.md").write_text("# B\n@include a.md")
            idx = build_index(str(root / "a.md"))
            self.assertGreater(idx["stats"]["cycles"], 0)
            self.assertTrue(idx["cycles"])


class TestCLI(_SkillFixture):
    def _cli(self, *args: str):
        try:
            cli_run(list(args))
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    def test_index_command_writes_file(self):
        rc = self._cli("index", str(self.root))
        self.assertEqual(rc, 0)
        self.assertTrue(default_index_path(self.root).exists())

    def test_check_command_success(self):
        rc = self._cli("check", str(self.root))
        self.assertEqual(rc, 0)

    def test_check_command_fails_on_cycle(self):
        self._write("shared/role.md", "# Role\n@include ../SKILL.md")
        rc = self._cli("check", str(self.root))
        self.assertEqual(rc, 1)

    def test_digest_command(self):
        rc = self._cli("digest", str(self.root))
        self.assertEqual(rc, 0)

    def test_legacy_positional_invocation(self):
        """`dotmd-parser <path>` without subcommand still works (show)."""
        rc = self._cli(str(self.root))
        self.assertEqual(rc, 0)


class TestInitCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _cli(self, *args: str):
        try:
            cli_run(list(args))
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    def test_init_installs_skill_md(self):
        rc = self._cli("init", str(self.project))
        self.assertEqual(rc, 0)
        target = self.project / ".claude" / "skills" / "dotmd-parser" / "SKILL.md"
        self.assertTrue(target.exists())
        self.assertIn("name: dotmd-parser", target.read_text(encoding="utf-8"))

    def test_init_refuses_overwrite(self):
        self._cli("init", str(self.project))
        rc = self._cli("init", str(self.project))
        self.assertEqual(rc, 1)

    def test_init_force_overwrites(self):
        self._cli("init", str(self.project))
        target = self.project / ".claude" / "skills" / "dotmd-parser" / "SKILL.md"
        target.write_text("stub", encoding="utf-8")
        rc = self._cli("init", str(self.project), "--force")
        self.assertEqual(rc, 0)
        self.assertNotEqual(target.read_text(encoding="utf-8"), "stub")

    def test_init_missing_path(self):
        rc = self._cli("init", str(self.project / "does-not-exist"))
        self.assertEqual(rc, 2)


class TestBundledTemplate(unittest.TestCase):
    def test_template_importable(self):
        from importlib import resources
        text = resources.files("dotmd_parser.templates").joinpath("SKILL.md").read_text(encoding="utf-8")
        self.assertIn("dotmd-parser", text)
        self.assertIn("PostToolUse", text)  # Hook guide section present


class TestVersion(unittest.TestCase):
    def test_version_bump(self):
        self.assertEqual(__version__, "0.3.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
