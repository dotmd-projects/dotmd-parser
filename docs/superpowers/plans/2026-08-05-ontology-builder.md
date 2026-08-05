# ドメインオントロジー自動構築 v1 (抽出コア) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `.md` 分析コーパス1フォルダから形式ドメインオントロジー（クラス/データ型prop/オブジェクトprop/カーディナリティ/統制語彙/不変条件/プロビナンス）をテキスト先行で抽出し、正本 IR `ontology.yml` と Turtle `.ttl` / §5形式設計md へ出力する `dotmd-parser ontology` サブコマンドを追加する。

**Architecture:** `analyze.py` の LLM 配管（stdlib-only urllib・prompt同梱・JSON抽出・host-agentモード）を `llm.py` に切り出して流用。新規 `ontology.py` が map(文書ごとLLM抽出)→reduce(正規化マージ)→emit(yml/ttl/md)→validate(決定的ゲート) を担う。IR は plain dict、`.ttl`/設計md は IR の投影。

**Tech Stack:** Python ≥3.10, stdlib のみ(runtime)。`rdflib` は Turtle 検証用の任意 extra。テストは `unittest` + `caller` モックフック（API 非依存）。

**Spec:** `docs/superpowers/specs/2026-08-05-ontology-builder-design.md`

## Global Constraints

- Python `>=3.10`。型注釈は `from __future__ import annotations` + PEP604(`X | None`)。
- **runtime は stdlib のみ**。`rdflib` は遅延 import の任意依存（`pdfplumber`/`python-docx` と同じ扱い）。無ければ機能縮退+warning、例外にしない。
- LLM 呼び出しは全経路で `caller` フック注入可能にする（テストは実 API を呼ばない）。
- 出力（`ontology.yml`/`.ttl`/`ontology-design.md`）は**決定的**: 同入力→同出力（キーソート・要素安定順）。
- プロビナンスは `merge_ontology` がソース md パスから付与（LLM 自己申告に頼らない）。
- 既存 `analyze.py` の**公開挙動を変えない**（Task 1 は behavior-preserving リファクタ）。
- コミットは `--no-gpg-sign`（この環境は SSH 署名鍵が無い）。型: feat/refactor/test/docs。

## File Structure

- Create `src/dotmd_parser/llm.py` — 共有 LLM 配管（`load_dotenv`/`call_claude`/`load_prompt_template`/`extract_json`）
- Modify `src/dotmd_parser/analyze.py` — 上記を `llm.py` へ委譲（薄い再エクスポートで後方互換）
- Create `src/dotmd_parser/ontology.py` — 抽出/マージ/emit/validate/orchestrator
- Create `src/dotmd_parser/templates/prompts/extract-ontology.md` — 抽出プロンプト+JSONスキーマ
- Modify `src/dotmd_parser/cli.py` — `cmd_ontology` + argparse 配線 + `known_cmds`
- Modify `src/dotmd_parser/__init__.py` — 公開 API 追加
- Modify `pyproject.toml` — `rdf` extra 追加
- Create `tests/test_ontology.py` — 単体（merge/emit/validate）
- Create `tests/test_cli_ontology.py` — CLI 経路（--plan/--apply-from/--check）

IR (plain dict) の正準形:
```python
IR = {
  "meta": {"namespace": str, "prefix": str, "domain": str,
           "built_from": str, "source_docs": list[str], "generated_by": str},
  "classes": [{"name","label_ja","domain_group","provenance": list[str]}],
  "datatype_properties": [{"name","domain","type","label_ja","enum": str|None,"provenance"}],
  "object_properties": [{"name","from","to","cardinality","characteristics": list[str],
                         "note","provenance"}],
  "vocabularies": [{"name","values": list[str],"provenance"}],
  "invariants": [{"id","statement","kind","provenance"}],
  "conflicts": [{"kind","detail","provenance"}],
  "open_questions": [{"text","provenance"}],
}
```

---

### Task 1: LLM 配管を `llm.py` へ切り出し（behavior-preserving）

**Files:**
- Create: `src/dotmd_parser/llm.py`
- Modify: `src/dotmd_parser/analyze.py` (先頭の定数/関数を llm へ委譲)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `load_dotenv(env_path: str | Path | None = None) -> None`
  - `call_claude(prompt: str, system: str, api_key: str, model: str, max_tokens: int = 4096) -> str`
  - `load_prompt_template(name: str) -> str`（`dotmd_parser.templates.prompts/<name>.md` を読む）
  - `extract_json(raw: str) -> dict`（fenced ```json 優先、無ければ本文 parse、失敗で `RuntimeError`）
  - 定数 `CLAUDE_API_URL`, `DEFAULT_MODEL = "claude-sonnet-4-5"`, `DEFAULT_MAX_TOKENS = 4096`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
import unittest
from dotmd_parser import llm


class TestExtractJson(unittest.TestCase):
    def test_fenced_block(self):
        raw = 'prose\n```json\n{"a": 1}\n```\ntrailing'
        self.assertEqual(llm.extract_json(raw), {"a": 1})

    def test_bare_json(self):
        self.assertEqual(llm.extract_json('{"b": 2}'), {"b": 2})

    def test_invalid_raises(self):
        with self.assertRaises(RuntimeError):
            llm.extract_json("not json at all")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dotmd_parser.llm'`

- [ ] **Step 3: Write `llm.py`**

