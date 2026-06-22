# Memory-as-Governance（編集前リスク警告）— 設計

- Date: 2026-06-21
- Status: Approved (design)
- Scope: 追記専用 JSONL イベントログ + 逆依存(affects) を組み合わせ、編集前にリスク警告を出す（5 機能構想の①）
- Related: `digest.affects`（逆依存）、`index_md.extract_frontmatter`（frontmatter 読取）、`index.default_index_path`（`.claude/` 配置規約）
- 参照概念: PROJECTMEM (Memory-as-Governance)、LedgerAgent（変更実行前のポリシーチェック）

## 1. 背景と目的

ファイルを編集する前に「このファイルは N 箇所に影響する」「前回の修正が失敗した」「脆弱」
といった履歴・リスク情報を提示できれば、危険な編集を未然に抑止できる。

本機能は **追記専用 JSONL イベントログ**でリスク履歴を保持し、`affects()`（逆依存）と
組み合わせて `risk` 照会コマンドを提供する。dotmd-parser は編集を直接傍受しないため、
PreToolUse フックや編集エージェントが編集直前に `risk` を呼ぶ運用を想定する。

非目標 (YAGNI):
- 編集そのものの傍受・自動ブロックはしない（フックが exit code を見て判断）。
- リスクの自動推定（git revert 検出等）はしない。タグは明示記録 + frontmatter 静的宣言のみ。
- deps.yml への risk 格納はしない（frontmatter を静的ソースとする）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| タグモデル | **固定語彙 enum**: `fix-failed` / `fragile` / `security-sensitive` / `deprecated`（+ 任意 `note`） |
| 状態導出 | **追記専用 JSONL を replay**。`add` で active 化、`clear` で除去（`tag:"all"` で全除去） |
| 終了コード | `--fail-on high\|any\|never`（既定 `high`）。high = fix-failed / security-sensitive、medium = fragile / deprecated |
| 静的ソース | frontmatter `risk:`（リスト）を merge。deps.yml は対象外 |
| 編集傍受 | しない。PreToolUse フックが `risk` を呼ぶ運用例を docs に提示 |

## 3. アーキテクチャ（最小変更・加算的）

新規 `src/dotmd_parser/ledger.py`。`digest.affects` と `index_md.extract_frontmatter` を再利用。
`parser.py` / `index.py` は無改変。完全新規機能（既存コマンドは不変）。

```
ledger.py
├─ RISK_TAGS = ("fix-failed", "fragile", "security-sensitive", "deprecated")
├─ HIGH_TAGS = frozenset({"fix-failed", "security-sensitive"})   # 残りは medium
├─ LEDGER_DIR = ".claude"; LEDGER_FILE = "dotmd-ledger.jsonl"
├─ default_ledger_path(root) -> Path                  # <root>/.claude/dotmd-ledger.jsonl
├─ append_event(root, file, action, tag, note=None, ts=None) -> Path
├─ read_events(root) -> list[dict]                    # 破損行はスキップ
├─ active_tags(root, file) -> set[str]                # JSONL replay
├─ static_tags(root, file) -> set[str]                # frontmatter `risk:`
├─ all_active_tags(root, file) -> set[str]            # active_tags ∪ static_tags
├─ risk_level(tags) -> str                            # "high" | "medium" | "none"
└─ risk_report(index, root, file) -> dict             # affects + tags + level + events
```

依存方向: `ledger.py` → `digest`(affects) / `index_md`(extract_frontmatter) / 自前 JSONL I/O。

## 4. JSONL イベントスキーマ（追記専用）

1 行 1 JSON イベント:
```json
{"ts":"2026-06-21T00:00:00Z","file":"shared/role.md","action":"add","tag":"fix-failed","note":"retry hung"}
{"ts":"2026-06-22T00:00:00Z","file":"shared/role.md","action":"clear","tag":"fix-failed"}
```

- `ts`: ISO8601 UTC（`...Z`）。`append_event` の `ts` 省略時は現在時刻。
- `file`: root 相対 POSIX 文字列。
- `action`: `"add"`（tag を active 化）/ `"clear"`（tag を除去。`tag:"all"` で当該ファイルの全タグ除去）。
- `tag`: 固定 enum のいずれか（`clear` のみ `"all"` も可）。
- `note`: 任意の自由文字列（集計には不使用、人間向け詳細）。

`append_event`:
- `<root>/.claude/` を作成（無ければ）し、1 行 append（`"a"` モード）。
- enum 外の `tag`（add 時）は `ValueError`。`action` は `add`/`clear` のみ。

`active_tags(root, file)`:
- `read_events` をファイル順（=時刻順、追記順）に replay。`file` 一致イベントのみ処理。
- `add tag` → 集合に追加。`clear tag` → 集合から除去。`clear all` → 集合を空に。
- 返り値は現在 active な enum タグ集合。

`read_events`:
- ledger が無ければ `[]`。各行を `json.loads`、失敗行はスキップし `stderr` に警告（堅牢）。

## 5. 静的タグ統合（frontmatter）

`static_tags(root, file)`:
- `<root>/<file>` を読み、`index_md.extract_frontmatter` で frontmatter を取得。
- `risk` キー（リスト or 単一文字列）から enum に一致するタグのみ採用。enum 外は無視。
- ファイル/ frontmatter 無し → 空集合。

`all_active_tags = active_tags ∪ static_tags`。これが「現在 active なタグ」。

