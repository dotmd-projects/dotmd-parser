# ドメインオントロジー自動構築 (v1: 抽出コア) — 設計

- Date: 2026-08-05
- Status: Approved (design)
- Scope: `.md` 分析コーパス1フォルダから、形式ドメインオントロジー（クラス / データ型プロパティ / オブジェクトプロパティ / カーディナリティ / 統制語彙 / 不変条件 / プロビナンス）を **テキスト先行**で自動抽出し、正本 IR (`ontology.yml`) と 2 投影（Turtle `.ttl` / §5 形式設計 md）へ出力する。決定的検証ゲート付き。
- Related: 新規 `ontology.py` / `llm.py`（`analyze.py` から LLM 配管を切り出し）/ prompt `templates/prompts/extract-ontology.md`、既存 `scan.py`(`scan_documents`)・`checks.py`(CI ゲート思想)・`cli.py`(サブコマンド配線)
- 着想源: `~/Python_Programing/freee/G2Rec/g2rec_freee/freenance-ads-analysis/フリーナンス_ドメイン知識_オントロジー再構築用.md` §5（手書きの 14 クラス / 34 データ型 prop / 16 オブジェクト prop のドメインオントロジー）

## 1. 背景と目的

フリーナンス案件では「データ検証 → 予算配分 → ROAS 分解 → 買取率 → … → オントロジー化」という**長い手作業の分析チェーン**の末に、`.md` の §5 として形式オントロジーを蒸留した。この蒸留物は再利用価値が高いが、構築は労働集約的で、案件ごとに手で組み直している。

本機能はこの構築を自動化する。dotmd-parser は好適なホスト: `.md` を第一級の構造化知識として扱い、`analyze` の LLM 抽出パイプライン（stdlib-only API 呼び出し・prompt 同梱・JSON 出力・host-agent モード）と、決定的ゲートの CI 思想を既に持つ。オントロジー抽出は**同じパイプラインの prompt / schema / 出力形式を差し替えたもの**として実装できる。

### 期待する 4 価値と phase 対応

| 価値 | 効く phase |
|---|---|
| 分析→蒸留の工数削減 | v1（①取込→②抽出→⑤出力） |
| 案件横断の再利用 | v1 + v4 |
| 整合性・矛盾検出 | v2 |
| クエリ/推論の土台 | v3 |

### phase ロードマップ（v1 は本 spec、v2 以降は別 spec）

| phase | 中身 |
|---|---|
| **v1** | 抽出コア: md 取込 → IR 抽出 → 文書横断マージ(最小) → 出力(yml/ttl/md) + 検証ゲート |
| v2 | 矛盾・整合の**能動**検出（審査CV<申請CV 型）・未解決質問抽出・名寄せ支援 |
| v3 | クエリ/推論: SPARQL / reasoner + dotmd-mcp ツール |
| v4 | 生きた維持: 新 md の差分更新・鮮度検証（テキスト+データ突合へ拡張） |

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| v1 入力範囲 | **テキスト先行**。分析 md コーパスのみ。プロビナンスはソース md 行/節まで。生データ突合は v4 |
| 置き場所 | dotmd-parser 新サブコマンド `dotmd-parser ontology <dir>` |
| 抽出方式 | **文書ごと LLM 抽出 (map) → 正規化ラベルでマージ (reduce)**。API 経路と `--plan`(host-agent) 経路の両対応 |
| 正本 | 構造化 IR `ontology.yml`。`.ttl` と設計 md は IR の**投影** |
| v1/v2 線引き | v1 は「本文に明記済み」の不変条件・矛盾・未解決質問を**抽出して記録**するのみ。**新規矛盾の能動推論検出・名寄せ解決は v2** |
| マージ衝突 | v1 は自動解決せず**フラグして併記**（`conflicts` へ） |
| カーディナリティ | 注釈プロパティ `frnc:cardinality "1:0..1"` で**無損失記録**。厳密な `owl:Restriction` 生成は phase2 |
| Turtle 検証 | `rdflib` を**遅延 import の任意依存**（pdfplumber 同様）。無ければ構造ゲートのみ + warning |
| LLM 品質採点 | **opt-in `--eval`**（単発ルーブリック採点）。v1 必須は決定的ゲートのみ |
| LLM 配管 | `analyze.py` の `_call_claude`/`_load_prompt_template`/`load_dotenv` を `llm.py` に切り出し両者から import |