```python
# src/dotmd_parser/llm.py
"""Shared LLM plumbing (stdlib-only) used by analyze.py and ontology.py."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 4096


def load_dotenv(env_path: str | Path | None = None) -> None:
    """Read a .env file and export its keys to os.environ (without overriding)."""
    path = Path(env_path) if env_path else Path.cwd() / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_prompt_template(name: str) -> str:
    return (
        resources.files("dotmd_parser.templates.prompts")
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
    )


def call_claude(
    prompt: str,
    system: str,
    api_key: str,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — URL is constant
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Claude API error {e.code}: {e.read().decode('utf-8', 'replace')}"
        ) from e
    return body["content"][0]["text"]


def extract_json(raw: str) -> dict:
    """Parse a Claude reply: prefer a fenced ```json block, fall back to raw body."""
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    payload = match.group(1) if match else raw.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude response was not valid JSON:\n{raw}") from e
```

- [ ] **Step 4: Point `analyze.py` at `llm.py` (behavior-preserving)**

In `src/dotmd_parser/analyze.py`, replace the local `load_dotenv`, `_load_prompt_template`, `_call_claude`, and the inline constants with re-exports/delegations. Keep the old private names as aliases so nothing else breaks:

```python
# near the top of analyze.py, after existing imports
from dotmd_parser.llm import (
    CLAUDE_API_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    call_claude as _call_claude,
    extract_json as _extract_json,
    load_dotenv,
    load_prompt_template as _load_prompt_template,
)
```

Then **delete** the now-duplicated definitions in analyze.py: the `CLAUDE_API_URL`/`DEFAULT_MODEL`/`DEFAULT_MAX_TOKENS` constants, `load_dotenv`, `_load_prompt_template`, `_call_claude`. In `analyze_dependencies`, replace the inline `re.search(...)`/`json.loads(...)` block with `parsed = _extract_json(raw)`.

- [ ] **Step 5: Run tests to verify pass (new + regression)**

Run: `python -m pytest tests/test_llm.py tests/test_analyze.py tests/test_cli_analyze_kind.py -q`
Expected: PASS (all). This proves the refactor preserved analyze behavior.

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/llm.py src/dotmd_parser/analyze.py tests/test_llm.py
git commit --no-gpg-sign -m "refactor: extract shared LLM plumbing into llm.py"
```

---

### Task 2: IR マージ (`merge_ontology`)

**Files:**
- Create: `src/dotmd_parser/ontology.py` (この Task で新規作成、以降のタスクで追記)
- Test: `tests/test_ontology.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `EMPTY_ELEMENTS: dict` — 抽出JSONの空テンプレ（全キーを空listに）
  - `merge_ontology(partials: list[dict], meta: dict) -> dict`
    - `partials`: `[{"source": str, "elements": <extraction json>}]`
    - 返り値: 上記 IR dict。全要素の `provenance` に `source` を付与・集約。
  - 正規化キー: `_norm(name: str) -> str`（`name.strip().lower()`）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ontology.py
import unittest
from dotmd_parser import ontology as O


def _meta():
    return {"namespace": "https://ex.org/o#", "prefix": "ex", "domain": "demo",
            "built_from": "corpus/", "source_docs": [], "generated_by": "test"}


class TestMerge(unittest.TestCase):
    def test_dedup_and_provenance(self):
        partials = [
            {"source": "a.md", "elements": {
                "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
                "object_properties": [], "datatype_properties": [], "vocabularies": [],
                "invariants": [], "conflicts": [], "open_questions": []}},
            {"source": "b.md", "elements": {
                "classes": [{"name": "application", "label_ja": "申請", "domain_group": "取引"}],
                "object_properties": [], "datatype_properties": [], "vocabularies": [],
                "invariants": [], "conflicts": [], "open_questions": []}},
        ]
        ir = O.merge_ontology(partials, _meta())
        self.assertEqual(len(ir["classes"]), 1)                      # deduped by normalized name
        self.assertEqual(ir["classes"][0]["provenance"], ["a.md", "b.md"])

    def test_conflict_flagged_not_resolved(self):
        partials = [
            {"source": "a.md", "elements": {"classes": [], "datatype_properties": [
                {"name": "feeRate", "domain": "Application", "type": "decimal",
                 "label_ja": "手数料率", "enum": None}],
                "object_properties": [], "vocabularies": [], "invariants": [],
                "conflicts": [], "open_questions": []}},
            {"source": "b.md", "elements": {"classes": [], "datatype_properties": [
                {"name": "feeRate", "domain": "Application", "type": "string",
                 "label_ja": "手数料率", "enum": None}],
                "object_properties": [], "vocabularies": [], "invariants": [],
                "conflicts": [], "open_questions": []}},
        ]
        ir = O.merge_ontology(partials, _meta())
        self.assertEqual(len(ir["datatype_properties"]), 1)
        self.assertEqual(ir["datatype_properties"][0]["type"], "decimal")  # first-seen wins
        self.assertTrue(any(c["kind"] == "naming" for c in ir["conflicts"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dotmd_parser.ontology'`

- [ ] **Step 3: Write `ontology.py` (module head + merge)**

```python
# src/dotmd_parser/ontology.py
"""dotmd-parser — text-first domain ontology construction (v1: extraction core).

