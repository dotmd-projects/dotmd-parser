# 並列委譲プラン生成 (parallel delegation plan) — 設計

- Date: 2026-06-20
- Status: Approved (design)
- Scope: dotmd-parser に `plan` 機能を 1 つ追加する（5 機能構想のうち②）
- Related: `parser.build_graph` の循環検出、`digest.affects`/`tree`、`analyze --plan` の host-agent パターン

## 1. 背景と目的

dotmd-parser は `@delegate path/to/agent.md [--parallel]` ディレクティブを解析できるが、
現状はエッジを記録するだけで「どの順序で・どれを並列に実行できるか」の実行計画は出さない。

本機能は依存グラフから **並列実行プラン (DAG + 並列バッチ)** を静的生成し、
親エージェント（Recursive Agent Harness 的な orchestrator）がそのまま消費できる
`plan(JSON)` を出力する。競合（同一バッチ内の共有依存）と循環を事前検出する。

非目標 (YAGNI):
- 実行そのもの（プラン実行ランタイム）は作らない。出力は計画のみ。
- ファイルの書込み競合の実測はしない（静的グラフのため構造的にしか判定しない）。
- LLM 呼び出しは一切しない（純粋にグラフから算出）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| タスクの単位 | `@delegate` のターゲット（agent ファイル）。ネストした子 delegate も含む。 |
| 実行順序 | タスク A の subtree 到達集合に タスク B が含まれれば「B → A（B が先）」。 |
| 並列バッチ | タスク DAG の topological レベル（antichain）= 1 バッチ。 |
| 競合の扱い | 同一バッチ内 2 タスクの subtree が重なれば **warning のみ・並列維持**。`conflicts[]` に記録。 |
| 循環 | エラー扱い。既存検出を転記し、相互到達タスク対は leveling から除外。 |
| JSON の中身 | **Rich**: 各タスクに subtree ファイル一覧（context）を同梱。親がそのままサブエージェントに渡せる。 |

## 3. アーキテクチャ（最小変更・加算的）

新規 `src/dotmd_parser/plan.py` を 1 つ追加する。`digest.py` と同様に
**compact index（`build_index` / `load_index` の出力）を入力にする純関数群**として実装し、
parser 本体・index スキーマ・既存コマンドは無改変とする。

```
plan.py
├─ _task_nodes(index)        -> set[str]              # type=="delegate" エッジの to を全収集
├─ _reachable(index, start)  -> set[str]              # deps を前方DFS（include/ref/delegate/read-ref）
├─ _task_dag(index)          -> dict[str, set[str]]   # {task: {prereq tasks}}
├─ _levels(dag)              -> list[list[str]]        # 最長路レベル付け = 並列バッチ
├─ _conflicts(index, levels) -> list[dict]            # 同一バッチ内の subtree 重なり
├─ _context_of(index, task)  -> list[dict]            # subtree の {path,type,title}
└─ build_plan(index)         -> dict                  # 上記を束ねた plan(JSON)
```

依存方向: `plan.py` → `index`（型のみ）/ 自前ヘルパ。`digest.affects` の逆方向に対し、
本モジュールは前方到達（forward reachability）を使う点が異なる。

## 4. アルゴリズム

入力は compact index（`files[rel] = {type, title, deps:[{to,type,parallel}], ...}`）。

1. **タスク収集** `_task_nodes`:
   全 `files[*].deps` を走査し `type=="delegate"` の `to` を集合化。これがタスク集合 `T`。

2. **前方到達** `_reachable(start)`:
   `start` から `deps[*].to` を辿る DFS。訪問済みで打ち切り（循環安全）。
   返り値は `start` 自身を除いた到達ノード集合。

3. **タスク DAG** `_task_dag`:
   各タスク `A∈T` について `prereqs(A) = _reachable(A) ∩ T`。
   これは「A を実行する前に終わっているべきタスク B（B→A）」を表す。

4. **レベル付け** `_levels`:
   `level(A) = 0` if `prereqs(A)==∅`、それ以外は `max(level(B) for B in prereqs(A)) + 1`。
   メモ化再帰で算出。`level` 昇順にグルーピングし、各レベルを 1 バッチとする。
   同一レベルのタスク同士は互いに prereq 関係を持たない（antichain）。

5. **競合検出** `_conflicts`:
   各バッチについて全ペア `(A,B)` を調べ、
   `shared = (_reachable(A) ∩ _reachable(B)) − T`（タスク自身は除外）が非空なら
   `{level, between:[A,B], shared: sorted(shared)}` を記録。**バッチは分割しない**。

6. **循環** `cycles`:
   - index の `cycles[]`（`build_graph` 由来の include/ref 循環メッセージ）をそのまま転記。
   - さらに `_task_dag` 上で相互到達 `A∈prereqs(B) ∧ B∈prereqs(A)` のタスク対を検出し、
     `cycles[]` に `"A <-> B (task cycle)"` を追記。該当タスクは `_levels` から除外し、
     `tasks` には残すが `level: null` を付す。

7. **context** `_context_of(A)`:
   `_reachable(A)` のうち index に存在する各ファイルを `{path, type, title}` で列挙（path 昇順）。

## 5. plan(JSON) スキーマ `dotmd-plan/v1`

