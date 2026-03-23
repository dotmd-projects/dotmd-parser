"""
dotMD — .md skill dependency parser
入力: スキルのルートディレクトリ（またはSKILL.mdのパス）
出力: { nodes, edges, warnings } の辞書

追加機能:
- resolve()       : @include を展開した最終テキストを出力
- parse_placeholders() : {{variable}} を検出
- dependents_of() : 逆依存（影響範囲）の問い合わせ
- カスタムノード型マッピング対応
"""

import re
import json
from pathlib import Path

# ディレクティブのパターン
# @include path/to/file.md
# @delegate path/to/agent.md
# @delegate path/to/agent.md --parallel
DIRECTIVE_PATTERN = re.compile(
    r'^\s*@(include|delegate)\s+([\w./_-]+\.md)(\s+--parallel)?\s*$',
    re.MULTILINE
)

# Read 参照のパターン（ランタイム依存 — 展開しない）
# Read `path/to/file.md` for ...
# See `path/to/file.md` for ...
# - `path/to/file.md` — description
# パスに / を含む .md ファイルのみ対象（誤検出防止）
READ_REF_PATTERN = re.compile(
    r'(?:Read|See)\s+[`"\']([^`"\']*?/[^`"\']+\.md)[`"\']'
    r'|'
    r'^\s*-\s+[`"\']([^`"\']*?/[^`"\']+\.md)[`"\']',
    re.MULTILINE,
)

# プレースホルダーのパターン: {{variableName}}
PLACEHOLDER_PATTERN = re.compile(r'\{\{(\w+)\}\}')

MAX_DEPTH = 10

# ============================================================
# deps.yml パーサー（軽量YAML — PyYAML不要）
# ============================================================

def parse_deps_yml(content: str) -> dict[str, list[str]]:
    """deps.yml のテキストをパースする（PyYAML不要の軽量パーサー）。"""
    result: dict[str, list[str]] = {}
    current_path = None
    in_includes = False

    for line in content.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        path_match = re.match(r'^-?\s*path:\s*(.+)$', stripped)
        if path_match:
            current_path = path_match.group(1).strip().strip('"').strip("'")
            result[current_path] = []
            in_includes = False
            continue

        if stripped == "includes:" or stripped == "includes: []":
            in_includes = stripped == "includes:"
            continue

        if in_includes and current_path and stripped.startswith("- "):
            include_path = stripped[2:].strip().strip('"').strip("'")
            if "  #" in include_path:
                include_path = include_path[:include_path.index("  #")].strip().strip('"').strip("'")
            if include_path:
                result[current_path].append(include_path)
            continue

        if ":" in stripped:
            in_includes = False

    return result

# デフォルトのノード型マッピング（パスキーワード → 型名）
# 順序が重要: 先にマッチしたものが優先
DEFAULT_TYPE_MAP = [
    ("agent", "agent"),
    ("shared", "shared"),
    ("prompt", "prompt"),
    ("reference", "reference"),
    ("asset", "template"),
    ("template", "template"),
]


def parse_directives(content: str) -> list[dict]:
    """ファイル内容から @include / @delegate を抽出する"""
    results = []
    for match in DIRECTIVE_PATTERN.finditer(content):
        results.append({
            "type": match.group(1),          # "include" or "delegate"
            "target": match.group(2),         # 参照先のパス
            "parallel": bool(match.group(3)), # --parallel フラグ
        })
    return results


def parse_read_refs(content: str) -> list[str]:
    """ファイル内容から Read/See/リスト形式の .md 参照を抽出する（重複排除・出現順）"""
    seen = set()
    result = []
    for match in READ_REF_PATTERN.finditer(content):
        # 2つの選択肢グループがあるため、最初に非Noneの方を取得
        target = match.group(1) or match.group(2)
        if target and target not in seen:
            seen.add(target)
            result.append(target)
    return result


