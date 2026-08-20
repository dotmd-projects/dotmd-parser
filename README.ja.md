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
個別に取り込むか、トップに index `SKILL.md` を足してください。`analyze` は依存ごとに `@include` / `@ref` を**自動判定**します（Claude が
ポインタを `kind: "ref"` と判定→`@ref` で注入、共有断片は `@include`）。循環は
常に `@ref` に強制降格され、`--max-include-bytes N` で大きいターゲットも `@ref`
に降格できます。よって手動の `@include`→`@ref` 直しは不要です（必要なら 1 行
書き換えで上書きも可能）。

## `ontology` — テキストファーストなドメインオントロジー構築

`.md` の分析ドキュメント群からドメインオントロジーを構築します。`analyze`
（または手書き）でエンティティ・関係・業務ルールを散文で記述した
ドキュメント一式ができたあと、それらを構造化された機械検証可能な成果物に
したいときに使います:

```bash
export ANTHROPIC_API_KEY=...   # または ./.env に記載

dotmd-parser ontology ./corpus/
```

`./corpus/ontology/` に 3 つの成果物を書き出します:

- `ontology.yml` — 正規の中間表現（IR）（クラス、データ型/オブジェクト
  プロパティ、語彙、不変条件、衝突、未解決事項）。決定的な順序で出力され、
  PR での diff にも安全。
- `ontology.ttl` — 同じ IR を OWL-lite Turtle 形式で表現したもの
  （クラス、プロパティ、カーディナリティ/特性の注釈、SKOS 風の enum）。
- `ontology-design.md` — 同じ IR から生成する §5 形式の人間可読な設計文書
  （クラス・プロパティ・関係のテーブル）。

フォルダ内の各ドキュメントは個別に抽出され、その部分結果は
正規化名によるデデュープ・provenance 追跡（どのソースドキュメントが
どの要素に寄与したか）・first-seen-wins の衝突解決でマージされます —
衝突は黙って解決されるのではなく IR 内にフラグとして残ります。出力は
決定的です: ドキュメントが発見される順序に関わらず、同じ入力は常に
バイト同一の `ontology.yml` / `.ttl` / `.md` を生成します。

### API キーなしのワークフロー（`--plan` / `--apply-from`）

`analyze` と同じ host-agent パターンです:

```bash
dotmd-parser ontology ./corpus/ --plan > plan.md
#   1. Claude Code（または任意の host agent）が plan.md を読み、
#      各ドキュメントに対して抽出タスクをローカルで実行
#   2. 収集した結果を ontology.json に保存
#   3. 適用:
dotmd-parser ontology ./corpus/ --apply-from ontology.json
```

### フラグ

```bash
dotmd-parser ontology ./corpus/ --check              # CI ゲート: 検証エラーで非ゼロ終了
dotmd-parser ontology ./corpus/ --emit yml,ttl        # design-md を省略
dotmd-parser ontology ./corpus/ --namespace "https://example.org/corpus#" \
                                 --prefix corpus --domain "Lending"
dotmd-parser ontology ./corpus/ --eval                # opt-in の LLM ルーブリックスコア（coverage/faithfulness）
dotmd-parser ontology ./corpus/ --dry-run             # 抽出せずに API コストを見積もる
```

- `--check` は構造検証（クラス参照の宙ぶらりん、不正なカーディナリティ、
  未知の OWL 特性）を実行し、エラーがあれば非ゼロ終了します — CI に組み込んで
  オントロジーのドリフトをブロックできます。
- `--namespace` / `--prefix` / `--domain` はオントロジーの IRI、Turtle
  prefix、人間可読なドメインラベルを指定します。省略時はプレースホルダの
  namespace に warning 付きでフォールバックします。
- `--eval` は opt-in で API キーが必要です — Claude にビルド済みオントロジーの
  coverage / faithfulness をコーパス要約と照らしてスコアリングさせます。
  ゲートではなくルーブリックのシグナルです。
- 現状はテキストファーストな v1 です — SPARQL やクエリレイヤーはまだ
  ありません。IR / Turtle 出力はレビュー・バージョン管理・下流ツールでの
  利用を想定しています。