非目標 (YAGNI):
- 生データ（BigQuery/CSV）突合・enum の実在検証・不変条件の数値検証はしない（v4）。
- SPARQL / reasoner / MCP ツール・RDF/XML・可視化 HTML は出さない（v3）。
- 新規矛盾の能動検出・②③媒体名寄せの自動解決はしない（v2）。
- 増分/差分更新はしない。v1 は毎回フルスキャン → フル生成。

## 3. アーキテクチャ

```
cli.py  cmd_ontology                         … サブコマンド配線
llm.py  (新規)                                … analyze から切り出した共有 LLM 配管
  ├─ load_dotenv / _call_claude / _load_prompt_template / _extract_json
ontology.py (新規)
  ├─ scan_corpus(dir)          … scan_documents 流用。summary でなく全文を渡す
  ├─ extract_ontology(dir,…)   … 文書ごと LLM 抽出 → 部分 IR(要素+プロビナンス) [map]
  ├─ merge_ontology(partials)  … 正規化ラベルで統合・プロビナンス集約・衝突フラグ [reduce]
  ├─ emit_yaml / emit_ttl / emit_design_md   … IR → 3 成果物
  ├─ validate_ontology(ir)     … 決定的ゲート（構造 + 任意 rdflib）
  ├─ estimate_cost / format_host_agent_plan  … analyze と同型（--dry-run / --plan）
  └─ apply_ontology_from_file  … --apply-from（事前抽出 JSON → merge → emit）
templates/prompts/extract-ontology.md         … 抽出指示 + JSON スキーマ
```

`parser.py` / `index.py` は無改変。`analyze.py` は `llm.py` への委譲のみの差分（挙動不変）。

出力先: 既定 `<dir>/ontology/`（`ontology.yml` / `ontology.ttl` / `ontology-design.md`）。freenance の `ontology/` 慣習に合わせる。

## 4. 中間表現 IR (`ontology.yml`) — v1 の正本

```yaml
meta:
  namespace: "https://freenance.net/ontology#"
  prefix: frnc
  domain: "フリーナンス 広告×与信"
  built_from: "freenance-ads-analysis/"
  source_docs: ["B-1c...md", "フリーナンス_ドメイン知識...md", ...]
  generated_by: "dotmd-parser ontology vX"
classes:
  - {name: Application, label_ja: 申請, domain_group: 取引, provenance: ["...md#5.1"]}
datatype_properties:
  - {name: feeRate, domain: Application, type: decimal, label_ja: 手数料率,
     enum: null, provenance: ["...md#5.2"]}
  - {name: applicationStatus, domain: Application, type: string, label_ja: 申請ステータス,
     enum: applicationStatus, provenance: [...]}       # enum は vocabularies の name を指す
object_properties:
  - {name: evaluatedBy, from: Application, to: CreditAssessment,
     cardinality: "1:1", characteristics: [Functional], note: 鎖, provenance: [...]}
vocabularies:                       # 統制語彙(enum)
  - {name: applicationStatus, values: [買取成立, 謝絶, キャンセル, 未処理], provenance: [...]}
invariants:                         # 不変条件・恒等式（本文に明記されたもの）
  - {id: roas-identity, statement: "ROAS = 申請件数単価 ÷ 申請CPA", kind: identity, provenance: [...]}
conflicts:                          # v1 は本文に明記済みのものを記録のみ（能動検出は v2）
  - {kind: contradiction, detail: "審査CV(681) < 申請CV(1290)", provenance: [...]}
open_questions:
  - {text: "審査CVの正確な定義", provenance: [...]}
```

