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

## トークン削減 — 実測値

dotmd-parser をエージェントループに組み込む最大のメリットは、Claude がフォルダ全体を理解するのに**全ファイルを読む必要がなくなる**ことです。以下の数値は `tests/test_token_savings.py` で実測したもの (`DOTMD_TOKEN_REPORT=1 pytest -s` で再現可能、`tiktoken` の `cl100k_base` で計測 — Claude のトークナイザーに近い近似):

| 想定ケース | ファイル数 | naive (全 .md 読込) | `dotmd-index.md` | `digest` |
|---|---:|---:|---:|---:|
| 小規模スキル (各 ~2 KB) | 4 | 1,610 トークン | **605 (0.38×)** | 174 (0.11×) |
| 中規模ドキュメント (各 ~2 KB) | 31 | 15,855 トークン | **2,837 (0.18× → 5.6× 節約)** | 1,285 (0.08×) |
| 大規模ドキュメント (各 ~2 KB) | 111 | 58,171 トークン | **9,535 (0.16× → 6.3× 節約)** | 4,606 (0.08×) |

**結論**: 30ファイル時点で既に **約 5.6× トークン削減**。フォルダが大きくなるほど節約率は上昇。100ファイル超では **同じコンテキストウィンドウで 6 倍長く対話** できるか、または **同じプロンプトで API 入力コストを 1/6** にできます。

永続化される `dotmd-index.md` は固定の frontmatter オーバーヘッドがあるため、**極小フォルダ** (数百バイト × 数個) では naive のほうが小さくなることもあります。`digest` はさらに圧縮されます (大規模で約 12×) が、ディスクに残らないため毎回再生成が必要 — 永続的なナビゲーションには `dotmd-index.md`、ワンショット要約には `digest` を使い分けてください。

**損益分岐点**は概ね **4 ファイル × 1 KB 以上**。実プロジェクトの大半はここを超えるので、ほぼ常に得です。

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

#### インジェクション検査

`resolve` は `@include` で取り込む内容をスキャンし、プロンプトインジェクション
（`System:` 等のロール詐称、"ignore previous instructions" 等の指示上書き）を
検出します。検出は stderr に出力され、既定では展開内容は変更されません。

```bash
dotmd-parser resolve ./skill/SKILL.md                      # scan 有効・warn（既定）
dotmd-parser resolve ./skill/SKILL.md --no-scan            # スキャン無効化
dotmd-parser resolve ./skill/SKILL.md --scan-rule tool-exfil   # opt-in ルール追加
dotmd-parser resolve ./skill/SKILL.md --block              # 検出した include をプレースホルダ置換
```

root（エントリ）は信頼され検査されず、`@include` 取り込みファイルのみが対象です。
コードフェンス内の一致は無視され、ファイル内の `<!-- dotmd-allow: role-spoof -->`
（または `all`）で該当ルールを抑制できます。

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
| `dotmd-parser dotmd-index <path>` | **API不要**: `<path>/dotmd-index.md` を生成 (1ファイルでフォルダ全体を把握) |
| `dotmd-parser dotmd-index <path> --aggregate` | 子フォルダの `dotmd-index.md` を `## Sub-Indexes` に集約 |
| `dotmd-parser dotmd-index <path> --push-openrag` | 生成後に OpenRAG に取り込み (`pip install dotmd-parser[openrag]`) |
| `dotmd-parser index <path>` | `.claude/dotmd-index.json` をビルド・保存 |
| `dotmd-parser index <path> --scope <subdir>` | サブディレクトリのみ増分インデックス (既存とマージ) |
| `dotmd-parser check <path>` | 健全性ゲート (CI): 循環・欠損・未解決 placeholder・矛盾 directive |
| `dotmd-parser affects <path> <file>` | `<file>` に依存しているファイル一覧 |
| `dotmd-parser deps <path> <file>` | `<file>` の直接依存先 |
| `dotmd-parser digest <path>` | LLM向けのトークン効率的な要約 |
| `dotmd-parser tree <path>` | ASCIIの依存ツリー |
| `dotmd-parser plan <path>` | 並列委譲プラン (JSON) |
| `dotmd-parser resolve <file> [--var k=v]` | `@include` を再帰的に展開 |
| `dotmd-parser analyze <path>` | AI依存検出 (`ANTHROPIC_API_KEY` 必須) |
| `dotmd-parser analyze <path> --dry-run` | **API不要**: トークン数・USDコスト見積もり |
| `dotmd-parser analyze <path> --plan` | **API不要**: Claude Code 等のホストエージェント向け手順書を出力 |
| `dotmd-parser analyze <path> --apply-from <json>` | 事前計算済みの分析 JSON を適用 |
| `dotmd-parser init [--skill dotmd-index]` | バンドル済みスキルを `.claude/skills/<id>/` にインストール |
| `dotmd-parser show <path>` | 概要 + 完全な JSON グラフ (旧来のデフォルト) |

