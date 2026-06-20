# ガイダンス健全性 CI ゲート (guidance health check) — 設計

- Date: 2026-06-20
- Status: Approved (design)
- Scope: 既存 `check` サブコマンドを拡張し、CI で落とせる健全性ゲートにする（5 機能構想のうち⑤）
- Related: `parser.build_graph` の循環/missing 検出、`index.compact_graph`（cycles/missing/warnings/placeholders）、既存 `cli.cmd_check`

## 1. 背景と目的

現状の `check` は循環参照と欠落参照で exit 1 する text 専用コマンド。本機能はこれを拡張し、
**矛盾する指示**と**欠落した参照**も構造的に検出して CI で落とせるゲートにする。
レポートは text / JSON / SARIF を選べ、SARIF は GitHub code scanning で PR インライン注釈になる。

設計の核心: **決定的（deterministic）で stdlib のみ**。CI ゲートは再現性が命なので、
矛盾検出は LLM ではなくルールベースに限定する（意味的な自然言語矛盾は対象外）。

非目標 (YAGNI):
- LLM による意味的矛盾検出はしない（CI の非決定性を持ち込まない）。
- placeholder の「宣言」機構は導入しない（dotmd に存在しないため、現れる `{{var}}` は全て未解決扱い）。
- 完全重複 directive 行の検出はしない（`build_graph` が edge を dedup するため index から見えない & 無害）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| 矛盾検出の手法 | **構造的・ルールベースのみ**（stdlib・決定的・API キー不要） |
| レポート形式 | **text(既定) + json + sarif** |
| 終了コードポリシー | `--fail-on error\|warning\|never`（既定 `error`） |
| 追加チェック | 未解決 placeholder(warn) / 矛盾 directive(warn) / depth・read_error の error 昇格。孤立ファイルは **opt-in** |
| 既存 check との関係 | `cmd_check` を後方互換のまま拡張（新設しない） |

## 3. アーキテクチャ（最小変更・加算的）

新規 `src/dotmd_parser/checks.py`。`digest.py` / `plan.py` と同様に
**compact index（`build_index` / `load_index` の出力）を入力にする純関数群**として実装。
3 つのフォーマッタも同モジュールに置く（いずれも小さい）。`parser.py` / `index.py` は無改変。

```
checks.py
├─ CHECK_SCHEMA = "dotmd-check/v1"
├─ run_checks(index, root=None, enable_orphans=False) -> list[Finding]
│   ├─ _circular_findings(index)
│   ├─ _missing_findings(index)
│   ├─ _graph_warning_findings(index)        # depth_exceeded / read_error を error 化
│   ├─ _placeholder_findings(index)          # index.files[].placeholders
│   ├─ _conflicting_directive_findings(index)# deps を source×target で groupby
│   └─ _orphan_findings(index, root)         # disk .md vs グラフノード（enable_orphans 時のみ）
├─ summarize(findings) -> dict               # {errors, warnings} カウント
├─ exit_code(findings, fail_on) -> int
├─ format_text(findings, index) -> str
├─ format_json(findings, index) -> str
└─ format_sarif(findings, index) -> str
```

依存方向: `checks.py` → `index`(型のみ) / `inventory`(disk 走査、orphan 用) / 自前ヘルパ。

## 4. Finding モデルと検出ルール

```python
Finding = {
  "rule": str,            # 下表の rule id
  "severity": str,        # "error" | "warning"
  "path": str,            # 対象ファイルの相対パス（POSIX）。グローバルなものは ""（空）
  "message": str,
  "line": int | None,     # 判明する場合のみ。多くは None
}
```

