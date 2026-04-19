"""
dotmd-parser — unit tests.

Run: `python3 -m unittest test_parser -v`
"""
import unittest
import json
import tempfile
from pathlib import Path
from dotmd_parser.parser import (
    parse_directives,
    parse_read_refs,
    parse_placeholders,
    build_graph,
    resolve,
    dependents_of,
    summary,
)


class TestParseDirectives(unittest.TestCase):
    """Unit tests for parse_directives()."""

    def test_include_basic(self):
        content = "@include references/google.md"
        result = parse_directives(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "include")
        self.assertEqual(result[0]["target"], "references/google.md")
        self.assertFalse(result[0]["parallel"])

    def test_delegate_basic(self):
        content = "@delegate agents/google-audit.md"
        result = parse_directives(content)
        self.assertEqual(result[0]["type"], "delegate")
        self.assertEqual(result[0]["target"], "agents/google-audit.md")

    def test_delegate_parallel(self):
        content = "@delegate agents/meta-audit.md --parallel"
        result = parse_directives(content)
        self.assertTrue(result[0]["parallel"])

    def test_multiple_directives(self):
        content = """
# Skill

@include references/google.md
@include references/meta.md
@delegate agents/audit.md --parallel
"""
        result = parse_directives(content)
        self.assertEqual(len(result), 3)
        types = [r["type"] for r in result]
        self.assertEqual(types, ["include", "include", "delegate"])

    def test_ignores_inline_mentions(self):
        """Inline @include mentions are ignored (only line-leading directives count)."""
        content = "See @include references/foo.md for details about @delegate"
        result = parse_directives(content)
        # Not at the start of a line, so zero matches.
        self.assertEqual(len(result), 0)

    def test_empty_file(self):
        result = parse_directives("")
        self.assertEqual(result, [])

    def test_ref_basic(self):
        content = "@ref analysts/market.md"
        result = parse_directives(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "ref")
        self.assertEqual(result[0]["target"], "analysts/market.md")
        self.assertFalse(result[0]["parallel"])

    def test_mixed_all_three_types(self):
        content = """@include shared/base.md
@delegate agents/audit.md --parallel
@ref analysts/market.md"""
        result = parse_directives(content)
        self.assertEqual(len(result), 3)
        types = [r["type"] for r in result]
        self.assertEqual(types, ["include", "delegate", "ref"])

    def test_indented_directive(self):
        """Indented directives are still detected."""
        content = "   @include references/google.md"
        result = parse_directives(content)
        self.assertEqual(len(result), 1)


class TestParseReadRefs(unittest.TestCase):
    """Unit tests for parse_read_refs()."""

    def test_backtick_read(self):
        content = "Read `ads/references/benchmarks.md` for CPC benchmarks"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/benchmarks.md"])

    def test_double_quote_read(self):
        content = 'Read "ads/references/benchmarks.md" for benchmarks'
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/benchmarks.md"])

    def test_single_quote_read(self):
        content = "Read 'ads/references/benchmarks.md' for benchmarks"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/benchmarks.md"])

    def test_multiple_read_refs(self):
        content = """1. Read `ads/references/google-audit.md` for checklist
2. Read `ads/references/benchmarks.md` for targets
3. Read `ads/references/scoring-system.md` for algorithm"""
        result = parse_read_refs(content)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "ads/references/google-audit.md")

    def test_duplicate_read_refs(self):
        """Multiple Read references to the same path are deduplicated."""
        content = """Read `ads/references/benchmarks.md` for CPC
Read `ads/references/benchmarks.md` for CTR"""
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/benchmarks.md"])

    def test_no_read_refs(self):
        content = "No read references here"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_non_md_file_ignored(self):
        """Only .md files are treated as Read references."""
        content = "Read `path/to/config.json` for settings"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_no_slash_ignored(self):
        """Paths without `/` are ignored to avoid false positives."""
        content = "Read `README.md` for info"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_numbered_step_read(self):
        """Read references inside numbered steps."""
        content = "2. Read `ads/references/linkedin-audit.md` for full 25-check audit"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/linkedin-audit.md"])

    def test_conditional_read(self):
        """Read references inside conditional sentences."""
        content = "**If CRM data present:** Read `ads/modules/crm-pipeline.audit.md` for CRM checks"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/modules/crm-pipeline.audit.md"])

    def test_see_reference(self):
        """`See` references are detected too."""
        content = "See `ads/references/scoring-system.md` for ESCALATE scoring"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/scoring-system.md"])

    def test_list_item_reference(self):
        """List-item style path references are detected."""
        content = "- `references/scoring-system.md` — Weighted scoring algorithm"
        result = parse_read_refs(content)
        self.assertEqual(result, ["references/scoring-system.md"])

    def test_multiple_list_items(self):
        """Multiple list-style references."""
        content = """- `references/benchmarks.md` — Industry benchmarks
- `references/compliance.md` — Regulatory requirements
- `modules/crm-pipeline.audit.md` — CRM checks"""
        result = parse_read_refs(content)
        self.assertEqual(len(result), 3)

    def test_mixed_read_see_list(self):
        """File containing Read, See and list-style references side by side."""
        content = """- `references/benchmarks.md` — Industry benchmarks
Read `ads/references/google-audit.md` for checklist
See `ads/references/scoring-system.md` for algorithm"""
        result = parse_read_refs(content)
        self.assertEqual(len(result), 3)