```bash
# 典型的な Claude Code ワークフロー
dotmd-parser inventory ./my-skill/         # フォルダを初めて見る時にまずこれ
dotmd-parser dotmd-index ./my-skill/       # ./my-skill/dotmd-index.md を生成 (Claude が 1 ファイルで全体把握)
dotmd-parser index ./my-skill/             # 一度実行、ファイル変更まで再利用
dotmd-parser digest ./my-skill/            # LLM 向けのコンパクトな要約
dotmd-parser affects ./my-skill/ shared/role.md
```

### `ledger` / `risk` — 編集リスクガバナンス

追記専用 JSONL 台帳（`.claude/dotmd-ledger.jsonl`）に per-file のリスク履歴を記録し、
編集前に照会します。`risk` は逆依存（affects）件数と active なリスクタグ
（台帳 replay ∪ frontmatter `risk:`）を組み合わせます。

```bash
dotmd-parser ledger add . shared/role.md --tag fix-failed --note "retry hung"
dotmd-parser ledger clear . shared/role.md --tag fix-failed   # または --all
dotmd-parser risk . shared/role.md                            # text レポート
dotmd-parser risk . shared/role.md --json
```

タグ: `fix-failed` / `fragile` / `security-sensitive` / `deprecated`（前2つが high）。
`--fail-on high|any|never` で終了コードを制御し、PreToolUse フックで編集前に警告できます:

```bash
dotmd-parser risk . "$FILE_PATH" --fail-on high \
  || echo "[dotmd] 高リスクファイル（前回修正失敗 / security-sensitive）。編集前に確認を。"
```

### `check` — ガイダンス健全性ゲート (CI)

依存グラフの決定的な健全性チェック。循環・欠落参照（error）に加え、未解決の
`{{placeholder}}` と矛盾 directive（warning）を検出します。孤立ファイルは opt-in。

```bash
dotmd-parser check ./my-skill                       # text、error で失敗
dotmd-parser check ./my-skill --fail-on warning     # warning でも失敗
dotmd-parser check ./my-skill --format json
dotmd-parser check ./my-skill --format sarif --out dotmd.sarif
dotmd-parser check ./my-skill --check orphans       # 孤立ファイル検出(opt-in)
```

`--fail-on` で終了コードの閾値を選びます（既定 `error` / `warning` / `never`）。
`--format sarif` を GitHub の `upload-sarif` アクションと組み合わせると PR に
インライン注釈が付きます:

```yaml
- run: dotmd-parser check . --format sarif --out dotmd.sarif --fail-on never
- uses: github/codeql-action/upload-sarif@v3
  with: { sarif_file: dotmd.sarif }
- run: dotmd-parser check . --fail-on warning   # PR をゲート
```
### `plan` — 並列委譲プラン

`@delegate` グラフから実行プランを静的生成します。topological バッチ
（並列レベル）、各タスクの subtree context、競合・循環の事前検出を含み、
サブエージェントを fan-out する親エージェントが消費する想定です。

```bash
dotmd-parser plan ./my-skill            # plan(JSON) を stdout へ
dotmd-parser plan ./my-skill --ascii    # 人間可読ビュー
dotmd-parser plan ./my-skill --out plan.json
dotmd-parser plan ./my-skill --strict   # 循環/競合で exit 1 (CI)
```

各タスクは `context`（サブエージェントに渡す subtree ファイル）を持ちます。
同一バッチ内の共有依存は `conflicts[]` に警告として記録され、バッチは並列の
まま維持されます。相互 `@delegate` は `cycles[]` に記録しバッチから除外します。

### `dotmd-index.md` (フォルダ概要を 1 ファイルで)

`dotmd-parser dotmd-index <path>` を実行すると、`<path>/dotmd-index.md` が生成されます。
Claude はこの 1 ファイルを読むだけで、フォルダ全体の構成・依存関係・未解決プレースホルダーを把握できます。

含まれる内容:

- YAML frontmatter (schema, content_hash, stats, RAG 用 `chunks[]`)
- `## Summary` (ファイル数・サイズ・健全性)
- `## Folder Map` (ASCII の階層ツリー)
- `## Files` (Markdown には title/desc/deps、それ以外には種別とサイズ)
- `## Dependency Tree` (`@include`/`@delegate`/`@ref` の依存関係を ASCII で可視化)
- `## Placeholders` (未解決の `{{...}}` 一覧)
- `<!-- chunk:id -->` HTML マーカー (任意の RAG ツールが安定して切り出し可能)