| rule | severity | データ源 | 内容 |
|---|---|---|---|
| `circular` | error | `index["cycles"]`（メッセージ） | 循環参照。path は循環の起点ノード（取得できなければ ""） |
| `missing-reference` | error | `index["missing"]`（rel リスト） | 参照先ファイルが存在しない |
| `depth-exceeded` | error | `index["warnings"]` type=depth_exceeded | @include ネストが MAX_DEPTH 超過（昇格） |
| `read-error` | error | `index["warnings"]` type=read_error | ファイルは存在するが読込不能（昇格） |
| `unresolved-placeholder` | warning | `index.files[rel].placeholders` | 残存 `{{var}}`。file×var で 1 件ずつ。message に var 名 |
| `conflicting-directive` | warning | `index.files[rel].deps` を target で groupby | 同一 source→同一 target に include/ref/delegate のうち **2 種以上**（read-ref は除外） |
| `orphan-file` | warning | disk の .md（`inventory` 走査）− グラフノード集合 | どのノードからも参照されない .md（ルート/エントリは除外）。**enable_orphans 時のみ** |

検出ロジック詳細:
- `_circular_findings`: `index["cycles"]` の各メッセージを 1 Finding に。message にそのまま循環文字列。
  path は循環文字列から最初のパスを抽出できれば設定、無理なら ""。
- `_missing_findings`: `index["missing"]` の各 rel を path に、message に "referenced file does not exist"。
  （`index["warnings"]` の type=missing と重複しうるので missing リスト由来に一本化し重複排除。）
- `_graph_warning_findings`: `index["warnings"]` のうち type∈{depth_exceeded, read_error} を error Finding 化。
- `_placeholder_findings`: 各 `files[rel]["placeholders"]` の各 var を warning Finding に（path=rel, message=f"unresolved placeholder: {{var}}"）。
- `_conflicting_directive_findings`: 各 source ファイルの deps を `to` でグルーピングし、
  `{include, ref, delegate}` のうち distinct type が 2 以上ある target を warning に
  （message に種別一覧）。read-ref は対象外。
- `_orphan_findings`: `inventory` でルート配下の .md を列挙し、グラフのノード集合（`index["files"]` のキー）に
  存在しない .md を orphan に。ルートの SKILL.md / エントリは除外。`root` が None のときは空（disk 走査不可）。

## 5. CLI（既存 `check` を拡張・後方互換）

```
dotmd-parser check <path> [--format text|json|sarif] [--fail-on error|warning|never] [--check orphans] [--out FILE]
```

- 既定 `--format text` `--fail-on error` → **現行 `check` と同じ挙動**（cycle/missing=error で exit 1）。
- `--check orphans`: orphan-file を有効化（既定 off）。将来別カテゴリを足せるよう値リスト形式（`action="append"`）。
- `--out FILE`: レポートをファイルに書く（主に sarif/json 用。指定時は stdout に出さない）。
- exit code: `errors>0 and fail_on∈{error,warning}` → 1; `warnings>0 and fail_on=warning` → 1; `never` → 0。
- `cmd_check` は `build_index(args.path)`（現行通り）→ `run_checks` → フォーマッタ → `exit_code`。
  キャッシュは使わず毎回ビルド（CI は最新状態を見るべき、現行 `cmd_check` も build_index 直呼び）。

text 出力（後方互換・上位互換）:
```
<files> files, <edges> edges — errors:<E> warnings:<W>
  [ERROR]   circular: a.md -> b.md -> a.md
  [ERROR]   missing-reference: shared/gone.md
  [WARNING] unresolved-placeholder: agents/x.md — {{company_id}}
  [WARNING] conflicting-directive: SKILL.md — shared/role.md (include, ref)
```
（サマリ行は維持。詳細行は旧 `CYCLE`/`MISSING` から統一フォーマットに変更。exit セマンティクスは不変。）

## 6. 出力フォーマット

### JSON `dotmd-check/v1`
```json
{
  "schema": "dotmd-check/v1",
  "root": "/abs/skill",
  "stats": {"files": 12, "edges": 8, "errors": 1, "warnings": 2},
  "findings": [
    {"rule": "missing-reference", "severity": "error", "path": "shared/gone.md",
     "message": "referenced file does not exist", "line": null}
  ]
}
```