`pip install 'dotmd-parser[rdf]'` を入れると `--check` 時に `rdflib` による
Turtle 構文検証が有効になります（任意。構造検証はどちらでも実行されます）。

## `ontology-audit` — ビルド済みオントロジーへのアクティブな整合性監査

`dotmd-parser ontology ./corpus/` が `ontology.yml`（および今回から
`ontology.json` — 正規 IR の sidecar）を生成したあと、それを監査して
内部矛盾や近似重複した語彙用語を検出します:

```bash
export ANTHROPIC_API_KEY=...   # または ./.env に記載

dotmd-parser ontology-audit ./corpus/
```

`./corpus/ontology/ontology.json` とコーパスのドキュメント本体を読み込み、
`./corpus/ontology/ontology-audit.md` と `ontology-audit.json` を書き出します。
`ontology.yml` は一切読み書きしません — この監査はビルド済みオントロジーの
上に載る読み取り専用のチェックです。

報告する内容:

- **矛盾（contradictions）** — 明示された不変条件に違反する候補クレームを
  まず検出し、次に第二パスがオントロジー自体の構造（例: 見かけ上の矛盾を
  完全に説明するスコープの違い）に照らして各クレームを反証しようと試みます。
  生き残ったものだけが `CONFIRMED` として報告されるため、構造的に正常な
  ニアミスが誤検出として出てくることはありません。
- **名寄せ提案（name-match proposals）** — 語彙値の近似重複（例: 同一媒体名
  の 2 通りの表記）を、決定的な difflib による候補生成のあと LLM が
  `same`/`different` を判定して見つけます。あくまで提案どまりで、自動での
  リネームやマージは行いません。
- **構造的所見（structural findings）** — LLM 不使用の決定的なもの:
  記録済み矛盾/マージ衝突（naming 衝突は `merge-conflict`、本文抽出の矛盾は `recorded-conflict`）と語彙の重複。
- **未解決事項（open questions）** — 矛盾検出の副産物として、モデルが
  クレームを完全には解決しきれなかった場合に表出します。

### フラグ

```bash
dotmd-parser ontology-audit ./corpus/ --structural-only   # 決定的、API キー不要
dotmd-parser ontology-audit ./corpus/ --check              # CI ゲート: CONFIRMED な矛盾があれば非ゼロ終了
dotmd-parser ontology-audit ./corpus/ --plan > plan.md     # host-agent プロンプトパック、API キー不要
dotmd-parser ontology-audit ./corpus/ --apply-from audit.json
dotmd-parser ontology-audit ./corpus/ --model claude-... --out ./corpus/ontology/
```

- `--structural-only` は LLM を一切使わず構造的所見のみを報告します —
  完全に決定的で、API キーなしでも安全に実行できます。
- `--check` は `CONFIRMED` な矛盾が 1 件でもあれば非ゼロ終了します — CI に
  組み込んでオントロジーのドリフトを検知できます。
- `--plan` / `--apply-from` は `ontology` / `analyze` と同じ host-agent
  パターンです: プロンプトパックを出力し、host agent（例: Claude Code）が
  API キーなしでローカル実行、収集した JSON 結果を適用します。
- `--model` は Claude モデルを指定、`--out` は出力先ディレクトリを上書き
  します（既定: `<path>/ontology/`）。

決定性について: 構造的所見と名寄せの*候補*生成は完全に決定的です（同じ
入力からは常にバイト同一の出力）。矛盾の検出/検証と名寄せの判定は LLM を
呼び出すため、実行ごとに決定的ではありません — `--structural-only` が
決定的なサブセットです。

これはオントロジーツールの v2 で、アクティブな整合性監査であり、
クエリレイヤーではありません — SPARQL 等のクエリインターフェースは
まだ存在しません（将来の課題です）。

## `ontology-query` — ビルド済みオントロジーへの SPARQL クエリ

`dotmd-parser ontology ./corpus/` が `ontology.ttl`（および、オントロジーの
namespace/prefix 解決に使う `ontology.json`）を生成したあと、それを SPARQL
でクエリできます。`rdf` extra が必要です:

