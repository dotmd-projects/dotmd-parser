"""
dotmd-parser — --host-agent / --plan mode tests.

Covers `format_host_agent_plan()` which emits a Markdown instruction pack
that Claude Code (or any host agent) can execute instead of the API.
Also covers `apply_analysis_from_file()` which ingests the resulting JSON.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from dotmd_parser.analyze import (
    format_host_agent_plan,
    apply_analysis_from_file,
)


class TestFormatHostAgentPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.md").write_text("# A\nsome text", encoding="utf-8")
        (self.root / "b.md").write_text("# B\nother text", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_string(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIsInstance(plan, str)
        self.assertGreater(len(plan), 0)

    def test_contains_file_paths(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIn("a.md", plan)
        self.assertIn("b.md", plan)

    def test_contains_task_description(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIn("depend", plan.lower())

    def test_contains_output_schema(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIn("edges", plan)
        self.assertIn("shared_proposals", plan)

    def test_contains_apply_instructions(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIn("--apply-from", plan)

    def test_empty_folder_returns_sentinel(self):
        with tempfile.TemporaryDirectory() as empty:
            plan = format_host_agent_plan(empty)
            self.assertIn("no documents", plan.lower())

    def test_plan_references_path(self):
        plan = format_host_agent_plan(str(self.root))
        self.assertIn(str(self.root), plan)


class TestApplyAnalysisFromFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "child.md").write_text("# child", encoding="utf-8")
        (self.root / "parent.md").write_text("# parent", encoding="utf-8")

        self.json_path = self.root / "analysis.json"
        self.json_path.write_text(
            json.dumps(
                {
                    "documents": [
                        {"path": "child.md", "summary": "# child"},
                        {"path": "parent.md", "summary": "# parent"},
                    ],
                    "edges": [
                        {
                            "from": "parent.md",
                            "to": "child.md",
                            "reason": "parent references child",
                        }
                    ],
                    "shared_proposals": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_applies_edges_to_text_files(self):
        result = apply_analysis_from_file(str(self.root), str(self.json_path))
        self.assertIn("parent.md", result["modified_files"])
        content = (self.root / "parent.md").read_text(encoding="utf-8")
        self.assertIn("@include child.md", content)

    def test_missing_json_raises(self):
        with self.assertRaises(FileNotFoundError):
            apply_analysis_from_file(str(self.root), "/nonexistent/x.json")

    def test_malformed_json_raises(self):
        bad = self.root / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            apply_analysis_from_file(str(self.root), str(bad))


class TestAnalyzeCliPlanMode(unittest.TestCase):
    """CLI smoke test: `dotmd-parser analyze <path> --plan` must not call the API."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "doc.md").write_text("# doc\nhello", encoding="utf-8")

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

    def test_plan_mode_does_not_require_api_key(self):
        import os

        prev = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            rc, stdout, _err = self._run_cli(["analyze", str(self.root), "--plan"])
        finally:
            if prev is not None:
                os.environ["ANTHROPIC_API_KEY"] = prev
        self.assertEqual(rc, 0)
        self.assertIn("doc.md", stdout)

    def test_apply_from_cli(self):
        analysis_path = self.root / "result.json"
        analysis_path.write_text(
            json.dumps(
                {
                    "documents": [{"path": "doc.md", "summary": "# doc"}],
                    "edges": [],
                    "shared_proposals": [
                        {
                            "name": "shared/intro.md",
                            "content_summary": "intro",
                            "used_by": ["doc.md"],
                            "reason": "shared intro",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        rc, stdout, _err = self._run_cli(
            ["analyze", str(self.root), "--apply-from", str(analysis_path)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("doc.md", stdout)
        content = (self.root / "doc.md").read_text(encoding="utf-8")
        self.assertIn("@include shared/intro.md", content)


if __name__ == "__main__":
    unittest.main()
