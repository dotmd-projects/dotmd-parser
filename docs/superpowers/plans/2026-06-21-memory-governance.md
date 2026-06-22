# Memory-as-Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only JSONL risk ledger and a `risk` query that combines reverse-dependency impact (`affects`) with active risk tags (ledger replay ∪ frontmatter), so a pre-edit hook/agent can warn before editing high-risk files.

**Architecture:** A new module `src/dotmd_parser/ledger.py` owns the JSONL ledger (`append_event`/`read_events`/`active_tags` via replay), frontmatter static tags, risk level, and the `risk_report` that calls `digest.affects`. The CLI adds a `ledger` subcommand (add/clear) and a `risk` query. `parser.py`/`index.py` are unchanged; this is a fully additive feature.

**Tech Stack:** Python 3 stdlib only (`json`, `datetime`, `pathlib`), pytest, argparse.

## Global Constraints

- **stdlib only** — no third-party dependency.
- **Fully additive** — do NOT modify `parser.py` or `index.py`. New code lives in `ledger.py` + `cli.py` (new handlers/subparsers) + `__init__.py` (exports). No existing command's behavior changes.
- **Type annotations** on every function signature; `from __future__ import annotations` at top of the new module.
- **Immutability** — do not mutate inputs; build new collections.
- **Tag vocabulary (fixed enum):** `RISK_TAGS = ("fix-failed", "fragile", "security-sensitive", "deprecated")`. `HIGH_TAGS = frozenset({"fix-failed", "security-sensitive"})` (the other two are "medium").
- **Ledger location:** `<root>/.claude/dotmd-ledger.jsonl` (constants `LEDGER_DIR = ".claude"`, `LEDGER_FILE = "dotmd-ledger.jsonl"`).
- **Event schema (one JSON object per line):** `{"ts": <ISO8601 Z>, "file": <root-relative POSIX str>, "action": "add"|"clear", "tag": <enum> | "all", "note": <optional str>}`. `note` is omitted when not provided.
- **State derivation:** replay events in file order — `add tag` adds to the active set, `clear tag` removes it, `clear all` empties the set. Only events whose `file` matches are considered.
- **Static tags:** read a file's own frontmatter `risk:` (list or single string) via `index_md.extract_frontmatter`; keep only values in `RISK_TAGS`.
- **risk level:** `high` if active tags intersect `HIGH_TAGS`; else `medium` if any active tags; else `none`.
- **`--fail-on` (risk CLI):** `high` (default) → exit 1 when level is `high`; `any` → exit 1 when there are any active tags; `never` → always exit 0.
- **Robustness:** `read_events` returns `[]` when the ledger is absent; malformed JSON lines are skipped (with a stderr warning), not fatal. `append_event` raises `ValueError` for an out-of-enum `add` tag.
- **Determinism in tests:** `append_event(..., ts=...)` accepts an explicit timestamp so tests don't depend on the clock; default is current UTC time.
- **Commit signing:** the SSH signing key is not readable in this sandbox. Commit with `git -c commit.gpgsign=false commit -m "..."` for every commit.
- **Branch:** work on `feat/memory-governance` (already created; design spec commit `51cc286` is its first commit). Do NOT branch off or switch.
- **Canonical test command:** `PYTHONPATH=src ./.venv/bin/python -m pytest` (the editable install is stale — you MUST use this exact form; never bare `python`/`pytest`).
- **Reference — existing helpers you reuse:** `from dotmd_parser.digest import affects` (signature `affects(index: dict, target_rel: str) -> list[str]`, returns reverse-deps sorted); `from dotmd_parser.index_md import extract_frontmatter` (signature `extract_frontmatter(md: str) -> dict`); the CLI's `_load_or_build_index(path)` returns a compact index dict.

---

### Task 1: ledger path + append_event + read_events