## 6. risk レポート + レベル

`risk_level(tags)`:
- `tags ∩ HIGH_TAGS` が非空 → `"high"`。
- それ以外でタグが非空 → `"medium"`。
- 空 → `"none"`。

`risk_report(index, root, file) -> dict`:
```json
{
  "file": "shared/role.md",
  "affects": ["SKILL.md", "agents/a.md"],
  "affects_count": 2,
  "active_tags": ["fix-failed"],
  "level": "high",
  "events": [ {"ts": "...", "action": "add", "tag": "fix-failed", "note": "..."} ]
}
```
- `affects` = `digest.affects(index, file)`（逆依存、ソート済み）。
- `active_tags` = `sorted(all_active_tags(root, file))`。
- `level` = `risk_level(active_tags)`。
- `events` = 当該ファイルの直近イベント（最大 N 件、新しい順、既定 5）。
- 要約「N 箇所に影響＋前回修正失敗」= `affects_count` + `active_tags` から生成。

## 7. CLI（既存規約踏襲）

記録（`ledger` サブコマンド + add/clear サブアクション）:
```
dotmd-parser ledger add   <path> <file> --tag <enum> [--note TEXT]
dotmd-parser ledger clear <path> <file> (--tag <enum> | --all)
```
- `add`: enum 検証後 `append_event(action="add")`。成功時 `stderr` に確認、exit 0。enum 外は exit 2。
- `clear`: `--tag` または `--all`（排他、どちらか必須）。`append_event(action="clear", tag=<tag>|"all")`。

照会:
```
dotmd-parser risk <path> <file> [--json] [--fail-on high|any|never]
```
- `risk_report` を生成。既定 text、`--json` で JSON。
- text 例: `shared/role.md — affects 2 files; active risk: fix-failed (high) [last add: 2026-06-21]`。
  タグ無しは `shared/role.md — affects 2 files; no active risk`。
- exit code: `--fail-on high`（既定）→ `level=="high"` で 1。`any` → タグが 1 つでも active で 1。
  `never` → 常に 0。
- index は `_load_or_build_index(path)` で取得（affects 用）。

`__init__` に `append_event`, `active_tags`, `all_active_tags`, `static_tags`, `risk_report`,
`risk_level`, `RISK_TAGS`, `HIGH_TAGS`, `default_ledger_path` を公開。

## 8. 編集直前フック連携（docs に例示）

dotmd-parser は編集を直接傍受しない。**PreToolUse フック**が `risk` を呼ぶ例を README / docs に示す:
```bash
# Edit/Write 前に対象ファイルのリスクを確認。high なら警告（exit 1）。
dotmd-parser risk . "$FILE_PATH" --fail-on high \
  || echo "[dotmd] 高リスクファイル（前回修正失敗 / security-sensitive）。編集前に確認を。"
```
LedgerAgent 的な「変更実行前ポリシーチェック」をフック層で実現する。ブロックしたい場合は
フックが非ゼロ終了をブロックに変換する（フック側の責務）。

## 9. エラー処理 / 後方互換

- 完全新規機能。既存コマンド・出力は不変。
- ledger ファイル無し → `active_tags` 空 / `level` none / `risk --fail-on` は exit 0。
- enum 外 tag（add）→ `ValueError` → CLI で stderr エラー + exit 2。
- JSONL 破損行 → スキップ + stderr 警告（処理は継続、堅牢）。
- 不正パス → 既存コマンド同様の挙動（`risk` は index ビルド失敗時 stderr + 非 0）。

## 10. 実装ステップ

1. `ledger.py`: `default_ledger_path` / `append_event`（enum 検証）/ `read_events`（破損行スキップ）/ `active_tags`（replay）を TDD 実装。
2. `static_tags`（frontmatter）/ `all_active_tags` / `risk_level` / `risk_report`（affects 統合）。
3. CLI: `ledger add` / `ledger clear`（nested subparser）+ `risk`（text/json/fail-on）。`known_cmds` 追記。
4. `__init__.py` に ledger API を公開。
5. `CHANGELOG.md` + `README.md` / `README.ja.md`（フック例含む）。

## 11. テスト方針

pytest・`tmp_path`・`capsys`、既存テスト準拠。

`tests/test_ledger.py`（純ロジック）:
- `append_event` → `read_events` ラウンドトリップ、`.claude/` 自動作成。
- replay: `add fix-failed` 後 `active_tags` に含む、`clear fix-failed` で消える、`clear all` で全消去。
- enum 外 tag の `add` は `ValueError`。
- JSONL 破損行はスキップされ正常行は読める。
- `static_tags`: frontmatter `risk: [fragile]` を拾う、enum 外は無視、frontmatter 無しは空。
- `all_active_tags` = ledger ∪ frontmatter。
- `risk_level`: high タグ→high、medium のみ→medium、空→none。
- `risk_report`: affects 統合（`digest.affects`）、active_tags、level、events 件数。

`tests/test_cli_ledger.py`（CLI）:
- `ledger add` がイベントを書く、`ledger clear --tag` / `--all` が反映。enum 外で exit 2。
- `risk` text 出力に affects 数 + active tag、`--json` で JSON 妥当。
- `--fail-on high` で high タグ active のとき exit 1、medium のみで exit 0、`any` で medium でも exit 1、`never` で 0。
- ledger 無しのファイルは exit 0 / no active risk。

カバレッジ 80%+ を維持。
