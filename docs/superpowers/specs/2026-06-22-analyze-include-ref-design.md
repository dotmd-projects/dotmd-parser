# analyze の @include / @ref 自動判定 — 設計

- Date: 2026-06-22
- Status: Approved (design)
- Scope: `analyze` が検出した依存ごとに `@include`（インライン展開）か `@ref`（指すだけ）かを自動判定し、`apply` 時に適切なディレクティブを注入する
- Related: `analyze.py`（`analyze_dependencies` / `generate_directives` / `apply_analysis` / `apply_analysis_from_file` / `format_host_agent_plan` / `format_proposal`）、prompt テンプレ `templates/prompts/analyze-dependencies.md`、`parser.resolve`（`@ref` は既に「展開せず依存記録」）

## 1. 背景と目的

現状 `generate_directives` は検出依存を**一律 `@include`** として注入する。しかし依存には
「相手の本文を貼るべき共有断片（include）」と「指すだけのポインタ（ref）」があり、
ポインタを `@include` のままにすると `resolve` が相手を丸ごと inline してプロンプトが
肥大化する（実測: ある skill で 459 行 vs `@ref` なら 162 行）。

現状はこの格下げを**人手**でやる必要がある。本機能は判定を**自動化**する:
`analyze` は既に Claude を呼ぶので、判定を出力スキーマに足し、決定的ガードで補強する。

非目標 (YAGNI):
- `@delegate` の自動判定はしない（agent 委譲は別概念。対象は include/ref のみ）。
- fan-in（被参照数）によるガードは入れない（共有 role 断片＝高 fan-in かつ inline 正解のため不適）。
- `@ref` の意味論自体は変更しない（既存どおり「展開せず依存記録」）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| 判定方式 | **LLM 判定 + 決定的ガード**。API 経路も `--plan`(host-agent) 経路も自動判定 |
| ガード範囲 | **cycle → ref を必須**（ハード強制）、**size → ref は `--max-include-bytes` で opt-in**（既定オフ）。fan-in 不採用 |
| スキーマ | edge に `kind: "include"\|"ref"` を追加。**省略時 include**（後方互換） |
| shared_proposals | 定義上いつも `include`（判定対象外） |
| フィールド名 | `kind` |

## 3. アーキテクチャ（最小変更・`analyze.py` で完結）

```
templates/prompts/analyze-dependencies.md   … edge スキーマに kind + 判定基準を追記
analyze.py
├─ analyze_dependencies        … parsed edges の kind をそのまま保持（無ければ include）
├─ generate_directives         … kind で @include / @ref を出し分け
├─ _apply_directive_guards     … (新規) cycle→ref 強制 + size opt-in。判定後・生成前に適用
├─ apply_analysis              … guards を通してから generate_directives
├─ apply_analysis_from_file    … 同上（--plan/--apply-from 経路）
├─ format_host_agent_plan      … --plan の期待 JSON に kind を明記
└─ format_proposal             … 表示に kind を反映
```
`parser.py` / `index.py` は無改変。

## 4. スキーマ変更（edge に `kind`）

```json
{
  "edges": [
    {"from": "a.md", "to": "shared/role.md", "kind": "include", "reason": "..."},
    {"from": "a.md", "to": "guide.md",       "kind": "ref",     "reason": "..."}
  ],
  "shared_proposals": [ {"name": "shared/x.md", "used_by": ["a.md"], ...} ]
}
```
- `kind` は `"include"` | `"ref"` のいずれか。**未知値・省略は `include`** に正規化（後方互換・堅牢）。
- `shared_proposals` は常に `include`。

## 5. LLM 判定（プロンプト更新）

`analyze-dependencies.md` の Output format に `kind` を追加し、基準を明記:
- `kind: "include"` = 相手の本文を**この場に貼るべき共有断片**（共通 role / 定義 / 定型ブロック）。
- `kind: "ref"` = **指すだけのポインタ**（see-also、独立した別ドキュメント、大きい資料、
  条件付きで読むもの、サブスキル）。
- `reason` と同様、判定は source 言語に依存しない（kind は enum なので言語非依存）。

同じスキーマを `format_host_agent_plan` の「Expected output」にも反映するため、
API 経路（`analyze`）と no-API 経路（`--plan` → host-agent が JSON を書く）の**両方で自動判定**される。

## 6. generate_directives（出し分け）