Scans a folder of markdown, asks Claude to extract ontology elements per
document (map), merges them by normalized name (reduce), and emits a canonical
IR (ontology.yml) plus Turtle (.ttl) and a §5-style design markdown.
"""
from __future__ import annotations

from pathlib import Path

ELEMENT_KEYS = (
    "classes", "datatype_properties", "object_properties",
    "vocabularies", "invariants", "conflicts", "open_questions",
)
EMPTY_ELEMENTS: dict = {k: [] for k in ELEMENT_KEYS}

VALID_TYPES = {"string", "decimal", "integer", "date", "dateTime", "boolean"}


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _add_prov(item: dict, source: str) -> dict:
    prov = list(item.get("provenance") or [])
    if source and source not in prov:
        prov.append(source)
    out = dict(item)
    out["provenance"] = prov
    return out


def _merge_named(bucket: dict, items: list[dict], source: str,
                 key_fields: tuple[str, ...], conflicts: list[dict]) -> None:
    """Merge `items` into `bucket` keyed by normalized name; flag field conflicts."""
    for raw in items:
        key = _norm(raw.get("name", ""))
        if not key:
            continue
        if key not in bucket:
            bucket[key] = _add_prov(raw, source)
            continue
        existing = bucket[key]
        for f in key_fields:
            if raw.get(f) is not None and existing.get(f) is not None and raw[f] != existing[f]:
                conflicts.append({
                    "kind": "naming",
                    "detail": f'{raw["name"]}.{f}: "{existing[f]}" vs "{raw[f]}"',
                    "provenance": sorted({*existing.get("provenance", []), source}),
                })
        # first-seen wins for representative values; accumulate provenance
        prov = existing["provenance"]
        if source not in prov:
            prov.append(source)


def merge_ontology(partials: list[dict], meta: dict) -> dict:
    classes: dict[str, dict] = {}
    dprops: dict[str, dict] = {}
    oprops: dict[str, dict] = {}
    vocabs: dict[str, dict] = {}
    invariants: list[dict] = []
    conflicts: list[dict] = []
    open_qs: list[dict] = []

    for part in partials:
        source = part.get("source", "")
        el = {**EMPTY_ELEMENTS, **(part.get("elements") or {})}
        _merge_named(classes, el["classes"], source, ("label_ja", "domain_group"), conflicts)
        _merge_named(dprops, el["datatype_properties"], source,
                     ("domain", "type", "enum"), conflicts)
        _merge_named(oprops, el["object_properties"], source,
                     ("from", "to", "cardinality"), conflicts)
        _merge_named(vocabs, el["vocabularies"], source, ("values",), conflicts)
        for inv in el["invariants"]:
            invariants.append(_add_prov(inv, source))
        for c in el["conflicts"]:
            conflicts.append(_add_prov(c, source))
        for q in el["open_questions"]:
            open_qs.append(_add_prov(q, source))

    return {
        "meta": meta,
        "classes": sorted(classes.values(), key=lambda x: x["name"]),
        "datatype_properties": sorted(dprops.values(), key=lambda x: (x.get("domain", ""), x["name"])),
        "object_properties": sorted(oprops.values(), key=lambda x: x["name"]),
        "vocabularies": sorted(vocabs.values(), key=lambda x: x["name"]),
        "invariants": invariants,
        "conflicts": conflicts,
        "open_questions": open_qs,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ontology.py tests/test_ontology.py
git commit --no-gpg-sign -m "feat: ontology IR merge (dedup + provenance + conflict flag)"
```

---

### Task 3: 抽出 (`extract_ontology`) + プロンプト

**Files:**
- Create: `src/dotmd_parser/templates/prompts/extract-ontology.md`
- Modify: `src/dotmd_parser/ontology.py` (関数追記)
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Consumes: `dotmd_parser.analyze.scan_documents`, `dotmd_parser.llm.{call_claude, load_prompt_template, extract_json, DEFAULT_MODEL}`
- Produces:
  - `extract_ontology(directory, api_key=None, extensions=None, model=None, *, caller=None) -> list[dict]`
    - 返り値 partials: `[{"source": rel, "elements": <parsed extraction json>}]`
    - `caller(prompt: str, system: str, model: str) -> str` を渡すと API を呼ばない。
    - 未知型は `string` へ正規化。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
import json, tempfile
from pathlib import Path


class TestExtract(unittest.TestCase):
    def test_extract_with_caller(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "d.md").write_text("申請は会員が出す。", encoding="utf-8")

        def fake(prompt, system, model):
            body = {"classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
                    "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                             "type": "money", "label_ja": "手数料率", "enum": None}],
                    "object_properties": [], "vocabularies": [], "invariants": [],
                    "conflicts": [], "open_questions": []}
            return "```json\n" + json.dumps(body) + "\n```"

        partials = O.extract_ontology(root, caller=fake)
        self.assertEqual(partials[0]["source"], "d.md")
        # unknown type normalized to string
        self.assertEqual(partials[0]["elements"]["datatype_properties"][0]["type"], "string")
        tmp.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestExtract -q`
Expected: FAIL — `AttributeError: module 'dotmd_parser.ontology' has no attribute 'extract_ontology'`

- [ ] **Step 3: Write the prompt template**

```markdown
<!-- src/dotmd_parser/templates/prompts/extract-ontology.md -->
You extract a formal domain ontology from a single analysis document.

Read the document below and identify ontology elements it states or implies:
classes (entities), datatype properties, object properties (typed relations
between classes), controlled vocabularies (enums), invariants (identities /
business rules explicitly written), conflicts (contradictions the text itself
notes), and open questions.

## Rules
- Only extract what the document supports. Do NOT invent classes or relations.
- Class / property `name`: English PascalCase (class) / camelCase (property).
- `label_ja`: the Japanese term as written.
- datatype `type` ∈ {string, decimal, integer, date, dateTime, boolean}.
- object property `cardinality`: one of `1:1`, `多:1`, `1:多`, `1:0..1`, `1:0..*`, `多:0..1`, `多:多`.
- `characteristics` ⊆ {Functional, InverseFunctional, Symmetric, Transitive}.
- `enum` on a datatype property = the `name` of a vocabulary in this same output, else null.
- Do NOT output provenance — the caller attaches the source path.

## Document
### {{doc_path}}
```
{{doc_content}}
```

## Output format (JSON)
Return **only** this JSON, no prose outside the block. Empty arrays are fine.

```json
{
  "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
  "datatype_properties": [{"name": "feeRate", "domain": "Application", "type": "decimal", "label_ja": "手数料率", "enum": null}],
  "object_properties": [{"name": "evaluatedBy", "from": "Application", "to": "CreditAssessment", "cardinality": "1:1", "characteristics": ["Functional"], "note": "鎖"}],
  "vocabularies": [{"name": "applicationStatus", "values": ["買取成立", "謝絶"]}],
  "invariants": [{"id": "roas-identity", "statement": "ROAS = 申請件数単価 ÷ 申請CPA", "kind": "identity"}],
  "conflicts": [{"kind": "contradiction", "detail": "審査CV(681) < 申請CV(1290)"}],
  "open_questions": [{"text": "審査CVの正確な定義"}]
}
```
```

- [ ] **Step 4: Add `extract_ontology` to `ontology.py`**

```python
# add imports at top of ontology.py
import os

from dotmd_parser.analyze import scan_documents
from dotmd_parser import llm


def _normalize_types(elements: dict) -> dict:
    out = {**EMPTY_ELEMENTS, **(elements or {})}
    for dp in out["datatype_properties"]:
        if dp.get("type") not in VALID_TYPES:
            dp["_type_warning"] = dp.get("type")
            dp["type"] = "string"
    return out


def extract_ontology(directory, api_key=None, extensions=None, model=None, *, caller=None):
    """Map step: per-document LLM extraction. Returns partials with source paths."""
    if extensions is None:
        extensions = [".md", ".txt"]
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or export it, "
            "or use --plan for the no-API-key host-agent path."
        )
    resolved_model = model or os.environ.get("CLAUDE_MODEL", llm.DEFAULT_MODEL)
    template = llm.load_prompt_template("extract-ontology")

    partials: list[dict] = []
    for doc in scan_documents(directory, extensions=extensions):
        prompt = (template
                  .replace("{{doc_path}}", doc["path"])
                  .replace("{{doc_content}}", doc["content"]))
        first, _, rest = prompt.partition("\n")
        system = first.strip() or "You extract a domain ontology."
        user = rest.strip() or prompt
        raw = caller(user, system, resolved_model) if caller else llm.call_claude(
            user, system, api_key, resolved_model)
        elements = _normalize_types(llm.extract_json(raw))
        partials.append({"source": doc["path"], "elements": elements})
    return partials
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py::TestExtract -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/ontology.py src/dotmd_parser/templates/prompts/extract-ontology.md tests/test_ontology.py
git commit --no-gpg-sign -m "feat: per-document ontology extraction with prompt template"
```

---

### Task 4: `emit_yaml`（決定的 IR シリアライズ）

**Files:**
- Modify: `src/dotmd_parser/ontology.py`
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Consumes: IR dict (Task 2)
- Produces: `emit_yaml(ir: dict) -> str` — stdlib のみのハンドロール YAML（block style・double-quoted scalars）。同入力→同出力。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
class TestEmitYaml(unittest.TestCase):
    def _ir(self):
        return O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
            "datatype_properties": [], "object_properties": [], "vocabularies": [],
            "invariants": [], "conflicts": [], "open_questions": []}}], _meta())

    def test_deterministic_and_contains_class(self):
        y1 = O.emit_yaml(self._ir())
        y2 = O.emit_yaml(self._ir())
        self.assertEqual(y1, y2)                 # deterministic
        self.assertIn('name: "Application"', y1)
        self.assertIn("classes:", y1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestEmitYaml -q`
Expected: FAIL — `AttributeError: ... has no attribute 'emit_yaml'`

- [ ] **Step 3: Implement `emit_yaml`**

```python
# add to ontology.py
def _yq(value) -> str:
    """Quote a scalar for YAML (double-quoted, escaped). None -> null."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _yaml_list_field(values: list) -> str:
    return "[" + ", ".join(_yq(v) for v in values) + "]"


