# dotmd-parser

[![PyPI version](https://img.shields.io/pypi/v/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![Python](https://img.shields.io/pypi/pyversions/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> [English README](README.md)

`.md` ファイルの依存グラフパーサー。`@include` / `@delegate` / `@ref` ディレクティブやレガシー `Read` 参照を解析し、ファイル間の依存関係をグラフとして構築します。

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) などの AI エージェントにおけるプロンプトエンジニアリングのために設計されています。

## なぜ dotmd-parser？

AI エージェントのプロジェクトが大きくなると、`.md` ファイル同士が `@include`、`@delegate`、`@ref` で参照し合うようになります。ツールなしでは、こうした基本的な疑問に手作業で答えるしかありません：

- *「`shared/role.md` を編集したら、どのファイルに影響がある？」*
- *「スキルツリーに循環参照が隠れていないか？」*
- *「展開後に未解決の `{{変数}}` はどれくらい残っている？」*

**dotmd-parser** は `.md` ファイルを解析して依存グラフを構築し、ディレクティブ・ランタイム参照・テンプレートプレースホルダーを自動検出します。関数一発で全体像が把握できます。

## 比較

| 機能 | 手動 / grep | dotmd-parser |
|---|---|---|
| `@include` / `@delegate` / `@ref` の参照検索 | `grep -r "@include"` — フラットなリスト | ノード型・エッジメタデータ付きの構造化グラフ |
| 循環参照の検出 | エージェントがループするまで気づかない | 完全なサイクルパス付きで自動検出 |
| 逆依存（「何が壊れる？」） | ファイルを一つずつ手動で追跡 | `dependents_of(graph, "shared/role.md")` で一発 |
| `@include` を最終テキストに展開 | コピペで手動展開 | `resolve("SKILL.md", variables={...})` で再帰展開 |
| 未解決 `{{変数}}` の検出 | `grep "{{" *.md` — ノイズが多い | ノードごと・展開後の重複排除済みリスト |
| 欠損ファイルの検出 | 実行時エラーで発覚 | パース時に正確なパス付きで警告 |

## インストール

```bash
pip install dotmd-parser
```

## 主要 API

```python
from dotmd_parser import build_graph, resolve, dependents_of, summary
```

### build_graph — 依存グラフ構築

```python
graph = build_graph("./my-skill/")
# or
graph = build_graph("./my-skill/SKILL.md")
```

返り値:

```json
{
  "nodes": [{"id": "...", "type": "skill", "missing": false, "placeholders": []}],
  "edges": [{"from": "...", "to": "...", "type": "include", "parallel": false}],
  "warnings": []
}
```

**カスタムノード型マッピング:**

デフォルトではパスのキーワード（`agent`, `shared`, `prompt`, `reference`, `asset`, `template`）からノード型を推定します。`type_map` パラメータで上書き可能です：

```python
graph = build_graph("./my-skill/", type_map=[
    ("helper", "utility"),
    ("core", "foundation"),
])
```

**deps.yml サポート:**

ルートディレクトリに `deps.yml` があれば、その依存関係を自動でグラフにマージします：

```yaml
- path: agents/planner.md
  includes:
    - shared/role.md
    - shared/tools.md
```

### resolve — @include 展開

`@include` ディレクティブを再帰的に展開し最終テキストを生成します。`@delegate` と `@ref` 行はそのまま保持されます。

```python
result = resolve("./prompts/main.md", variables={"name": "Alice"})

print(result["content"])       # 展開後のテキスト
print(result["placeholders"])  # 未解決の {{変数}} リスト
print(result["warnings"])      # 循環参照、欠損ファイルなど
```

### dependents_of — 逆依存クエリ

```python
# shared/role.md を変更したら影響を受けるファイル一覧
affected = dependents_of(graph, "/abs/path/to/shared/role.md")
```

### summary — 概要表示

```python
print(summary(graph))
# Nodes: 5  (agent:1, prompt:1, shared:2, skill:1)
# Edges: 4  (include:2, ref:1, read-ref:1)
# Warnings: 0
# Placeholders: name, role
```

## ディレクティブ仕様

| ディレクティブ | エッジ型 | `resolve()` で展開？ | 説明 |
|---|---|---|---|
| `@include path/to/file.md` | `include` | Yes | ファイルをインライン展開 |
| `@delegate path/to/agent.md` | `delegate` | No | エージェントに委譲（展開しない） |
| `@delegate path/to/agent.md --parallel` | `delegate` | No | 並列実行フラグ付き委譲 |
| `@ref path/to/file.md` | `ref` | No | ランタイム参照（展開せずグラフに記録） |
| `` Read `path/to/file.md` `` | `read-ref` | No | レガシー参照（`@ref` と同じ動作、後方互換のため維持） |

## ユーティリティ関数

低レベルのパース関数もエクスポートされています：

```python
from dotmd_parser import parse_directives, parse_read_refs, parse_placeholders, parse_deps_yml
```

| 関数 | 説明 |
|---|---|
| `parse_directives(content)` | `@include` / `@delegate` / `@ref` ディレクティブを抽出 |
| `parse_read_refs(content)` | レガシー `Read`/`See`/リスト形式の `.md` 参照を抽出（重複排除済み） |
| `parse_placeholders(content)` | `{{variable}}` プレースホルダー名を抽出（重複排除済み） |
| `parse_deps_yml(content)` | `deps.yml` テキストを `{path: [includes]}` 辞書にパース（PyYAML 不要） |

## CLI

| コマンド | 用途 |
|---|---|
| `dotmd-parser inventory <path>` | **API不要**: 拡張子別ファイル数・サイズ・Markdown比率・大きいファイル一覧 |
| `dotmd-parser index <path>` | `.claude/dotmd-index.json` をビルド・保存 |
| `dotmd-parser index <path> --scope <subdir>` | サブディレクトリのみ増分インデックス (既存とマージ) |
| `dotmd-parser check <path>` | 循環依存・欠損参照があれば非ゼロ終了 (CI向け) |
| `dotmd-parser affects <path> <file>` | `<file>` に依存しているファイル一覧 |
| `dotmd-parser deps <path> <file>` | `<file>` の直接依存先 |
| `dotmd-parser digest <path>` | LLM向けのトークン効率的な要約 |
| `dotmd-parser tree <path>` | ASCIIの依存ツリー |
| `dotmd-parser resolve <file> [--var k=v]` | `@include` を再帰的に展開 |
| `dotmd-parser analyze <path>` | AI依存検出 (`ANTHROPIC_API_KEY` 必須) |
| `dotmd-parser analyze <path> --dry-run` | **API不要**: トークン数・USDコスト見積もり |
| `dotmd-parser analyze <path> --plan` | **API不要**: Claude Code 等のホストエージェント向け手順書を出力 |
| `dotmd-parser analyze <path> --apply-from <json>` | 事前計算済みの分析 JSON を適用 |
| `dotmd-parser show <path>` | 概要 + 完全な JSON グラフ (旧来のデフォルト) |

```bash
# 典型的な Claude Code ワークフロー
dotmd-parser inventory ./my-skill/       # フォルダを初めて見る時にまずこれ
dotmd-parser index ./my-skill/           # 一度実行、ファイル変更まで再利用
dotmd-parser digest ./my-skill/          # LLM 向けのコンパクトな要約
dotmd-parser affects ./my-skill/ shared/role.md
```

### API キーなしのワークフロー

```bash
# API を叩かずにコストを見積もる
dotmd-parser analyze ./docs/ --dry-run

# 分析自体を Claude Code 等に委譲 — API キー不要
dotmd-parser analyze ./docs/ --plan > plan.md
#   1. Claude Code が plan.md を読んでローカルで実行
#   2. 結果を analysis.json に保存
#   3. 適用:
dotmd-parser analyze ./docs/ --apply-from analysis.json
```

## 開発

```bash
git clone https://github.com/dotmd-projects/dotmd-parser.git
cd dotmd-parser
pip install -e .
pip install pytest
pytest tests/ -v
```

## ライセンス

MIT