### 複数フォルダの集約

各サブフォルダが自身の `dotmd-index.md` を持つモノレポ / docs ツリーでは、`--aggregate` を使って親に集約できます:

```bash
dotmd-parser dotmd-index ./project/ --aggregate
# project/dotmd-index.md が project/docs/dotmd-index.md と
# project/src/dotmd-index.md を「## Sub-Indexes」セクションで参照する形になる。
```

親側に追加されるもの:

- `## Sub-Indexes` セクション (各子のパス、ファイル数、エッジ数、健全性、生成日時)
- frontmatter `aggregates[]` (各子の `content_hash` / `generated_at` / 統計)

集約はあくまで**参照** (マージではない)。Claude は親で全体構造を把握し、必要に応じて該当する子ファイルにドリルダウンします。これにより親はトークン効率を保ったまま深いツリーまでスケールします。`generated_by: dotmd-parser` を持たない手書きの `dotmd-index.md` は黙ってスキップされます。

### OpenRAG 連携

[OpenRAG](https://github.com/langflow-ai/openrag) (Langflow + Docling + OpenSearch ベースの RAG プラットフォーム) に直接送り込めます:

```bash
pip install dotmd-parser[openrag]      # openrag-sdk を追加
export OPENRAG_URL=http://localhost:3000
export OPENRAG_API_KEY=...

dotmd-parser dotmd-index ./docs/ --push-openrag
# → ./docs/dotmd-index.md を生成 → OpenRAG に ingest
# → frontmatter の exports.openrag に document_id を記録
```

`dotmd-index.md` (フォルダの「地図」) と OpenRAG (全文検索インデックス) は相補的に機能します。
OpenRAG の MCP サーバーを Claude Code に登録すれば、同じコンテンツが検索ツールとしても利用可能です。

### キャッシュ親和ソート（`--order cache`）

`dotmd-index --order cache` は `## Files` セクションを変更頻度の低い順
（git 履歴から推定）に並べ、再生成しても `dotmd-index.md` のプレフィックスが
安定するようにします（読み手 LLM の KV キャッシュ再利用に有利）。既定の
`--order alpha` は従来どおりです。

```bash
dotmd-parser dotmd-index ./skill --order cache
dotmd-parser dotmd-index ./skill --order cache --stdout
```

効果は `stability`（2 世代を比較）で計測できます:

```bash
dotmd-parser stability old-index.md new-index.md          # prefix stable: 42/50 lines (0.84)
dotmd-parser stability old-index.md new-index.md --json
```

git リポジトリ外（または未追跡ファイル）では頻度 0 として扱われ、`cache` は
アルファベット順に穏当に縮退します。

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

### 実例: ディレクティブ無しスキルの移行

実在のスキル（ここでは Claude Code プラグインからコピーした 1 つ）が、別ファイルを
本文中で参照しているだけだと、`dotmd-parser` はまだ依存グラフを認識できません:

```bash
$ dotmd-parser digest ./brainstorming
# dotmd index — 1 files
Health: OK
## Files
- [skill] SKILL.md — Brainstorming Ideas Into Designs   # 0 edges
```

API キー不要のプランを取得し、host agent（Claude Code 等、または自分）が
`analysis.json` を記入します:

```bash
dotmd-parser analyze ./brainstorming --plan > plan.md
# Claude Code が plan.md を読んで依存を推定し analysis.json を出力。例:
#   {"edges": [{"from": "SKILL.md", "to": "visual-companion.md",
#               "reason": "SKILL.md が visual-companion.md を読むよう指示している"}]}

dotmd-parser analyze ./brainstorming --apply-from analysis.json
#   Injected @include into 1 file(s): SKILL.md
```

これで同じフォルダが一級の依存グラフになります:

```bash
$ dotmd-parser digest ./brainstorming
# dotmd index — 2 files
Edges: 1 (include:1)
## Files
- [skill] SKILL.md  deps: include→visual-companion.md

$ dotmd-parser affects ./brainstorming visual-companion.md
SKILL.md                                   # 影響範囲が照会可能に

$ dotmd-parser check ./brainstorming       # exit 0 — CI ゲート可能
```

移行は**スキル単位**（`SKILL.md` をエントリに持つフォルダ）で行います。`analyze` は
各ソースファイルからの相対パスで `@include` を注入するため、同一ディレクトリ内の
参照はきれいに解決します。独立スキルの寄せ集め（単一の root が無い）は、各スキルを
個別に取り込むか、トップに index `SKILL.md` を足してください。インライン展開したくない
参照は、注入された `@include` を `@ref` に直すと実運用上きれいです。

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