def emit_yaml(ir: dict) -> str:
    lines: list[str] = ["# ontology.yml — dotmd auto-constructed (v1, text-first)"]
    m = ir["meta"]
    lines.append("meta:")
    for k in ("namespace", "prefix", "domain", "built_from", "generated_by"):
        lines.append(f"  {k}: {_yq(m.get(k))}")
    lines.append(f"  source_docs: {_yaml_list_field(m.get('source_docs', []))}")

    def block(section: str, rows: list[dict], fields: list[str]) -> None:
        lines.append(f"{section}:")
        if not rows:
            lines[-1] = f"{section}: []"
            return
        for row in rows:
            first = True
            for f in fields:
                if f not in row:
                    continue
                val = row[f]
                rendered = _yaml_list_field(val) if isinstance(val, list) else _yq(val)
                prefix = "  - " if first else "    "
                lines.append(f"{prefix}{f}: {rendered}")
                first = False

    block("classes", ir["classes"], ["name", "label_ja", "domain_group", "provenance"])
    block("datatype_properties", ir["datatype_properties"],
          ["name", "domain", "type", "label_ja", "enum", "provenance"])
    block("object_properties", ir["object_properties"],
          ["name", "from", "to", "cardinality", "characteristics", "note", "provenance"])
    block("vocabularies", ir["vocabularies"], ["name", "values", "provenance"])
    block("invariants", ir["invariants"], ["id", "statement", "kind", "provenance"])
    block("conflicts", ir["conflicts"], ["kind", "detail", "provenance"])
    block("open_questions", ir["open_questions"], ["text", "provenance"])
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py::TestEmitYaml -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ontology.py tests/test_ontology.py
git commit --no-gpg-sign -m "feat: deterministic ontology.yml emitter"
```

---

### Task 5: `emit_ttl`（Turtle / OWL-lite）+ 型マップ

**Files:**
- Modify: `src/dotmd_parser/ontology.py`
- Modify: `pyproject.toml` (`rdf` extra 追加)
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Consumes: IR dict
- Produces: `emit_ttl(ir: dict) -> str` — Turtle テキスト。型マップ `XSD_MAP`。カーディナリティは `<prefix>:cardinality` 注釈で無損失。

- [ ] **Step 1: Add the `rdf` extra to pyproject.toml**

In `pyproject.toml` under `[project.optional-dependencies]`, add:

```toml
rdf = ["rdflib>=7.0"]
```

and add `"rdflib>=7.0"` to the existing `all = [...]` list.

- [ ] **Step 2: Write the failing test**

```python
# append to tests/test_ontology.py
class TestEmitTtl(unittest.TestCase):
    def _ir(self):
        return O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                        {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
            "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                     "type": "decimal", "label_ja": "手数料率", "enum": None}],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": "鎖"}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())

    def test_parses_with_rdflib(self):
        try:
            import rdflib  # noqa: F401
        except ImportError:
            self.skipTest("rdflib not installed")
        from rdflib import Graph
        g = Graph()
        g.parse(data=O.emit_ttl(self._ir()), format="turtle")
        # 2 classes present as owl:Class
        from rdflib.namespace import OWL, RDF
        classes = set(g.subjects(RDF.type, OWL.Class))
        self.assertEqual(len(classes), 2)

    def test_cardinality_annotation_present(self):
        self.assertIn('ex:cardinality "1:1"', O.emit_ttl(self._ir()))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestEmitTtl -q`
Expected: FAIL — `AttributeError: ... has no attribute 'emit_ttl'`

- [ ] **Step 4: Implement `emit_ttl`**

```python
# add to ontology.py
XSD_MAP = {"string": "xsd:string", "decimal": "xsd:decimal", "integer": "xsd:integer",
           "date": "xsd:date", "dateTime": "xsd:dateTime", "boolean": "xsd:boolean"}
CHAR_MAP = {"Functional": "owl:FunctionalProperty",
            "InverseFunctional": "owl:InverseFunctionalProperty",
            "Symmetric": "owl:SymmetricProperty", "Transitive": "owl:TransitiveProperty"}


def _ttl_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def emit_ttl(ir: dict) -> str:
    m = ir["meta"]
    p = m["prefix"]
    lines = [
        f"@prefix {p}: <{m['namespace']}> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "",
    ]

    def prov(item, indent="    "):
        for src in item.get("provenance", []):
            lines.append(f"{indent}{p}:sourceDoc {_ttl_str(src)} ;")

    for c in ir["classes"]:
        lines.append(f"{p}:{c['name']} a owl:Class ;")
        if c.get("label_ja"):
            lines.append(f'    rdfs:label {_ttl_str(c["label_ja"])}@ja ;')
        prov(c)
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")

    for dp in ir["datatype_properties"]:
        lines.append(f"{p}:{dp['name']} a owl:DatatypeProperty ;")
        if dp.get("domain"):
            lines.append(f"    rdfs:domain {p}:{dp['domain']} ;")
        lines.append(f"    rdfs:range {XSD_MAP.get(dp.get('type'), 'xsd:string')} ;")
        if dp.get("label_ja"):
            lines.append(f'    rdfs:label {_ttl_str(dp["label_ja"])}@ja ;')
        if dp.get("enum"):
            lines.append(f"    {p}:usesVocabulary {p}:{dp['enum']} ;")
        prov(dp)
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")

    for op in ir["object_properties"]:
        types = ["owl:ObjectProperty"] + [CHAR_MAP[c] for c in op.get("characteristics", [])
                                          if c in CHAR_MAP]
        lines.append(f"{p}:{op['name']} a {', '.join(types)} ;")
        if op.get("from"):
            lines.append(f"    rdfs:domain {p}:{op['from']} ;")
        if op.get("to"):
            lines.append(f"    rdfs:range {p}:{op['to']} ;")
        if op.get("cardinality"):
            lines.append(f"    {p}:cardinality {_ttl_str(op['cardinality'])} ;")
        prov(op)
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")

    for v in ir["vocabularies"]:
        lines.append(f"{p}:{v['name']} a skos:ConceptScheme .")
        for i, val in enumerate(v.get("values", [])):
            lines.append(f"{p}:{v['name']}_{i} a skos:Concept ; "
                         f"skos:prefLabel {_ttl_str(val)} ; skos:inScheme {p}:{v['name']} .")
        lines.append("")

    for inv in ir["invariants"]:
        lines.append(f"{p}:{inv['id']} a {p}:Invariant ;")
        lines.append(f"    rdfs:comment {_ttl_str(inv['statement'])} ;")
        prov(inv)
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Install rdflib and run test**

Run: `pip install 'rdflib>=7.0' && python -m pytest tests/test_ontology.py::TestEmitTtl -q`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/ontology.py pyproject.toml tests/test_ontology.py
git commit --no-gpg-sign -m "feat: Turtle emitter (OWL-lite) + rdf extra"
```

---

### Task 6: `emit_design_md`（§5 形式）

**Files:**
- Modify: `src/dotmd_parser/ontology.py`
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Produces: `emit_design_md(ir: dict) -> str` — ドメイン別クラス表 / クラス別データ型 prop / objprop 表 / enum / 不変条件 / 矛盾・未解決質問。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
class TestEmitDesignMd(unittest.TestCase):
    def test_tables_present(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
            "datatype_properties": [{"name": "feeRate", "domain": "Application",
                                     "type": "decimal", "label_ja": "手数料率", "enum": None}],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": "鎖"}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())
        md = O.emit_design_md(ir)
        self.assertIn("## クラス", md)
        self.assertIn("Application", md)
        self.assertIn("evaluatedBy", md)
        self.assertIn("| Application → CreditAssessment | 1:1 |", md)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestEmitDesignMd -q`
Expected: FAIL — no attribute `emit_design_md`

- [ ] **Step 3: Implement `emit_design_md`**