```json
{
  "schema": "dotmd-plan/v1",
  "generated_at": "2026-06-20T00:00:00Z",
  "root": "/abs/skill",
  "stats": {"tasks": 3, "batches": 2, "conflicts": 1, "cycles": 0},
  "batches": [
    {"level": 0, "parallelizable": true,  "tasks": ["agents/a.md", "agents/b.md"]},
    {"level": 1, "parallelizable": false, "tasks": ["agents/c.md"]}
  ],
  "tasks": {
    "agents/a.md": {
      "title": "Receipt classifier",
      "type": "agent",
      "parallel_flag": true,
      "depends_on": [],
      "level": 0,
      "context": [
        {"path": "shared/role.md", "type": "shared", "title": "Role"},
        {"path": "shared/account-items.md", "type": "shared", "title": "Accounts"}
      ]
    }
  },
  "conflicts": [
    {"level": 0, "between": ["agents/a.md", "agents/b.md"], "shared": ["shared/role.md"]}
  ],
  "cycles": [],
  "warnings": []
}
```

フィールド定義:
- `batches[].level`: 0 始まりの実行レベル。配列は level 昇順。
- `batches[].parallelizable`: そのバッチが 2 タスク以上を含むか（構造的事実）。
- `tasks[id].parallel_flag`: その delegate に `--parallel` が付いていたか（著者の意図）。
  複数の delegate 由来でフラグが割れる場合は **いずれかが true なら true**。
- `tasks[id].depends_on`: prereq タスク（task DAG の親）。path 昇順。
- `tasks[id].context`: subtree の非タスク・タスク両方のファイル（親がサブエージェントに渡す材料）。
- `tasks[id].level`: 各タスクの実行レベル（int）。循環で leveling 除外された場合は `null`。
  batches でも表現されるが、消費側が batches を参照せずタスク単体で level を読めるよう
  全タスクに付与する（2026-06-20 実装時に決定。当初案の「通常は省略」から変更）。
- `cycles[]`: include/ref 循環メッセージ + task cycle メッセージ。
- `warnings[]`: missing delegate ターゲット等、index 由来の関連警告を転記。

設計メモ: `parallel_flag`（意図）と `parallelizable`（構造）の不一致検出は将来の警告候補だが、
今回は両方を出すに留める（YAGNI）。

## 6. CLI

既存 subcommand 規約（`cli.py` の `cmd_*` / `_load_or_build_index` / `known_cmds`）を踏襲。

```
dotmd-parser plan <path> [--json] [--ascii] [--out FILE] [--no-cache] [--strict]
```

- 既定出力: **JSON を stdout**（契約が JSON のため。`--json` は明示用エイリアスで挙動は既定と同じ）。
- `--ascii`: レベル別の ASCII ビュー（`digest.tree` スタイル）を stdout に出す。
  JSON とは排他にはせず、`--ascii` 単独なら ASCII のみ、`--ascii --json` 併用なら
  ASCII を先頭・JSON を後段に出す（人間とツールの両用）。既定（フラグ無し）は JSON のみ。
- `--out FILE`: JSON をファイルへ書く（stdout には書かない）。
- `--no-cache`: 既存同様、保存済み index を使わず再ビルド。
- `--strict`: `cycles` または `conflicts` が 1 件以上なら exit 1（CI 用）。既定は exit 0。

`__init__.py` に `build_plan` を公開。`run()` の `known_cmds` に `"plan"` を追加し、
モジュール docstring のサブコマンド一覧にも 1 行追記。

## 7. エラー処理

- 空ディレクトリ / SKILL.md 無し → 空プラン（`tasks:{}`, `batches:[]`, `stats` 全 0）。
  既存 `_maybe_warn_empty(path)` の stderr ヒントを流用。exit 0。
- `@delegate` が 1 つも無い → `tasks` 空。`warnings` に `"no @delegate directives found"` を 1 件。exit 0。
- missing な delegate ターゲット → タスクとして残し `context: []`。
  index の missing 警告を `warnings[]` に転記。
- 不正パス（存在しない path）→ 既存コマンド同様 stderr にエラー、exit 2。

## 8. 実装ステップ

1. `src/dotmd_parser/plan.py` を TDD で実装
   （`_reachable` → `_task_nodes` → `_task_dag` → `_levels` → `_conflicts` → `_context_of` → `build_plan`）。
2. `__init__.py` に `build_plan` をエクスポート（`__all__` も更新）。
3. `cli.py` に `cmd_plan` + `p_plan`（argparse）+ `known_cmds`/docstring 追記。
4. `--ascii` レンダラを `plan.py` に小関数として追加（`digest.tree` の体裁を流用）。
5. `CHANGELOG.md` に 0.8.0 エントリ、`README.md` / `README.ja.md` に `plan` 使用例。

## 9. テスト方針

pytest・`tmp_path`・`capsys` を使い、既存 `tests/test_host_agent_plan.py` / `test_parser.py` の体裁に合わせる。

ユニット `tests/test_plan.py`:
- 2 つの `@delegate --parallel` が `shared/role.md` を共有 → 1 バッチ 2 タスク + `conflicts` 1 件。
- チェーン delegate（A が B を delegate）→ 2 バッチ、`depends_on` が正しい。
- 相互 delegate（A↔B）→ `cycles[]` に task cycle、両タスク `level: null`、leveling から除外。
- delegate 無し → `tasks` 空 + `warnings` に no-directive メッセージ。
- missing ターゲット → タスク存在・`context: []`・`warnings` に missing 転記。

不変条件テスト:
- 各バッチが antichain（バッチ内に prereq 関係が存在しない）。
- 全（非循環）タスクが必ずいずれかのバッチに **1 回だけ** 出現する。
- `batches` は level 昇順、`level` に欠番が無い。

CLI `tests/test_cli_plan.py`:
- `run(["plan", path, "--json"])` を capsys で JSON 検証。
- `--ascii` 出力に各レベルが現れる。
- `--strict` が cycles/conflicts ありで exit 1、無しで exit 0。
- `--no-cache` / `--out FILE`。

カバレッジ 80%+ を維持（既存方針）。
