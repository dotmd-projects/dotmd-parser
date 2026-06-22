# index のキャッシュ親和ソート (cache-affine order) — 設計

- Date: 2026-06-21
- Status: Approved (design)
- Scope: `dotmd-index.md` の Files セクション順を「低変更頻度を前方」に並べ替え、LLM プレフィックス安定化を狙う（5 機能構想の③）
- Related: `index_md.generate_index_md` / `_files_section`（現状 path 順）、`index_md._compute_content_hash`（body 順非依存）
- 参照概念: TokenPilot（プレフィックス安定化による KV キャッシュ無効化抑制）

## 1. 背景と目的

LLM/RAG が `dotmd-index.md` を読むとき、出力の**先頭（プレフィックス）が安定**していれば
KV キャッシュの再利用が効きやすい。現状 Files セクションはパス(アルファベット)順で、
頻繁に変わるファイルが前方に来ると後続全体が無効化されやすい。

本機能は **変更頻度の低いファイルを前方**に並べる opt-in ソート (`--order cache`) を追加し、
プレフィックス安定性を計測する `stability` コマンドを提供する。

非目標 (YAGNI):
- 既定の並び順は変えない（`--order alpha` 既定で完全後方互換）。
- 依存ツリーや frontmatter の順序は変えない（Files セクションのみ対象）。
- ① の event log には依存しない（変更頻度は git 履歴、非リポは fallback）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| 変更頻度ソース | **git 履歴**（ファイル別 commit 数）。非リポ/未追跡は freq 0（最安定→前方）、alpha tiebreak |
| 有効化 | **opt-in `--order alpha\|cache`**（既定 `alpha` = 現状維持） |
| 対象範囲 | `## Files` セクションの並びのみ（content_hash 不変） |
| 安定性計測 | 新 `stability <old> <new>` コマンド + `prefix_stability` 純関数 |
| ① 依存 | なし（git 履歴のみ） |

## 3. アーキテクチャ（最小変更・加算的）

新規 `src/dotmd_parser/cache_order.py`。`index_md` にソート層を 1 つ足す。
`parser.py` / `index.py` は無改変。

```
cache_order.py
├─ git_change_counts(root) -> dict[str, int]      # git log 集計、非リポは {}
├─ order_key(rel, counts) -> tuple[int, str]      # (counts.get(rel, 0), rel)
└─ prefix_stability(old_text, new_text) -> dict   # 先頭一致行 / 比率
```

依存方向: `cache_order.py` → stdlib（`subprocess`, `shutil`）のみ。`index_md` → `cache_order`。

## 4. 変更頻度（git 履歴 + fallback）

`git_change_counts(root) -> dict[str, int]`:
- `shutil.which("git")` が無ければ `{}`。
- `subprocess.run(["git", "-C", str(root), "log", "--format=", "--name-only", "--relative", "--", "."], capture_output=True, text=True)` を実行。
  - `--relative` で出力パスを `root` 相対にする。`--format=` でコミットヘッダを抑制し name-only 行だけにする。
  - `returncode != 0`（非 git リポ等）→ `{}`。例外（`OSError` 等）→ `{}`。
- stdout の非空行を POSIX パスとして数え、`{rel: count}` を返す。
- 同一ファイルが複数コミットに出れば count が積み上がる = 変更頻度。

freq 0（git に出ない＝未追跡/新規/非リポ）は「最も安定」とみなし前方に置く。

## 5. ソートキー

`order_key(rel, counts) -> tuple[int, str]`:
- `(counts.get(rel, 0), rel)` を返す。
- 低 count（低頻度）ほど前方。同 count はパス昇順（決定的・安定）。

## 6. index_md 統合（Files セクションのみ）

- `generate_index_md(root, *, order="alpha", ...)` と `write_index_md(root, *, order="alpha", ...)` に
  `order` 引数を追加（既定 `"alpha"`、`{"alpha","cache"}`）。
- `order=="cache"` のとき `counts = git_change_counts(base)` を 1 回計算し `_files_section` に渡す。
- `_files_section(root, inv, idx, max_files, order="alpha", counts=None)`:
  - `order=="cache"` のとき、md_entries と other_entries を各々 `order_key(rel, counts)` でソート。
  - `order=="alpha"`（既定）は現状の `_walk_files` 由来の path 順を維持（挙動不変）。
