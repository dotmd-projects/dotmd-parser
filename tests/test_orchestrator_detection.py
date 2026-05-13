"""Sprint 13 Phase 36: orchestrator のマルチシグナル検出テスト.

dotmd-parser の build_graph が以下 3 パターンの orchestrator を検出できることを確認:
- root SKILL.md (大文字、既存)
- subdirectory SKILL.md (claude-ads, TradingAgents)
- .claude/skills/<name>/skill.md (Claude Code plugin convention, freee-ads-skills)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dotmd_parser.parser import build_graph


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """テスト用の最小リポジトリ構造を作る."""
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "target.md").write_text("# target")
    return tmp_path


class TestOrchestratorDetection:
    def test_detects_claude_code_plugin_skill(self, temp_repo: Path) -> None:
        """`.claude/skills/<name>/skill.md` が directory 引数で auto-detect される."""
        plugin_dir = temp_repo / ".claude" / "skills" / "ads"
        plugin_dir.mkdir(parents=True)
        skill_file = plugin_dir / "skill.md"
        skill_file.write_text(
            "# orchestrator\n\n@delegate ../../../references/target.md\n"
        )

        graph = build_graph(str(temp_repo))

        node_ids = [n["id"] for n in graph["nodes"]]
        assert any("skill.md" in nid for nid in node_ids), (
            f"plugin skill.md not detected. nodes={node_ids}"
        )
        assert any("target.md" in nid for nid in node_ids), (
            f"@delegate target not resolved. nodes={node_ids}"
        )

    def test_root_skill_md_still_works(self, temp_repo: Path) -> None:
        """既存の root SKILL.md (大文字) 検出が回帰していない."""
        (temp_repo / "SKILL.md").write_text(
            "# top\n\n@delegate references/target.md\n"
        )

        graph = build_graph(str(temp_repo))
        node_ids = [n["id"] for n in graph["nodes"]]
        assert any("SKILL.md" in nid for nid in node_ids)
        assert any("target.md" in nid for nid in node_ids)

    def test_root_skill_md_preferred_over_plugin(self, temp_repo: Path) -> None:
        """root SKILL.md が存在する場合は plugin より優先される (後方互換)."""
        (temp_repo / "SKILL.md").write_text("# root\n")
        plugin_dir = temp_repo / ".claude" / "skills" / "ads"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "skill.md").write_text("# plugin\n")

        graph = build_graph(str(temp_repo))
        node_ids = [n["id"] for n in graph["nodes"]]
        assert any(nid.endswith("/SKILL.md") or nid.endswith("SKILL.md") for nid in node_ids)

    def test_no_skill_anywhere_returns_warning(self, temp_repo: Path) -> None:
        """SKILL.md も plugin skill.md もない場合は既存 warning を返す."""
        graph = build_graph(str(temp_repo))
        warnings = graph.get("warnings", [])
        assert any(w.get("type") == "missing" for w in warnings)
