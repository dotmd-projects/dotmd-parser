# analyze include/ref Auto-Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `analyze` classify each detected dependency as `@include` (inline) or `@ref` (pointer) automatically — LLM-judged via a per-edge `kind`, with a deterministic cycle→ref guard (always) and an opt-in size→ref guard — so users no longer hand-downgrade pointers.

**Architecture:** All changes are inside `src/dotmd_parser/analyze.py` plus its bundled prompt template and the `analyze` CLI handler. `generate_directives` emits `@include`/`@ref` by `kind`; a new `_apply_directive_guards` demotes include→ref for cycles (hard) and oversize targets (opt-in) before directives are generated. `parser.py`/`index.py` are unchanged; `@ref` semantics already exist.

**Tech Stack:** Python 3 stdlib only, pytest, argparse.

## Global Constraints

- **stdlib only** — no third-party dependency. (PDF/docx extraction stays optional as today.)
- **Changes limited to** `src/dotmd_parser/analyze.py`, `src/dotmd_parser/templates/prompts/analyze-dependencies.md`, `src/dotmd_parser/cli.py` (the `analyze` flag), and docs. Do NOT modify `parser.py` or `index.py`.
- **`kind` field:** each edge may carry `"kind": "include" | "ref"`. **Unknown/missing `kind` normalizes to `"include"`** (backward compatible).
- **`shared_proposals` are always `@include`** (they are extracted shared fragments — not classified).
- **Deterministic guards (override the LLM), applied AFTER LLM kind, BEFORE directive generation:**
  - **cycle → ref (hard, always):** consider only `include` edges (since `@ref` does not recurse). Build the prospective include graph = existing include edges (from `build_index` of the directory, `deps` with `type=="include"`) ∪ new edges with `kind=="include"`. Add new include edges in sorted `(from, to)` order; any new edge whose addition would close a cycle (its `to` can already reach its `from`, or `from == to`) is demoted to `ref`. Only NEW edges are demoted; existing file directives are never rewritten.
  - **size → ref (opt-in):** when `max_include_bytes` is set, a `kind=="include"` edge whose target file exceeds that many bytes is demoted to `ref`. Default `None` (off). Unreadable/missing targets are skipped.
  - **fan-in is NOT used as a guard.**
- **Both paths classify:** the API path (`analyze`) and the no-API `--plan`/`--apply-from` path (host agent fills the JSON) both carry `kind` — `format_host_agent_plan`'s expected-output schema must include it.
- **Backward compatibility:** with no `kind` and no `--max-include-bytes`, behavior is identical to today (all `@include`); the binary-source `deps.yml` path and duplicate-suppression are unchanged.
- **Determinism:** guards must produce stable results (sorted edge processing).
- **Commit signing:** SSH signing key is not readable in this sandbox. Commit with `git -c commit.gpgsign=false commit -m "..."`.
- **Branch:** work on `feat/analyze-include-ref` (already created; spec commit `256decd` is its first commit). Do NOT branch off or switch.
- **Canonical test command:** `PYTHONPATH=src ./.venv/bin/python -m pytest` (the editable install is stale — use this exact form; never bare `python`/`pytest`).
- **Reference — current `analyze.py` shapes you edit:**
  - `analyze_dependencies(...)` returns `{"documents", "edges", "shared_proposals"}`; `edges` is `parsed.get("edges", [])` verbatim, so any `kind` Claude returns already passes through unchanged.
  - `generate_directives(analysis) -> dict[str, list[str]]` currently hardcodes `f"@include {edge['to']}"`.
  - `apply_directives(directory, directives)` prepends new directive lines to text files and already dedups against existing lines starting with `("@include ", "@delegate ", "@ref ")`.
  - `apply_analysis(directory, analysis)` calls `generate_directives` → `apply_directives`, then builds `binary_deps` for non-text sources via `d.removeprefix("@include ")` and writes `deps.yml`.
  - `apply_analysis_from_file(directory, json_path)` loads JSON, `setdefault`s `documents/edges/shared_proposals`, then calls `apply_analysis`.
  - `is_text_editable(path)` → `Path(path).suffix.lower() in TEXT_EXTENSIONS`.