- `kind`(invariant): `identity` | `constraint` | `cardinality-rule`。
- `characteristics`: `{Functional, InverseFunctional, Symmetric, Transitive, ...}` の部分集合。
- `provenance`: 全要素に必須（`ソースmd#節` または `ソースmd:行`）。欠落は検証で warning。
- **決定的シリアライズ**: キーソート・要素安定順。同入力 → 同出力（stability 保証）。

## 5. 抽出 (`extract_ontology`) と JSON スキーマ

文書ごとに `extract-ontology.md` プロンプトで LLM を 1 回呼び、部分 IR を得る。期待 JSON:

```json
{
  "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
  "datatype_properties": [{"name": "feeRate", "domain": "Application", "type": "decimal",
                           "label_ja": "手数料率", "enum": null}],
  "object_properties": [{"name": "evaluatedBy", "from": "Application", "to": "CreditAssessment",
                         "cardinality": "1:1", "characteristics": ["Functional"], "note": "鎖"}],
  "vocabularies": [{"name": "applicationStatus", "values": ["買取成立","謝絶","キャンセル","未処理"]}],
  "invariants": [{"id": "roas-identity", "statement": "ROAS = 申請件数単価 ÷ 申請CPA", "kind": "identity"}],
  "conflicts": [{"kind": "contradiction", "detail": "審査CV(681) < 申請CV(1290)"}],
  "open_questions": [{"text": "審査CVの正確な定義"}]
}
```

- プロビナンスは抽出器がソース md パスを知っているので、`merge_ontology` が各要素に**呼び出し元文書パス**を付与する（LLM に自己申告させない → 正確）。節番号は LLM が任意で `note`/`detail` に含めてよいが必須ではない。
- JSON 抽出は `analyze` と同じ: fenced ```json 優先、無ければ本文全体を parse。不正 JSON は `RuntimeError`。
- 型の許容値: `string|decimal|integer|date|dateTime|boolean`。未知型は `string` に正規化 + warning。

## 6. マージ (`merge_ontology`)

- クラス/prop/vocab を **正規化ラベル（`name` の小文字化・trim）** で突合し統合。
- 同一要素の複数文書出現 → **プロビナンス配列に集約**（重複排除）。
- **衝突**（同名だが `type`/`domain`/`cardinality` 等が食い違う）→ 自動解決せず、両値を `conflicts` に `kind: naming` で追加し、代表値は**最初の出現**を採用（決定的）。
- objprop の `from`/`to` が未定義クラスを指す → マージ後も残す（検証がゲート）。v1 では**クラスの暗黙生成はしない**（誤生成を避け、検証で dangling として顕在化）。

## 7. 出力 (`emit_*`)

### 7.1 `emit_ttl` — Turtle (OWL-lite)

- prefix: `frnc:`(meta.namespace) `owl:` `rdfs:` `xsd:` `skos:`。
- クラス → `frnc:Application a owl:Class ; rdfs:label "申請"@ja .`
- データ型 prop → `owl:DatatypeProperty ; rdfs:domain frnc:Application ; rdfs:range xsd:decimal ; rdfs:label "手数料率"@ja .`
- オブジェクト prop → `owl:ObjectProperty ; rdfs:domain … ; rdfs:range … .`。characteristics → `a owl:FunctionalProperty` / `owl:SymmetricProperty` 等を追記。
- **カーディナリティ** → 注釈 `frnc:cardinality "1:0..1"`（無損失）。厳密 Restriction は phase2。
- enum(vocabularies) → `skos:ConceptScheme` + 各値 `skos:Concept`（`skos:inScheme`）。enum 付き datatype prop から `frnc:usesVocabulary` 注釈でリンク。
- 不変条件 → `frnc:roas-identity a frnc:Invariant ; rdfs:comment "ROAS = …" .`（OWL で恒等式は表現不能。SHACL 化は phase2）。
- プロビナンス → 各主語へ `frnc:sourceDoc "…md#5.1"` 注釈。

型マップ: `string→xsd:string, decimal→xsd:decimal, integer→xsd:integer, date→xsd:date, dateTime→xsd:dateTime, boolean→xsd:boolean`。不明→`xsd:string`+warning。

### 7.2 `emit_design_md` — §5 形式の人間向け設計 md

節: ドメイン別クラス表 / クラス別データ型 prop / オブジェクト prop 表（From→To・カーディナリティ・characteristics）/ 統制語彙 / 不変条件 / 矛盾・未解決質問。**「工数削減」の実成果物**（手書き §5 を再現）。

### 7.3 `emit_yaml` — IR 正本（§4）。決定的シリアライズ。

## 8. 検証 (`validate_ontology`)

**構造ゲート（stdlib のみ・常時実行・エラーは非ゼロ終了 = ハードゲート）:**
- dangling 参照: objprop の `from`/`to`・datatype prop の `domain` が定義済みクラス／`enum` 参照が `vocabularies` に存在。
- カーディナリティ書式の文法（`N:M` / `1:0..1` / `多:1` / `1:0..*` 等）。
- `characteristics` 語彙チェック。
- 重複クラス/prop 名。
- プロビナンス欠落 → **warning**。

**Turtle 構文/整合ゲート:** `rdflib` を遅延 import。有れば `.ttl` を parse + 整合確認。無ければ構造ゲートのみ + warning。

戻り値 `{errors: [...], warnings: [...]}`。`--check` 時は errors ありで非ゼロ終了（CI ゲート）。

**LLM 品質採点 (opt-in `--eval`):** ルーブリックで抽出のカバレッジ/忠実度を 1 回採点。多ラウンド/stability は将来。

## 9. CLI (`dotmd-parser ontology <dir>`)

| フラグ | 意味 |
|---|---|
| `--emit yml,ttl,md` | 出力選択（既定 = 全部） |
| `--out <dir>` | 既定 `<dir>/ontology` |
| `--plan` | host-agent プラン（API キー不要）→ JSON を `--apply-from` で戻す |
| `--apply-from <json>` | 事前抽出 JSON を merge→emit |
| `--dry-run` | コスト見積り（`estimate_cost`） |
| `--namespace / --prefix / --domain` | メタ上書き。無ければ folder 名から推定（`https://example.org/<slug>#`）+ warning |
| `--eval` | 任意 LLM 採点 |
| `--check` | emit + validate、エラーで非ゼロ終了 |
| `--model` | Claude モデル ID（既定 = env `CLAUDE_MODEL` or ライブラリ既定） |

