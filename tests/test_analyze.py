"""Tests for the AI-powered dependency detection module (analyze.py).

The real Claude API is never called — we inject a mock via the `caller`
parameter so the test suite stays offline and deterministic.
"""
import json
import tempfile
import unittest
from pathlib import Path

from dotmd_parser import (
    analyze_dependencies,
    apply_analysis,
    format_proposal,
    generate_directives,
    load_deps_yml,
    save_deps_yml,
    scan_documents,
)
from dotmd_parser.analyze import is_text_editable
from dotmd_parser.cli import run as cli_run


def _fake_response(edges=None, proposals=None) -> str:
    """Build a Claude-shaped ```json payload that analyze can parse."""
    body = {
        "edges": edges or [],
        "shared_proposals": proposals or [],
    }
    return "```json\n" + json.dumps(body) + "\n```"


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p


class TestScanDocuments(_Fixture):
    def test_skips_hidden_and_deps(self):
        self._write("a.md", "alpha")
        self._write(".hidden/b.md", "hidden")
        self._write("deps.yml", "files: []")
        self._write("node_modules/c.md", "nm")
        docs = scan_documents(self.root)
        paths = [d["path"] for d in docs]
        self.assertIn("a.md", paths)
        self.assertNotIn(".hidden/b.md", paths)
        self.assertNotIn("deps.yml", paths)

    def test_summary_truncated(self):
        long = "x" * 2000
        self._write("a.md", long)
        docs = scan_documents(self.root)
        self.assertEqual(len(docs[0]["summary"]), 500)


class TestGenerateDirectives(unittest.TestCase):
    def test_directives_from_edges(self):
        analysis = {
            "edges": [
                {"from": "a.md", "to": "b.md", "reason": "x"},
                {"from": "a.md", "to": "c.md", "reason": "y"},
            ],
            "shared_proposals": [],
        }
        d = generate_directives(analysis)
        self.assertEqual(d["a.md"], ["@include b.md", "@include c.md"])

    def test_directives_include_shared_proposals(self):
        analysis = {
            "edges": [],
            "shared_proposals": [
                {"name": "shared/role.md", "used_by": ["a.md", "b.md"],
                 "content_summary": "", "reason": ""},
            ],
        }
        d = generate_directives(analysis)
        self.assertIn("@include shared/role.md", d["a.md"])
        self.assertIn("@include shared/role.md", d["b.md"])

    def test_dedup(self):
        analysis = {
            "edges": [{"from": "a.md", "to": "b.md", "reason": ""}],
            "shared_proposals": [
                {"name": "b.md", "used_by": ["a.md"], "content_summary": "", "reason": ""},
            ],
        }
        d = generate_directives(analysis)
        self.assertEqual(d["a.md"].count("@include b.md"), 1)


class TestDepsYml(_Fixture):
    def test_save_and_roundtrip(self):
        path = save_deps_yml(self.root, {"doc.pdf": ["shared/a.md"]}, analysis=None)
        self.assertTrue(Path(path).exists())
        loaded = load_deps_yml(self.root)
        self.assertEqual(loaded["doc.pdf"], ["shared/a.md"])

    def test_merge_preserves_existing(self):
        save_deps_yml(self.root, {"doc.pdf": ["shared/a.md"]})
        save_deps_yml(self.root, {"doc.pdf": ["shared/b.md"]})
        loaded = load_deps_yml(self.root)
        self.assertEqual(loaded["doc.pdf"], ["shared/a.md", "shared/b.md"])

    def test_embeds_reasons(self):
        analysis = {"edges": [{"from": "doc.pdf", "to": "shared/a.md", "reason": "common term"}]}
        save_deps_yml(self.root, {"doc.pdf": ["shared/a.md"]}, analysis=analysis)
        text = (self.root / "deps.yml").read_text(encoding="utf-8")
        self.assertIn("# common term", text)


class TestAnalyzeWithMock(_Fixture):
    def test_empty_directory(self):
        result = analyze_dependencies(self.root, caller=lambda p, s, m: _fake_response([]))
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["edges"], [])

    def test_mock_edges(self):
        self._write("a.md", "depends on B")
        self._write("b.md", "base file")
        captured = {}

        def caller(prompt, system, model):
            captured["prompt"] = prompt
            captured["system"] = system
            captured["model"] = model
            return _fake_response([
                {"from": "a.md", "to": "b.md", "reason": "mentions B"},
            ])

        result = analyze_dependencies(self.root, caller=caller, model="test-model")
        self.assertEqual(result["edges"][0]["to"], "b.md")
        self.assertEqual(captured["model"], "test-model")
        self.assertIn("a.md", captured["prompt"])

    def test_invalid_json_raises(self):
        self._write("a.md", "x")
        with self.assertRaises(RuntimeError):
            analyze_dependencies(self.root, caller=lambda p, s, m: "not json at all")

    def test_plain_json_without_fence(self):
        self._write("a.md", "x")
        body = json.dumps({"edges": [], "shared_proposals": []})
        result = analyze_dependencies(self.root, caller=lambda p, s, m: body)
        self.assertEqual(result["edges"], [])

    def test_missing_api_key_errors(self):
        import os
        prev = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                analyze_dependencies(self.root)
        finally:
            if prev is not None:
                os.environ["ANTHROPIC_API_KEY"] = prev


