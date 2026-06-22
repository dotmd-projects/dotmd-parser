# @include インジェクション検査 (include injection scan) — 設計

- Date: 2026-06-21
- Status: Approved (design)
- Scope: `resolve()` の @include 展開パイプラインにプロンプトインジェクション検出スキャナを追加する（5 機能構想のうち④）
- Related: `parser.resolve` / `_expand`（@include 展開）、既存 `cli.cmd_resolve`
- 参照概念: Handlebars ロール注入問題、Web 汚染（FORGE）

## 1. 背景と目的

`resolve()` は `@include` ターゲットの内容を再帰的に inline する。inline されるのは
外部（共有・ベンダリングされた）ファイルの可能性があり、そこにプロンプトインジェクション
（ロール詐称・指示上書き等）が仕込まれていても素通しで埋め込まれてしまう。

本機能は **@include で取り込まれる内容を展開時に検査**し、インジェクション/区切り偽装を
検出する。既定は警告のみ（inline は継続）、opt-in で block（該当 include をプレースホルダ化）。

非目標 (YAGNI):
- 自然言語の意味解析や LLM 判定はしない（決定的・stdlib `re` のみ）。
- root（自分が書いた SKILL.md / エントリ）は信頼し検査しない。
- 完全なインジェクション防御は目標ではない（既知パターンの検出補助）。

## 2. 設計判断（ブレストで確定）

| 論点 | 決定 |
|---|---|
| 検査対象 | **@include された内容のみ**（root=depth0 は信頼し非検査） |
| ポリシー | **warn 既定 + block opt-in**（block は該当 include をプレースホルダ置換） |
| ルールセット | **role-spoof / instruction-override を既定**、delimiter-spoof / tool-exfil は opt-in |
| 誤検知抑制 | フェンス済みコードブロック内を除外 + `<!-- dotmd-allow: <rule> -->` 抑制 |
| 戻り値 | `resolve()` に新キー `injections` を追加（既存 content/placeholders/warnings は不変） |

## 3. アーキテクチャ（最小変更・加算的）

新規 `src/dotmd_parser/scan.py`（純スキャナ）。`parser.resolve` / `_expand` にフックする。
グラフ構築系（build_graph 等）や他モジュールは無改変。

```
scan.py
├─ DEFAULT_RULES = ("role-spoof", "instruction-override")
├─ OPTIONAL_RULES = ("delimiter-spoof", "tool-exfil")
├─ ALL_RULES = DEFAULT_RULES + OPTIONAL_RULES
├─ scan_content(text, source="", rules=None) -> list[Finding]
│   ├─ _mask_code_fences(text)   # ``` 区間を空行化（行番号保持）
│   ├─ _suppressed_rules(text)   # <!-- dotmd-allow: rule|all --> を集合化
│   └─ 各有効ルールの正規表現を行単位で適用
```

Finding 形:
```python
Finding = {
  "rule": str,          # role-spoof | instruction-override | delimiter-spoof | tool-exfil
  "severity": "warning",
  "source": str,        # 検出されたファイル（resolve からは相対/絶対の元パス文字列）
  "line": int,          # 1 始まりの行番号
  "snippet": str,       # 一致した行（前後空白 strip、長い場合は省略）
  "message": str,
}
```

`rules` 引数: `None` のとき `DEFAULT_RULES`。明示リストで opt-in ルールを足せる。
未知のルール名は無視（堅牢）。

## 4. 検出ルール（行単位・stdlib `re`）

検査は **コードフェンスをマスクした後**のテキストに対し、行ごとに適用する。

- **role-spoof**（既定）:
  - 行頭ロール: `^\s*(System|Assistant|Human|User|AI)\s*:`（先頭大文字想定、`re.MULTILINE`）。
  - チャットテンプレトークン: `<|im_start|>` `<|im_end|>` `[INST]` `[/INST]` `<<SYS>>` `<</SYS>>`（リテラル）。
  - message 例: `"possible role impersonation: 'System:'"`。
- **instruction-override**（既定）:
  - `(?i)\b(ignore|disregard|forget)\b[^\n]*\b(previous|above|prior|earlier|all)\b[^\n]*\b(instruction|instructions|prompt|prompts|context|rule|rules)\b`
  - `(?i)\bnew\s+instructions?\s*:`
  - message 例: `"possible instruction override"`。
- **delimiter-spoof**（opt-in）:
  - 単独の区切り線: `^\s*---\s*$`（先頭行＝frontmatter は除外: 行番号 1 はスキップ）。
  - システム見出し風: `(?i)^#{1,6}\s*(system|instructions?|prompt)\b`。
- **tool-exfil**（opt-in）:
  - `(?i)\b(print|reveal|show|repeat|output|display)\b[^\n]*\b(system prompt|your instructions|the prompt above|previous prompt)\b`

各ルールは「正規表現 + メッセージ生成」の小さなレコードとして登録し、`scan_content` が
有効ルールのみ走らせる。一致ごとに 1 Finding（同一行で複数一致は最初の 1 件に集約してよい）。

## 5. 誤検知抑制