class TestParsePlaceholders(unittest.TestCase):
    """Unit tests for parse_placeholders()."""

    def test_single_placeholder(self):
        result = parse_placeholders("Hello {{name}}")
        self.assertEqual(result, ["name"])

    def test_multiple_placeholders(self):
        result = parse_placeholders("{{foo}} and {{bar}} and {{baz}}")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_duplicate_placeholders(self):
        """Repeated placeholder names appear only once in the result."""
        result = parse_placeholders("{{name}} is {{name}}")
        self.assertEqual(result, ["name"])

    def test_no_placeholders(self):
        result = parse_placeholders("No placeholders here")
        self.assertEqual(result, [])

    def test_empty_string(self):
        result = parse_placeholders("")
        self.assertEqual(result, [])

    def test_nested_braces_ignored(self):
        """Malformed forms like {{{triple}}} capture the inner {{triple}}."""
        result = parse_placeholders("{{{triple}}}")
        self.assertEqual(result, ["triple"])

    def test_preserves_order(self):
        """Order of first occurrence is preserved."""
        result = parse_placeholders("{{z}} then {{a}} then {{m}}")
        self.assertEqual(result, ["z", "a", "m"])


class TestBuildGraph(unittest.TestCase):
    """Integration tests for build_graph() using a temp directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel_path: str, content: str) -> Path:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    # ---- Happy path ----

    def test_single_file_no_deps(self):
        self._write("SKILL.md", "# Simple skill\nNo dependencies.")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(len(graph["edges"]), 0)
        self.assertEqual(len(graph["warnings"]), 0)

    def test_include_chain(self):
        self._write("references/google.md", "# Google Ads reference")
        self._write("SKILL.md", "@include references/google.md\n")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["type"], "include")

    def test_delegate_parallel_flag(self):
        self._write("agents/audit.md", "# Audit agent")
        self._write("SKILL.md", "@delegate agents/audit.md --parallel\n")
        graph = build_graph(str(self.root))
        self.assertTrue(graph["edges"][0]["parallel"])

    def test_multiple_references(self):
        for name in ["google", "meta", "linkedin"]:
            self._write(f"references/{name}.md", f"# {name}")
        self._write("SKILL.md", "\n".join([
            "@include references/google.md",
            "@include references/meta.md",
            "@include references/linkedin.md",
        ]))
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(len(graph["edges"]), 3)

    def test_directory_input(self):
        """Passing a directory auto-discovers SKILL.md."""
        self._write("SKILL.md", "# Skill")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 1)

    def test_skill_md_path_input(self):
        """Passing the SKILL.md path directly works too."""
        skill = self._write("SKILL.md", "# Skill")
        graph = build_graph(str(skill))
        self.assertEqual(len(graph["nodes"]), 1)

    # ---- Error / edge cases ----

    def test_missing_reference(self):
        """References to nonexistent files produce a warning."""
        self._write("SKILL.md", "@include references/nonexistent.md\n")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["warnings"]), 1)
        self.assertEqual(graph["warnings"][0]["type"], "missing")

    def test_circular_reference(self):
        """Detect a simple A -> B -> A cycle."""
        self._write("a.md", "@include b.md\n")
        self._write("b.md", "@include a.md\n")
        graph = build_graph(str(self.root / "a.md"))
        circulars = [w for w in graph["warnings"] if w["type"] == "circular"]
        self.assertGreater(len(circulars), 0)

    def test_depth_limit(self):
        """Chains deeper than 10 levels trigger a warning."""
        # 12-level chain
        for i in range(12):
            next_ref = f"@include level{i+1}.md\n" if i < 11 else ""
            self._write(f"level{i}.md", next_ref)
        self._write("SKILL.md", "@include level0.md\n")
        graph = build_graph(str(self.root))
        depth_warnings = [w for w in graph["warnings"] if w["type"] == "depth_exceeded"]
        self.assertGreater(len(depth_warnings), 0)

    def test_empty_skill_md(self):
        """An empty SKILL.md is handled without crashing."""
        self._write("SKILL.md", "")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(len(graph["edges"]), 0)

    def test_no_skill_md_found(self):
        """A directory with no SKILL.md returns a warning."""
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["warnings"]), 1)
        self.assertEqual(graph["warnings"][0]["type"], "missing")

    def test_duplicate_edges_not_added(self):
        """Repeated references to the same target produce only one edge."""
        self._write("references/google.md", "# Google")
        self._write("SKILL.md", "@include references/google.md\n@include references/google.md\n")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["edges"]), 1)

    # ---- Placeholder detection ----

    def test_placeholders_detected_in_nodes(self):
        """Nodes carry their placeholder list."""
        self._write("SKILL.md", "Account items: {{accountItems}}\nTax code: {{taxCode}}")
        graph = build_graph(str(self.root))
        node = graph["nodes"][0]
        self.assertIn("placeholders", node)
        self.assertEqual(sorted(node["placeholders"]), ["accountItems", "taxCode"])

    def test_no_placeholders(self):
        """A file without placeholders returns an empty list."""
        self._write("SKILL.md", "# No placeholders")
        graph = build_graph(str(self.root))
        self.assertEqual(graph["nodes"][0]["placeholders"], [])

    def test_placeholders_in_included_files(self):
        """Placeholders inside @include targets are also captured."""
        self._write("shared/tax.md", "Tax code: {{taxCode}}")
        self._write("SKILL.md", "Account items: {{accountItems}}\n@include shared/tax.md")
        graph = build_graph(str(self.root))
        # Placeholders on SKILL.md
        skill_node = [n for n in graph["nodes"] if n["id"].endswith("SKILL.md")][0]
        self.assertEqual(skill_node["placeholders"], ["accountItems"])
        # Placeholders on shared/tax.md
        tax_node = [n for n in graph["nodes"] if "tax.md" in n["id"]][0]
        self.assertEqual(tax_node["placeholders"], ["taxCode"])

    # ---- Custom node-type mapping ----

    def test_custom_type_map(self):
        """Custom type_map controls the inferred node type."""
        self._write("prompts/receipt.md", "# Receipt prompt")
        custom_map = [("prompt", "prompt")]
        graph = build_graph(str(self.root / "prompts/receipt.md"), type_map=custom_map)
        self.assertEqual(graph["nodes"][0]["type"], "prompt")

    def test_default_type_map_shared(self):
        """Default mapping classifies files under shared/ as the 'shared' type."""
        self._write("shared/common.md", "# Common")
        self._write("SKILL.md", "@include shared/common.md")
        graph = build_graph(str(self.root))
        shared_node = [n for n in graph["nodes"] if "common.md" in n["id"]][0]
        self.assertEqual(shared_node["type"], "shared")

    def test_type_map_none_uses_default(self):
        """Passing type_map=None uses the default mapping."""
        self._write("agents/audit.md", "# Audit")
        graph = build_graph(str(self.root / "agents/audit.md"), type_map=None)
        self.assertEqual(graph["nodes"][0]["type"], "agent")

    # ---- Read reference detection ----

    def test_read_ref_detected(self):
        """A Read reference becomes a 'read-ref' edge."""
        self._write("references/benchmarks.md", "# Benchmarks")
        self._write("SKILL.md", "# Skill\nRead `references/benchmarks.md` for targets")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["type"], "read-ref")
        self.assertFalse(graph["edges"][0]["parallel"])

    def test_read_ref_not_recursive(self):
        """Directives inside a Read target are NOT followed recursively."""
        self._write("references/deep.md", "# Deep\n@include references/deeper.md")
        self._write("references/deeper.md", "# Deeper")
        self._write("SKILL.md", "# Skill\nRead `references/deep.md` for info")
        graph = build_graph(str(self.root))
        # Only SKILL.md + deep.md (deeper.md is not walked).
        self.assertEqual(len(graph["nodes"]), 2)
        read_edges = [e for e in graph["edges"] if e["type"] == "read-ref"]
        self.assertEqual(len(read_edges), 1)

    def test_read_ref_missing_target(self):
        """A Read reference to a missing file produces a warning."""
        self._write("SKILL.md", "Read `references/nonexistent.md` for info")
        graph = build_graph(str(self.root))
        missing = [w for w in graph["warnings"] if w["type"] == "missing"]
        self.assertEqual(len(missing), 1)
        self.assertIn("Read reference target", missing[0]["message"])

    def test_read_ref_coexists_with_include(self):
        """@include and a Read reference can coexist in one file."""
        self._write("shared/role.md", "# Role")
        self._write("references/benchmarks.md", "# Benchmarks")
        self._write("SKILL.md", "@include shared/role.md\nRead `references/benchmarks.md` for data")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 3)
        include_edges = [e for e in graph["edges"] if e["type"] == "include"]
        read_edges = [e for e in graph["edges"] if e["type"] == "read-ref"]
        self.assertEqual(len(include_edges), 1)
        self.assertEqual(len(read_edges), 1)

    def test_read_ref_dependents_of(self):
        """Read references also appear in dependents_of() reverse lookups."""
        self._write("references/benchmarks.md", "# Benchmarks")
        self._write("SKILL.md", "Read `references/benchmarks.md` for data")
        graph = build_graph(str(self.root))
        bench_id = [n["id"] for n in graph["nodes"] if "benchmarks" in n["id"]][0]
        deps = dependents_of(graph, bench_id)
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0].endswith("SKILL.md"))

    # ---- @ref directive ----

    def test_ref_basic(self):
        """@ref becomes a 'ref' edge."""
        self._write("analysts/market.md", "# Market Analyst")
        self._write("SKILL.md", "@ref analysts/market.md")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["type"], "ref")
        self.assertFalse(graph["edges"][0]["parallel"])

    def test_ref_not_recursive(self):
        """Directives inside an @ref target are NOT followed recursively."""
        self._write("analysts/deep.md", "# Deep\n@include analysts/deeper.md")
        self._write("analysts/deeper.md", "# Deeper")
        self._write("SKILL.md", "@ref analysts/deep.md")
        graph = build_graph(str(self.root))
        # Only SKILL.md + deep.md (deeper.md is not walked).
        self.assertEqual(len(graph["nodes"]), 2)
        ref_edges = [e for e in graph["edges"] if e["type"] == "ref"]
        self.assertEqual(len(ref_edges), 1)

    def test_ref_missing_target(self):
        """@ref to a missing file produces a warning."""
        self._write("SKILL.md", "@ref analysts/nonexistent.md")
        graph = build_graph(str(self.root))
        missing = [w for w in graph["warnings"] if w["type"] == "missing"]
        self.assertEqual(len(missing), 1)
        self.assertIn("@ref target", missing[0]["message"])

    def test_ref_coexists_with_include_and_delegate(self):
        """@include, @delegate and @ref can coexist in one file."""
        self._write("shared/base.md", "# Base")
        self._write("agents/researcher.md", "# Researcher")
        self._write("analysts/market.md", "# Market")
        self._write("SKILL.md",
            "@include shared/base.md\n"
            "@delegate agents/researcher.md\n"
            "@ref analysts/market.md"
        )
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 4)
        include_edges = [e for e in graph["edges"] if e["type"] == "include"]
        delegate_edges = [e for e in graph["edges"] if e["type"] == "delegate"]
        ref_edges = [e for e in graph["edges"] if e["type"] == "ref"]
        self.assertEqual(len(include_edges), 1)
        self.assertEqual(len(delegate_edges), 1)
        self.assertEqual(len(ref_edges), 1)

    def test_ref_dependents_of(self):
        """@ref also appears in dependents_of() reverse lookups."""
        self._write("analysts/market.md", "# Market")
        self._write("SKILL.md", "@ref analysts/market.md")
        graph = build_graph(str(self.root))
        market_id = [n["id"] for n in graph["nodes"] if "market" in n["id"]][0]
        deps = dependents_of(graph, market_id)
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0].endswith("SKILL.md"))

    # ---- Arbitrary file as entry point ----

    def test_arbitrary_md_as_entry(self):
        """Any .md file (not only SKILL.md) can serve as the entry point."""
        self._write("shared/role.md", "You are an assistant.")
        entry = self._write("prompts/receipt.md", "# Receipt\n@include ../shared/role.md")
        graph = build_graph(str(entry))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)


class TestResolve(unittest.TestCase):
    """Tests for resolve()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel_path: str, content: str) -> Path:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_include_expansion(self):
        """@include content is inlined into the final text."""
        self._write("shared/role.md", "You are an assistant.")
        entry = self._write("main.md", "# Prompt\n@include shared/role.md\nEnd.")
        result = resolve(str(entry))
        self.assertIn("You are an assistant.", result["content"])
        self.assertIn("# Prompt", result["content"])
        self.assertIn("End.", result["content"])
        self.assertNotIn("@include", result["content"])

    def test_nested_include(self):
        """Nested @include directives are expanded recursively."""
        self._write("c.md", "C-content")
        self._write("b.md", "B-start\n@include c.md\nB-end")
        entry = self._write("a.md", "A-start\n@include b.md\nA-end")
        result = resolve(str(entry))
        content = result["content"]
        self.assertIn("A-start", content)
        self.assertIn("B-start", content)
        self.assertIn("C-content", content)
        self.assertIn("B-end", content)
        self.assertIn("A-end", content)
        self.assertNotIn("@include", content)

    def test_delegate_preserved(self):
        """@delegate lines are preserved, not expanded."""
        self._write("agents/audit.md", "# Audit")
        entry = self._write("main.md", "# Start\n@delegate agents/audit.md --parallel\n# End")
        result = resolve(str(entry))
        self.assertIn("@delegate agents/audit.md --parallel", result["content"])

    def test_ref_preserved(self):
        """@ref lines are preserved, not expanded."""
        self._write("analysts/market.md", "# Market")
        entry = self._write("main.md", "# Start\n@ref analysts/market.md\n# End")
        result = resolve(str(entry))
        self.assertIn("@ref analysts/market.md", result["content"])
        self.assertIn("# Start", result["content"])
        self.assertIn("# End", result["content"])

    def test_variable_substitution(self):
        """{{key}} placeholders are replaced by `variables`."""
        entry = self._write("main.md", "Account items:\n{{accountItems}}\nTax code: {{taxCode}}")
        result = resolve(str(entry), variables={"accountItems": "- Travel\n- Supplies", "taxCode": "1"})
        self.assertIn("- Travel", result["content"])
        self.assertIn("Tax code: 1", result["content"])
        self.assertEqual(result["placeholders"], [])

    def test_partial_variable_substitution(self):
        """Unsupplied placeholders are reported in `placeholders`."""
        entry = self._write("main.md", "{{foo}} and {{bar}}")
        result = resolve(str(entry), variables={"foo": "FOO"})
        self.assertIn("FOO", result["content"])
        self.assertIn("{{bar}}", result["content"])
        self.assertEqual(result["placeholders"], ["bar"])

    def test_no_variables(self):
        """With variables=None, placeholders stay in place."""
        entry = self._write("main.md", "Hello {{name}}")
        result = resolve(str(entry))
        self.assertIn("{{name}}", result["content"])
        self.assertEqual(result["placeholders"], ["name"])

    def test_include_with_placeholders(self):
        """Placeholders from @include targets are detected / substituted after expansion."""
        self._write("shared/tax.md", "Tax code: {{taxCode}}")
        entry = self._write("main.md", "# Main\n@include shared/tax.md")
        # Without variables.
        result = resolve(str(entry))
        self.assertEqual(result["placeholders"], ["taxCode"])
        # With variables.
        result = resolve(str(entry), variables={"taxCode": "1"})
        self.assertIn("Tax code: 1", result["content"])
        self.assertEqual(result["placeholders"], [])

    def test_circular_reference_warning(self):
        """Circular references emit a warning and do not loop forever."""
        self._write("a.md", "A\n@include b.md")
        self._write("b.md", "B\n@include a.md")
        result = resolve(str(self.root / "a.md"))
        circulars = [w for w in result["warnings"] if w["type"] == "circular"]
        self.assertGreater(len(circulars), 0)

    def test_missing_include_warning(self):
        """Missing @include targets emit a warning and expand to empty text."""
        entry = self._write("main.md", "Before\n@include nonexistent.md\nAfter")
        result = resolve(str(entry))
        self.assertIn("Before", result["content"])
        self.assertIn("After", result["content"])
        missing = [w for w in result["warnings"] if w["type"] == "missing"]
        self.assertEqual(len(missing), 1)

    def test_empty_file(self):
        """An empty file is handled without crashing."""
        entry = self._write("empty.md", "")
        result = resolve(str(entry))
        self.assertEqual(result["content"], "")
        self.assertEqual(result["placeholders"], [])