人間可読サマリ `format_ontology_summary(ir, report)` を既定表示。

## 10. テスト方針（TDD・`caller` フックで API 不要）

- **単体**:
  - `merge_ontology`: canned 部分 IR 2 件 → dedup + プロビナンス集約 + 衝突フラグ。
  - `emit_ttl`: rdflib で parse 可、クラス/prop 数一致。
  - `validate_ontology`: dangling 参照・不正カーディナリティ・未知 characteristics を検出。
  - `emit_design_md`: 期待テーブル（クラス表・objprop 表）出現。
  - 型正規化: 未知型 → string + warning。
- **ゴールデンファイル**: 固定 IR → yml/ttl/md の決定的出力（stability）。
- **ラウンドトリップ**: IR → ttl → rdflib parse → クラス/prop 数一致。
- **受入 (slow / API 任意)**: freenance コーパスで ~14 クラス / ~16 objprop をファジー回収（±許容）。API キー無しなら skip。

## 11. 受け入れ基準 (v1)

1. `dotmd-parser ontology <dir>` が `<dir>/ontology/{ontology.yml, ontology.ttl, ontology-design.md}` を生成する。
2. `--plan` → `--apply-from` の host-agent 経路が API キー無しで同じ成果物を生む。
3. `validate_ontology` が dangling 参照を errors として検出し `--check` が非ゼロ終了する。
4. 同一コーパスの 2 回実行が同一 `ontology.yml`/`ttl`/`md` を生む（決定的）。
5. freenance コーパスに対し、クラス/オブジェクト prop の主要要素を回収し、`ontology-design.md` が手書き §5 に構造的に対応する。