---

### Task 1: `generate_directives` — emit @include/@ref by `kind`

**Files:**
- Modify: `src/dotmd_parser/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Produces: `generate_directives(analysis: dict) -> dict[str, list[str]]` — for each edge emit `@ref {to}` when `edge.get("kind")=="ref"`, else `@include {to}`; `shared_proposals` always `@include {name}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py — append (module already imports from dotmd_parser.analyze)
from dotmd_parser.analyze import generate_directives


def test_generate_directives_respects_kind():
    analysis = {
        "edges": [
            {"from": "a.md", "to": "shared/role.md", "kind": "include", "reason": "x"},
            {"from": "a.md", "to": "guide.md", "kind": "ref", "reason": "y"},
            {"from": "b.md", "to": "z.md", "reason": "no kind -> include"},
        ],
        "shared_proposals": [
            {"name": "shared/common.md", "used_by": ["c.md"]},
        ],
    }
    d = generate_directives(analysis)
    assert d["a.md"] == ["@include shared/role.md", "@ref guide.md"]
    assert d["b.md"] == ["@include z.md"]          # missing kind -> include
    assert d["c.md"] == ["@include shared/common.md"]  # shared_proposals always include


def test_generate_directives_unknown_kind_is_include():
    analysis = {"edges": [{"from": "a.md", "to": "b.md", "kind": "weird"}], "shared_proposals": []}
    assert generate_directives(analysis)["a.md"] == ["@include b.md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k generate_directives -v`
Expected: FAIL — current code emits `@include` for the `ref` edge, so `test_generate_directives_respects_kind` fails.

- [ ] **Step 3: Write minimal implementation**

Replace the existing `generate_directives` function in `src/dotmd_parser/analyze.py` with:

```python
def generate_directives(analysis: dict) -> dict[str, list[str]]:
    """Convert edges + shared proposals into `{src: ["@include|@ref target", ...]}`.

    Each edge's `kind` ("include" | "ref") selects the directive; unknown or
    missing kind falls back to "include". Shared proposals are always @include.
    """
    directives: dict[str, list[str]] = {}
    for edge in analysis.get("edges", []):
        directive = "@ref" if edge.get("kind") == "ref" else "@include"
        entry = f"{directive} {edge['to']}"
        bucket = directives.setdefault(edge["from"], [])
        if entry not in bucket:
            bucket.append(entry)
    for proposal in analysis.get("shared_proposals", []):
        target = proposal.get("name")
        if not target:
            continue
        for user_file in proposal.get("used_by", []):
            entry = f"@include {target}"
            bucket = directives.setdefault(user_file, [])
            if entry not in bucket:
                bucket.append(entry)
    return directives
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k generate_directives -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/analyze.py tests/test_analyze.py
git -c commit.gpgsign=false commit -m "feat: generate_directives emits @include/@ref by kind"
```

---

### Task 2: `_apply_directive_guards` — cycle (hard) + size (opt-in)

**Files:**
- Modify: `src/dotmd_parser/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Produces:
  - `_reaches(adj: dict[str, set[str]], src: str, dst: str) -> bool` — DFS reachability.
  - `_existing_include_adjacency(directory) -> dict[str, set[str]]` — include edges already present in the folder (via `build_index`).
  - `_apply_directive_guards(analysis: dict, directory, max_include_bytes: int | None = None) -> dict` — returns a NEW analysis whose edges' `kind` is normalized and demoted to `ref` per the cycle (hard) and size (opt-in) guards.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py — append
from dotmd_parser.analyze import _apply_directive_guards


def _kinds(analysis):
    return {(e["from"], e["to"]): e["kind"] for e in analysis["edges"]}


def test_guard_normalizes_unknown_kind(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "b.md", "kind": "bogus"}], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    assert _kinds(out)[("a.md", "b.md")] == "include"


def test_guard_demotes_cycle_to_ref(tmp_path):
    # a->b and b->a both include; one must be demoted so inlining can't cycle.
    a = {"edges": [
        {"from": "a.md", "to": "b.md", "kind": "include"},
        {"from": "b.md", "to": "a.md", "kind": "include"},
    ], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    k = _kinds(out)
    # deterministic: (a.md,b.md) added first stays include; (b.md,a.md) closes cycle -> ref
    assert k[("a.md", "b.md")] == "include"
    assert k[("b.md", "a.md")] == "ref"


def test_guard_demotes_self_edge(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "a.md", "kind": "include"}], "shared_proposals": []}
    out = _apply_directive_guards(a, tmp_path)
    assert _kinds(out)[("a.md", "a.md")] == "ref"


def test_guard_size_optin(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * 500, encoding="utf-8")
    a = {"edges": [{"from": "a.md", "to": "big.md", "kind": "include"}], "shared_proposals": []}
    # without the cap: stays include
    assert _kinds(_apply_directive_guards(a, tmp_path))[("a.md", "big.md")] == "include"
    # with a small cap: demoted to ref
    out = _apply_directive_guards(a, tmp_path, max_include_bytes=100)
    assert _kinds(out)[("a.md", "big.md")] == "ref"


def test_guard_does_not_mutate_input(tmp_path):
    a = {"edges": [{"from": "a.md", "to": "a.md", "kind": "include"}], "shared_proposals": []}
    _apply_directive_guards(a, tmp_path)
    assert a["edges"][0]["kind"] == "include"  # original untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k guard -v`
Expected: FAIL — `ImportError` (`_apply_directive_guards` not defined).

- [ ] **Step 3: Write minimal implementation**

Append to `src/dotmd_parser/analyze.py` (note: `Path` is already imported at the top of the module):

```python
def _reaches(adj: dict[str, set[str]], src: str, dst: str) -> bool:
    """Return True if `dst` is reachable from `src` in adjacency `adj`."""
    seen: set[str] = set()
    stack = [src]
    while stack:
        node = stack.pop()
        if node == dst:
            return True
        for nxt in adj.get(node, ()):  # type: ignore[arg-type]
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _existing_include_adjacency(directory: str | Path) -> dict[str, set[str]]:
    """Adjacency of include edges already present in `directory` (via build_index)."""
    from dotmd_parser.index import build_index  # local import avoids cycle at import time

    idx = build_index(str(directory))
    adj: dict[str, set[str]] = {}
    for rel, entry in idx.get("files", {}).items():
        for dep in entry.get("deps", []):
            if dep.get("type") == "include":
                adj.setdefault(rel, set()).add(dep["to"])
    return adj


def _apply_directive_guards(
    analysis: dict,
    directory: str | Path,
    max_include_bytes: int | None = None,
) -> dict:
    """Return a new analysis with edge `kind` normalized and demoted to ref per guards."""
    edges = [dict(e) for e in analysis.get("edges", [])]
    for edge in edges:
        if edge.get("kind") not in ("include", "ref"):
            edge["kind"] = "include"

    # size guard (opt-in)
    if max_include_bytes is not None:
        base = Path(directory).resolve()
        for edge in edges:
            if edge["kind"] != "include":
                continue
            target = base / edge["to"]
            try:
                if target.is_file() and target.stat().st_size > max_include_bytes:
                    edge["kind"] = "ref"
            except OSError:
                pass

    # cycle guard (hard): only include edges can inline/recurse
    adj = _existing_include_adjacency(directory)
    new_includes = sorted(
        (e for e in edges if e["kind"] == "include"),
        key=lambda e: (e["from"], e["to"]),
    )
    for edge in new_includes:
        src, dst = edge["from"], edge["to"]
        if src == dst or _reaches(adj, dst, src):
            edge["kind"] = "ref"  # adding src->dst would close a cycle
        else:
            adj.setdefault(src, set()).add(dst)

    out = dict(analysis)
    out["edges"] = edges
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k guard -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/analyze.py tests/test_analyze.py
git -c commit.gpgsign=false commit -m "feat: add cycle/size directive guards for analyze"
```

---

### Task 3: wire guards into `apply_analysis` / `apply_analysis_from_file`

**Files:**
- Modify: `src/dotmd_parser/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Consumes: `_apply_directive_guards`, `generate_directives`, `apply_directives`, `save_deps_yml`, `is_text_editable`.
- Produces:
  - `apply_analysis(directory, analysis, max_include_bytes=None) -> dict` — guard → generate → apply; returns `{"modified_files", "deps_yml"}`.
  - `apply_analysis_from_file(directory, json_path, max_include_bytes=None) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py — append
from dotmd_parser.analyze import apply_analysis


def test_apply_injects_ref_for_ref_kind(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "guide.md", "kind": "ref"}],
                "shared_proposals": []}
    result = apply_analysis(tmp_path, analysis)
    assert "SKILL.md" in result["modified_files"]
    body = (tmp_path / "SKILL.md").read_text(encoding="utf-8")
    assert body.startswith("@ref guide.md")