class TestDependentsOf(unittest.TestCase):
    """Tests for dependents_of()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel_path: str, content: str) -> Path:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_direct_dependent(self):
        """Direct dependents are returned."""
        self._write("shared/tax.md", "Tax rules")
        self._write("SKILL.md", "@include shared/tax.md")
        graph = build_graph(str(self.root))
        tax_id = [n["id"] for n in graph["nodes"] if "tax.md" in n["id"]][0]
        deps = dependents_of(graph, tax_id)
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0].endswith("SKILL.md"))

    def test_transitive_dependents(self):
        """Transitive dependents: A -> B -> C, dependents of C are B and A."""
        self._write("c.md", "C content")
        self._write("b.md", "@include c.md")
        self._write("a.md", "@include b.md")
        graph = build_graph(str(self.root / "a.md"))
        c_id = [n["id"] for n in graph["nodes"] if n["id"].endswith("c.md")][0]
        deps = dependents_of(graph, c_id)
        self.assertEqual(len(deps), 2)

    def test_no_dependents(self):
        """Root nodes with no dependents return an empty list."""
        self._write("SKILL.md", "# Root")
        graph = build_graph(str(self.root))
        skill_id = graph["nodes"][0]["id"]
        deps = dependents_of(graph, skill_id)
        self.assertEqual(deps, [])

    def test_shared_dependency(self):
        """A shared file referenced by multiple files returns all dependents."""
        self._write("shared/role.md", "Role definition")
        self._write("a.md", "@include shared/role.md")
        self._write("b.md", "@include shared/role.md")
        self._write("SKILL.md", "@include a.md\n@include b.md")
        graph = build_graph(str(self.root))
        role_id = [n["id"] for n in graph["nodes"] if "role.md" in n["id"]][0]
        deps = dependents_of(graph, role_id)
        # a.md, b.md and SKILL.md are all affected.
        self.assertEqual(len(deps), 3)

    def test_nonexistent_target(self):
        """An unknown node id returns an empty list."""
        self._write("SKILL.md", "# Root")
        graph = build_graph(str(self.root))
        deps = dependents_of(graph, "/nonexistent/file.md")
        self.assertEqual(deps, [])


class TestSummary(unittest.TestCase):
    def test_summary_runs(self):
        graph = {"nodes": [{"id": "a.md", "type": "skill"}], "edges": [], "warnings": []}
        result = summary(graph)
        self.assertIn("Nodes", result)

    def test_summary_with_placeholders(self):
        """Placeholders are listed when present."""
        graph = {
            "nodes": [{"id": "a.md", "type": "skill", "placeholders": ["foo", "bar"]}],
            "edges": [],
            "warnings": [],
        }
        result = summary(graph)
        self.assertIn("Placeholders", result)
        self.assertIn("bar", result)
        self.assertIn("foo", result)

    def test_summary_dynamic_types(self):
        """Node types are counted dynamically."""
        graph = {
            "nodes": [
                {"id": "a.md", "type": "prompt", "placeholders": []},
                {"id": "b.md", "type": "shared", "placeholders": []},
            ],
            "edges": [],
            "warnings": [],
        }
        result = summary(graph)
        self.assertIn("prompt:1", result)
        self.assertIn("shared:1", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