**Files:**
- Create: `src/dotmd_parser/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `RISK_TAGS: tuple[str, ...]`, `HIGH_TAGS: frozenset[str]`, `LEDGER_DIR`, `LEDGER_FILE`.
  - `default_ledger_path(root: str | Path) -> Path` — `<root>/.claude/dotmd-ledger.jsonl`.
  - `append_event(root, file, action, tag, note=None, ts=None) -> Path` — append one JSONL line; creates `.claude/`. Raises `ValueError` for `action="add"` with a tag not in `RISK_TAGS` (clear allows any enum tag or `"all"`). Returns the ledger path.
  - `read_events(root) -> list[dict]` — parse all lines; `[]` if no file; skip malformed lines (stderr warning).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py
import json
import pytest
from dotmd_parser.ledger import (
    default_ledger_path, append_event, read_events, RISK_TAGS, HIGH_TAGS,
)


def test_constants():
    assert RISK_TAGS == ("fix-failed", "fragile", "security-sensitive", "deprecated")
    assert HIGH_TAGS == frozenset({"fix-failed", "security-sensitive"})


def test_default_ledger_path(tmp_path):
    p = default_ledger_path(tmp_path)
    assert p == tmp_path / ".claude" / "dotmd-ledger.jsonl"


def test_append_then_read_roundtrip(tmp_path):
    append_event(tmp_path, "shared/role.md", "add", "fix-failed",
                 note="retry hung", ts="2026-06-21T00:00:00Z")
    append_event(tmp_path, "shared/role.md", "clear", "fix-failed",
                 ts="2026-06-22T00:00:00Z")
    events = read_events(tmp_path)
    assert len(events) == 2
    assert events[0] == {"ts": "2026-06-21T00:00:00Z", "file": "shared/role.md",
                         "action": "add", "tag": "fix-failed", "note": "retry hung"}
    assert events[1] == {"ts": "2026-06-22T00:00:00Z", "file": "shared/role.md",
                         "action": "clear", "tag": "fix-failed"}
    # .claude/ auto-created
    assert (tmp_path / ".claude" / "dotmd-ledger.jsonl").exists()


def test_append_rejects_unknown_add_tag(tmp_path):
    with pytest.raises(ValueError):
        append_event(tmp_path, "a.md", "add", "bogus")


def test_read_events_absent_is_empty(tmp_path):
    assert read_events(tmp_path) == []


def test_read_events_skips_malformed_lines(tmp_path):
    path = default_ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"t","file":"a.md","action":"add","tag":"fragile"}\n'
        'NOT JSON\n'
        '{"ts":"t2","file":"b.md","action":"add","tag":"deprecated"}\n',
        encoding="utf-8",
    )
    events = read_events(tmp_path)
    assert [e["file"] for e in events] == ["a.md", "b.md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/ledger.py
"""
dotmd-parser — Memory-as-Governance risk ledger.

An append-only JSONL event log of per-file risk tags, combined with reverse
dependencies (`digest.affects`) and static frontmatter tags to produce a
pre-edit risk report. Pure stdlib. The parser/index are not touched.

Event schema (one JSON object per line):
    {"ts": <ISO8601 Z>, "file": <root-relative POSIX>,
     "action": "add"|"clear", "tag": <enum>|"all", "note"?: <str>}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RISK_TAGS = ("fix-failed", "fragile", "security-sensitive", "deprecated")
HIGH_TAGS = frozenset({"fix-failed", "security-sensitive"})
LEDGER_DIR = ".claude"
LEDGER_FILE = "dotmd-ledger.jsonl"


def default_ledger_path(root: str | Path) -> Path:
    """Return `<root>/.claude/dotmd-ledger.jsonl`."""
    return Path(root) / LEDGER_DIR / LEDGER_FILE


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def append_event(
    root: str | Path,
    file: str,
    action: str,
    tag: str,
    note: str | None = None,
    ts: str | None = None,
) -> Path:
    """Append one event to the ledger; create `.claude/` if missing."""
    if action not in ("add", "clear"):
        raise ValueError(f"action must be 'add' or 'clear', got {action!r}")
    if action == "add" and tag not in RISK_TAGS:
        raise ValueError(f"unknown risk tag {tag!r}; choose from {RISK_TAGS}")
    if action == "clear" and tag != "all" and tag not in RISK_TAGS:
        raise ValueError(f"unknown clear tag {tag!r}; choose from {RISK_TAGS} or 'all'")

    event: dict = {
        "ts": ts or _utc_now(),
        "file": file,
        "action": action,
        "tag": tag,
    }
    if note:
        event["note"] = note

    path = default_ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def read_events(root: str | Path) -> list[dict]:
    """Read all ledger events; [] if absent; skip malformed lines."""
    path = default_ledger_path(root)
    if not path.exists():
        return []
    events: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed ledger line: {line[:80]}", file=sys.stderr)
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ledger.py tests/test_ledger.py
git -c commit.gpgsign=false commit -m "feat: add risk ledger append_event/read_events"
```

