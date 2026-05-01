"""
dotmd-parser — dotmd-index.md generation tests.

Generates a single Markdown artifact at <root>/dotmd-index.md that combines
inventory + build_index into a token-efficient overview Claude can read
without scanning every file in the folder.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from dotmd_parser.index_md import (
    DEFAULT_INDEX_FILENAME,
    INDEX_MD_SCHEMA,
    extract_frontmatter,
    generate_index_md,
    write_index_md,
)


def _w(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestGenerateIndexMd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _w(
            self.root / "SKILL.md",
            "---\nname: demo\n---\n# Demo Skill\n\nA demo skill.\n\n@include shared/role.md\n",
        )
        _w(self.root / "shared" / "role.md", "# Role\n\nYou are an assistant.\n")
        _w(self.root / "agents" / "classifier.md", "# Classifier\n\nClassifies things.\n")
        _w(self.root / "specs" / "manual.txt", "manual\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_markdown_string(self):
        md = generate_index_md(str(self.root))
        self.assertIsInstance(md, str)
        self.assertTrue(md.startswith("---\n"))

    def test_has_yaml_frontmatter_with_required_fields(self):
        md = generate_index_md(str(self.root))
        fm = extract_frontmatter(md)
        self.assertEqual(fm["schema"], INDEX_MD_SCHEMA)
        self.assertEqual(fm["generated_by"], "dotmd-parser")
        self.assertIn("generated_at", fm)
        self.assertIn("generator_version", fm)
        self.assertIn("content_hash", fm)
        self.assertTrue(fm["content_hash"].startswith("sha256:"))

    def test_frontmatter_contains_stats(self):
        md = generate_index_md(str(self.root))
        fm = extract_frontmatter(md)
        stats = fm["stats"]
        self.assertGreaterEqual(stats["files"], 4)
        self.assertGreaterEqual(stats["markdown"], 3)
        self.assertEqual(stats["cycles"], 0)
        self.assertEqual(stats["missing"], 0)

    def test_body_lists_markdown_files_with_titles(self):
        md = generate_index_md(str(self.root))
        self.assertIn("# Folder Index", md)
        self.assertIn("## Summary", md)
        self.assertIn("## Files", md)
        self.assertIn("Demo Skill", md)
        self.assertIn("Role", md)

    def test_body_includes_dependency_info_for_md_files(self):
        md = generate_index_md(str(self.root))
        # SKILL.md @includes shared/role.md — that edge should appear
        self.assertRegex(md, r"include.*shared/role\.md|shared/role\.md.*include")

    def test_content_hash_stable_across_calls(self):
        md1 = generate_index_md(str(self.root))
        md2 = generate_index_md(str(self.root))
        h1 = extract_frontmatter(md1)["content_hash"]
        h2 = extract_frontmatter(md2)["content_hash"]
        self.assertEqual(h1, h2)

    def test_content_hash_changes_when_file_changes(self):
        md1 = generate_index_md(str(self.root))
        h1 = extract_frontmatter(md1)["content_hash"]
        # Modify a file
        (self.root / "shared" / "role.md").write_text("# Role v2\n", encoding="utf-8")
        md2 = generate_index_md(str(self.root))
        h2 = extract_frontmatter(md2)["content_hash"]
        self.assertNotEqual(h1, h2)

    def test_content_hash_independent_of_generated_at(self):
        # generated_at is a timestamp; the content_hash must NOT include it.
        md = generate_index_md(str(self.root))
        fm = extract_frontmatter(md)
        self.assertNotIn(fm["generated_at"], fm["content_hash"])

    def test_chunks_listed_in_frontmatter(self):
        md = generate_index_md(str(self.root))
        fm = extract_frontmatter(md)
        chunks = fm["chunks"]
        ids = [c["id"] for c in chunks]
        self.assertIn("summary", ids)
        self.assertIn("files", ids)
        # Each chunk must have an anchor referencing a heading present in the body
        for c in chunks:
            self.assertTrue(c["anchor"].startswith("#"))

    def test_chunk_markers_present_in_body(self):
        md = generate_index_md(str(self.root))
        # HTML comment markers delimit each chunk for RAG ingestion
        self.assertIn("<!-- chunk:summary -->", md)
        self.assertIn("<!-- chunk:files -->", md)

    def test_handles_empty_folder(self):
        with tempfile.TemporaryDirectory() as empty:
            md = generate_index_md(empty)
            fm = extract_frontmatter(md)
            self.assertEqual(fm["stats"]["files"], 0)
            self.assertIn("# Folder Index", md)

    def test_raises_on_nonexistent_path(self):
        with self.assertRaises(ValueError):
            generate_index_md("/nonexistent/path/that/does/not/exist")

    def test_folder_map_section_when_enabled(self):
        md = generate_index_md(str(self.root), include_folder_map=True)
        self.assertIn("## Folder Map", md)
        # Tree-style entries
        self.assertTrue(re.search(r"(├──|└──|^[a-zA-Z])", md, re.MULTILINE))

    def test_folder_map_omitted_when_disabled(self):
        md = generate_index_md(str(self.root), include_folder_map=False)
        self.assertNotIn("## Folder Map", md)

    def test_max_files_clip_appends_omitted_marker(self):
        # Create many small files
        for i in range(30):
            _w(self.root / "many" / f"file_{i:02d}.md", f"# F{i}\n")
        md = generate_index_md(str(self.root), max_files=10)
        fm = extract_frontmatter(md)
        # Stats reflects the true total, but body is clipped
        self.assertGreaterEqual(fm["stats"]["files"], 30)
        self.assertIn("files omitted", md.lower())


class TestWriteIndexMd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _w(self.root / "SKILL.md", "# X\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_filename(self):
        path, written = write_index_md(str(self.root))
        self.assertEqual(path.name, DEFAULT_INDEX_FILENAME)
        self.assertTrue(written)
        self.assertTrue(path.exists())

    def test_writes_to_root(self):
        path, _ = write_index_md(str(self.root))
        self.assertEqual(path.parent.resolve(), self.root.resolve())

    def test_idempotent_when_content_hash_unchanged(self):
        path, w1 = write_index_md(str(self.root))
        original_mtime = path.stat().st_mtime_ns
        # Second call should detect content_hash matches and skip the write
        path2, w2 = write_index_md(str(self.root))
        self.assertEqual(path, path2)
        self.assertTrue(w1)
        self.assertFalse(w2)
        self.assertEqual(path.stat().st_mtime_ns, original_mtime)

    def test_rewrites_when_content_changes(self):
        write_index_md(str(self.root))
        # Add a new file -> content_hash changes -> rewrite expected
        _w(self.root / "new.md", "# new\n")
        _, w2 = write_index_md(str(self.root))
        self.assertTrue(w2)

    def test_refuses_to_overwrite_user_file_without_force(self):
        target = self.root / DEFAULT_INDEX_FILENAME
        target.write_text("# Hand-written, not from dotmd-parser\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            write_index_md(str(self.root))

    def test_force_overwrites_user_file(self):
        target = self.root / DEFAULT_INDEX_FILENAME
        target.write_text("# Hand-written\n", encoding="utf-8")
        path, written = write_index_md(str(self.root), force=True)
        self.assertTrue(written)
        self.assertIn("generated_by: dotmd-parser", path.read_text(encoding="utf-8"))

    def test_overwrites_when_existing_file_is_dotmd_generated(self):
        # First write: legitimate dotmd-parser artifact
        write_index_md(str(self.root))
        # Modify content & write again — should overwrite without --force
        _w(self.root / "another.md", "# another\n")
        _, written = write_index_md(str(self.root))
        self.assertTrue(written)


class TestExtractFrontmatter(unittest.TestCase):
    def test_parses_simple_keys(self):
        md = "---\nschema: dotmd-index/v1\nname: demo\n---\nbody\n"
        fm = extract_frontmatter(md)
        self.assertEqual(fm["schema"], "dotmd-index/v1")
        self.assertEqual(fm["name"], "demo")

    def test_parses_nested_dict(self):
        md = "---\nstats:\n  files: 5\n  cycles: 0\n---\n"
        fm = extract_frontmatter(md)
        self.assertEqual(fm["stats"], {"files": 5, "cycles": 0})

    def test_parses_list(self):
        md = '---\nchunks:\n  - id: summary\n    anchor: "#summary"\n  - id: files\n    anchor: "#files"\n---\n'
        fm = extract_frontmatter(md)
        self.assertEqual(len(fm["chunks"]), 2)
        self.assertEqual(fm["chunks"][0]["id"], "summary")

    def test_returns_empty_dict_when_no_frontmatter(self):
        self.assertEqual(extract_frontmatter("# just a body\n"), {})


if __name__ == "__main__":
    unittest.main()