```bash
pip install 'dotmd-parser[rdf]'
```

生の SPARQL をそのまま渡す:

```bash
dotmd-parser ontology-query ./corpus/ --sparql \
  "SELECT ?c WHERE { ?c a <http://www.w3.org/2002/07/owl#Class> }"

dotmd-parser ontology-query ./corpus/ --sparql \
  "ASK { ex:Application ex:evaluatedBy ?x }"
```

結果はクエリ種別に応じて整形されます: `SELECT` は行の集合、`ASK` は真偽値、
`CONSTRUCT`/`DESCRIBE` はシリアライズされた Turtle を返します。

名前付きクエリ — オントロジー自身の語彙 prefix に対するパラメータ化済み
SPARQL テンプレートで、PREFIX 宣言やクラス IRI を手書きする必要がありません:

```bash
dotmd-parser ontology-query ./corpus/ properties Application
dotmd-parser ontology-query ./corpus/ relations Application
dotmd-parser ontology-query ./corpus/ defines feeRate
dotmd-parser ontology-query ./corpus/ list classes
dotmd-parser ontology-query ./corpus/ list properties
dotmd-parser ontology-query ./corpus/ list vocabularies
```

- `properties <Class>` — `<Class>` を domain とする datatype/object
  property を、種別と（宣言があれば）range とともに列挙します。
- `relations <Class>` — `<Class>` が domain または range のどちらかとして
  現れる object property を列挙します。
- `defines <name>` — `<name>` を導入したソースドキュメントを返します。
- `list classes|properties|vocabularies` — グラフ内の全クラス、全
  property、または全 SKOS concept scheme を列挙します。

`--format table|json` で出力形式を指定します（既定: `table`、`SELECT` の
結果はヘッダ行付きタブ区切り）。行は毎回同じ順序になるようソートされ、
再実行しても出力は決定的です。

```bash
dotmd-parser ontology-query ./corpus/ defines feeRate --format json
```

`--with-abox` — オプション: `ontology-abox.ttl`（v4c インスタンス）をクエリグラフに
読み込み、実データ・インスタンスチェーンのクエリを有効にします。既定は off
（TBox `ontology.ttl` のみをクエリ）。

注意: これは*アサートされたグラフのみ*をクエリします — OWL 推論器や
推論ステップはありません（例えば subclass/subproperty のエンテールメント
は展開されません）。推論レイヤーの追加は将来の課題です。

## `ontology-verify-enums` — 統制語彙を CSV データで実在検証

`dotmd-parser ontology ./corpus/` が `ontology.json` を生成したあと、その
統制語彙（`applicationStatus` のような `skos:ConceptScheme` enum）が実データと
一致しているかを検証できます:

```bash
dotmd-parser ontology-verify-enums ./corpus/ \
  --data ./data/applications.csv --data ./data/events.csv
```

`./corpus/ontology/ontology.json` と指定した CSV ファイルを読み込み、
`./corpus/ontology/ontology-enum-report.md` と `ontology-enum-report.json`
を書き出します。`ontology.yml` は一切読み書きせず、オントロジー自体も
変更しません — `ontology-audit` と同様、ビルド済みオントロジーの上に
載る読み取り専用のチェックです。

各語彙について、enum との重なりが最も大きい CSV 列を自動でマッチングし
（スコアは `shared / |enum|`、共有値数を enum のサイズで割ったもの）、
マッチした `file:column` を報告します — どの列がどの語彙に対応するかを
手動で指定する必要はありません。そのうえで 2 種類の不一致を報告します:

- **`enum_not_in_data`** — 語彙で宣言されているが、マッチした列には一度も
  現れない値。死んだ enum 値や typo の可能性があります。
- **`data_not_in_enum`** — マッチした列には存在するが、語彙で宣言されて
  いない値。カバレッジのギャップで、頻度の高い順に上位 N 件を列挙します。

### フラグ