def parse_placeholders(content: str) -> list[str]:
    """ファイル内容から {{variable}} プレースホルダーを抽出する（重複排除・出現順）"""
    seen = set()
    result = []
    for match in PLACEHOLDER_PATTERN.finditer(content):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def build_graph(root_path: str, type_map: list[tuple[str, str]] | None = None) -> dict:
    """
    ルートのSKILL.md（または任意の.mdファイル / ディレクトリ）を起点に
    依存グラフを構築する。deps.yml が存在する場合は統合する。

    Args:
        root_path: ディレクトリ、SKILL.md、または任意の.mdファイルのパス
        type_map:  ノード型推定のカスタムマッピング。
                   [(パスキーワード, 型名), ...] のリスト。
                   None の場合はデフォルトマッピングを使用。

    返り値:
    {
      "nodes": [{"id": "...", "type": "...", "missing": false, "placeholders": ["var1"]}],
      "edges": [{"from": "...", "to": "...", "type": "include", "parallel": false}],
      "warnings": [{"type": "circular|missing|depth_exceeded", "path": "...", "message": "..."}]
    }
    """
    root = Path(root_path)
    mapping = type_map if type_map is not None else DEFAULT_TYPE_MAP

    # ディレクトリが渡された場合の deps.yml パスを記録
    base_dir = root if root.is_dir() else root.parent

    # ディレクトリが渡された場合はSKILL.mdを探す
    has_skill_md = True
    if root.is_dir():
        candidate = root / "SKILL.md"
        if not candidate.exists():
            # 大文字小文字を無視して探す
            candidates = list(root.glob("*.md"))
            skill_files = [f for f in candidates if f.name.upper() == "SKILL.MD"]
            if skill_files:
                candidate = skill_files[0]
            else:
                has_skill_md = False
                candidate = None

        if candidate:
            root = candidate

    nodes = {}   # id -> node dict（重複排除用）
    edges = []
    warnings = []
    visited_stack = []  # 循環参照検出用（DFS スタック）

    def _infer_node_type(path: Path) -> str:
        """パスからノード種別を推定する"""
        path_lower = str(path).lower()
        name = path.name.lower()
        if name == "skill.md":
            return "skill"
        for keyword, node_type in mapping:
            if keyword in path_lower:
                return node_type
        return "reference"

    def _resolve(current_file: Path, target: str) -> Path:
        """相対パスを絶対パスに解決する"""
        return (current_file.parent / target).resolve()

    def _walk(file_path: Path, depth: int):
        rel = str(file_path)

        # 深さ制限
        if depth > MAX_DEPTH:
            warnings.append({
                "type": "depth_exceeded",
                "path": rel,
                "message": f"最大深さ {MAX_DEPTH} を超えました"
            })
            return

        # 循環参照チェック
        if rel in visited_stack:
            cycle_path = " -> ".join(visited_stack + [rel])
            warnings.append({
                "type": "circular",
                "path": rel,
                "message": f"循環参照: {cycle_path}"
            })
            return

        # ファイル存在チェック
        if not file_path.exists():
            warnings.append({
                "type": "missing",
                "path": rel,
                "message": f"参照先ファイルが存在しません: {rel}"
            })
            # ノードは追加（欠損として記録）
            if rel not in nodes:
                nodes[rel] = {"id": rel, "type": _infer_node_type(file_path), "missing": True, "placeholders": []}
            return

        # ノード登録
        if rel not in nodes:
            nodes[rel] = {"id": rel, "type": _infer_node_type(file_path), "missing": False, "placeholders": []}

        # 既訪問ならエッジのみ追加して終了（ノードの中身は再帰しない）
        if rel in [n["id"] for n in nodes.values() if not n.get("_unvisited", True)]:
            pass

        nodes[rel]["_unvisited"] = False

        # ファイル読み込み
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            warnings.append({
                "type": "read_error",
                "path": rel,
                "message": str(e)
            })
            return

        # プレースホルダー検出
        nodes[rel]["placeholders"] = parse_placeholders(content)

        # ディレクティブ抽出 & 再帰
        visited_stack.append(rel)
        for directive in parse_directives(content):
            target_path = _resolve(file_path, directive["target"])
            target_rel = str(target_path)

            # エッジ追加（重複チェック）
            edge = {
                "from": rel,
                "to": target_rel,
                "type": directive["type"],
                "parallel": directive["parallel"],
            }
            if edge not in edges:
                edges.append(edge)

            _walk(target_path, depth + 1)

        # Read 参照の検出（ランタイム依存 — 再帰しない）
        for read_target in parse_read_refs(content):
            # ファイルの親ディレクトリからの相対パス → 絶対パス
            target_path = _resolve(file_path, read_target)
            target_rel = str(target_path)

            # 解決できない場合は祖先ディレクトリを遡って探索
            if not target_path.exists():
                search_dir = file_path.parent
                while search_dir != search_dir.parent:
                    alt_path = (search_dir / read_target).resolve()
                    if alt_path.exists():
                        target_path = alt_path
                        target_rel = str(alt_path)
                        break
                    search_dir = search_dir.parent

            # エッジ追加（重複チェック）
            edge = {
                "from": rel,
                "to": target_rel,
                "type": "read-ref",
                "parallel": False,
            }
            if edge not in edges:
                edges.append(edge)

            # ノード登録（再帰はしない）
            if target_rel not in nodes:
                is_missing = not target_path.exists()
                nodes[target_rel] = {
                    "id": target_rel,
                    "type": _infer_node_type(target_path),
                    "missing": is_missing,
                    "placeholders": [],
                }
                if is_missing:
                    warnings.append({
                        "type": "missing",
                        "path": target_rel,
                        "message": f"Read参照先が存在しません: {read_target}",
                    })

        visited_stack.pop()

    if has_skill_md and root:
        _walk(root.resolve(), 0)
    elif not has_skill_md:
        # SKILL.md がない場合は deps.yml のみで動作する可能性がある
        pass

    # deps.yml の読み込み・統合
    deps_yml_path = base_dir / "deps.yml"
    if deps_yml_path.exists():
        try:
            deps_content = deps_yml_path.read_text(encoding="utf-8")
            deps = parse_deps_yml(deps_content)

            for from_file, includes in deps.items():
                from_path = (base_dir / from_file).resolve()
                from_rel = str(from_path)

                # ノード登録
                if from_rel not in nodes:
                    node_type = _infer_node_type(from_path)
                    is_missing = not from_path.exists()
                    nodes[from_rel] = {
                        "id": from_rel,
                        "type": node_type if node_type != "reference" else "document",
                        "missing": is_missing,
                        "placeholders": [],
                    }

                for to_file in includes:
                    to_path = (base_dir / to_file).resolve()
                    to_rel = str(to_path)

                    # 参照先ノード登録
                    if to_rel not in nodes:
                        is_missing = not to_path.exists()
                        node_type = _infer_node_type(to_path)
                        nodes[to_rel] = {
                            "id": to_rel,
                            "type": node_type if node_type != "reference" else "document",
                            "missing": is_missing,
                            "placeholders": [],
                        }
                        if is_missing:
                            warnings.append({
                                "type": "missing",
                                "path": to_rel,
                                "message": f"deps.yml の参照先が存在しません: {to_file}",
                            })

                    # エッジ登録（重複チェック）
                    edge = {
                        "from": from_rel,
                        "to": to_rel,
                        "type": "include",
                        "parallel": False,
                    }
                    if edge not in edges:
                        edges.append(edge)

        except Exception as e:
            warnings.append({
                "type": "read_error",
                "path": str(deps_yml_path),
                "message": f"deps.yml 読み込みエラー: {e}",
            })

    # SKILL.md も deps.yml もない場合
    if not nodes and not edges:
        if not has_skill_md:
            warnings.append({
                "type": "missing",
                "path": str(base_dir),
                "message": "SKILL.md も deps.yml も見つかりません",
            })

    # _unvisited フラグをクリーンアップ
    for node in nodes.values():
        node.pop("_unvisited", None)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "warnings": warnings,
    }


