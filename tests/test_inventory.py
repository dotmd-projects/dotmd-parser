"""
dotmd-parser — inventory command unit tests.

Run: `python3 -m unittest tests.test_inventory -v`
"""
import unittest
import tempfile
from pathlib import Path

from dotmd_parser.inventory import (
    inventory,
    format_inventory,
    BINARY_EXTENSIONS,
    TEXT_EXTENSIONS,
)


class TestInventoryBasic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel: str, content: bytes = b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_empty_folder(self):
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 0)
        self.assertEqual(inv["total_bytes"], 0)
        self.assertEqual(inv["extensions"], {})
        self.assertEqual(inv["markdown_count"], 0)
        self.assertEqual(inv["markdown_ratio"], 0.0)
        self.assertEqual(inv["binary_ratio"], 0.0)

    def test_single_markdown_file(self):
        self._touch("README.md", b"hello")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 1)
        self.assertEqual(inv["total_bytes"], 5)
        self.assertIn(".md", inv["extensions"])
        self.assertEqual(inv["extensions"][".md"]["count"], 1)
        self.assertEqual(inv["markdown_count"], 1)
        self.assertEqual(inv["markdown_ratio"], 1.0)
        self.assertEqual(inv["binary_ratio"], 0.0)

    def test_binary_only_folder(self):
        self._touch("a.pdf", b"pdf-bytes")
        self._touch("b.docx", b"docx-bytes")
        self._touch("c.pptx", b"pptx-bytes")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 3)
        self.assertEqual(inv["markdown_count"], 0)
        self.assertEqual(inv["markdown_ratio"], 0.0)
        self.assertEqual(inv["binary_ratio"], 1.0)

    def test_mixed_folder(self):
        self._touch("doc.md")
        self._touch("doc.pdf")
        self._touch("notes.txt")
        self._touch("image.png")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 4)
        self.assertEqual(inv["markdown_count"], 1)
        self.assertAlmostEqual(inv["markdown_ratio"], 0.25)
        self.assertAlmostEqual(inv["binary_ratio"], 0.5)  # pdf + png

    def test_extensions_aggregation(self):
        self._touch("a.md", b"1")
        self._touch("b.md", b"12")
        self._touch("c.pdf", b"123")
        inv = inventory(str(self.root))
        self.assertEqual(inv["extensions"][".md"]["count"], 2)
        self.assertEqual(inv["extensions"][".md"]["bytes"], 3)
        self.assertEqual(inv["extensions"][".pdf"]["count"], 1)
        self.assertEqual(inv["extensions"][".pdf"]["bytes"], 3)

    def test_case_insensitive_extensions(self):
        self._touch("a.PDF", b"1")
        self._touch("b.pdf", b"2")
        inv = inventory(str(self.root))
        self.assertEqual(inv["extensions"][".pdf"]["count"], 2)

    def test_skip_hidden_files(self):
        self._touch(".DS_Store", b"meta")
        self._touch(".git/config", b"[core]")
        self._touch("visible.md", b"x")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 1)

    def test_skip_office_lock_files(self):
        self._touch("~$report.pptx", b"lock")
        self._touch("report.pptx", b"real")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 1)

    def test_recursive(self):
        self._touch("a.md")
        self._touch("sub/b.md")
        self._touch("sub/deep/c.pdf")
        inv = inventory(str(self.root))
        self.assertEqual(inv["total_files"], 3)

    def test_largest_files(self):
        self._touch("small.md", b"x")
        self._touch("big.pdf", b"x" * 10_000)
        self._touch("medium.docx", b"x" * 500)
        inv = inventory(str(self.root))
        self.assertIn("largest_files", inv)
        self.assertEqual(len(inv["largest_files"]), 3)
        self.assertEqual(inv["largest_files"][0]["path"], "big.pdf")
        self.assertEqual(inv["largest_files"][0]["bytes"], 10_000)

    def test_largest_files_capped_at_five(self):
        for i in range(10):
            self._touch(f"f{i}.md", b"x" * (i + 1))
        inv = inventory(str(self.root))
        self.assertEqual(len(inv["largest_files"]), 5)

    def test_nonexistent_path_raises(self):
        with self.assertRaises(ValueError):
            inventory("/nonexistent/xyz/abc")


class TestClassification(unittest.TestCase):
    def test_md_in_text(self):
        self.assertIn(".md", TEXT_EXTENSIONS)

    def test_pdf_in_binary(self):
        self.assertIn(".pdf", BINARY_EXTENSIONS)

    def test_docx_in_binary(self):
        self.assertIn(".docx", BINARY_EXTENSIONS)

    def test_pptx_in_binary(self):
        self.assertIn(".pptx", BINARY_EXTENSIONS)

    def test_text_binary_disjoint(self):
        self.assertEqual(BINARY_EXTENSIONS & TEXT_EXTENSIONS, set())


class TestFormatInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.md").write_bytes(b"x")
        (self.root / "b.pdf").write_bytes(b"x" * 2048)

    def tearDown(self):
        self.tmp.cleanup()

    def test_format_contains_totals(self):
        inv = inventory(str(self.root))
        out = format_inventory(inv)
        self.assertIn("2 files", out)

    def test_format_contains_extensions(self):
        inv = inventory(str(self.root))
        out = format_inventory(inv)
        self.assertIn(".md", out)
        self.assertIn(".pdf", out)

    def test_format_shows_markdown_ratio(self):
        inv = inventory(str(self.root))
        out = format_inventory(inv)
        self.assertIn("markdown", out.lower())


class TestLanguageDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_language_hint_japanese_filename(self):
        (self.root / "会社紹介.pdf").write_bytes(b"x")
        inv = inventory(str(self.root))
        self.assertIn("has_japanese_names", inv)
        self.assertTrue(inv["has_japanese_names"])

    def test_language_hint_ascii_only(self):
        (self.root / "intro.pdf").write_bytes(b"x")
        inv = inventory(str(self.root))
        self.assertFalse(inv["has_japanese_names"])


if __name__ == "__main__":
    unittest.main()
