"""
dotMD parser — unit tests
python3 -m unittest test_parser -v
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
    """parse_directives() のユニットテスト"""

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
        """文中の @include 言及は無視する（行頭のみ有効）"""
        content = "See @include references/foo.md for details about @delegate"
        result = parse_directives(content)
        # 行頭でないので0件
        self.assertEqual(len(result), 0)

    def test_empty_file(self):
        result = parse_directives("")
        self.assertEqual(result, [])

    def test_indented_directive(self):
        """インデントされたディレクティブも有効"""
        content = "   @include references/google.md"
        result = parse_directives(content)
        self.assertEqual(len(result), 1)


class TestParseReadRefs(unittest.TestCase):
    """parse_read_refs() のユニットテスト"""

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
        """同じパスへの複数Read参照は重複排除"""
        content = """Read `ads/references/benchmarks.md` for CPC
Read `ads/references/benchmarks.md` for CTR"""
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/benchmarks.md"])

    def test_no_read_refs(self):
        content = "No read references here"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_non_md_file_ignored(self):
        """Read参照は.mdファイルのみ検出"""
        content = "Read `path/to/config.json` for settings"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_no_slash_ignored(self):
        """パスに/を含まない参照は誤検出防止で無視"""
        content = "Read `README.md` for info"
        result = parse_read_refs(content)
        self.assertEqual(result, [])

    def test_numbered_step_read(self):
        """番号付きステップ内のRead参照"""
        content = "2. Read `ads/references/linkedin-audit.md` for full 25-check audit"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/linkedin-audit.md"])

    def test_conditional_read(self):
        """条件付きRead参照"""
        content = "**If CRM data present:** Read `ads/modules/crm-pipeline.audit.md` for CRM checks"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/modules/crm-pipeline.audit.md"])

    def test_see_reference(self):
        """See参照も検出する"""
        content = "See `ads/references/scoring-system.md` for ESCALATE scoring"
        result = parse_read_refs(content)
        self.assertEqual(result, ["ads/references/scoring-system.md"])

    def test_list_item_reference(self):
        """リスト形式のパス参照を検出する"""
        content = "- `references/scoring-system.md` — Weighted scoring algorithm"
        result = parse_read_refs(content)
        self.assertEqual(result, ["references/scoring-system.md"])

    def test_multiple_list_items(self):
        """複数のリスト形式参照"""
        content = """- `references/benchmarks.md` — Industry benchmarks
- `references/compliance.md` — Regulatory requirements
- `modules/crm-pipeline.audit.md` — CRM checks"""
        result = parse_read_refs(content)
        self.assertEqual(len(result), 3)

    def test_mixed_read_see_list(self):
        """Read/See/リスト形式が混在するファイル"""
        content = """- `references/benchmarks.md` — Industry benchmarks