def test_apply_kindless_is_include_backward_compat(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "x.md").write_text("# X\n", encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "x.md"}], "shared_proposals": []}
    apply_analysis(tmp_path, analysis)
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("@include x.md")


def test_apply_cycle_demotes_one_side(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    analysis = {"edges": [
        {"from": "a.md", "to": "b.md", "kind": "include"},
        {"from": "b.md", "to": "a.md", "kind": "include"},
    ], "shared_proposals": []}
    apply_analysis(tmp_path, analysis)
    assert (tmp_path / "a.md").read_text(encoding="utf-8").startswith("@include b.md")
    assert (tmp_path / "b.md").read_text(encoding="utf-8").startswith("@ref a.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k "apply_injects_ref or apply_kindless or apply_cycle" -v`
Expected: FAIL — `apply_analysis` does not yet run guards (ref edge would be injected as `@include`, cycle not demoted).

- [ ] **Step 3: Write minimal implementation**

Replace the existing `apply_analysis` and `apply_analysis_from_file` functions in `src/dotmd_parser/analyze.py` with:

```python
def apply_analysis(directory: str | Path, analysis: dict, max_include_bytes: int | None = None) -> dict:
    """
    Apply the analysis: inject `@include`/`@ref` into text files (per each edge's
    `kind`, after cycle/size guards), write `deps.yml` for binary sources.
    """
    guarded = _apply_directive_guards(analysis, directory, max_include_bytes=max_include_bytes)
    directives = generate_directives(guarded)
    modified = apply_directives(directory, directives)

    binary_deps: dict[str, list[str]] = {
        src: [d.split(" ", 1)[1] for d in entries]
        for src, entries in directives.items()
        if not is_text_editable(src)
    }
    deps_yml_path = save_deps_yml(directory, binary_deps, guarded) if binary_deps else None

    return {"modified_files": modified, "deps_yml": deps_yml_path}


def apply_analysis_from_file(
    directory: str | Path,
    json_path: str | Path,
    max_include_bytes: int | None = None,
) -> dict:
    """Load a pre-computed analysis JSON and apply it.

    Raises:
        FileNotFoundError: if `json_path` does not exist.
        ValueError: if the file is not valid JSON or the schema is wrong.
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"analysis JSON not found: {json_path}")
    try:
        analysis = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {json_path}: {e}") from e

    analysis.setdefault("documents", [])
    analysis.setdefault("edges", [])
    analysis.setdefault("shared_proposals", [])
    return apply_analysis(directory, analysis, max_include_bytes=max_include_bytes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k "apply_injects_ref or apply_kindless or apply_cycle" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the whole analyze test file (regression)**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -v`
Expected: PASS (existing analyze tests + new ones).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/analyze.py tests/test_analyze.py
git -c commit.gpgsign=false commit -m "feat: run directive guards in apply_analysis; max_include_bytes param"
```

---

### Task 4: surface `kind` in host-agent plan + proposal output

**Files:**
- Modify: `src/dotmd_parser/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Produces: `format_host_agent_plan(...)` expected-output JSON includes a `kind` field with a one-line criterion; `format_proposal(analysis)` prints each edge's kind.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py — append
from dotmd_parser.analyze import format_host_agent_plan, format_proposal


def test_host_agent_plan_mentions_kind(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    plan = format_host_agent_plan(tmp_path)
    assert '"kind"' in plan
    assert "include" in plan and "ref" in plan


def test_format_proposal_shows_kind():
    analysis = {
        "documents": [{"path": "a.md", "summary": "s"}],
        "edges": [{"from": "a.md", "to": "guide.md", "kind": "ref", "reason": "pointer"}],
        "shared_proposals": [],
    }
    text = format_proposal(analysis)
    assert "ref" in text
    assert "guide.md" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k "host_agent_plan_mentions_kind or format_proposal_shows_kind" -v`
Expected: FAIL — current plan/proposal omit `kind`.

- [ ] **Step 3: Write minimal implementation**

(a) In `format_host_agent_plan`, replace the expected-output edges line. Find:

```python
        '  "edges": [{"from": "...", "to": "...", "reason": "..."}],',
```

Replace it with these two lines:

```python
        '  "edges": [{"from": "...", "to": "...",',
        '             "kind": "include|ref", "reason": "..."}],',
```

And immediately after the closing ```` "```" ```` that ends that JSON block (the line `"```",` right after the `"}"` line), insert this guidance line:

```python
        "",
        "Set `kind` per edge: \"include\" when the target is a shared fragment to "
        "inline here; \"ref\" when it is only a pointer (see-also / standalone / "
        "large / sub-skill).",
```

(b) In `format_proposal`, find the edges rendering loop:

```python
    if analysis["edges"]:
        lines.append("--- Detected dependencies ---")
        for edge in analysis["edges"]:
            lines += [
                f"  {edge['from']}",
                f"    └── depends on: {edge['to']}",
                f"        reason: {edge.get('reason', '')}",
            ]
        lines.append("")
```

Replace the inner `lines += [...]` block with one that includes kind:

```python
    if analysis["edges"]:
        lines.append("--- Detected dependencies ---")
        for edge in analysis["edges"]:
            kind = edge.get("kind", "include")
            lines += [
                f"  {edge['from']}",
                f"    └── depends on: {edge['to']}  [{kind}]",
                f"        reason: {edge.get('reason', '')}",
            ]
        lines.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k "host_agent_plan_mentions_kind or format_proposal_shows_kind" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/analyze.py tests/test_analyze.py
git -c commit.gpgsign=false commit -m "feat: surface edge kind in host-agent plan and proposal"
```

---

### Task 5: prompt template — ask Claude for `kind`

**Files:**
- Modify: `src/dotmd_parser/templates/prompts/analyze-dependencies.md`
- Test: `tests/test_analyze.py`

**Interfaces:** the bundled prompt's JSON schema includes `kind` with criteria (consumed by `analyze_dependencies` via `_load_prompt_template`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py — append
def test_prompt_template_requests_kind():
    from importlib import resources
    text = (
        resources.files("dotmd_parser.templates.prompts")
        .joinpath("analyze-dependencies.md")
        .read_text(encoding="utf-8")
    )
    assert '"kind"' in text
    assert "include" in text and "ref" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k prompt_template_requests_kind -v`
Expected: FAIL — template has no `kind`.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/templates/prompts/analyze-dependencies.md`, find the edges object in the `## Output format (JSON)` block:

```json
    {
      "from": "relative path of the depending file",
      "to": "relative path of the depended-on file",
      "reason": "one-sentence justification, matching the source language"
    }
```

Replace it with (adds the `kind` field):

```json
    {
      "from": "relative path of the depending file",
      "to": "relative path of the depended-on file",
      "kind": "include or ref",
      "reason": "one-sentence justification, matching the source language"
    }
```

And under the `## Criteria` section, add these two bullets at the end of the list:

```markdown
- Set `kind` to `"include"` when the depended-on file is a shared fragment whose
  text should be inlined into the depending file (shared role, definitions,
  boilerplate blocks).
- Set `kind` to `"ref"` when it is only a pointer that should NOT be inlined: a
  see-also link, a standalone or large document, a conditional/optional guide,
  or a sub-skill.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_analyze.py -k prompt_template_requests_kind -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/templates/prompts/analyze-dependencies.md tests/test_analyze.py
git -c commit.gpgsign=false commit -m "feat: ask analyze prompt to classify each edge kind"
```

---

### Task 6: CLI — `analyze --max-include-bytes`

**Files:**
- Modify: `src/dotmd_parser/cli.py`
- Test: `tests/test_cli_analyze_kind.py`

**Interfaces:**
- Produces: `dotmd-parser analyze <path> --max-include-bytes N` threading the cap into `--apply` and `--apply-from`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_analyze_kind.py
import json
import pytest
from dotmd_parser.cli import run


def test_apply_from_ref_kind_via_cli(tmp_path, capsys):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "guide.md", "kind": "ref"}],
                "shared_proposals": []}
    js = tmp_path / "analysis.json"
    js.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["analyze", str(tmp_path), "--apply-from", str(js)])
    assert e.value.code == 0
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("@ref guide.md")