- **コードフェンス除外**: ` ``` ` で囲まれた区間を `_mask_code_fences` が空行に置換してから走査する。
  行番号は保持（行数を変えない）。ドキュメントが例として `System:` 等を載せるケースを除外。
- **allow コメント**: ファイル内のどこかに `<!-- dotmd-allow: role-spoof -->` があれば
  そのルールを当該ファイルで抑制。`<!-- dotmd-allow: all -->` で全ルール抑制。
  複数指定可（カンマ区切り or 複数コメント）。
- 既定有効は低 FP の role-spoof / instruction-override のみ。delimiter-spoof / tool-exfil は
  明示 opt-in。

## 6. フック点（`parser.resolve` / `_expand`）

`resolve` のシグネチャを後方互換で拡張:
```python
def resolve(
    file_path: str,
    variables: dict[str, str] | None = None,
    *,
    scan: bool = True,
    scan_rules: list[str] | None = None,
    on_injection: str = "warn",   # "warn" | "block"
) -> dict:
```

- `_expand(fp, depth)` 内で、**depth > 0**（=@include 経由で読まれたファイル）の raw content に
  対し `scan` が True のとき `scan_content(content, source=str(fp), rules=scan_rules)` を実行。
  root（depth 0）は検査しない。ネストした include も各々 depth>0 で検査される。
- `on_injection == "warn"`: finding を蓄積し、inline は通常どおり継続（content 不変）。
- `on_injection == "block"`: そのファイルで finding が出たら、展開結果の代わりに
  `<!-- dotmd: blocked injection ({rules}) from {source} -->` を返し inline を抑止
  （finding は引き続き記録）。`{rules}` は検出ルール名のソート済みカンマ連結。
- 戻り値に新キー `"injections": [Finding...]` を追加。`scan=False` のときは空リスト。
  既存 `content` / `placeholders` / `warnings` は不変（後方互換）。

実装メモ: scan の蓄積は `_expand` のクロージャが参照する外側リスト（warnings と同様の方式）。

## 7. CLI（`resolve` を拡張・後方互換）

```
dotmd-parser resolve <file> [--var k=v] [--no-scan] [--scan-rule NAME] [--block]
```

- 既定: scan ON / warn。`content` を stdout、`injections` を stderr に
  `[INJECTION {rule}] {source}:{line} — {message}` で出力（既存 warnings 出力の隣）。
- `--no-scan`: scan を無効化。
- `--scan-rule NAME`（`action="append"`, choices=ALL_RULES）: 有効ルールを明示指定。
  指定が 1 つでもあれば「既定 + 指定」ではなく「**既定 ∪ 指定**」とする
  （= opt-in ルールを足す用途。delimiter-spoof / tool-exfil を有効化できる）。
- `--block`: `on_injection="block"`。
- `__init__` に `scan_content`, `DEFAULT_RULES`, `OPTIONAL_RULES`, `ALL_RULES` を公開。

## 8. エラー処理 / 後方互換

- `scan_content` は任意テキストに対し例外を投げない（堅牢）。`resolve` は常に結果を返す。
- `resolve()` 既定 `scan=True` だが warn では content 不変・戻り値にキー追加のみ →
  既存呼び出し側（`content`/`placeholders`/`warnings` 参照）は無影響。
- 既存 `cmd_resolve` の stdout（展開済み content）は不変。injections は stderr のみ。

## 9. 実装ステップ

1. `scan.py`: ルールレジストリ + `_mask_code_fences` + `_suppressed_rules` + `scan_content` を TDD 実装。
2. `parser.resolve` / `_expand` にフック（scan/scan_rules/on_injection 引数、injections キー、block 置換）。
3. `cmd_resolve` を拡張（--no-scan / --scan-rule / --block、stderr 出力）。
4. `__init__.py` に scan API を公開。
5. `CHANGELOG.md` + `README.md` / `README.ja.md` に scan 機能を追記。

## 10. テスト方針

pytest・`tmp_path`・`capsys`、既存テスト準拠。

`tests/test_scan.py`（純スキャナ）:
- role-spoof: `System:` 行・`<|im_start|>` を検出。
- instruction-override: 「ignore previous instructions」を検出、無害文は非検出。
- delimiter-spoof / tool-exfil: 既定では検出されず、`rules` に含めると検出される。
- コードフェンス内の `System:` は検出されない（行番号は保持）。
- `<!-- dotmd-allow: role-spoof -->` で当該ルール抑制、`all` で全抑制。
- 未知ルール名は無視。

`tests/test_resolve_scan.py`（resolve 統合）:
- warn: @include 先に `System:` → `injections` に 1 件、`content` は inline 継続で不変。
- root（エントリ自身）に `System:` があっても検出されない（depth0 非検査）。
- ネスト include（A→B、B に注入）でも B が検出される。
- block: 検出ファイルの内容が `<!-- dotmd: blocked injection ... -->` に置換される。
- `scan=False` で `injections` 空・従来どおり。

`tests/test_cli_resolve_scan.py`（CLI）:
- 既定で injections が stderr に出る、stdout(content) は不変。
- `--no-scan` で injections 無し、`--scan-rule delimiter-spoof` で opt-in 検出、`--block` で置換。

カバレッジ 80%+ を維持。