```python
# add to ontology.py
def emit_design_md(ir: dict) -> str:
    m = ir["meta"]
    out = [f"# {m.get('domain','')} ドメインオントロジー（自動生成）", "",
           f"- namespace: `{m['namespace']}` / prefix: `{m['prefix']}`",
           f"- built_from: `{m.get('built_from','')}`",
           f"- source_docs: {len(m.get('source_docs', []))} 件", ""]

    # classes grouped by domain_group
    out += ["## クラス", "", "| ドメイン | クラス | 日本語 |", "|---|---|---|"]
    for c in ir["classes"]:
        out.append(f"| {c.get('domain_group','')} | {c['name']} | {c.get('label_ja','')} |")
    out.append("")

    # datatype properties by class
    out += ["## データ型プロパティ", "", "| クラス | プロパティ | 型 | 日本語 | enum |", "|---|---|---|---|---|"]
    for dp in ir["datatype_properties"]:
        out.append(f"| {dp.get('domain','')} | {dp['name']} | {dp.get('type','')} | "
                   f"{dp.get('label_ja','')} | {dp.get('enum') or ''} |")
    out.append("")

    # object properties
    out += ["## オブジェクトプロパティ", "",
            "| 関係 | From → To | カーディナリティ | characteristics | 備考 |",
            "|---|---|---|---|---|"]
    for op in ir["object_properties"]:
        out.append(f"| {op['name']} | {op.get('from','')} → {op.get('to','')} | "
                   f"{op.get('cardinality','')} | {', '.join(op.get('characteristics', []))} | "
                   f"{op.get('note','')} |")
    out.append("")

    if ir["vocabularies"]:
        out += ["## 統制語彙", ""]
        for v in ir["vocabularies"]:
            out.append(f"- **{v['name']}**: {' | '.join(v.get('values', []))}")
        out.append("")

    if ir["invariants"]:
        out += ["## 不変条件", ""]
        for inv in ir["invariants"]:
            out.append(f"- `{inv['id']}` ({inv.get('kind','')}): {inv['statement']}")
        out.append("")

    if ir["conflicts"] or ir["open_questions"]:
        out += ["## 矛盾・未解決質問", ""]
        for c in ir["conflicts"]:
            out.append(f"- ⚠️ [{c.get('kind','')}] {c.get('detail','')}")
        for q in ir["open_questions"]:
            out.append(f"- ❓ {q.get('text','')}")
        out.append("")

    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py::TestEmitDesignMd -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ontology.py tests/test_ontology.py
git commit --no-gpg-sign -m "feat: §5-style design markdown emitter"
```

---

### Task 7: `validate_ontology`（決定的ゲート）

**Files:**
- Modify: `src/dotmd_parser/ontology.py`
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Produces: `validate_ontology(ir: dict, ttl: str | None = None) -> dict` → `{"errors": list[str], "warnings": list[str]}`
  - 構造ゲート常時（stdlib）。`ttl` が渡され `rdflib` があれば Turtle parse も実施。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