def test_apply_from_max_include_bytes_demotes(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "big.md").write_text("x" * 500, encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "big.md", "kind": "include"}],
                "shared_proposals": []}
    js = tmp_path / "analysis.json"
    js.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(SystemExit):
        run(["analyze", str(tmp_path), "--apply-from", str(js), "--max-include-bytes", "100"])
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("@ref big.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_analyze_kind.py -v`
Expected: FAIL — `--max-include-bytes` not recognized (argparse exit 2) for the second test; the first may already pass since apply-from now honors kind (that's fine).

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/cli.py`:

(a) In `cmd_analyze`, the `--apply-from` branch currently calls `_apply_analysis_from_file(args.path, args.apply_from)`. Change that call to pass the cap:

```python
            result = _apply_analysis_from_file(
                args.path, args.apply_from, max_include_bytes=args.max_include_bytes
            )
```

(b) In `cmd_analyze`, the `--apply` branch currently calls `_apply_analysis(args.path, analysis)`. Change it to:

```python
        result = _apply_analysis(args.path, analysis, max_include_bytes=args.max_include_bytes)
```

(c) Register the flag on the `p_analyze` parser (add near its other `add_argument` calls):

```python
    p_analyze.add_argument(
        "--max-include-bytes",
        type=int,
        default=None,
        dest="max_include_bytes",
        help="Demote @include to @ref when the target file exceeds N bytes (default: off)",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_analyze_kind.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py tests/test_cli_analyze_kind.py
git -c commit.gpgsign=false commit -m "feat: add analyze --max-include-bytes flag"
```

---

### Task 7: Docs — CHANGELOG + README (auto-classification)

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.ja.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

Under the top `## [Unreleased]` section in `CHANGELOG.md` (add an `### Added` block if missing), insert:

```markdown
- **`analyze` が @include / @ref を自動判定** — 依存ごとに Claude が
  `kind`（include=共有断片を inline / ref=指すだけのポインタ）を判定し、`apply`
  時に適切なディレクティブを注入。循環は常に `@ref` へ強制降格（inline 循環を防止）、
  `--max-include-bytes N` で大きいターゲットを `@ref` に降格（opt-in）。`--plan`
  経路でも host-agent が `kind` を埋める。`kind` 省略時は `@include`（後方互換）。
  これにより「ポインタを手動で `@ref` に直す」運用が不要になる。
  設計: `docs/superpowers/specs/2026-06-22-analyze-include-ref-design.md`
```

- [ ] **Step 2: Update the README migration note (English)**

In `README.md`, find the "Worked example: migrating a directive-less skill" subsection's final paragraph that currently ends with:

```
Tidy any reference
that should not be inlined by changing its injected `@include` to `@ref`.
```

Replace that sentence with:

```
`analyze` now classifies each dependency automatically — Claude marks pointers
as `kind: "ref"` (injected as `@ref`, not inlined) and shared fragments as
`@include`; cycles are always forced to `@ref`, and `--max-include-bytes N`
demotes oversized targets. So the manual `@include`→`@ref` cleanup is no longer
required (you can still override by editing a line).
```

- [ ] **Step 3: Update the README migration note (Japanese)**

In `README.ja.md`, find the equivalent final sentence of the "実例: ディレクティブ無しスキルの移行" subsection:

```
インライン展開したくない
参照は、注入された `@include` を `@ref` に直すと実運用上きれいです。
```

Replace it with:

```
`analyze` は依存ごとに `@include` / `@ref` を**自動判定**します（Claude が
ポインタを `kind: "ref"` と判定→`@ref` で注入、共有断片は `@include`）。循環は
常に `@ref` に強制降格され、`--max-include-bytes N` で大きいターゲットも `@ref`
に降格できます。よって手動の `@include`→`@ref` 直しは不要です（必要なら 1 行
書き換えで上書きも可能）。
```

- [ ] **Step 4: Verify the suite still passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (docs don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md README.ja.md
git -c commit.gpgsign=false commit -m "docs: document analyze include/ref auto-classification"
```

---

## Self-Review

**1. Spec coverage:**
- §3 architecture (analyze.py + prompt + cli) → Tasks 1–6. ✓
- §4 `kind` field + normalize to include → Tasks 1–2. ✓
- §5 LLM prompt criteria → Task 5; host-agent plan parity → Task 4. ✓
- §6 generate_directives by kind (shared always include) → Task 1. ✓
- §7 guards: cycle hard (include-only graph, existing ∪ new, demote new back-edges deterministically, self-edge) + size opt-in → Task 2; wired into apply → Task 3. ✓
- §8 CLI `--max-include-bytes` + apply signatures → Tasks 3 (signatures) + 6 (flag). ✓
- §9 backward compat (kindless→include, cap off, deps.yml unchanged) → Tasks 1/3 (binary `d.split(" ",1)[1]` still records the target) + back-compat tests. ✓
- §10 steps → Tasks 1–7. ✓
- §11 tests (kind→ref inject, kindless include, cycle demotion, size opt-in, shared always include, host-agent plan kind, apply-from) → Tasks 1–6. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases" — every code step shows complete code and exact commands. ✓

**3. Type consistency:** `generate_directives(analysis)`, `_reaches(adj, src, dst)`, `_existing_include_adjacency(directory)`, `_apply_directive_guards(analysis, directory, max_include_bytes=None)`, `apply_analysis(directory, analysis, max_include_bytes=None)`, `apply_analysis_from_file(directory, json_path, max_include_bytes=None)` — names/params consistent across tasks and CLI call sites (Task 6 passes `max_include_bytes=args.max_include_bytes` to both). Edge `kind` key used consistently; default-to-include rule applied in both `generate_directives` (Task 1) and `_apply_directive_guards` normalization (Task 2). ✓

Note: `binary_deps` now strips the directive token with `d.split(" ", 1)[1]` (handles both `@include `/`@ref `), so a `@ref` to a binary source still records the bare target path in `deps.yml` — consistent with prior behavior for `@include`.