# ============================================================
# resolve() — @include 展開
# ============================================================

def resolve(file_path: str, variables: dict[str, str] | None = None) -> dict:
    """
    @include ディレクティブを再帰的に展開し、最終テキストを生成する。
    @delegate 行はそのまま残す（実行時に別エージェントが処理するため）。

    Args:
        file_path: 起点となる .md ファイルのパス
        variables: {{key}} を置換する辞書。None の場合はプレースホルダーをそのまま残す。

    Returns:
        {
          "content":      展開後の最終テキスト,
          "placeholders": 展開後に残っている未解決プレースホルダー名のリスト,
          "warnings":     処理中の警告リスト
        }
    """
    root = Path(file_path).resolve()
    warnings = []
    visited_stack = []

    def _expand(fp: Path, depth: int) -> str:
        rel = str(fp)

        if depth > MAX_DEPTH:
            warnings.append({"type": "depth_exceeded", "path": rel, "message": f"最大深さ {MAX_DEPTH} を超えました"})
            return ""

        if rel in visited_stack:
            warnings.append({"type": "circular", "path": rel, "message": f"循環参照: {' -> '.join(visited_stack + [rel])}"})
            return ""

        if not fp.exists():
            warnings.append({"type": "missing", "path": rel, "message": f"参照先ファイルが存在しません: {rel}"})
            return ""

        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            warnings.append({"type": "read_error", "path": rel, "message": str(e)})
            return ""

        visited_stack.append(rel)

        # @include 行を展開後の内容で置換する
        def _replace_include(match):
            directive_type = match.group(1)
            target = match.group(2)
            if directive_type != "include":
                # @delegate はそのまま残す
                return match.group(0)
            target_path = (fp.parent / target).resolve()
            return _expand(target_path, depth + 1)

        result = DIRECTIVE_PATTERN.sub(_replace_include, content)
        visited_stack.pop()
        return result

    expanded = _expand(root, 0)

    # 変数置換
    if variables:
        for key, value in variables.items():
            expanded = expanded.replace(f"{{{{{key}}}}}", value)

    # 未解決プレースホルダーを検出
    remaining = parse_placeholders(expanded)

    return {
        "content": expanded,
        "placeholders": remaining,
        "warnings": warnings,
    }