```bash
dotmd-parser ontology-verify-enums ./corpus/ --data a.csv --data b.csv --check
dotmd-parser ontology-verify-enums ./corpus/ --data a.csv --threshold 0.6 --top 10
dotmd-parser ontology-verify-enums ./corpus/ --data a.csv --out ./corpus/ontology/
```

- `--data` — 検証対象の CSV データファイル（複数指定可、必須）。
- `--threshold` — 語彙が列にマッチしたとみなす最小スコア `shared/|enum|`
  （既定 `0.5`）。
- `--top` — 語彙ごとに報告する `data_not_in_enum` の最大件数、頻度順
  （既定 `20`）。
- `--check` — `enum_not_in_data` を持つ語彙が 1 件でもあれば非ゼロ終了
  します — CI に組み込んで死んだ/typo の enum 値を検知できます。
  注: `--check` が検証するのはマッチした語彙のみです。対応列がない語彙は
  `unmatched`（警告）として報告され、check の失敗にはなりません。
- `--out` — 出力先ディレクトリを上書きします（既定: `<path>/ontology/`）。

前提条件: 先に `dotmd-parser ontology ./corpus/` を実行して `ontology.json`
を生成しておく必要があります。このサブコマンドは stdlib のみで動作し
（LLM 不使用、API キー不要）、完全に決定的で、読み取り専用です — オントロジー
を変更することはありません。検証対象は CSV データのみです。BigQuery
（などのウェアハウス）テーブルへの直接照合は将来の課題です。

## `ontology-abox` — CSV データからインスタンス（ABox）を生成

`dotmd-parser ontology ./corpus/` が `ontology.json` を生成したあと、実際の
CSV 行をオントロジーのインスタンスに変換できます:

```bash
dotmd-parser ontology-abox ./corpus/ \
  --map Application=./data/applications.csv --map Purchase=./data/purchases.csv
```

`--map Class=csv` はそれぞれ CSV ファイルをオントロジーのクラスに割り当てます。
そのクラスについて `ontology-abox` は `./corpus/ontology/ontology.json` を読み込み、
CSV の列を正規化した名前の類似度でクラスの datatype property に自動マッチング
し（`--threshold`、既定 `0.6`）、CSV の各行をインスタンス
`<prefix>:<Class>_<N>` としてそのクラスの型を付けて生成し、マッチした
プロパティごとに型付きリテラルを付与します（マッチしない列は推測せずスキップ
されます）。

`./corpus/ontology/ontology-abox.ttl`（インスタンスのみ — クラス/プロパティの
定義は含まない）と `ontology-abox-report.json`（クラスごとのマッチ/未マッチ
列とインスタンス数）を書き出します。`ontology-abox.ttl` を `ontology.ttl`
（TBox）と合わせて読み込めば、`ontology-query` で実データをクエリしたり、
将来の推論器への入力として使えます。

### フラグ

```bash
dotmd-parser ontology-abox ./corpus/ --map Application=a.csv --map Purchase=b.csv
dotmd-parser ontology-abox ./corpus/ --map Application=a.csv --threshold 0.8
dotmd-parser ontology-abox ./corpus/ --map Application=a.csv --out ./corpus/ontology/
```

- `--map` — CSV ファイルをオントロジーのクラスに割り当てる `Class=csv`
  （複数指定可、必須）。クラスは `ontology.json` に存在する必要があり、
  クラス・CSV とも重複指定はできません。
- `--threshold` — CSV 列を datatype property にマッチさせる際の、正規化した
  名前類似度の最小スコア（既定 `0.6`）。
- `--out` — 出力先ディレクトリを上書きします（既定: `<path>/ontology/`）。

前提条件: 先に `dotmd-parser ontology ./corpus/` を実行して `ontology.json`
を生成しておく必要があります。このサブコマンドは stdlib のみで動作し
（LLM 不使用、API キー不要）、完全に決定的で、オントロジーを読み取るのみです
— `ontology.yml` も `ontology.json` も変更しません。このバージョンで扱うのは
**datatype property のみ**です。各リテラル値はマッチした CSV 列からそのまま
取得されます。インスタンス間のオブジェクトプロパティのリンク（外部キー列を
別クラスのインスタンスに配線するなど）は将来の課題です。

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