Read `ads/references/google-audit.md` for checklist
See `ads/references/scoring-system.md` for algorithm"""
        result = parse_read_refs(content)
        self.assertEqual(len(result), 3)


class TestParsePlaceholders(unittest.TestCase):
    """parse_placeholders() のユニットテスト"""

    def test_single_placeholder(self):
        result = parse_placeholders("Hello {{name}}")
        self.assertEqual(result, ["name"])

    def test_multiple_placeholders(self):
        result = parse_placeholders("{{foo}} and {{bar}} and {{baz}}")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_duplicate_placeholders(self):
        """同じ変数名が複数回出現しても重複しない"""
        result = parse_placeholders("{{name}} is {{name}}")
        self.assertEqual(result, ["name"])

    def test_no_placeholders(self):
        result = parse_placeholders("No placeholders here")
        self.assertEqual(result, [])

    def test_empty_string(self):
        result = parse_placeholders("")
        self.assertEqual(result, [])

    def test_nested_braces_ignored(self):
        """{{{triple}}} のような不正形式は内側の {{triple}} をキャプチャ"""
        result = parse_placeholders("{{{triple}}}")
        self.assertEqual(result, ["triple"])

    def test_preserves_order(self):
        """出現順を維持する"""
        result = parse_placeholders("{{z}} then {{a}} then {{m}}")
        self.assertEqual(result, ["z", "a", "m"])


class TestBuildGraph(unittest.TestCase):
    """build_graph() のインテグレーションテスト（一時ディレクトリ使用）"""

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

    # ---- 正常系 ----

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
        """ディレクトリを渡してもSKILL.mdを自動検出する"""
        self._write("SKILL.md", "# Skill")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 1)

    def test_skill_md_path_input(self):
        """SKILL.mdのパスを直接渡しても動作する"""
        skill = self._write("SKILL.md", "# Skill")
        graph = build_graph(str(skill))
        self.assertEqual(len(graph["nodes"]), 1)

    # ---- 異常系・エッジケース ----

    def test_missing_reference(self):
        """存在しないファイルへの参照は警告を出す"""
        self._write("SKILL.md", "@include references/nonexistent.md\n")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["warnings"]), 1)
        self.assertEqual(graph["warnings"][0]["type"], "missing")

    def test_circular_reference(self):
        """A -> B -> A の循環参照を検出する"""
        self._write("a.md", "@include b.md\n")
        self._write("b.md", "@include a.md\n")
        graph = build_graph(str(self.root / "a.md"))
        circulars = [w for w in graph["warnings"] if w["type"] == "circular"]
        self.assertGreater(len(circulars), 0)

    def test_depth_limit(self):
        """深さ10を超えるチェーンは警告を出す"""
        # 11段の深いチェーンを作成
        for i in range(12):
            next_ref = f"@include level{i+1}.md\n" if i < 11 else ""
            self._write(f"level{i}.md", next_ref)
        self._write("SKILL.md", "@include level0.md\n")
        graph = build_graph(str(self.root))
        depth_warnings = [w for w in graph["warnings"] if w["type"] == "depth_exceeded"]
        self.assertGreater(len(depth_warnings), 0)

    def test_empty_skill_md(self):
        """空のSKILL.mdでもクラッシュしない"""
        self._write("SKILL.md", "")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(len(graph["edges"]), 0)

    def test_no_skill_md_found(self):
        """SKILL.mdが存在しないディレクトリは警告を返す"""
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["warnings"]), 1)
        self.assertEqual(graph["warnings"][0]["type"], "missing")

    def test_duplicate_edges_not_added(self):
        """同じ参照が複数回書かれていてもエッジは重複しない"""
        self._write("references/google.md", "# Google")
        self._write("SKILL.md", "@include references/google.md\n@include references/google.md\n")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["edges"]), 1)

    # ---- プレースホルダー検出 ----

    def test_placeholders_detected_in_nodes(self):
        """ノードにプレースホルダー情報が含まれる"""
        self._write("SKILL.md", "勘定科目: {{accountItems}}\n税区分: {{taxCode}}")
        graph = build_graph(str(self.root))
        node = graph["nodes"][0]
        self.assertIn("placeholders", node)
        self.assertEqual(sorted(node["placeholders"]), ["accountItems", "taxCode"])

    def test_no_placeholders(self):
        """プレースホルダーがないファイルは空リスト"""
        self._write("SKILL.md", "# No placeholders")
        graph = build_graph(str(self.root))
        self.assertEqual(graph["nodes"][0]["placeholders"], [])

    def test_placeholders_in_included_files(self):
        """@include先のファイルのプレースホルダーも検出される"""
        self._write("shared/tax.md", "税区分: {{taxCode}}")
        self._write("SKILL.md", "勘定科目: {{accountItems}}\n@include shared/tax.md")
        graph = build_graph(str(self.root))
        # SKILL.md のプレースホルダー
        skill_node = [n for n in graph["nodes"] if n["id"].endswith("SKILL.md")][0]
        self.assertEqual(skill_node["placeholders"], ["accountItems"])
        # shared/tax.md のプレースホルダー
        tax_node = [n for n in graph["nodes"] if "tax.md" in n["id"]][0]
        self.assertEqual(tax_node["placeholders"], ["taxCode"])

    # ---- カスタムノード型マッピング ----

    def test_custom_type_map(self):
        """カスタム type_map でノード型を制御できる"""
        self._write("prompts/receipt.md", "# Receipt prompt")
        custom_map = [("prompt", "prompt")]
        graph = build_graph(str(self.root / "prompts/receipt.md"), type_map=custom_map)
        self.assertEqual(graph["nodes"][0]["type"], "prompt")

    def test_default_type_map_shared(self):
        """デフォルトマッピングで shared/ 配下は shared 型"""
        self._write("shared/common.md", "# Common")
        self._write("SKILL.md", "@include shared/common.md")
        graph = build_graph(str(self.root))
        shared_node = [n for n in graph["nodes"] if "common.md" in n["id"]][0]
        self.assertEqual(shared_node["type"], "shared")

    def test_type_map_none_uses_default(self):
        """type_map=None はデフォルトマッピングを使用"""
        self._write("agents/audit.md", "# Audit")
        graph = build_graph(str(self.root / "agents/audit.md"), type_map=None)
        self.assertEqual(graph["nodes"][0]["type"], "agent")

    # ---- Read 参照の検出 ----

    def test_read_ref_detected(self):
        """Read参照がread-refエッジとして検出される"""
        self._write("references/benchmarks.md", "# Benchmarks")
        self._write("SKILL.md", "# Skill\nRead `references/benchmarks.md` for targets")
        graph = build_graph(str(self.root))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["type"], "read-ref")
        self.assertFalse(graph["edges"][0]["parallel"])

    def test_read_ref_not_recursive(self):
        """Read参照先のファイル内のディレクティブは再帰的に辿らない"""
        self._write("references/deep.md", "# Deep\n@include references/deeper.md")
        self._write("references/deeper.md", "# Deeper")
        self._write("SKILL.md", "# Skill\nRead `references/deep.md` for info")
        graph = build_graph(str(self.root))
        # SKILL.md + deep.md のみ（deeper.md は辿らない）
        self.assertEqual(len(graph["nodes"]), 2)
        read_edges = [e for e in graph["edges"] if e["type"] == "read-ref"]
        self.assertEqual(len(read_edges), 1)

    def test_read_ref_missing_target(self):
        """存在しないRead参照先は警告を出す"""
        self._write("SKILL.md", "Read `references/nonexistent.md` for info")
        graph = build_graph(str(self.root))
        missing = [w for w in graph["warnings"] if w["type"] == "missing"]
        self.assertEqual(len(missing), 1)
        self.assertIn("Read reference target", missing[0]["message"])

    def test_read_ref_coexists_with_include(self):
        """@includeとRead参照が同一ファイルに共存する"""
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
        """Read参照もdependents_of()で逆依存として検出される"""
        self._write("references/benchmarks.md", "# Benchmarks")
        self._write("SKILL.md", "Read `references/benchmarks.md` for data")
        graph = build_graph(str(self.root))
        bench_id = [n["id"] for n in graph["nodes"] if "benchmarks" in n["id"]][0]
        deps = dependents_of(graph, bench_id)
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0].endswith("SKILL.md"))

    # ---- 任意ファイルをエントリポイント ----

    def test_arbitrary_md_as_entry(self):
        """SKILL.md以外の任意.mdファイルをエントリポイントにできる"""
        self._write("shared/role.md", "あなたはアシスタントです。")
        entry = self._write("prompts/receipt.md", "# Receipt\n@include ../shared/role.md")
        graph = build_graph(str(entry))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)


class TestResolve(unittest.TestCase):
    """resolve() のテスト"""

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
        """@include の内容がインライン展開される"""
        self._write("shared/role.md", "あなたはアシスタントです。")
        entry = self._write("main.md", "# Prompt\n@include shared/role.md\n以上。")
        result = resolve(str(entry))
        self.assertIn("あなたはアシスタントです。", result["content"])
        self.assertIn("# Prompt", result["content"])
        self.assertIn("以上。", result["content"])
        self.assertNotIn("@include", result["content"])

    def test_nested_include(self):
        """多段 @include が再帰的に展開される"""
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
        """@delegate 行は展開せずそのまま残る"""
        self._write("agents/audit.md", "# Audit")
        entry = self._write("main.md", "# Start\n@delegate agents/audit.md --parallel\n# End")
        result = resolve(str(entry))
        self.assertIn("@delegate agents/audit.md --parallel", result["content"])

    def test_variable_substitution(self):
        """variables で {{key}} が置換される"""
        entry = self._write("main.md", "勘定科目:\n{{accountItems}}\n税区分: {{taxCode}}")
        result = resolve(str(entry), variables={"accountItems": "- 旅費交通費\n- 消耗品費", "taxCode": "1"})
        self.assertIn("- 旅費交通費", result["content"])
        self.assertIn("税区分: 1", result["content"])
        self.assertEqual(result["placeholders"], [])

    def test_partial_variable_substitution(self):
        """一部の変数のみ指定した場合、未解決分が placeholders に残る"""
        entry = self._write("main.md", "{{foo}} and {{bar}}")
        result = resolve(str(entry), variables={"foo": "FOO"})
        self.assertIn("FOO", result["content"])
        self.assertIn("{{bar}}", result["content"])
        self.assertEqual(result["placeholders"], ["bar"])

    def test_no_variables(self):
        """variables=None ではプレースホルダーがそのまま残る"""
        entry = self._write("main.md", "Hello {{name}}")
        result = resolve(str(entry))
        self.assertIn("{{name}}", result["content"])
        self.assertEqual(result["placeholders"], ["name"])

    def test_include_with_placeholders(self):
        """@include 先のプレースホルダーも展開後に検出・置換される"""
        self._write("shared/tax.md", "税区分: {{taxCode}}")
        entry = self._write("main.md", "# Main\n@include shared/tax.md")
        # 変数なし
        result = resolve(str(entry))
        self.assertEqual(result["placeholders"], ["taxCode"])
        # 変数あり
        result = resolve(str(entry), variables={"taxCode": "1"})
        self.assertIn("税区分: 1", result["content"])
        self.assertEqual(result["placeholders"], [])

    def test_circular_reference_warning(self):
        """循環参照は警告を出して無限ループしない"""
        self._write("a.md", "A\n@include b.md")
        self._write("b.md", "B\n@include a.md")
        result = resolve(str(self.root / "a.md"))
        circulars = [w for w in result["warnings"] if w["type"] == "circular"]
        self.assertGreater(len(circulars), 0)

    def test_missing_include_warning(self):
        """存在しない @include は警告を出して空文字に置換"""
        entry = self._write("main.md", "Before\n@include nonexistent.md\nAfter")
        result = resolve(str(entry))
        self.assertIn("Before", result["content"])
        self.assertIn("After", result["content"])
        missing = [w for w in result["warnings"] if w["type"] == "missing"]
        self.assertEqual(len(missing), 1)

    def test_empty_file(self):
        """空ファイルでもクラッシュしない"""
        entry = self._write("empty.md", "")
        result = resolve(str(entry))
        self.assertEqual(result["content"], "")
        self.assertEqual(result["placeholders"], [])


class TestDependentsOf(unittest.TestCase):
    """dependents_of() のテスト"""

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
        """直接依存元を返す"""
        self._write("shared/tax.md", "税区分ルール")
        self._write("SKILL.md", "@include shared/tax.md")
        graph = build_graph(str(self.root))
        tax_id = [n["id"] for n in graph["nodes"] if "tax.md" in n["id"]][0]
        deps = dependents_of(graph, tax_id)
        self.assertEqual(len(deps), 1)
        self.assertTrue(deps[0].endswith("SKILL.md"))

    def test_transitive_dependents(self):
        """間接依存（A -> B -> C のとき、Cの依存元はB, A）"""
        self._write("c.md", "C content")
        self._write("b.md", "@include c.md")
        self._write("a.md", "@include b.md")
        graph = build_graph(str(self.root / "a.md"))
        c_id = [n["id"] for n in graph["nodes"] if n["id"].endswith("c.md")][0]
        deps = dependents_of(graph, c_id)
        self.assertEqual(len(deps), 2)

    def test_no_dependents(self):
        """依存元がないルートノードは空リスト"""
        self._write("SKILL.md", "# Root")
        graph = build_graph(str(self.root))
        skill_id = graph["nodes"][0]["id"]
        deps = dependents_of(graph, skill_id)
        self.assertEqual(deps, [])

    def test_shared_dependency(self):
        """共有ファイルが複数ファイルから参照されている場合、全依存元を返す"""
        self._write("shared/role.md", "ロール定義")
        self._write("a.md", "@include shared/role.md")
        self._write("b.md", "@include shared/role.md")
        self._write("SKILL.md", "@include a.md\n@include b.md")
        graph = build_graph(str(self.root))
        role_id = [n["id"] for n in graph["nodes"] if "role.md" in n["id"]][0]
        deps = dependents_of(graph, role_id)
        # a.md, b.md, SKILL.md の3ファイルが影響を受ける
        self.assertEqual(len(deps), 3)

    def test_nonexistent_target(self):
        """グラフに存在しないIDを指定しても空リスト"""
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
        """プレースホルダーがある場合に表示される"""
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
        """動的にノード型が集計される"""
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