class TestApplyAnalysis(_Fixture):
    def test_inserts_include_into_markdown(self):
        self._write("a.md", "# A\nBody")
        self._write("b.md", "# B")
        analysis = {
            "edges": [{"from": "a.md", "to": "b.md", "reason": ""}],
            "shared_proposals": [],
        }
        out = apply_analysis(self.root, analysis)
        self.assertIn("a.md", out["modified_files"])
        self.assertTrue((self.root / "a.md").read_text(encoding="utf-8").startswith("@include b.md"))

    def test_skips_already_present_directive(self):
        self._write("a.md", "@include b.md\n\n# A")
        self._write("b.md", "# B")
        analysis = {"edges": [{"from": "a.md", "to": "b.md", "reason": ""}]}
        out = apply_analysis(self.root, analysis)
        self.assertEqual(out["modified_files"], [])

    def test_binary_source_goes_into_deps_yml(self):
        self._write("manual.pdf", "")  # content irrelevant for this path
        self._write("shared/terms.md", "# Terms")
        analysis = {
            "edges": [{"from": "manual.pdf", "to": "shared/terms.md", "reason": ""}],
            "shared_proposals": [],
        }
        out = apply_analysis(self.root, analysis)
        self.assertIsNotNone(out["deps_yml"])
        deps = load_deps_yml(self.root)
        self.assertEqual(deps["manual.pdf"], ["shared/terms.md"])


class TestFormatProposal(unittest.TestCase):
    def test_renders_edges_and_proposals(self):
        analysis = {
            "documents": [{"path": "a.md", "summary": ""}],
            "edges": [{"from": "a.md", "to": "b.md", "reason": "x"}],
            "shared_proposals": [
                {"name": "shared/z.md", "used_by": ["a.md"],
                 "content_summary": "s", "reason": "r"},
            ],
        }
        text = format_proposal(analysis)
        self.assertIn("a.md", text)
        self.assertIn("shared/z.md", text)
        self.assertIn("Detected dependencies", text)

    def test_is_text_editable(self):
        self.assertTrue(is_text_editable("a.md"))
        self.assertTrue(is_text_editable("b.yaml"))
        self.assertFalse(is_text_editable("c.pdf"))
        self.assertFalse(is_text_editable("d.docx"))


class TestCLIAnalyze(_Fixture):
    def _cli(self, *args: str):
        try:
            cli_run(list(args))
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    def test_missing_key_exits_2(self):
        import os
        prev = os.environ.pop("ANTHROPIC_API_KEY", None)
        self._write("a.md", "x")
        try:
            rc = self._cli("analyze", str(self.root))
            self.assertEqual(rc, 2)
        finally:
            if prev is not None:
                os.environ["ANTHROPIC_API_KEY"] = prev


class TestBundledPrompt(unittest.TestCase):
    def test_prompt_is_packaged(self):
        from importlib import resources
        text = (
            resources.files("dotmd_parser.templates.prompts")
            .joinpath("analyze-dependencies.md")
            .read_text(encoding="utf-8")
        )
        self.assertIn("{{file_list}}", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# Task 1: generate_directives respects kind
# ---------------------------------------------------------------------------

from dotmd_parser.analyze import generate_directives  # noqa: E402 (already imported above via __init__)


def test_generate_directives_respects_kind():
    analysis = {
        "edges": [
            {"from": "a.md", "to": "shared/role.md", "kind": "include", "reason": "x"},
            {"from": "a.md", "to": "guide.md", "kind": "ref", "reason": "y"},
            {"from": "b.md", "to": "z.md", "reason": "no kind -> include"},
        ],
        "shared_proposals": [
            {"name": "shared/common.md", "used_by": ["c.md"]},
        ],
    }
    d = generate_directives(analysis)
    assert d["a.md"] == ["@include shared/role.md", "@ref guide.md"]
    assert d["b.md"] == ["@include z.md"]          # missing kind -> include
    assert d["c.md"] == ["@include shared/common.md"]  # shared_proposals always include


def test_generate_directives_unknown_kind_is_include():
    analysis = {"edges": [{"from": "a.md", "to": "b.md", "kind": "weird"}], "shared_proposals": []}
    assert generate_directives(analysis)["a.md"] == ["@include b.md"]


# ---------------------------------------------------------------------------
# Task 2: _apply_directive_guards — cycle (hard) + size (opt-in)
# ---------------------------------------------------------------------------

from dotmd_parser.analyze import _apply_directive_guards  # noqa: E402


def _kinds(analysis):
    return {(e["from"], e["to"]): e["kind"] for e in analysis["edges"]}


def test_guard_normalizes_unknown_kind(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "b.md", "kind": "bogus"}], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    assert _kinds(out)[("a.md", "b.md")] == "include"


def test_guard_demotes_cycle_to_ref(tmp_path):
    # a->b and b->a both include; one must be demoted so inlining can't cycle.
    a = {"edges": [
        {"from": "a.md", "to": "b.md", "kind": "include"},
        {"from": "b.md", "to": "a.md", "kind": "include"},
    ], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    k = _kinds(out)
    # deterministic: (a.md,b.md) added first stays include; (b.md,a.md) closes cycle -> ref
    assert k[("a.md", "b.md")] == "include"
    assert k[("b.md", "a.md")] == "ref"


def test_guard_demotes_self_edge(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "a.md", "kind": "include"}], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    assert _kinds(out)[("a.md", "a.md")] == "ref"


def test_guard_size_optin(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * 500, encoding="utf-8")
    a = {"edges": [{"from": "a.md", "to": "big.md", "kind": "include"}], "shared_proposals": []}
    # without the cap: stays include
    assert _kinds(_apply_directive_guards(a, tmp_path))[("a.md", "big.md")] == "include"
    # with a small cap: demoted to ref
    out = _apply_directive_guards(a, tmp_path, max_include_bytes=100)
    assert _kinds(out)[("a.md", "big.md")] == "ref"


def test_guard_does_not_mutate_input(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "a.md", "kind": "include"}], "shared_proposals": []}
    _apply_directive_guards(a, tmp_path)
    assert a["edges"][0]["kind"] == "include"  # original untouched
