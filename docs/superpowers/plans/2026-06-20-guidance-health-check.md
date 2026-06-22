# Guidance Health Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `check` subcommand into a deterministic CI gate that detects cycles, missing references, unresolved placeholders, and conflicting directives, emits text/JSON/SARIF reports, and fails per a configurable `--fail-on` threshold.

**Architecture:** A new pure-function module `src/dotmd_parser/checks.py` consumes the compact index (`build_index` output), produces a flat list of `Finding` dicts, and renders them as text/JSON/SARIF. The CLI's existing `cmd_check` is extended (backward-compatible) to call it. `parser.py` and `index.py` are unchanged. Deterministic, stdlib only — no LLM, no new dependency.

**Tech Stack:** Python 3 stdlib only, pytest, argparse, json.

## Global Constraints

- **stdlib only** — do NOT add any third-party dependency.
- **Deterministic** — no LLM/API calls; findings are derived purely from the compact index (+ optional disk walk for orphans). Findings must be emitted in a stable, sorted order.
- **Input is the compact index dict** (from `dotmd_parser.index.build_index`). Index `files` keys, `deps[].to`, `missing[]`, and `warnings[].path` are POSIX-relative strings; `cycles[]` are message strings; `root` is an absolute path string. Always use `.get(...)` with defaults — most entry keys are optional.
- **Do not modify** `parser.py` or `index.py`. New code lives in `checks.py` + `cli.py` + `__init__.py`.
- **Type annotations** on every function signature; `from __future__ import annotations` at top of the new module.
- **Immutability** — do not mutate the input index.
- **Backward compatibility** — default `check` (no flags) keeps exit semantics: exit 1 when any error-severity finding exists (cycles/missing), exit 0 otherwise. The text layout may be enriched; exit codes must not change.
- **Severity model:** error = {circular, missing-reference, depth-exceeded, read-error}; warning = {unresolved-placeholder, conflicting-directive, orphan-file}. orphan-file is opt-in (`--check orphans`).
- **Finding shape:** `{"rule": str, "severity": "error"|"warning", "path": str, "message": str, "line": int | None}`. `path` is "" for findings with no single file (e.g. circular).
- **Schema string:** JSON report uses `"schema": "dotmd-check/v1"`. SARIF uses version `"2.1.0"`.
- **Commit signing**: the SSH signing key is not readable in this sandbox. Commit with `git -c commit.gpgsign=false commit -m "..."` for every commit.
- **Branch:** work on `feat/guidance-health-check` (already created; the design spec commit `8d984e8` is its first commit). Do NOT branch off or switch.
- **Canonical test command:** `PYTHONPATH=src ./.venv/bin/python -m pytest` (the repo's editable install is stale — you MUST use this exact form; never bare `python`/`pytest`).

---

### Task 1: error-severity findings from the index

**Files:**
- Create: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces:
  - `_circular_findings(index: dict) -> list[dict]` — one error finding per `index["cycles"]` message; `path=""`, `message` = the cycle string.
  - `_missing_findings(index: dict) -> list[dict]` — one error finding per `index["missing"]` rel; `path=rel`, `message="referenced file does not exist"`.
  - `_graph_warning_findings(index: dict) -> list[dict]` — for `index["warnings"]` whose `type` is `depth_exceeded` or `read_error`, an error finding with rule `depth-exceeded` / `read-error`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py
from dotmd_parser.checks import (
    _circular_findings, _missing_findings, _graph_warning_findings,
)


def _idx(files=None, cycles=None, missing=None, warnings=None, root="/x", edges=0):
    files = files or {}
    return {
        "schema": 1, "root": root,
        "stats": {"files": len(files), "edges": edges,
                  "cycles": len(cycles or []), "missing": len(missing or [])},
        "files": files,
        "cycles": cycles or [],
        "missing": missing or [],
        "warnings": warnings or [],
    }


def test_circular_findings():
    idx = _idx(cycles=["Circular reference: /x/a.md -> /x/b.md -> /x/a.md"])
    res = _circular_findings(idx)
    assert len(res) == 1
    assert res[0]["rule"] == "circular"
    assert res[0]["severity"] == "error"
    assert res[0]["path"] == ""
    assert "a.md" in res[0]["message"]


def test_missing_findings():
    idx = _idx(missing=["shared/gone.md"])
    res = _missing_findings(idx)
    assert res == [{
        "rule": "missing-reference", "severity": "error",
        "path": "shared/gone.md",
        "message": "referenced file does not exist", "line": None,
    }]


def test_graph_warning_findings_promotes_depth_and_read_error_only():
    idx = _idx(warnings=[
        {"type": "depth_exceeded", "path": "deep.md", "message": "max depth 10 exceeded"},
        {"type": "read_error", "path": "bad.md", "message": "could not read"},
        {"type": "missing", "path": "x.md", "message": "nope"},  # must be ignored here
        {"type": "circular", "path": "c.md", "message": "cycle"},  # must be ignored here
    ])
    res = _graph_warning_findings(idx)
    rules = sorted(f["rule"] for f in res)
    assert rules == ["depth-exceeded", "read-error"]
    assert all(f["severity"] == "error" for f in res)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` (no `checks` module yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py
"""
dotmd-parser — guidance health checks (deterministic CI gate).

Consumes a compact index (from `index.build_index` / `index.load_index`) and
produces a flat list of Finding dicts, rendered as text / JSON / SARIF. Pure,
stdlib-only, no LLM. The raw graph / parser are not touched.

Finding shape:
    {"rule": str, "severity": "error"|"warning", "path": str,
     "message": str, "line": int | None}
"""

from __future__ import annotations

CHECK_SCHEMA = "dotmd-check/v1"

_GRAPH_WARNING_RULES = {
    "depth_exceeded": "depth-exceeded",
    "read_error": "read-error",
}


def _finding(rule: str, severity: str, path: str, message: str,
             line: int | None = None) -> dict:
    return {"rule": rule, "severity": severity, "path": path,
            "message": message, "line": line}


def _circular_findings(index: dict) -> list[dict]:
    """One error finding per recorded cycle message (path unknown → '')."""
    return [
        _finding("circular", "error", "", msg)
        for msg in index.get("cycles", [])
    ]


def _missing_findings(index: dict) -> list[dict]:
    """One error finding per missing referenced file."""
    return [
        _finding("missing-reference", "error", rel,
                 "referenced file does not exist")
        for rel in index.get("missing", [])
    ]


def _graph_warning_findings(index: dict) -> list[dict]:
    """Promote depth_exceeded / read_error graph warnings to error findings."""
    out: list[dict] = []
    for warning in index.get("warnings", []):
        rule = _GRAPH_WARNING_RULES.get(warning.get("type", ""))
        if rule is None:
            continue
        out.append(_finding(rule, "error", warning.get("path", ""),
                            warning.get("message", "")))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add error-severity findings (circular/missing/graph-warnings)"
```

---

### Task 2: unresolved-placeholder findings

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `_placeholder_findings(index: dict) -> list[dict]` — for each `files[rel]["placeholders"]` var, a warning finding `path=rel`, `message="unresolved placeholder: {{var}}"`. Sorted by (path, var) for determinism.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
from dotmd_parser.checks import _placeholder_findings


def test_placeholder_findings_one_per_var_sorted():
    idx = _idx(files={
        "b.md": {"type": "shared"},
        "a.md": {"type": "agent", "placeholders": ["year", "company_id"]},
    })
    res = _placeholder_findings(idx)
    assert [(f["path"], f["message"]) for f in res] == [
        ("a.md", "unresolved placeholder: {{company_id}}"),
        ("a.md", "unresolved placeholder: {{year}}"),
    ]
    assert all(f["rule"] == "unresolved-placeholder" and f["severity"] == "warning"
               for f in res)


def test_placeholder_findings_empty_when_none():
    idx = _idx(files={"a.md": {"type": "agent"}})
    assert _placeholder_findings(idx) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k placeholder -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — append
def _placeholder_findings(index: dict) -> list[dict]:
    """One warning finding per unresolved {{var}} (sorted by path, var)."""
    out: list[dict] = []
    files = index.get("files", {})
    for rel in sorted(files):
        for var in sorted(files[rel].get("placeholders", []) or []):
            out.append(_finding(
                "unresolved-placeholder", "warning", rel,
                f"unresolved placeholder: {{{{{var}}}}}",
            ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k placeholder -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add unresolved-placeholder findings"
```

---

### Task 3: conflicting-directive findings

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `_conflicting_directive_findings(index: dict) -> list[dict]` — for each source file, group its `deps` by `to` considering only explicit types `{include, ref, delegate}` (read-ref excluded); if a target has ≥2 distinct such types, emit one warning finding `path=source`, message naming the target and the sorted types.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
from dotmd_parser.checks import _conflicting_directive_findings


def test_conflicting_directive_include_and_ref_same_target():
    idx = _idx(files={
        "SKILL.md": {"type": "skill", "deps": [
            {"to": "shared/role.md", "type": "include"},
            {"to": "shared/role.md", "type": "ref"},
        ]},
        "shared/role.md": {"type": "shared"},
    })
    res = _conflicting_directive_findings(idx)
    assert len(res) == 1
    assert res[0]["rule"] == "conflicting-directive"
    assert res[0]["severity"] == "warning"
    assert res[0]["path"] == "SKILL.md"
    assert "shared/role.md" in res[0]["message"]
    assert "include" in res[0]["message"] and "ref" in res[0]["message"]


def test_conflicting_directive_single_type_is_clean():
    idx = _idx(files={"SKILL.md": {"type": "skill", "deps": [
        {"to": "a.md", "type": "include"},
    ]}})
    assert _conflicting_directive_findings(idx) == []


def test_conflicting_directive_ignores_read_ref():
    # include + read-ref to same target: only one EXPLICIT type -> no conflict
    idx = _idx(files={"SKILL.md": {"type": "skill", "deps": [
        {"to": "a.md", "type": "include"},
        {"to": "a.md", "type": "read-ref"},
    ]}})
    assert _conflicting_directive_findings(idx) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k conflicting -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — append
_EXPLICIT_DIRECTIVE_TYPES = {"include", "ref", "delegate"}


def _conflicting_directive_findings(index: dict) -> list[dict]:
    """Warn when a source reaches one target via ≥2 distinct explicit types."""
    out: list[dict] = []
    files = index.get("files", {})
    for rel in sorted(files):
        by_target: dict[str, set[str]] = {}
        for dep in files[rel].get("deps", []):
            dtype = dep.get("type", "")
            if dtype not in _EXPLICIT_DIRECTIVE_TYPES:
                continue
            by_target.setdefault(dep["to"], set()).add(dtype)
        for target in sorted(by_target):
            types = by_target[target]
            if len(types) >= 2:
                joined = ", ".join(sorted(types))
                out.append(_finding(
                    "conflicting-directive", "warning", rel,
                    f"{target} is referenced by multiple directive types ({joined})",
                ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k conflicting -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add conflicting-directive findings"
```

---

### Task 4: orphan-file findings (opt-in)

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `_orphan_findings(index: dict, root: str | None) -> list[dict]` — walk `.md` files on disk under `root`, skipping hidden paths and `node_modules`; any `.md` whose relative POSIX path is not a key in `index["files"]` is a warning finding `path=rel`, `message="file is not referenced by any node"`. Returns `[]` when `root` is None or not a directory. Sorted by path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
from dotmd_parser.checks import _orphan_findings


def test_orphan_findings_flags_unreferenced_md(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s", encoding="utf-8")
    (tmp_path / "used.md").write_text("# u", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# o", encoding="utf-8")
    idx = _idx(files={"SKILL.md": {"type": "skill"}, "used.md": {"type": "reference"}},
               root=str(tmp_path))
    res = _orphan_findings(idx, str(tmp_path))
    assert [f["path"] for f in res] == ["orphan.md"]
    assert res[0]["rule"] == "orphan-file" and res[0]["severity"] == "warning"


def test_orphan_findings_none_root_returns_empty():
    idx = _idx(files={"SKILL.md": {"type": "skill"}})
    assert _orphan_findings(idx, None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k orphan -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — add this import at the TOP of the file,
# directly under `from __future__ import annotations`:
#
#     from pathlib import Path
#
# Then append:

def _orphan_findings(index: dict, root: str | None) -> list[dict]:
    """Warn about .md files on disk that no graph node references."""
    if root is None:
        return []
    base = Path(root)
    if base.is_file():
        base = base.parent
    if not base.is_dir():
        return []
    node_set = set(index.get("files", {}).keys())
    out: list[dict] = []
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(base).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = path.relative_to(base).as_posix()
        if "node_modules" in rel:
            continue
        if not path.is_file():
            continue
        if rel not in node_set:
            out.append(_finding("orphan-file", "warning", rel,
                               "file is not referenced by any node"))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k orphan -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add orphan-file findings (opt-in)"
```

---

### Task 5: run_checks, summarize, exit_code

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: all `_*_findings` helpers.
- Produces:
  - `run_checks(index: dict, root: str | None = None, enable_orphans: bool = False) -> list[dict]` — concatenates circular, missing, graph-warning, placeholder, conflicting-directive findings; appends orphan findings only when `enable_orphans`.
  - `summarize(findings: list[dict]) -> dict` — `{"errors": int, "warnings": int}`.
  - `exit_code(findings: list[dict], fail_on: str) -> int` — `fail_on="never"` → 0; `"warning"` → 1 if any error or warning; `"error"` (default) → 1 if any error.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
from dotmd_parser.checks import run_checks, summarize, exit_code


def test_run_checks_integrates_and_respects_orphan_flag(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# o", encoding="utf-8")
    idx = _idx(
        files={"SKILL.md": {"type": "skill", "placeholders": ["x"]}},
        missing=["gone.md"], root=str(tmp_path),
    )
    without = run_checks(idx, root=str(tmp_path), enable_orphans=False)
    rules_without = {f["rule"] for f in without}
    assert "missing-reference" in rules_without
    assert "unresolved-placeholder" in rules_without
    assert "orphan-file" not in rules_without

    with_orphans = run_checks(idx, root=str(tmp_path), enable_orphans=True)
    assert "orphan-file" in {f["rule"] for f in with_orphans}


def test_summarize_counts_by_severity():
    findings = [
        {"rule": "circular", "severity": "error", "path": "", "message": "", "line": None},
        {"rule": "unresolved-placeholder", "severity": "warning", "path": "a", "message": "", "line": None},
        {"rule": "conflicting-directive", "severity": "warning", "path": "a", "message": "", "line": None},
    ]
    assert summarize(findings) == {"errors": 1, "warnings": 2}


def test_exit_code_matrix():
    err = [{"rule": "x", "severity": "error", "path": "", "message": "", "line": None}]
    warn = [{"rule": "x", "severity": "warning", "path": "", "message": "", "line": None}]
    clean: list[dict] = []
    assert exit_code(err, "error") == 1
    assert exit_code(err, "warning") == 1
    assert exit_code(err, "never") == 0
    assert exit_code(warn, "error") == 0
    assert exit_code(warn, "warning") == 1
    assert exit_code(warn, "never") == 0
    assert exit_code(clean, "warning") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k "run_checks or summarize or exit_code" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — append
def run_checks(index: dict, root: str | None = None,
               enable_orphans: bool = False) -> list[dict]:
    """Run all enabled checks and return a flat list of findings."""
    findings: list[dict] = []
    findings += _circular_findings(index)
    findings += _missing_findings(index)
    findings += _graph_warning_findings(index)
    findings += _placeholder_findings(index)
    findings += _conflicting_directive_findings(index)
    if enable_orphans:
        findings += _orphan_findings(index, root)
    return findings


def summarize(findings: list[dict]) -> dict:
    """Count findings by severity."""
    errors = sum(1 for f in findings if f.get("severity") == "error")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    return {"errors": errors, "warnings": warnings}


def exit_code(findings: list[dict], fail_on: str) -> int:
    """Map findings to a CI exit code per the fail_on threshold."""
    counts = summarize(findings)
    if fail_on == "never":
        return 0
    if fail_on == "warning":
        return 1 if (counts["errors"] or counts["warnings"]) else 0
    # default: "error"
    return 1 if counts["errors"] else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k "run_checks or summarize or exit_code" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add run_checks/summarize/exit_code"
```

---

### Task 6: text and JSON formatters

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `summarize`, `CHECK_SCHEMA`.
- Produces:
  - `format_text(findings: list[dict], index: dict) -> str` — a summary line `<files> files, <edges> edges — errors:<E> warnings:<W>` followed by one `  [SEVERITY] rule: <path or -> — <message>` line per finding.
  - `format_json(findings: list[dict], index: dict) -> str` — `json.dumps` of `{schema, root, stats:{files,edges,errors,warnings}, findings}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
import json
from dotmd_parser.checks import format_text, format_json


def test_format_text_has_summary_and_lines():
    idx = _idx(files={"a.md": {"type": "agent"}}, missing=["gone.md"], edges=2)
    findings = run_checks(idx)
    text = format_text(findings, idx)
    lines = text.splitlines()
    assert "errors:1 warnings:0" in lines[0]
    assert any("[ERROR]" in ln and "missing-reference" in ln for ln in lines)


def test_format_json_shape():
    idx = _idx(files={"a.md": {"type": "agent", "placeholders": ["v"]}}, edges=0)
    findings = run_checks(idx)
    payload = json.loads(format_json(findings, idx))
    assert payload["schema"] == "dotmd-check/v1"
    assert payload["stats"]["warnings"] == 1
    assert payload["stats"]["errors"] == 0
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["rule"] == "unresolved-placeholder"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k "format_text or format_json" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — add this import at the TOP of the file,
# directly under `from pathlib import Path`:
#
#     import json
#
# Then append:

def format_text(findings: list[dict], index: dict) -> str:
    """Render findings as a backward-compatible text summary + detail lines."""
    stats = index.get("stats", {})
    counts = summarize(findings)
    lines = [
        f"{stats.get('files', 0)} files, {stats.get('edges', 0)} edges — "
        f"errors:{counts['errors']} warnings:{counts['warnings']}"
    ]
    for f in findings:
        loc = f.get("path") or "-"
        lines.append(
            f"  [{f['severity'].upper()}] {f['rule']}: {loc} — {f['message']}"
        )
    return "\n".join(lines)


def format_json(findings: list[dict], index: dict) -> str:
    """Render findings as dotmd-check/v1 JSON."""
    stats = index.get("stats", {})
    counts = summarize(findings)
    payload = {
        "schema": CHECK_SCHEMA,
        "root": index.get("root", ""),
        "stats": {
            "files": stats.get("files", 0),
            "edges": stats.get("edges", 0),
            "errors": counts["errors"],
            "warnings": counts["warnings"],
        },
        "findings": findings,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k "format_text or format_json" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add text and JSON check formatters"
```

---

### Task 7: SARIF formatter

**Files:**
- Modify: `src/dotmd_parser/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `format_sarif(findings: list[dict], index: dict) -> str` — SARIF 2.1.0 JSON. `runs[0].tool.driver.name == "dotmd-parser"`, `version` = package `__version__`, `rules[]` = the distinct rules that appear. Each finding → a `results[]` entry with `ruleId`, `level` (= severity), `message.text`; `locations[]` with `artifactLocation.uri = path` only when `path` is non-empty (plus `region.startLine` when `line` is set).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
from dotmd_parser.checks import format_sarif


def test_format_sarif_shape_and_locations():
    idx = _idx(
        files={"a.md": {"type": "agent"}},
        missing=["gone.md"],
        cycles=["Circular reference: /x/a.md -> /x/a.md"],
    )
    findings = run_checks(idx)
    sarif = json.loads(format_sarif(findings, idx))
    assert sarif["version"] == "2.1.0"
    driver = sarif["runs"][0]["tool"]["driver"]
    assert driver["name"] == "dotmd-parser"
    rule_ids = {r["id"] for r in driver["rules"]}
    assert {"missing-reference", "circular"} <= rule_ids

    results = sarif["runs"][0]["results"]
    by_rule = {r["ruleId"]: r for r in results}
    # missing-reference has a path -> has a location
    assert by_rule["missing-reference"]["level"] == "error"
    assert (by_rule["missing-reference"]["locations"][0]["physicalLocation"]
            ["artifactLocation"]["uri"] == "gone.md")
    # circular has path "" -> no locations key
    assert "locations" not in by_rule["circular"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k sarif -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/checks.py — append
def _camel(rule_id: str) -> str:
    parts = rule_id.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def format_sarif(findings: list[dict], index: dict) -> str:
    """Render findings as SARIF 2.1.0 JSON (for GitHub code scanning)."""
    from dotmd_parser import __version__  # local import avoids cycle at import time

    rules: dict[str, dict] = {}
    results: list[dict] = []
    for f in findings:
        rule_id = f["rule"]
        rules.setdefault(rule_id, {"id": rule_id, "name": _camel(rule_id)})
        result: dict = {
            "ruleId": rule_id,
            "level": f["severity"],
            "message": {"text": f["message"]},
        }
        if f.get("path"):
            physical: dict = {"artifactLocation": {"uri": f["path"]}}
            if f.get("line"):
                physical["region"] = {"startLine": f["line"]}
            result["locations"] = [{"physicalLocation": physical}]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "dotmd-parser",
                "informationUri": "https://github.com/dotmd-projects/dotmd-parser",
                "version": __version__,
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k sarif -v`
Expected: PASS.

- [ ] **Step 5: Run the whole checks test file**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -v`
Expected: PASS (all checks tests so far).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/checks.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: add SARIF check formatter"
```

---

### Task 8: Export check API from the package

**Files:**
- Modify: `src/dotmd_parser/__init__.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `from dotmd_parser import run_checks, summarize, exit_code, format_text, format_json, format_sarif, CHECK_SCHEMA` works; all in `__all__`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checks.py — append
def test_checks_api_is_exported():
    import dotmd_parser
    for name in ("run_checks", "summarize", "exit_code",
                 "format_text", "format_json", "format_sarif", "CHECK_SCHEMA"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k exported -v`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/__init__.py`, add after the `from dotmd_parser.index_md import (...)` block:

```python
from dotmd_parser.checks import (
    run_checks,
    summarize,
    exit_code,
    format_text,
    format_json,
    format_sarif,
    CHECK_SCHEMA,
)
```

And in the `__all__` list, add after the `# index_md` group's last entry (`"INDEX_MD_SCHEMA",`):

```python
    # checks
    "run_checks",
    "summarize",
    "exit_code",
    "format_text",
    "format_json",
    "format_sarif",
    "CHECK_SCHEMA",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_checks.py -k exported -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/__init__.py tests/test_checks.py
git -c commit.gpgsign=false commit -m "feat: export check API from package"
```

---

### Task 9: Extend the `check` CLI subcommand

**Files:**
- Modify: `src/dotmd_parser/cli.py`
- Test: `tests/test_cli_check.py`

**Interfaces:**
- Consumes: `run_checks`, `format_text`, `format_json`, `format_sarif`, `exit_code` from `dotmd_parser.checks`; existing `build_index`.
- Produces: `dotmd-parser check <path> [--format text|json|sarif] [--fail-on error|warning|never] [--check orphans] [--out FILE]`.

**Current code being replaced** (`cmd_check` in `cli.py`):
```python
def cmd_check(args: argparse.Namespace) -> int:
    idx = build_index(args.path)
    stats = idx["stats"]
    print(
        f"{stats['files']} files, {stats['edges']} edges — "
        f"cycles:{stats['cycles']} missing:{stats['missing']}"
    )
    for cycle in idx.get("cycles", []):
        print(f"  CYCLE   {cycle}")
    for miss in idx.get("missing", []):
        print(f"  MISSING {miss}")
    return 1 if (stats["cycles"] or stats["missing"]) else 0
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_check.py
import json

import pytest

from dotmd_parser.cli import run


def _skill_missing(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\n@include shared/gone.md\n", encoding="utf-8"
    )
    return tmp_path


def _skill_warn_only(tmp_path):
    # unresolved placeholder, no errors
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\nUse {{company_id}} here.\n", encoding="utf-8"
    )
    return tmp_path


def _skill_orphan(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "extra.md").write_text("# Extra\n", encoding="utf-8")
    return tmp_path


def test_check_default_fails_on_missing(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill)])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "missing-reference" in out


def test_check_warning_only_passes_by_default(tmp_path):
    skill = _skill_warn_only(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill)])
    assert exc.value.code == 0


def test_check_fail_on_warning_fails_on_placeholder(tmp_path):
    skill = _skill_warn_only(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill), "--fail-on", "warning"])
    assert exc.value.code == 1


def test_check_fail_on_never_always_zero(tmp_path):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill), "--fail-on", "never"])
    assert exc.value.code == 0


def test_check_json_format(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "json", "--fail-on", "never"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "dotmd-check/v1"
    assert payload["stats"]["errors"] >= 1


def test_check_sarif_format(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "sarif", "--fail-on", "never"])
    sarif = json.loads(capsys.readouterr().out)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "dotmd-parser"


def test_check_orphans_opt_in(tmp_path, capsys):
    skill = _skill_orphan(tmp_path)
    # without --check orphans: clean (exit 0, no orphan finding)
    with pytest.raises(SystemExit) as exc1:
        run(["check", str(skill), "--fail-on", "warning"])
    assert exc1.value.code == 0
    # with --check orphans: extra.md is flagged -> warning -> exit 1
    with pytest.raises(SystemExit) as exc2:
        run(["check", str(skill), "--check", "orphans", "--fail-on", "warning"])
    assert exc2.value.code == 1
    assert "orphan-file" in capsys.readouterr().out


def test_check_out_writes_file(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    out_file = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "json", "--out", str(out_file),
             "--fail-on", "never"])
    assert capsys.readouterr().out.strip() == ""
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["schema"] == "dotmd-check/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_check.py -v`
Expected: FAIL — new flags not recognized (argparse error) / `missing-reference` not in output.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/cli.py`:

(a) Add the import after the `from dotmd_parser.index_md import (...)` block:

```python
from dotmd_parser.checks import (
    run_checks as _run_checks,
    format_text as _format_check_text,
    format_json as _format_check_json,
    format_sarif as _format_check_sarif,
    exit_code as _check_exit_code,
)
```

(b) Replace the entire existing `cmd_check` function with:

```python
def cmd_check(args: argparse.Namespace) -> int:
    idx = build_index(args.path)
    enable_orphans = bool(args.check and "orphans" in args.check)
    findings = _run_checks(idx, root=args.path, enable_orphans=enable_orphans)

    if args.format == "json":
        report = _format_check_json(findings, idx)
    elif args.format == "sarif":
        report = _format_check_sarif(findings, idx)
    else:
        report = _format_check_text(findings, idx)

    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    else:
        print(report)

    return _check_exit_code(findings, args.fail_on)
```

(c) Replace the `p_check` registration block in `_build_parser()`:

```python
    p_check = sub.add_parser("check", help="Health-check the graph (CI gate)")
    p_check.add_argument("path", help="Directory or SKILL.md")
    p_check.add_argument(
        "--format", choices=["text", "json", "sarif"], default="text",
        help="Report format (default: text)",
    )
    p_check.add_argument(
        "--fail-on", choices=["error", "warning", "never"], default="error",
        dest="fail_on",
        help="Exit non-zero threshold (default: error)",
    )
    p_check.add_argument(
        "--check", action="append", choices=["orphans"],
        help="Enable an optional check (repeatable; e.g. --check orphans)",
    )
    p_check.add_argument("--out", help="Write the report to FILE instead of stdout")
    p_check.set_defaults(func=cmd_check)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_check.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (all existing tests + new checks + CLI tests; no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py tests/test_cli_check.py
git -c commit.gpgsign=false commit -m "feat: extend check CLI with formats, fail-on, orphans"
```

---

### Task 10: Docs — CHANGELOG + README + GitHub Actions example

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.ja.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

At the top of `CHANGELOG.md` (above the most recent `## [...]` entry), insert:

```markdown
## [Unreleased]

### Changed
- **`check` を CI ゲートに拡張** — 循環/欠落参照に加え、未解決 placeholder・
  矛盾 directive（同一ターゲットへ include/ref/delegate のうち 2 種以上）を
  検出。`--format text|json|sarif`、`--fail-on error|warning|never`、
  `--check orphans`（opt-in の孤立ファイル検出）、`--out FILE` を追加。
  SARIF は GitHub code scanning に upload して PR インライン注釈にできる。
  既定挙動（cycle/missing で exit 1）は後方互換。`run_checks` ほかを公開 API に追加。
  設計: `docs/superpowers/specs/2026-06-20-guidance-health-check-design.md`
```

- [ ] **Step 2: Add a README section (English)**

In `README.md`, find where the `check` subcommand is documented (search for `check`) and replace/expand it with:

```markdown
### `check` — guidance health gate (CI)

Deterministic health check over the dependency graph. Detects cycles and
missing references (errors), plus unresolved `{{placeholders}}` and
conflicting directives (warnings). Optionally flags orphan files.

```bash
dotmd-parser check ./my-skill                       # text, fails on errors
dotmd-parser check ./my-skill --fail-on warning     # also fail on warnings
dotmd-parser check ./my-skill --format json
dotmd-parser check ./my-skill --format sarif --out dotmd.sarif
dotmd-parser check ./my-skill --check orphans       # opt-in orphan detection
```

`--fail-on` chooses the exit-code threshold (`error` default, `warning`, or
`never`). Use `--format sarif` with GitHub's `upload-sarif` action to get
inline PR annotations:

```yaml
- run: dotmd-parser check . --format sarif --out dotmd.sarif --fail-on never
- uses: github/codeql-action/upload-sarif@v3
  with: { sarif_file: dotmd.sarif }
- run: dotmd-parser check . --fail-on warning   # gate the PR
```
```

- [ ] **Step 3: Add a README section (Japanese)**

In `README.ja.md`, find the `check` subcommand docs and replace/expand with:

```markdown
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
```

- [ ] **Step 4: Verify the suite still passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (docs changes don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md README.ja.md
git -c commit.gpgsign=false commit -m "docs: document check CI gate (formats, fail-on, SARIF, orphans)"
```

---

## Self-Review

**1. Spec coverage:**
- §3 module `checks.py` (pure functions + 3 formatters) → Tasks 1–7. ✓
- §4 Finding model + all 7 rules (circular, missing-reference, depth-exceeded, read-error, unresolved-placeholder, conflicting-directive, orphan-file) → Tasks 1–4. ✓ (missing-list-vs-warnings dedup: `_missing_findings` uses `index["missing"]`; `_graph_warning_findings` only takes depth/read_error — no double count. circular from `index["cycles"]` only.)
- §5 CLI (`--format`/`--fail-on`/`--check orphans`/`--out`, backward-compat exit semantics) → Task 9. ✓
- §6 JSON `dotmd-check/v1` + SARIF 2.1.0 → Tasks 6–7. ✓
- §7 GitHub Actions example → Task 10. ✓
- §8 error handling / backward compat (no new path validation; empty graph → exit 0) → Task 9 (build_index direct call preserved) + Task 5 (clean → exit 0). ✓
- §9 implementation steps → Tasks 1–10. ✓
- §10 testing (per-rule, fail-on matrix, CLI formats, sarif schema, orphan opt-in, 80%+) → Tasks 1–9. ✓
- Export of public API (`run_checks` etc.) → Task 8. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step has complete code; every command is exact with expected output. ✓

**3. Type consistency:** Names consistent across tasks — `_finding`, `_circular_findings`, `_missing_findings`, `_graph_warning_findings`, `_placeholder_findings`, `_conflicting_directive_findings`, `_orphan_findings(index, root)`, `run_checks(index, root, enable_orphans)`, `summarize`, `exit_code(findings, fail_on)`, `format_text/json/sarif(findings, index)`, `CHECK_SCHEMA`, `_camel`, `_EXPLICIT_DIRECTIVE_TYPES`, `_GRAPH_WARNING_RULES`. CLI imports them under `_`-prefixed aliases. Finding dict keys (`rule`/`severity`/`path`/`message`/`line`) used consistently in producers (Tasks 1–4) and consumers (Tasks 5–7). JSON/SARIF field names match the spec §6. ✓

Note: `import json` is added at module top in Task 6 and reused by Task 7's `format_sarif`; `from pathlib import Path` is added in Task 4. Both are introduced before first use in task order, and are idempotent stdlib imports — if an implementer runs tasks out of order, ensure the top-of-file imports (`from __future__ import annotations`, `from pathlib import Path`, `import json`) are present before Tasks 4/6/7 code runs.