```python
kind = edge.get("kind", "include")
directive = "@ref" if kind == "ref" else "@include"
entry = f"{directive} {edge['to']}"
```
- `shared_proposals` 由来は `@include` 固定。
- 既存の重複防止（対象ファイルに既にある `@include`/`@ref`/`@delegate` 行は再注入しない）はそのまま。

## 7. 決定的ガード（`_apply_directive_guards(analysis, directory, max_include_bytes=None) -> analysis`）

判定後・`generate_directives` の前に、edges の `kind` を必要に応じ `ref` に降格する。

**(a) cycle → ref（ハード強制・常時）**
- 「適用後に存在する **include グラフ**」を構築する:
  - 既存エッジ: 対象フォルダを `build_index` し、その `deps` のうち `type=="include"` のもの。
  - 新規エッジ: 本 analysis の edges のうち `kind=="include"` のもの（`shared_proposals` 由来 include も含む）。
- `@ref` は展開しない（resolve は recurse しない）ので **循環判定は include エッジのみ**で行う。
- 構築した有向グラフで循環を検出し、**循環を閉じる"新規"エッジを `ref` に降格**する
  （既存ファイルのディレクティブは書き換えない。降格は新規エッジのみ）。
- アルゴリズム: 新規 include エッジを決定的順序（`(from, to)` 昇順）で1本ずつ追加し、
  追加で循環が生じるエッジは `ref` に降格（back-edge demotion）。結果は決定的。

**(b) size → ref（opt-in）**
- `max_include_bytes` が指定されたとき、`kind=="include"` のターゲット実ファイルが
  そのバイト数を超えれば `ref` に降格。ファイルが読めない/存在しないものはスキップ。
- 既定 `None`（オフ）。

降格時は edge に痕跡を残してよい（`reason` 末尾に ` [auto: cycle→ref]` 等）。実装簡潔性のため必須ではない。

## 8. CLI

- `analyze` に `--max-include-bytes N`（int、既定なし=オフ）を追加。
- `apply_analysis(directory, analysis, max_include_bytes=None)` /
  `apply_analysis_from_file(directory, json_path, max_include_bytes=None)` に引数追加。
- `--apply` / `--apply-from` 両経路で kind 判定 + ガードが効く。新フラグはこれ 1 つのみ。

## 9. 後方互換

- 旧 analysis.json / 旧プロンプト由来の `kind` 無し edge → `include`（現状の挙動を完全維持）。
- `--max-include-bytes` 未指定 → size ガードは作動しない。
- deps.yml（binary ソース）経路・重複防止は不変。
- `@ref` セマンティクスは既存のまま（resolve 非展開、graph は edge 記録、affects/check は従来どおり両方をエッジ扱い）。

## 10. 実装ステップ

1. プロンプトテンプレ `analyze-dependencies.md` に `kind` フィールド + 基準を追記。
2. `generate_directives` を `kind` 対応（include/ref 出し分け、shared は include 固定）。
3. `_apply_directive_guards`（cycle 強制降格 + size opt-in）を実装。
4. `apply_analysis` / `apply_analysis_from_file` に組込み + `--max-include-bytes` 配線（CLI）。
5. `format_host_agent_plan`（期待 JSON に kind）/ `format_proposal`（kind 表示）更新。
6. `CHANGELOG.md` + `README.md` / `README.ja.md`（移行例の "@ref に直す" 説明を「自動判定される」に更新）。

## 11. テスト方針

`tests/test_analyze.py` 拡張（既存の caller モック方式を流用 = API 不要）:
- caller が `kind:"ref"` を返す edge → 対象ファイルに **`@ref` が注入**される。
- `kind:"include"` / kind 省略 → `@include`（後方互換）。
- `shared_proposals` は kind に関わらず `@include`。
- **cycle 強制**: a→b(include) と b→a(include) を与えると、一方が `@ref` に降格され循環 inline を防ぐ（`check` が circular にならない）。既存ファイルのディレクティブは降格対象外。
- **size opt-in**: `--max-include-bytes` 相当の引数で、閾値超ターゲットの include が `ref` に降格。未指定なら降格しない。
- `generate_directives` 単体: kind→ディレクティブ文字列のマッピング。
- `format_host_agent_plan` の出力に `kind` が含まれる。
- `apply_analysis_from_file`: kind 付き JSON を適用 → 正しいディレクティブ。

`tests/test_cli_*`（既存 analyze CLI テストがあれば）: `--max-include-bytes` 受理。

カバレッジ 80%+ を維持。
