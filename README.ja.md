# dotmd-parser

[![PyPI version](https://img.shields.io/pypi/v/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![Python](https://img.shields.io/pypi/pyversions/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> [English README](README.md)

`.md` ファイルの依存グラフパーサー。`@include` / `@delegate` ディレクティブや `Read` 参照を解析し、ファイル間の依存関係をグラフとして構築します。

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) などの AI エージェントにおけるプロンプトエンジニアリングのために設計されています。

## なぜ dotmd-parser？

AI エージェントのプロジェクトが大きくなると、`SKILL.md` ファイル同士が `@include` や `@delegate` で参照し合うようになります。ツールなしでは、こうした基本的な疑問に手作業で答えるしかありません：

- *「`shared/role.md` を編集したら、どのファイルに影響がある？」*
- *「スキルツリーに循環参照が隠れていないか？」*
- *「展開後に未解決の `{{変数}}` はどれくらい残っている？」*

**dotmd-parser** は `.md` ファイルを解析して依存グラフを構築し、ディレクティブ・ランタイム参照・テンプレートプレースホルダーを自動検出します。関数一発で全体像が把握できます。

## 比較

| 機能 | 手動 / grep | dotmd-parser |
|---|---|---|
| `@include` / `@delegate` の参照検索 | `grep -r "@include"` — フラットなリスト | ノード型・エッジメタデータ付きの構造化グラフ |
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

### resolve — @include 展開

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
# ノード数: 5  (agent:1, shared:2, skill:1, reference:1)
# エッジ数: 4  (include:3, read-ref:1)
# 警告数:  0
```

## ディレクティブ仕様

| ディレクティブ | 説明 |
|---|---|
| `@include path/to/file.md` | ファイルをインライン展開 |
| `@delegate path/to/agent.md` | エージェントに委譲（展開しない） |
| `@delegate path/to/agent.md --parallel` | 並列実行フラグ付き委譲 |
| `@ref path/to/file.md` | ランタイム参照（展開せずグラフに記録） |
| `` Read `path/to/file.md` `` | レガシー参照（`@ref` と同じ動作、後方互換のため維持） |

## CLI

```bash
# コマンドとして実行
dotmd-parser ./my-skill/

# Python モジュールとして実行
python -m dotmd_parser.parser ./my-skill/
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