### SARIF 2.1.0（最小）
```json
{
  "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
  "version": "2.1.0",
  "runs": [{
    "tool": {"driver": {"name": "dotmd-parser", "informationUri": "https://github.com/dotmd-projects/dotmd-parser",
      "version": "<__version__>", "rules": [{"id": "missing-reference", "name": "missingReference"}]}},
    "results": [{
      "ruleId": "missing-reference", "level": "error",
      "message": {"text": "referenced file does not exist"},
      "locations": [{"physicalLocation": {"artifactLocation": {"uri": "shared/gone.md"}}}]
    }]
  }]
}
```
- `level` は SARIF 語彙（error/warning）にそのままマップ。`rules[]` は出現したルールのみ列挙。
- `uri` は index 由来の相対パス（POSIX）。`line` があれば `region.startLine` に。
- 既出の YAML/JSON ヘルパは使わず stdlib `json` で生成。

## 7. GitHub Actions 連携（README / docs に例示）

```yaml
name: dotmd-health
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.x" }
      - run: pip install dotmd-parser
      - run: dotmd-parser check . --format sarif --out dotmd.sarif --fail-on warning
        continue-on-error: true     # 注釈は出しつつ後続 upload を必ず実行
      - uses: github/codeql-action/upload-sarif@v3
        with: { sarif_file: dotmd.sarif }
      - run: dotmd-parser check . --fail-on warning   # ゲート（exit code で落とす）
```
注釈だけ欲しくゲートしたくない場合は `--fail-on never`。

## 8. エラー処理 / 後方互換

- 不正パス（存在しない）→ 現行 `cmd_check` はパスを明示検証せず `build_index` を直呼びするため、その挙動を踏襲する（存在しないパスは空グラフ＋missing 警告として現れ、findings に反映される）。新たな検証は追加しない。
- 空グラフ → findings 空、stats 0、exit 0。
- 既存 `check` の exit セマンティクス（cycle/missing で 1）は default 設定で完全維持。
- text の詳細行レイアウトのみ統一フォーマットに変更（サマリ行と exit code は不変）。専用テストは未存在のため破壊なし。

## 9. 実装ステップ

1. `checks.py` に Finding 検出群を TDD 実装（_circular → _missing → _graph_warning → _placeholder → _conflicting_directive → _orphan → run_checks → summarize → exit_code）。
2. `format_text` / `format_json` / `format_sarif` を実装。
3. `cmd_check` を拡張（argparse 引数追加、run_checks 連携、format 分岐、exit_code）。
4. `__init__.py` に `run_checks` 等を公開（`__all__` 更新）。
5. `CHANGELOG.md`（次版）+ `README.md` / `README.ja.md` に check 拡張 + GH Actions 例。

## 10. テスト方針

pytest・`tmp_path`・`capsys`、既存テストの体裁準拠。

`tests/test_checks.py`（純関数）:
- circular / missing / depth_exceeded / read_error が正しい severity の Finding になる。
- 未解決 placeholder: `{{var}}` を持つファイルが warning に（file×var）。
- conflicting-directive: 同一 target に @include + @ref → warning。1 種だけなら出ない。
- orphan: enable_orphans=True のとき未参照 .md が warning、False のとき出ない。root=None で空。
- summarize / exit_code の fail-on マトリクス（error/warning/never × errors/warnings 有無）。

`tests/test_cli_check.py`（CLI）:
- 既定（text, fail-on error）で cycle/missing → exit 1（後方互換）。
- `--format json` の JSON 妥当性（schema/stats/findings）。
- `--format sarif` の SARIF 妥当性（version, runs[0].tool.driver.name, results[].ruleId/level/locations）。
- `--fail-on warning` で warning のみのスキルが exit 1、`--fail-on never` で exit 0。
- `--check orphans` で orphan 検出が有効化、未指定で無効。
- `--out FILE` がファイルに書き stdout が空。

カバレッジ 80%+ を維持。