- **content_hash に order を織り込む（重要）**: `_compute_content_hash` は `_walk_files`(sorted) を
  走査するため body の並びに依存しない。このままだと `write_index_md` の idempotency チェック
  （content_hash 一致で書込スキップ）により、`--order alpha`↔`cache` を切り替えても
  「フォルダ内容が同じ」と判定され**並べ替えが反映されない**。これを避けるため、
  `order != "alpha"` のときだけハッシュ入力に order マーカーを足す:
  `if order != "alpha": h.update(b"|order=cache")`。
  - 効果: `alpha` は現行と完全に同一の hash 値（既存ファイルの migration churn なし・後方互換）。
    `cache` は別 hash 名前空間 → alpha↔cache 切替で必ず再書込、cache↔cache（フォルダ不変）は idempotent。
  - `_compute_content_hash(root, idx, order)` に `order` 引数を足す（既定 `"alpha"`）。

## 7. 安定性計測（`stability`）

`prefix_stability(old_text, new_text) -> dict`:
- 両者を改行分割。先頭から一致する行数 `common` を数える（最初に不一致になった行で停止）。
- 返り値: `{"common_prefix_lines": common, "new_lines": len(new_lines), "ratio": round(common / max(len(new_lines), 1), 4)}`。
- 用途: あるフォルダを `--order alpha` と `--order cache` で生成し、（途中で 1 ファイルを変更して）
  再生成した旧/新を比較すると、cache 版で `ratio` が高くなる（プレフィックスが崩れにくい）ことを定量確認できる。

## 8. CLI（既存規約踏襲）

- `dotmd-index` に `--order {alpha,cache}`（既定 `alpha`）を追加。`write_index_md(order=...)` / `generate_index_md(order=...)` に委譲（`--stdout` 経路含む）。
- 新 `dotmd-parser stability <old_file> <new_file> [--json]`:
  - 2 ファイルを読み `prefix_stability` を出力。text 例:
    `prefix stable: 42/50 lines (0.84)`。`--json` で dict。
  - 入力ファイル欠如 → stderr エラー + exit 2。
- `__init__` に `git_change_counts`, `order_key`, `prefix_stability` を公開。`known_cmds` に `"stability"` 追記。

## 9. エラー処理 / 後方互換

- 既定 `--order alpha` → 出力は現状と完全一致（後方互換）。
- git 不在 / 非リポ / git エラー → `git_change_counts` が `{}` を返し、cache 指定でも全 freq 0 →
  実質 alpha 同等順。クラッシュしない。
- `stability` の入力欠如 → exit 2。
- `order` に未知値 → argparse `choices` が exit 2。

## 10. 実装ステップ

1. `cache_order.py`: `git_change_counts`（subprocess + fallback）/ `order_key` / `prefix_stability` を TDD 実装。
2. `index_md`: `generate_index_md` / `write_index_md` に `order` 引数、`_files_section` にソート分岐 + counts 受け渡し。
3. CLI: `dotmd-index --order` 配線 + 新 `stability` コマンド + `known_cmds`。
4. `__init__.py` に cache_order API を公開。
5. `CHANGELOG.md` + `README.md` / `README.ja.md`。

## 11. テスト方針

pytest・`tmp_path`・`capsys`、既存テスト準拠。

`tests/test_cache_order.py`:
- `git_change_counts`: tmp に git init → 2 ファイルを別回数 commit → count が回数を反映。
  非 git ディレクトリ → `{}`。
- `order_key`: 低 count が前、同 count は alpha。
- `prefix_stability`: 完全一致 → ratio 1.0、先頭 N 行一致 → common=N、全不一致 → 0.0。

`tests/test_index_md_order.py`:
- `order="cache"`: 高頻度ファイルが Files セクションで後方、低頻度が前方（tmp git リポで検証）。
- `order="alpha"`（既定）: 現状の path 順と一致（既存テストが緑のまま）、content_hash は現行と同一値。
- `order="cache"` の content_hash は `order="alpha"` と**異なる**（order マーカーで切替が再書込される）。
- 同一フォルダ・同一 order の再生成は content_hash 一致（idempotent）。

`tests/test_cli_stability.py`:
- `stability` が text / `--json` を出力、欠如ファイルで exit 2。
- `dotmd-index --order cache` が動作（git リポ tmp で前方が低頻度）。

カバレッジ 80%+ を維持。