# ============================================================
# dependents_of() — 逆依存の問い合わせ
# ============================================================

def dependents_of(graph: dict, target_id: str) -> list[str]:
    """
    指定ノードに（直接・間接に）依存しているノードを返す。
    「target_id を変更したら影響を受けるファイル」のリスト。

    Args:
        graph:     build_graph() の返り値
        target_id: 対象ノードの id（絶対パス文字列）

    Returns:
        依存元ノード id のリスト（ルートに近い順）
    """
    # 逆隣接リストを構築
    reverse_adj: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        reverse_adj.setdefault(edge["to"], []).append(edge["from"])

    # BFS で逆方向に辿る
    visited = set()
    queue = [target_id]
    result = []

    while queue:
        current = queue.pop(0)
        for parent in reverse_adj.get(current, []):
            if parent not in visited:
                visited.add(parent)
                result.append(parent)
                queue.append(parent)

    return result


# ============================================================
# summary() — 改善版
# ============================================================

def summary(graph: dict) -> str:
    """グラフの概要を人間が読めるテキストで返す"""
    nodes = graph["nodes"]
    edges = graph["edges"]
    warnings = graph["warnings"]

    by_type: dict[str, int] = {}
    for n in nodes:
        t = n["type"]
        by_type[t] = by_type.get(t, 0) + 1

    # ノード型の表示（動的に存在する型のみ）
    type_parts = [f"{k}:{v}" for k, v in sorted(by_type.items())]
    type_str = ", ".join(type_parts) if type_parts else "none"

    # エッジ型の集計
    edge_types: dict[str, int] = {}
    for e in edges:
        edge_types[e["type"]] = edge_types.get(e["type"], 0) + 1
    edge_parts = [f"{k}:{v}" for k, v in sorted(edge_types.items())]
    edge_str = ", ".join(edge_parts) if edge_parts else "none"

    # プレースホルダー集計
    all_placeholders: set[str] = set()
    for n in nodes:
        for p in n.get("placeholders", []):
            all_placeholders.add(p)

    lines = [
        f"ノード数: {len(nodes)}  ({type_str})",
        f"エッジ数: {len(edges)}  ({edge_str})",
        f"警告数:  {len(warnings)}",
    ]

    if all_placeholders:
        lines.append(f"プレースホルダー: {', '.join(sorted(all_placeholders))}")

    for w in warnings:
        lines.append(f"  [{w['type'].upper()}] {w['message']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        args = ["."]

    target = args[0]
    graph = build_graph(target)
    print(summary(graph))
    print("\n--- JSON出力 ---")
    print(json.dumps(graph, ensure_ascii=False, indent=2))