---

### Task 2: active_tags (replay)

**Files:**
- Modify: `src/dotmd_parser/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `read_events`.
- Produces: `active_tags(root, file) -> set[str]` — replay events for `file` in order; `add` adds, `clear <tag>` removes, `clear all` empties. Returns the active enum-tag set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — append
from dotmd_parser.ledger import active_tags


def test_active_tags_add_then_clear(tmp_path):
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    append_event(tmp_path, "a.md", "add", "fragile", ts="t2")
    assert active_tags(tmp_path, "a.md") == {"fix-failed", "fragile"}
    append_event(tmp_path, "a.md", "clear", "fix-failed", ts="t3")
    assert active_tags(tmp_path, "a.md") == {"fragile"}


def test_active_tags_clear_all(tmp_path):
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    append_event(tmp_path, "a.md", "add", "deprecated", ts="t2")
    append_event(tmp_path, "a.md", "clear", "all", ts="t3")
    assert active_tags(tmp_path, "a.md") == set()


def test_active_tags_isolated_per_file(tmp_path):
    append_event(tmp_path, "a.md", "add", "fragile", ts="t1")
    append_event(tmp_path, "b.md", "add", "deprecated", ts="t2")
    assert active_tags(tmp_path, "a.md") == {"fragile"}
    assert active_tags(tmp_path, "b.md") == {"deprecated"}


def test_active_tags_empty_when_no_events(tmp_path):
    assert active_tags(tmp_path, "a.md") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k active_tags -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/ledger.py — append
def active_tags(root: str | Path, file: str) -> set[str]:
    """Replay ledger events for `file` and return the active risk-tag set."""
    tags: set[str] = set()
    for event in read_events(root):
        if event.get("file") != file:
            continue
        action = event.get("action")
        tag = event.get("tag")
        if action == "add" and tag in RISK_TAGS:
            tags.add(tag)
        elif action == "clear":
            if tag == "all":
                tags.clear()
            else:
                tags.discard(tag)
    return tags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k active_tags -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ledger.py tests/test_ledger.py
git -c commit.gpgsign=false commit -m "feat: add active_tags replay"
```

---

### Task 3: static_tags (frontmatter) + all_active_tags + risk_level

**Files:**
- Modify: `src/dotmd_parser/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `active_tags`, `index_md.extract_frontmatter`.
- Produces:
  - `static_tags(root, file) -> set[str]` — read `<root>/<file>` frontmatter `risk:` (list or str), keep only `RISK_TAGS`. Empty if file/frontmatter absent or unreadable.
  - `all_active_tags(root, file) -> set[str]` — `active_tags ∪ static_tags`.
  - `risk_level(tags) -> str` — `"high"` if `tags & HIGH_TAGS`; else `"medium"` if `tags`; else `"none"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — append