class TestValidate(unittest.TestCase):
    def test_dangling_and_cardinality(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}],
            "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "Missing", "cardinality": "??",
                                   "characteristics": ["Nope"], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())
        report = O.validate_ontology(ir)
        joined = " ".join(report["errors"])
        self.assertIn("Missing", joined)          # dangling range
        self.assertIn("??", joined)               # bad cardinality
        self.assertIn("Nope", joined)             # unknown characteristic

    def test_clean_ir_passes(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                        {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
            "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}], _meta())
        self.assertEqual(O.validate_ontology(ir)["errors"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestValidate -q`
Expected: FAIL — no attribute `validate_ontology`

- [ ] **Step 3: Implement `validate_ontology`**

```python
# add to ontology.py
import re as _re

_CARD_RE = _re.compile(r"^(1|多|N|M|\d+)(:(0\.\.1|0\.\.\*|1|多|N|M|\d+))?$")


def validate_ontology(ir: dict, ttl: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    class_names = {c["name"] for c in ir["classes"]}
    vocab_names = {v["name"] for v in ir["vocabularies"]}

    # duplicate names
    def dup_check(rows, kind):
        seen = set()
        for r in rows:
            if r["name"] in seen:
                errors.append(f"duplicate {kind} name: {r['name']}")
            seen.add(r["name"])
    dup_check(ir["classes"], "class")
    dup_check(ir["datatype_properties"], "datatype property")
    dup_check(ir["object_properties"], "object property")

    for dp in ir["datatype_properties"]:
        if dp.get("domain") and dp["domain"] not in class_names:
            errors.append(f"datatype property {dp['name']} has dangling domain: {dp['domain']}")
        if dp.get("enum") and dp["enum"] not in vocab_names:
            errors.append(f"datatype property {dp['name']} references unknown enum: {dp['enum']}")
        if dp.get("_type_warning"):
            warnings.append(f"datatype property {dp['name']} had unknown type "
                            f"'{dp['_type_warning']}', coerced to string")

    for op in ir["object_properties"]:
        for side in ("from", "to"):
            if op.get(side) and op[side] not in class_names:
                errors.append(f"object property {op['name']} has dangling {side}: {op[side]}")
        if op.get("cardinality") and not _CARD_RE.match(op["cardinality"]):
            errors.append(f"object property {op['name']} has bad cardinality: {op['cardinality']}")
        for ch in op.get("characteristics", []):
            if ch not in CHAR_MAP:
                errors.append(f"object property {op['name']} has unknown characteristic: {ch}")

    for row in [*ir["classes"], *ir["datatype_properties"], *ir["object_properties"]]:
        if not row.get("provenance"):
            warnings.append(f"{row['name']} has no provenance")

    if ttl is not None:
        try:
            from rdflib import Graph
            Graph().parse(data=ttl, format="turtle")
        except ImportError:
            warnings.append("rdflib not installed; skipped Turtle syntax validation")
        except Exception as e:  # noqa: BLE001 — surface any parse error as a gate failure
            errors.append(f"Turtle parse error: {e}")

    return {"errors": errors, "warnings": warnings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py::TestValidate -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ontology.py tests/test_ontology.py
git commit --no-gpg-sign -m "feat: deterministic ontology validation gate"
```

---

### Task 8: オーケストレータ + host-agent プラン + apply-from + cost

**Files:**
- Modify: `src/dotmd_parser/ontology.py`
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Consumes: 全 emit_* / validate / extract / merge
- Produces:
  - `infer_meta(directory, namespace=None, prefix=None, domain=None, source_docs=None) -> (dict, list[str])` → (meta, warnings)。未指定は folder 名から `https://example.org/<slug>#` を推定。
  - `write_ontology(ir, out_dir, emit=("yml","ttl","md")) -> list[str]` → 書いたパス。
  - `build_ontology(directory, out_dir=None, emit=("yml","ttl","md"), *, caller=None, **kw) -> dict` → `{"ir","report","written","meta_warnings"}`。
  - `format_host_agent_plan(directory, extensions=None) -> str`（extract プロンプト同梱、`--apply-from` 手順）。
  - `apply_ontology_from_file(directory, json_path, out_dir=None, emit=(...)) -> dict`（事前抽出 JSON=partials or 単一 elements を受理）。
  - `estimate_cost(directory, model=None, extensions=None) -> dict`（analyze の `estimate_cost` を流用: `from dotmd_parser.analyze import estimate_cost`）。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
class TestOrchestrator(unittest.TestCase):
    def test_build_and_write(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "d.md").write_text("申請は会員が出し、審査される。", encoding="utf-8")

        def fake(prompt, system, model):
            body = {"classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                                {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
                    "datatype_properties": [],
                    "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                           "to": "CreditAssessment", "cardinality": "1:1",
                                           "characteristics": ["Functional"], "note": "鎖"}],
                    "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}
            return "```json\n" + json.dumps(body) + "\n```"

        res = O.build_ontology(root, caller=fake)
        self.assertEqual(res["report"]["errors"], [])
        names = {Path(w).name for w in res["written"]}
        self.assertEqual(names, {"ontology.yml", "ontology.ttl", "ontology-design.md"})
        self.assertTrue((root / "ontology" / "ontology.yml").exists())
        tmp.cleanup()

    def test_host_agent_plan_mentions_apply_from(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "d.md").write_text("x", encoding="utf-8")
        plan = O.format_host_agent_plan(root)
        self.assertIn("--apply-from", plan)
        tmp.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestOrchestrator -q`
Expected: FAIL — no attribute `build_ontology`

- [ ] **Step 3: Implement orchestrator functions**

```python
# add to ontology.py
import re as _re2
from dotmd_parser.analyze import estimate_cost  # reuse analyze's cost model

_EMIT_FILE = {"yml": "ontology.yml", "ttl": "ontology.ttl", "md": "ontology-design.md"}


def _slug(name: str) -> str:
    return _re2.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ontology"


def infer_meta(directory, namespace=None, prefix=None, domain=None, source_docs=None):
    warnings: list[str] = []
    base = Path(directory).resolve()
    slug = _slug(base.name)
    if not namespace:
        namespace = f"https://example.org/{slug}#"
        warnings.append(f"no --namespace given; using placeholder {namespace}")
    if not prefix:
        prefix = slug.replace("-", "")[:8] or "onto"
    meta = {"namespace": namespace, "prefix": prefix, "domain": domain or base.name,
            "built_from": base.name, "source_docs": source_docs or [],
            "generated_by": "dotmd-parser ontology v1"}
    return meta, warnings


def write_ontology(ir, out_dir, emit=("yml", "ttl", "md")) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderers = {"yml": emit_yaml, "ttl": emit_ttl, "md": emit_design_md}
    written: list[str] = []
    for kind in ("yml", "ttl", "md"):
        if kind in emit:
            path = out / _EMIT_FILE[kind]
            path.write_text(renderers[kind](ir), encoding="utf-8")
            written.append(str(path))
    return written


def build_ontology(directory, out_dir=None, emit=("yml", "ttl", "md"), *,
                   namespace=None, prefix=None, domain=None,
                   api_key=None, extensions=None, model=None, caller=None) -> dict:
    partials = extract_ontology(directory, api_key=api_key, extensions=extensions,
                                model=model, caller=caller)
    source_docs = [p["source"] for p in partials]
    meta, meta_warnings = infer_meta(directory, namespace, prefix, domain, source_docs)
    ir = merge_ontology(partials, meta)
    ttl = emit_ttl(ir)
    report = validate_ontology(ir, ttl=ttl)
    out_dir = out_dir or (Path(directory).resolve() / "ontology")
    written = write_ontology(ir, out_dir, emit=emit)
    return {"ir": ir, "report": report, "written": written, "meta_warnings": meta_warnings}


def format_host_agent_plan(directory, extensions=None) -> str:
    if extensions is None:
        extensions = [".md", ".txt"]
    root = Path(directory).resolve()
    docs = [fp.relative_to(root).as_posix()
            for ext in extensions for fp in sorted(root.rglob(f"*{ext}"))
            if not any(part.startswith(".") for part in fp.relative_to(root).parts)]
    template = llm.load_prompt_template("extract-ontology")
    files = "\n".join(f"- `{d}`" for d in docs)
    return (
        "# dotmd-parser — ontology host-agent plan\n\n"
        f"Target: `{root}`  ({len(docs)} documents)\n\n"
        "For EACH document below, run the extraction task and collect the JSON "
        "objects into a list of `{\"source\": <path>, \"elements\": <json>}`. "
        "Save that list to `ontology.json`, then apply:\n\n"
        "```bash\n"
        f'dotmd-parser ontology "{root}" --apply-from ontology.json\n'
        "```\n\n"
        "## Documents\n\n" + files + "\n\n"
        "## Extraction task (per document)\n\n```\n" + template.strip() + "\n```\n"
    )


def apply_ontology_from_file(directory, json_path, out_dir=None,
                             emit=("yml", "ttl", "md"),
                             namespace=None, prefix=None, domain=None) -> dict:
    import json as _json
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"ontology JSON not found: {json_path}")
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {json_path}: {e}") from e
    # accept either a list of partials, or a single {"source","elements"} object
    partials = data if isinstance(data, list) else [data]
    partials = [{"source": p.get("source", "unknown"),
                 "elements": _normalize_types(p.get("elements", p))} for p in partials]
    source_docs = [p["source"] for p in partials]
    meta, meta_warnings = infer_meta(directory, namespace, prefix, domain, source_docs)
    ir = merge_ontology(partials, meta)
    report = validate_ontology(ir, ttl=emit_ttl(ir))
    out_dir = out_dir or (Path(directory).resolve() / "ontology")
    written = write_ontology(ir, out_dir, emit=emit)
    return {"ir": ir, "report": report, "written": written, "meta_warnings": meta_warnings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ontology.py::TestOrchestrator -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ontology.py tests/test_ontology.py
git commit --no-gpg-sign -m "feat: ontology orchestrator, host-agent plan, apply-from"
```

---

### Task 9: CLI `ontology` サブコマンド + 公開 API

**Files:**
- Modify: `src/dotmd_parser/cli.py` (`cmd_ontology` + argparse + `known_cmds`)
- Modify: `src/dotmd_parser/__init__.py` (公開エクスポート)
- Test: `tests/test_cli_ontology.py`

**Interfaces:**
- Consumes: `ontology.build_ontology / format_host_agent_plan / apply_ontology_from_file / estimate_cost`
- Produces: `dotmd-parser ontology <dir>` CLI（§9 のフラグ）。`--check` は errors ありで exit 1。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_ontology.py
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dotmd_parser.cli import run as cli_run


class TestCliOntology(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "d.md").write_text("申請→審査。", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_path_no_api(self):
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--plan"])
        self.assertEqual(cm.exception.code, 0)

    def test_apply_from_and_check(self):
        partials = [{"source": "d.md", "elements": {
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"},
                        {"name": "CreditAssessment", "label_ja": "審査", "domain_group": "取引"}],
            "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "CreditAssessment", "cardinality": "1:1",
                                   "characteristics": ["Functional"], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}]
        jp = self.root / "ontology.json"
        jp.write_text(json.dumps(partials), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--apply-from", str(jp), "--check"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue((self.root / "ontology" / "ontology.ttl").exists())

    def test_check_fails_on_dangling(self):
        partials = [{"source": "d.md", "elements": {
            "classes": [], "datatype_properties": [],
            "object_properties": [{"name": "evaluatedBy", "from": "Application",
                                   "to": "Missing", "cardinality": "1:1",
                                   "characteristics": [], "note": ""}],
            "vocabularies": [], "invariants": [], "conflicts": [], "open_questions": []}}]
        jp = self.root / "ontology.json"
        jp.write_text(json.dumps(partials), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli_run(["ontology", str(self.root), "--apply-from", str(jp), "--check"])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_ontology.py -q`
Expected: FAIL — argparse で `invalid choice: 'ontology'`（サブコマンド未定義）

- [ ] **Step 3: Add `cmd_ontology` and wiring to `cli.py`**

Add the imports near the other analyze imports at the top of `cli.py`:

```python
from dotmd_parser.ontology import (
    build_ontology as _build_ontology,
    format_host_agent_plan as _onto_plan,
    apply_ontology_from_file as _onto_apply_from,
    estimate_cost as _onto_estimate_cost,
)
from dotmd_parser.llm import load_dotenv as _onto_load_dotenv
```

Add the command handler (near `cmd_analyze`):

```python
def cmd_ontology(args: argparse.Namespace) -> int:
    """Construct a domain ontology from a folder of .md analysis docs."""
    extensions = None
    if args.ext:
        extensions = [(e if e.startswith(".") else f".{e}") for e in args.ext]
    emit = tuple(args.emit.split(",")) if args.emit else ("yml", "ttl", "md")

    if args.dry_run:
        est = _onto_estimate_cost(args.path, model=args.model, extensions=extensions)
        print(json.dumps(est, ensure_ascii=False, indent=2))
        return 0

    if args.plan:
        print(_onto_plan(args.path, extensions=extensions))
        return 0

    try:
        if args.apply_from:
            res = _onto_apply_from(args.path, args.apply_from, out_dir=args.out, emit=emit,
                                   namespace=args.namespace, prefix=args.prefix, domain=args.domain)
        else:
            _onto_load_dotenv()
            res = _build_ontology(args.path, out_dir=args.out, emit=emit,
                                  namespace=args.namespace, prefix=args.prefix,
                                  domain=args.domain, extensions=extensions, model=args.model)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        print("hint: use `--plan` for the no-API-key host-agent path.", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    for w in res["meta_warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    for w in res["report"]["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    print(f"Wrote {len(res['written'])} file(s):")
    for f in res["written"]:
        print(f"  {f}")
    ir = res["ir"]
    print(f"  classes={len(ir['classes'])} dprops={len(ir['datatype_properties'])} "
          f"oprops={len(ir['object_properties'])} vocabs={len(ir['vocabularies'])}")

    if res["report"]["errors"]:
        for e in res["report"]["errors"]:
            print(f"error: {e}", file=sys.stderr)
        if args.check:
            return 1
    return 0
```

Add the argparse block inside `_build_parser` (near the `analyze` block):

```python
    p_onto = sub.add_parser("ontology", help="Construct a domain ontology from .md docs")
    p_onto.add_argument("path", help="Directory to scan")
    p_onto.add_argument("--emit", help="Comma list of outputs: yml,ttl,md (default: all)")
    p_onto.add_argument("--out", help="Output dir (default: <path>/ontology)")
    p_onto.add_argument("--plan", action="store_true",
                        help="Emit a host-agent prompt pack (no API key needed)")
    p_onto.add_argument("--apply-from", metavar="JSON", dest="apply_from",
                        help="Apply a pre-computed extraction JSON (pairs with --plan)")
    p_onto.add_argument("--dry-run", action="store_true", help="Estimate cost only")
    p_onto.add_argument("--namespace", help="Ontology namespace IRI")
    p_onto.add_argument("--prefix", help="Ontology prefix")
    p_onto.add_argument("--domain", help="Human domain label")
    p_onto.add_argument("--model", help="Claude model id")
    p_onto.add_argument("--ext", action="append", help="Extension to include (repeatable)")
    p_onto.add_argument("--check", action="store_true",
                        help="Exit non-zero when validation finds errors (CI gate)")
    p_onto.set_defaults(func=cmd_ontology)
```

Add `"ontology"` to the `known_cmds` set in `run()`:

```python
    known_cmds = {"init", "index", "check", "affects", "deps", "digest", "tree", "resolve", "analyze", "inventory", "dotmd-index", "show", "plan", "ledger", "risk", "stability", "ontology"}
```

- [ ] **Step 4: Add exports to `__init__.py`**

Append after the analyze import block in `src/dotmd_parser/__init__.py`:

```python
from dotmd_parser.ontology import (
    build_ontology,
    merge_ontology,
    extract_ontology,
    emit_yaml,
    emit_ttl,
    emit_design_md,
    validate_ontology,
)
```

and add those names to `__all__`.

- [ ] **Step 5: Run tests to verify pass (new + full regression)**

Run: `python -m pytest tests/test_cli_ontology.py tests/test_ontology.py tests/test_analyze.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py src/dotmd_parser/__init__.py tests/test_cli_ontology.py
git commit --no-gpg-sign -m "feat: dotmd-parser ontology CLI subcommand + public API"
```

---

### Task 10: `--eval`（opt-in LLM ルーブリック採点）

**Files:**
- Modify: `src/dotmd_parser/ontology.py` (`eval_ontology`)
- Modify: `src/dotmd_parser/cli.py` (`--eval` フラグ配線)
- Create: `src/dotmd_parser/templates/prompts/eval-ontology.md`
- Test: `tests/test_ontology.py` (追記)

**Interfaces:**
- Produces: `eval_ontology(ir: dict, corpus_summary: str, model=None, api_key=None, *, caller=None) -> dict` → `{"coverage": 0..1, "faithfulness": 0..1, "notes": str}`。`caller` 注入可。

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ontology.py
class TestEval(unittest.TestCase):
    def test_eval_with_caller(self):
        ir = O.merge_ontology([{"source": "a.md", "elements": {**O.EMPTY_ELEMENTS,
            "classes": [{"name": "Application", "label_ja": "申請", "domain_group": "取引"}]}}], _meta())

        def fake(prompt, system, model):
            return '```json\n{"coverage": 0.8, "faithfulness": 0.9, "notes": "ok"}\n```'

        score = O.eval_ontology(ir, "corpus summary", caller=fake)
        self.assertEqual(score["coverage"], 0.8)
        self.assertEqual(score["faithfulness"], 0.9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ontology.py::TestEval -q`
Expected: FAIL — no attribute `eval_ontology`

- [ ] **Step 3: Write the eval prompt + function**

Create `src/dotmd_parser/templates/prompts/eval-ontology.md`:

```markdown
You score an auto-extracted domain ontology against its source corpus.

Given the ontology summary and a corpus summary, rate:
- coverage: fraction of important domain concepts/relations captured (0..1)
- faithfulness: fraction of ontology elements actually supported by the corpus (0..1)

## Ontology
{{ontology_summary}}

## Corpus summary
{{corpus_summary}}

## Output (JSON only)
```json
{"coverage": 0.0, "faithfulness": 0.0, "notes": "one sentence"}
```
```

Add to `ontology.py`:

```python
def eval_ontology(ir, corpus_summary, model=None, api_key=None, *, caller=None) -> dict:
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError("ANTHROPIC_API_KEY required for --eval (or inject a caller).")
    resolved_model = model or os.environ.get("CLAUDE_MODEL", llm.DEFAULT_MODEL)
    summary = (f"classes={[c['name'] for c in ir['classes']]}\n"
               f"object_properties={[o['name'] for o in ir['object_properties']]}\n"
               f"datatype_properties={[d['name'] for d in ir['datatype_properties']]}")
    template = llm.load_prompt_template("eval-ontology")
    prompt = (template.replace("{{ontology_summary}}", summary)
                      .replace("{{corpus_summary}}", corpus_summary))
    first, _, rest = prompt.partition("\n")
    raw = (caller(rest.strip(), first.strip(), resolved_model) if caller
           else llm.call_claude(rest.strip(), first.strip(), api_key, resolved_model))
    return llm.extract_json(raw)
```

- [ ] **Step 4: Wire `--eval` into `cmd_ontology`**

In `cli.py`, add the flag in the argparse block:

```python
    p_onto.add_argument("--eval", action="store_true", dest="do_eval",
                        help="Also run an LLM rubric score (needs API key)")
```

And in `cmd_ontology`, after printing counts and before the errors block, add:

```python
    if args.do_eval and not args.apply_from:
        from dotmd_parser.ontology import eval_ontology as _eval
        summary = f"{len(ir['classes'])} classes over docs: {ir['meta']['source_docs']}"
        try:
            score = _eval(ir, summary, model=args.model)
            print(f"eval: coverage={score.get('coverage')} "
                  f"faithfulness={score.get('faithfulness')} — {score.get('notes','')}")
        except (ValueError, RuntimeError) as e:
            print(f"warning: eval skipped: {e}", file=sys.stderr)
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_ontology.py::TestEval tests/test_cli_ontology.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/ontology.py src/dotmd_parser/cli.py src/dotmd_parser/templates/prompts/eval-ontology.md tests/test_ontology.py
git commit --no-gpg-sign -m "feat: opt-in --eval ontology rubric scoring"
```

---

### Task 11: 決定性の受け入れテスト + README + CHANGELOG

**Files:**
- Modify: `tests/test_ontology.py` (決定性テスト)
- Modify: `README.md` / `README.ja.md` (ontology サブコマンド節)
- Modify: `CHANGELOG.md`
- Test: 全スイート

**Interfaces:** なし（統合）

- [ ] **Step 1: Write the determinism acceptance test**

```python
# append to tests/test_ontology.py
class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        partials = [{"source": "a.md", "elements": {**O.EMPTY_ELEMENTS,
            "classes": [{"name": "B", "label_ja": "b", "domain_group": "g"},
                        {"name": "A", "label_ja": "a", "domain_group": "g"}]}}]
        m = {"namespace": "https://ex.org/o#", "prefix": "ex", "domain": "d",
             "built_from": "c", "source_docs": ["a.md"], "generated_by": "t"}
        ir1 = O.merge_ontology(partials, m)
        ir2 = O.merge_ontology(list(partials), dict(m))
        self.assertEqual(O.emit_yaml(ir1), O.emit_yaml(ir2))
        self.assertEqual(O.emit_ttl(ir1), O.emit_ttl(ir2))
        self.assertEqual(O.emit_design_md(ir1), O.emit_design_md(ir2))
        # classes sorted A before B regardless of input order
        self.assertEqual([c["name"] for c in ir1["classes"]], ["A", "B"])
```

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all). If any pre-existing test breaks, fix the regression before continuing.

- [ ] **Step 3: Document the subcommand in README.md and README.ja.md**

Add a section after the `analyze` docs describing:
- `dotmd-parser ontology <folder>` — build `<folder>/ontology/{ontology.yml,ontology.ttl,ontology-design.md}`
- `--plan` / `--apply-from` host-agent path (no API key)
- `--check` CI gate, `--emit`, `--namespace/--prefix/--domain`, `--eval`
- note: `pip install 'dotmd-parser[rdf]'` enables Turtle syntax validation

- [ ] **Step 4: Add a CHANGELOG entry**

Add under a new unreleased/next-version heading in `CHANGELOG.md`:
```markdown
### Added
- `ontology` subcommand: text-first domain ontology construction from a folder
  of .md analysis docs. Emits ontology.yml (canonical IR), ontology.ttl
  (OWL-lite), and a §5-style design markdown. Host-agent mode (`--plan` /
  `--apply-from`), deterministic output, structural validation gate (`--check`),
  optional Turtle validation via the `rdf` extra, opt-in `--eval` scoring.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_ontology.py README.md README.ja.md CHANGELOG.md
git commit --no-gpg-sign -m "test: determinism acceptance + docs for ontology subcommand"
```

---

## Self-Review

**1. Spec coverage:**
- §3 アーキ (llm.py 切り出し / ontology.py 各関数) → Task 1,2,3,8。✓
- §4 IR → Task 2 の IR dict。✓
- §5 抽出 + JSON スキーマ + プロンプト → Task 3。✓
- §6 マージ (dedup/provenance/衝突/暗黙生成なし) → Task 2。✓
- §7 emit (ttl/design-md/yml/型マップ/カーディナリティ注釈/enum=SKOS/不変条件=注釈) → Task 4,5,6。✓
- §8 検証 (構造ゲート/rdflib 任意/`--check`/`--eval`) → Task 7,9,10。✓
- §9 CLI 全フラグ → Task 9,10。✓
- §10 テスト方針 (merge/ttl/validate/design-md/型正規化/決定性/ラウンドトリップ) → Task 2–11。受入(freenance)は API 任意のため手動確認手順を §受入 に残す。
- §11 受け入れ基準 1–4 → Task 9,11 で自動検証。基準5(freenance)は API 必須のため手動。

**2. Placeholder scan:** 各コード step に実コードを記載。TODO/TBD 無し。✓

**3. Type consistency:**
- IR キー名は全 Task で `classes/datatype_properties/object_properties/vocabularies/invariants/conflicts/open_questions` に統一。✓
- `merge_ontology(partials, meta)` / partial 形状 `{"source","elements"}` は Task 2,3,8 で一致。✓
- `validate_ontology(ir, ttl=None)` は Task 7 定義・Task 8,9 で使用一致。✓
- `CHAR_MAP` は Task 5 定義、Task 7 validate で参照（Task 5 が Task 7 の前提 = 順序OK）。✓
- `_normalize_types` は Task 3 定義、Task 8 apply-from で再利用。✓

## 手動受け入れ確認（API キー必要・任意）

```bash
export ANTHROPIC_API_KEY=...
dotmd-parser ontology "~/Python_Programing/freee/G2Rec/g2rec_freee/freenance-ads-analysis" --check
# 期待: ontology/ に3成果物、design-md が手書き §5 に構造対応、クラス/objprop の主要要素を回収
# 2回実行して ontology.yml/ttl/md が完全一致（決定性）
```
```