from dotmd_parser.ledger import static_tags, all_active_tags, risk_level


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_static_tags_from_frontmatter_list(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk:\n  - fragile\n  - bogus\n---\n# A\n")
    assert static_tags(tmp_path, "a.md") == {"fragile"}  # bogus dropped


def test_static_tags_from_frontmatter_scalar(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk: deprecated\n---\n# A\n")
    assert static_tags(tmp_path, "a.md") == {"deprecated"}


def test_static_tags_absent(tmp_path):
    _write(tmp_path, "a.md", "# A\nno frontmatter\n")
    assert static_tags(tmp_path, "a.md") == set()
    assert static_tags(tmp_path, "missing.md") == set()


def test_all_active_tags_union(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk: fragile\n---\n# A\n")
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    assert all_active_tags(tmp_path, "a.md") == {"fragile", "fix-failed"}


def test_risk_level():
    assert risk_level({"fix-failed"}) == "high"
    assert risk_level({"security-sensitive", "fragile"}) == "high"
    assert risk_level({"fragile"}) == "medium"
    assert risk_level({"deprecated"}) == "medium"
    assert risk_level(set()) == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k "static_tags or all_active or risk_level" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/ledger.py — add this import near the top imports:
#     from dotmd_parser.index_md import extract_frontmatter
# Then append:

def static_tags(root: str | Path, file: str) -> set[str]:
    """Read `risk:` from the file's frontmatter; keep only known enum tags."""
    path = Path(root) / file
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    fm = extract_frontmatter(text)
    value = fm.get("risk")
    if value is None:
        return set()
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [v for v in value if isinstance(v, str)]
    else:
        candidates = []
    return {c for c in candidates if c in RISK_TAGS}


def all_active_tags(root: str | Path, file: str) -> set[str]:
    """Union of ledger-active tags and static frontmatter tags."""
    return active_tags(root, file) | static_tags(root, file)


def risk_level(tags: set[str]) -> str:
    """Map an active-tag set to a level: high | medium | none."""
    if tags & HIGH_TAGS:
        return "high"
    if tags:
        return "medium"
    return "none"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k "static_tags or all_active or risk_level" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/ledger.py tests/test_ledger.py
git -c commit.gpgsign=false commit -m "feat: add static_tags, all_active_tags, risk_level"
```

---

### Task 4: risk_report (affects integration)

**Files:**
- Modify: `src/dotmd_parser/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `all_active_tags`, `risk_level`, `read_events`, `digest.affects`.
- Produces: `risk_report(index, root, file, recent=5) -> dict` returning
  `{"file", "affects": [str], "affects_count": int, "active_tags": [str sorted], "level": str, "events": [last `recent` events for file, newest first]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — append
from dotmd_parser.ledger import risk_report


def _idx_with_dep():
    # SKILL.md and agents/a.md both include shared/role.md → role.md affects both
    return {
        "root": "/x",
        "files": {
            "SKILL.md": {"type": "skill", "deps": [{"to": "shared/role.md", "type": "include"}]},
            "agents/a.md": {"type": "agent", "deps": [{"to": "shared/role.md", "type": "include"}]},
            "shared/role.md": {"type": "shared", "deps": []},
        },
        "stats": {"files": 3},
    }


def test_risk_report_combines_affects_and_tags(tmp_path):
    append_event(tmp_path, "shared/role.md", "add", "fix-failed", ts="t1")
    report = risk_report(_idx_with_dep(), tmp_path, "shared/role.md")
    assert report["file"] == "shared/role.md"
    assert sorted(report["affects"]) == ["SKILL.md", "agents/a.md"]
    assert report["affects_count"] == 2
    assert report["active_tags"] == ["fix-failed"]
    assert report["level"] == "high"
    assert report["events"][0]["tag"] == "fix-failed"


def test_risk_report_no_risk(tmp_path):
    report = risk_report(_idx_with_dep(), tmp_path, "shared/role.md")
    assert report["active_tags"] == []
    assert report["level"] == "none"
    assert report["events"] == []


def test_risk_report_recent_limit_newest_first(tmp_path):
    for i in range(7):
        append_event(tmp_path, "a.md", "add", "fragile", ts=f"t{i}")
    report = risk_report({"files": {}, "root": "/x"}, tmp_path, "a.md", recent=3)
    assert len(report["events"]) == 3
    assert [e["ts"] for e in report["events"]] == ["t6", "t5", "t4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k risk_report -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/ledger.py — add this import near the top imports:
#     from dotmd_parser.digest import affects as _affects
# Then append:

def risk_report(index: dict, root: str | Path, file: str, recent: int = 5) -> dict:
    """Combine reverse-deps and active risk tags into a report dict."""
    impacted = _affects(index, file)
    tags = sorted(all_active_tags(root, file))
    file_events = [e for e in read_events(root) if e.get("file") == file]
    return {
        "file": file,
        "affects": impacted,
        "affects_count": len(impacted),
        "active_tags": tags,
        "level": risk_level(set(tags)),
        "events": list(reversed(file_events))[:recent],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k risk_report -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full ledger test file**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -v`
Expected: PASS (all ledger tests).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/ledger.py tests/test_ledger.py
git -c commit.gpgsign=false commit -m "feat: add risk_report combining affects and tags"
```

---

### Task 5: export ledger API from the package

**Files:**
- Modify: `src/dotmd_parser/__init__.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces: `from dotmd_parser import append_event, active_tags, all_active_tags, static_tags, risk_report, risk_level, RISK_TAGS, HIGH_TAGS, default_ledger_path` works; all in `__all__`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py — append
def test_ledger_api_is_exported():
    import dotmd_parser
    for name in ("append_event", "active_tags", "all_active_tags", "static_tags",
                 "risk_report", "risk_level", "RISK_TAGS", "HIGH_TAGS",
                 "default_ledger_path"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k exported -v`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/__init__.py`, add after the `from dotmd_parser.index_md import (...)` block:

```python
from dotmd_parser.ledger import (
    append_event,
    read_events,
    active_tags,
    static_tags,
    all_active_tags,
    risk_level,
    risk_report,
    default_ledger_path,
    RISK_TAGS,
    HIGH_TAGS,
)
```

And in the `__all__` list, add after the `# index_md` group's last entry (`"INDEX_MD_SCHEMA",`):

```python
    # ledger
    "append_event",
    "read_events",
    "active_tags",
    "static_tags",
    "all_active_tags",
    "risk_level",
    "risk_report",
    "default_ledger_path",
    "RISK_TAGS",
    "HIGH_TAGS",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_ledger.py -k exported -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/__init__.py tests/test_ledger.py
git -c commit.gpgsign=false commit -m "feat: export ledger API from package"
```

---

### Task 6: CLI — `ledger add` / `ledger clear` + `risk`

**Files:**
- Modify: `src/dotmd_parser/cli.py`
- Test: `tests/test_cli_ledger.py`

**Interfaces:**
- Consumes: `append_event`, `risk_report`, `RISK_TAGS` from `dotmd_parser.ledger`; existing `_load_or_build_index`.
- Produces: `dotmd-parser ledger add <path> <file> --tag <enum> [--note T]`, `dotmd-parser ledger clear <path> <file> (--tag <enum> | --all)`, `dotmd-parser risk <path> <file> [--json] [--fail-on high|any|never]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_ledger.py
import json
import pytest
from dotmd_parser.cli import run


def _skill(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "SKILL.md").write_text("# Root\n\n@include shared/role.md\n", encoding="utf-8")
    (tmp_path / "shared" / "role.md").write_text("# Role\n", encoding="utf-8")
    return tmp_path


def test_ledger_add_then_risk_text(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit) as e1:
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fix-failed"])
    assert e1.value.code == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as e2:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "never"])
    assert e2.value.code == 0
    out = capsys.readouterr().out
    assert "fix-failed" in out
    assert "affects" in out


def test_ledger_add_unknown_tag_exits_2(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit) as e:
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "bogus"])
    # argparse choices rejects -> exit 2
    assert e.value.code == 2


def test_ledger_clear_removes_tag(tmp_path, capsys):
    skill = _skill(tmp_path)
    for args in (["ledger", "add", str(skill), "shared/role.md", "--tag", "fix-failed"],
                 ["ledger", "clear", str(skill), "shared/role.md", "--tag", "fix-failed"]):
        with pytest.raises(SystemExit):
            run(args)
        capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["risk", str(skill), "shared/role.md", "--json", "--fail-on", "never"])
    report = json.loads(capsys.readouterr().out)
    assert report["active_tags"] == []
    assert report["level"] == "none"


def test_risk_json_shape(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fragile"])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["risk", str(skill), "shared/role.md", "--json", "--fail-on", "never"])
    report = json.loads(capsys.readouterr().out)
    assert report["file"] == "shared/role.md"
    assert report["affects_count"] == 1   # SKILL.md includes role.md
    assert report["level"] == "medium"


def test_risk_fail_on_high_exit_code(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "security-sensitive"])
    capsys.readouterr()
    # high tag active -> --fail-on high exits 1
    with pytest.raises(SystemExit) as e_high:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "high"])
    assert e_high.value.code == 1
    capsys.readouterr()


def test_risk_fail_on_medium_vs_any(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fragile"])
    capsys.readouterr()
    # medium tag: --fail-on high -> 0; --fail-on any -> 1
    with pytest.raises(SystemExit) as e_high:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "high"])
    assert e_high.value.code == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as e_any:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "any"])
    assert e_any.value.code == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_ledger.py -v`
Expected: FAIL — `ledger`/`risk` not known commands (argparse error / SystemExit 2).

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/cli.py`:

(a) Add the import after the `from dotmd_parser.index_md import (...)` block:

```python
from dotmd_parser.ledger import (
    append_event as _append_event,
    risk_report as _risk_report,
    RISK_TAGS as _RISK_TAGS,
)
```

(b) Add the command handlers (place near `cmd_resolve`):

```python
def cmd_ledger(args: argparse.Namespace) -> int:
    if args.ledger_action == "add":
        _append_event(args.path, args.file, "add", args.tag, note=args.note)
        print(f"ledger: add {args.tag} -> {args.file}", file=sys.stderr)
        return 0
    # clear
    if not args.all and not args.tag:
        print("error: ledger clear requires --tag <tag> or --all", file=sys.stderr)
        return 2
    tag = "all" if args.all else args.tag
    _append_event(args.path, args.file, "clear", tag)
    print(f"ledger: clear {tag} -> {args.file}", file=sys.stderr)
    return 0


def cmd_risk(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path)
    report = _risk_report(idx, args.path, args.file)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        tags = report["active_tags"]
        if tags:
            risk_str = f"active risk: {', '.join(tags)} ({report['level']})"
            last_add = next((e for e in report["events"] if e.get("action") == "add"), None)
            if last_add:
                risk_str += f" [last add: {last_add.get('ts', '')}]"
        else:
            risk_str = "no active risk"
        print(f"{report['file']} — affects {report['affects_count']} files; {risk_str}")

    fail_on = args.fail_on
    if fail_on == "never":
        return 0
    if fail_on == "any":
        return 1 if report["active_tags"] else 0
    return 1 if report["level"] == "high" else 0   # default: high
```

(c) Register the subparsers inside `_build_parser()` (after the `p_resolve` block, before `p_analyze`):

```python
    p_ledger = sub.add_parser("ledger", help="Record risk events (append-only JSONL)")
    ledger_sub = p_ledger.add_subparsers(dest="ledger_action", required=True)

    p_ledger_add = ledger_sub.add_parser("add", help="Add a risk tag to a file")
    p_ledger_add.add_argument("path", help="Project root (where .claude/ lives)")
    p_ledger_add.add_argument("file", help="File path relative to root")
    p_ledger_add.add_argument("--tag", required=True, choices=list(_RISK_TAGS), help="Risk tag")
    p_ledger_add.add_argument("--note", help="Optional free-text note")
    p_ledger_add.set_defaults(func=cmd_ledger)

    p_ledger_clear = ledger_sub.add_parser("clear", help="Clear a risk tag (or all) from a file")
    p_ledger_clear.add_argument("path", help="Project root (where .claude/ lives)")
    p_ledger_clear.add_argument("file", help="File path relative to root")
    p_ledger_clear.add_argument("--tag", choices=list(_RISK_TAGS), help="Risk tag to clear")
    p_ledger_clear.add_argument("--all", action="store_true", help="Clear all tags for the file")
    p_ledger_clear.set_defaults(func=cmd_ledger)

    p_risk = sub.add_parser("risk", help="Report edit risk (affects + active tags)")
    p_risk.add_argument("path", help="Directory or SKILL.md")
    p_risk.add_argument("file", help="File path relative to root")
    p_risk.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    p_risk.add_argument("--fail-on", choices=["high", "any", "never"], default="high",
                        dest="fail_on", help="Exit-1 threshold (default: high)")
    p_risk.set_defaults(func=cmd_risk)
```

(d) Add `"ledger"` and `"risk"` to the `known_cmds` set inside `run()`:

```python
    known_cmds = {"init", "index", "check", "affects", "deps", "digest", "tree", "resolve", "analyze", "inventory", "dotmd-index", "show", "ledger", "risk"}
```

(e) Add two lines to the module docstring subcommand list (under the `resolve` line):

```python
- `ledger  <add|clear> ...`  Record risk events (append-only JSONL).
- `risk    <path> <file>`    Report edit risk (affects + active tags).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_ledger.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (existing tests + new ledger/CLI tests; no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py tests/test_cli_ledger.py
git -c commit.gpgsign=false commit -m "feat: add ledger add/clear and risk CLI commands"
```

---

### Task 7: Docs — CHANGELOG + README (incl. hook example)

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.ja.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

At the top of `CHANGELOG.md` (above the most recent `## [...]` entry; if an `## [Unreleased]` section already exists, add the bullet under its `### Added` instead of duplicating the heading):

```markdown
## [Unreleased]

### Added
- **Memory-as-Governance リスク台帳** — 追記専用 JSONL（`.claude/dotmd-ledger.jsonl`）に
  per-file リスクタグ（fix-failed / fragile / security-sensitive / deprecated）を記録
  （`ledger add` / `ledger clear`、replay で状態導出）。`risk <path> <file>` が逆依存
  （affects）件数と active タグ・レベル（high/medium/none）を返し、`--fail-on high|any|never`
  で CI / PreToolUse フックのゲートに使える。frontmatter `risk:` の静的タグも統合。
  `risk_report` 等を公開 API に追加。
  設計: `docs/superpowers/specs/2026-06-21-memory-governance-design.md`
```

- [ ] **Step 2: Add a README section (English)**

In `README.md`, add near the other subcommand docs:

```markdown
### `ledger` / `risk` — edit-risk governance

Record per-file risk history in an append-only JSONL ledger
(`.claude/dotmd-ledger.jsonl`) and query it before editing. `risk` combines
reverse-dependency impact (`affects`) with active risk tags (ledger replay ∪
frontmatter `risk:`).

```bash
dotmd-parser ledger add . shared/role.md --tag fix-failed --note "retry hung"
dotmd-parser ledger clear . shared/role.md --tag fix-failed   # or --all
dotmd-parser risk . shared/role.md                            # text report
dotmd-parser risk . shared/role.md --json
```

Tags: `fix-failed`, `fragile`, `security-sensitive`, `deprecated` (the first
two are "high"). `--fail-on high|any|never` controls the exit code, so a
PreToolUse hook can warn before risky edits:

```bash
dotmd-parser risk . "$FILE_PATH" --fail-on high \
  || echo "[dotmd] high-risk file (last fix failed / security-sensitive) — review before editing"
```
```

- [ ] **Step 3: Add a README section (Japanese)**

In `README.ja.md`, add the equivalent:

```markdown
### `ledger` / `risk` — 編集リスクガバナンス

追記専用 JSONL 台帳（`.claude/dotmd-ledger.jsonl`）に per-file のリスク履歴を記録し、
編集前に照会します。`risk` は逆依存（affects）件数と active なリスクタグ
（台帳 replay ∪ frontmatter `risk:`）を組み合わせます。

```bash
dotmd-parser ledger add . shared/role.md --tag fix-failed --note "retry hung"
dotmd-parser ledger clear . shared/role.md --tag fix-failed   # または --all
dotmd-parser risk . shared/role.md                            # text レポート
dotmd-parser risk . shared/role.md --json
```

タグ: `fix-failed` / `fragile` / `security-sensitive` / `deprecated`（前2つが high）。
`--fail-on high|any|never` で終了コードを制御し、PreToolUse フックで編集前に警告できます:

```bash
dotmd-parser risk . "$FILE_PATH" --fail-on high \
  || echo "[dotmd] 高リスクファイル（前回修正失敗 / security-sensitive）。編集前に確認を。"
```
```

- [ ] **Step 4: Verify the suite still passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (docs changes don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md README.ja.md
git -c commit.gpgsign=false commit -m "docs: document ledger/risk governance commands"
```

---

## Self-Review

**1. Spec coverage:**
- §3 module `ledger.py` (path/append/read/active_tags/static_tags/all_active_tags/risk_level/risk_report) → Tasks 1–4. ✓
- §4 JSONL schema + append_event (enum validation) + read_events (skip malformed) + active_tags replay (add/clear/clear-all) → Tasks 1–2. ✓
- §5 static_tags from frontmatter (list/scalar, enum filter) + all_active_tags union → Task 3. ✓
- §6 risk_level + risk_report (affects integration, active_tags sorted, level, recent events newest-first) → Tasks 3–4. ✓
- §7 CLI (`ledger add`/`clear`, `risk` text/json, `--fail-on` matrix, known_cmds, docstring) → Task 6. ✓
- §8 hook example → Task 7. ✓
- §9 error handling (absent ledger → empty/none/exit 0; enum-out → exit 2 via argparse choices; malformed skip) → Tasks 1/6. ✓
- §10 steps → Tasks 1–7. ✓
- §11 testing (ledger logic, CLI, fail-on matrix, 80%+) → Tasks 1–6. ✓
- Export of ledger API → Task 5. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step has complete code; every command is exact with expected output. ✓

**3. Type consistency:** Names consistent across tasks — `default_ledger_path`, `append_event(root, file, action, tag, note, ts)`, `read_events`, `active_tags(root, file)`, `static_tags`, `all_active_tags`, `risk_level(tags)`, `risk_report(index, root, file, recent)`, `RISK_TAGS`, `HIGH_TAGS`. CLI imports them aliased (`_append_event`/`_risk_report`/`_RISK_TAGS`). Event dict keys (`ts`/`file`/`action`/`tag`/`note`) consistent between producer (Task 1) and consumers (Tasks 2/4). `risk_report` dict keys (`file`/`affects`/`affects_count`/`active_tags`/`level`/`events`) match between Task 4 and the CLI's text/json rendering + the CLI tests in Task 6. The enum-out CLI rejection is via argparse `choices` (exit 2), which is what `test_ledger_add_unknown_tag_exits_2` asserts — consistent with spec §9. ✓

Note: spec §9 says "enum 外 tag (add) → ValueError → CLI で stderr エラー + exit 2." The CLI achieves exit 2 via argparse `choices=list(_RISK_TAGS)` (argparse prints to stderr and exits 2 before `cmd_ledger` runs); `append_event`'s `ValueError` remains the library-level guard (covered by `test_append_rejects_unknown_add_tag` in Task 1). Both paths exist and agree on exit 2 / stderr.
