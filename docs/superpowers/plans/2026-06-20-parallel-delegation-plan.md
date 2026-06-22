# Parallel Delegation Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `plan` subcommand that statically generates a parallel execution plan (topological batches + per-task subtree context) from the `@delegate` graph, pre-detecting conflicts and cycles, and emits it as `plan(JSON)` for a parent agent.

**Architecture:** A new pure-function module `src/dotmd_parser/plan.py` operates on the compact index (`build_index`/`load_index` output), exactly like `digest.py`. It computes forward reachability over `deps`, derives a task DAG over `@delegate` targets, levels it into parallel batches, detects same-batch shared-dependency conflicts (warning only) and mutual-reachability cycles. The CLI wires it in following the existing `cmd_*` conventions. `parser.py` and `index.py` are unchanged.

**Tech Stack:** Python 3 stdlib only (no new dependencies), pytest, argparse.

## Global Constraints

- **stdlib only** — do NOT add any third-party dependency. (Project invariant: `pyproject.toml` has no runtime deps.)
- **Input is the compact index dict** (from `dotmd_parser.index.build_index`), NOT the raw graph. Index `files` keys and `deps[].to` are POSIX-relative strings.
- **Do not modify** `parser.py` or `index.py`. New code lives in `plan.py` + `cli.py` + `__init__.py`.
- **Type annotations** on every function signature (PEP 8 / project style). `from __future__ import annotations` at top of new module.
- **Immutability** — build new dicts/lists; do not mutate the input index.
- **Commit signing**: the SSH signing key is not readable in this sandbox. Commit with `git -c commit.gpgsign=false commit -m "..."` for every commit in this plan.
- **Work on branch** `feat/parallel-delegation-plan` (already created; the design spec commit `3c722d0` is its first commit).
- **Index `files` entry shape** (what you read): `{"type": str, "title": str, "desc": str, "hash": str, "size": int, "placeholders": [str], "missing": bool, "deps": [{"to": str, "type": str, "parallel": bool}]}`. Most keys are optional/omitted when empty — always use `.get(...)` with defaults. Top-level index also has `"root": str`, `"cycles": [str]`, `"stats": {...}`.

---

### Task 1: `_reachable` — forward reachability

**Files:**
- Create: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Produces: `_reachable(index: dict, start: str) -> set[str]` — set of nodes reachable from `start` by following `files[*].deps[*].to`, excluding `start` itself. Cycle-safe.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py
from dotmd_parser.plan import _reachable


def _idx(files: dict) -> dict:
    """Build a minimal compact-index dict from a {rel: deps_list} map."""
    return {
        "root": "/x",
        "files": {
            rel: {"type": "agent", "title": rel, "deps": deps}
            for rel, deps in files.items()
        },
        "cycles": [],
        "stats": {"files": len(files)},
    }


def _d(to: str, type_: str = "include", parallel: bool = False) -> dict:
    return {"to": to, "type": type_, "parallel": parallel}


def test_reachable_follows_deps_and_excludes_start():
    idx = _idx({
        "a.md": [_d("b.md"), _d("c.md")],
        "b.md": [_d("d.md")],
        "c.md": [],
        "d.md": [],
    })
    assert _reachable(idx, "a.md") == {"b.md", "c.md", "d.md"}
    assert _reachable(idx, "b.md") == {"d.md"}
    assert _reachable(idx, "d.md") == set()


def test_reachable_is_cycle_safe():
    idx = _idx({"a.md": [_d("b.md")], "b.md": [_d("a.md")]})
    assert _reachable(idx, "a.md") == {"b.md"}  # start excluded even via cycle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError` (no `plan` module yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py
"""
dotmd-parser — static parallel-delegation plan generator.

Consumes a compact index (from `index.build_index` / `index.load_index`) and
produces an execution plan: parallel batches (topological levels) over
`@delegate` targets, with per-task subtree context, conflict detection
(same-batch shared dependencies — warning only), and cycle detection.

Pure functions, stdlib only. The raw graph / parser are not touched.
"""

from __future__ import annotations


def _reachable(index: dict, start: str) -> set[str]:
    """Return nodes reachable from `start` via `deps`, excluding `start`."""
    files = index.get("files", {})
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        for dep in files.get(current, {}).get("deps", []):
            target = dep["to"]
            if target not in seen:
                seen.add(target)
                stack.append(target)
    seen.discard(start)
    return seen
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _reachable forward reachability for plan"
```

---

### Task 2: `_task_nodes` — collect delegate targets

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: index dict.
- Produces: `_task_nodes(index: dict) -> set[str]` — every `deps[].to` whose `type == "delegate"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import _task_nodes


def test_task_nodes_collects_only_delegate_targets():
    idx = _idx({
        "SKILL.md": [
            _d("agents/a.md", "delegate", True),
            _d("agents/b.md", "delegate", True),
            _d("shared/role.md", "include"),
        ],
        "agents/a.md": [_d("shared/role.md", "include")],
        "agents/b.md": [_d("agents/c.md", "delegate")],
        "agents/c.md": [],
        "shared/role.md": [],
    })
    assert _task_nodes(idx) == {"agents/a.md", "agents/b.md", "agents/c.md"}


def test_task_nodes_empty_when_no_delegates():
    idx = _idx({"a.md": [_d("b.md", "include")], "b.md": []})
    assert _task_nodes(idx) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k task_nodes -v`
Expected: FAIL with `ImportError: cannot import name '_task_nodes'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def _task_nodes(index: dict) -> set[str]:
    """Return the set of `@delegate` target paths (the plan's tasks)."""
    tasks: set[str] = set()
    for entry in index.get("files", {}).values():
        for dep in entry.get("deps", []):
            if dep.get("type") == "delegate":
                tasks.add(dep["to"])
    return tasks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k task_nodes -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _task_nodes (delegate target collection)"
```

---

### Task 3: `_task_dag` and `_task_cycles`

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `_reachable`, `_task_nodes`.
- Produces:
  - `_task_dag(index: dict) -> dict[str, set[str]]` — `{task: set_of_prereq_tasks}` where a prereq is another task reachable from this task's subtree.
  - `_task_cycles(dag: dict[str, set[str]]) -> list[tuple[str, str]]` — unordered mutual-prereq pairs `(a, b)` where `a ∈ dag[b]` and `b ∈ dag[a]`. Each pair appears once.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import _task_dag, _task_cycles


def test_task_dag_links_prereqs_via_subtree():
    # SKILL delegates a; a delegates b. So b is a prereq of a.
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate")],
        "a.md": [_d("b.md", "delegate")],
        "b.md": [],
    })
    dag = _task_dag(idx)
    assert dag == {"a.md": {"b.md"}, "b.md": set()}


def test_task_dag_independent_tasks_have_no_prereqs():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared.md", "include")],
        "b.md": [_d("shared.md", "include")],
        "shared.md": [],
    })
    dag = _task_dag(idx)
    assert dag == {"a.md": set(), "b.md": set()}


def test_task_cycles_detects_mutual_pairs():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    dag = _task_dag(idx)
    cycles = _task_cycles(dag)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a.md", "b.md"}


def test_task_cycles_empty_for_acyclic():
    dag = {"a.md": {"b.md"}, "b.md": set()}
    assert _task_cycles(dag) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k "task_dag or task_cycles" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def _task_dag(index: dict) -> dict[str, set[str]]:
    """Map each task to the set of other tasks reachable from its subtree."""
    tasks = _task_nodes(index)
    return {task: (_reachable(index, task) & tasks) for task in tasks}


def _task_cycles(dag: dict[str, set[str]]) -> list[tuple[str, str]]:
    """Return unordered mutual-prereq task pairs (each pair once)."""
    cycles: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a, prereqs in dag.items():
        for b in prereqs:
            if a in dag.get(b, set()) and (a, b) not in seen:
                cycles.append((a, b))
                seen.add((a, b))
                seen.add((b, a))
    return cycles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k "task_dag or task_cycles" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _task_dag and _task_cycles"
```

---

### Task 4: `_levels` — topological leveling into batches

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Produces: `_levels(dag: dict[str, set[str]], excluded: set[str] | None = None) -> list[list[str]]` — list indexed by level; each element is the sorted list of tasks at that level. `level(t) = 0` if no (non-excluded) prereqs, else `1 + max(level(prereq))`. Excluded tasks (cycle members) are omitted entirely. Prereqs that are excluded are ignored when leveling the remaining tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import _levels


def test_levels_independent_tasks_one_batch():
    dag = {"a.md": set(), "b.md": set()}
    assert _levels(dag) == [["a.md", "b.md"]]


def test_levels_chain_two_batches():
    dag = {"a.md": {"b.md"}, "b.md": set()}
    # b has no prereqs -> level 0; a depends on b -> level 1
    assert _levels(dag) == [["b.md"], ["a.md"]]


def test_levels_excludes_cycle_members():
    dag = {"a.md": {"b.md"}, "b.md": {"a.md"}, "c.md": set()}
    assert _levels(dag, excluded={"a.md", "b.md"}) == [["c.md"]]


def test_levels_empty_when_all_excluded():
    dag = {"a.md": {"b.md"}, "b.md": {"a.md"}}
    assert _levels(dag, excluded={"a.md", "b.md"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k levels -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def _levels(
    dag: dict[str, set[str]],
    excluded: set[str] | None = None,
) -> list[list[str]]:
    """Group tasks into topological levels (parallel batches)."""
    excluded = excluded or set()
    active = {
        task: {p for p in prereqs if p not in excluded}
        for task, prereqs in dag.items()
        if task not in excluded
    }

    memo: dict[str, int] = {}

    def level_of(task: str) -> int:
        if task in memo:
            return memo[task]
        prereqs = active.get(task, set())
        memo[task] = 0 if not prereqs else 1 + max(level_of(p) for p in prereqs)
        return memo[task]

    levels_map = {task: level_of(task) for task in active}
    if not levels_map:
        return []
    max_level = max(levels_map.values())
    return [
        sorted(t for t, lvl in levels_map.items() if lvl == depth)
        for depth in range(max_level + 1)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k levels -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _levels topological batching"
```

---

### Task 5: `_conflicts` — same-batch shared dependencies

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `_reachable`, `_task_nodes`.
- Produces: `_conflicts(index: dict, levels: list[list[str]]) -> list[dict]` — for each unordered pair within a batch whose reachable sets overlap (excluding task nodes), `{"level": int, "between": [a, b], "shared": sorted_list}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import _conflicts


def test_conflicts_reports_shared_dependency_in_batch():
    idx = _idx({
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    conflicts = _conflicts(idx, [["a.md", "b.md"]])
    assert conflicts == [
        {"level": 0, "between": ["a.md", "b.md"], "shared": ["shared/role.md"]}
    ]


def test_conflicts_none_when_no_overlap():
    idx = _idx({
        "a.md": [_d("x.md", "include")],
        "b.md": [_d("y.md", "include")],
        "x.md": [],
        "y.md": [],
    })
    assert _conflicts(idx, [["a.md", "b.md"]]) == []


def test_conflicts_ignore_shared_task_nodes():
    # a and b both reach task c -> c is a task, so not counted as a conflict.
    idx = _idx({
        "a.md": [_d("c.md", "delegate")],
        "b.md": [_d("c.md", "delegate")],
        "c.md": [],
    })
    # batch is [a, b]; their only shared reachable is task c -> excluded
    assert _conflicts(idx, [["a.md", "b.md"]]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k conflicts -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def _conflicts(index: dict, levels: list[list[str]]) -> list[dict]:
    """Report same-batch task pairs that share a non-task dependency."""
    tasks = _task_nodes(index)
    out: list[dict] = []
    for depth, batch in enumerate(levels):
        reach = {task: _reachable(index, task) for task in batch}
        for i in range(len(batch)):
            for j in range(i + 1, len(batch)):
                a, b = batch[i], batch[j]
                shared = (reach[a] & reach[b]) - tasks
                if shared:
                    out.append({
                        "level": depth,
                        "between": [a, b],
                        "shared": sorted(shared),
                    })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k conflicts -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _conflicts (same-batch shared deps)"
```

---

### Task 6: `_context_of` — subtree context for a task

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `_reachable`.
- Produces: `_context_of(index: dict, task: str) -> list[dict]` — for each reachable file present in the index, `{"path": str, "type": str, "title": str}`, sorted by path. Files not in the index (missing) are skipped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import _context_of


def test_context_of_lists_subtree_files_sorted():
    idx = {
        "root": "/x",
        "files": {
            "a.md": {"type": "agent", "title": "A",
                     "deps": [{"to": "shared/role.md", "type": "include"},
                              {"to": "shared/acc.md", "type": "include"}]},
            "shared/role.md": {"type": "shared", "title": "Role", "deps": []},
            "shared/acc.md": {"type": "shared", "title": "Accounts", "deps": []},
        },
        "cycles": [],
        "stats": {"files": 3},
    }
    assert _context_of(idx, "a.md") == [
        {"path": "shared/acc.md", "type": "shared", "title": "Accounts"},
        {"path": "shared/role.md", "type": "shared", "title": "Role"},
    ]


def test_context_of_skips_missing_files():
    idx = {
        "root": "/x",
        "files": {
            "a.md": {"type": "agent", "title": "A",
                     "deps": [{"to": "gone.md", "type": "include"}]},
        },
        "cycles": [],
        "stats": {"files": 1},
    }
    assert _context_of(idx, "a.md") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k context_of -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def _context_of(index: dict, task: str) -> list[dict]:
    """Return subtree files for `task` as {path, type, title}, sorted by path."""
    files = index.get("files", {})
    out: list[dict] = []
    for rel in sorted(_reachable(index, task)):
        entry = files.get(rel)
        if entry is None:
            continue
        out.append({
            "path": rel,
            "type": entry.get("type", "reference"),
            "title": entry.get("title", ""),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k context_of -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add _context_of subtree context"
```

---

### Task 7: `build_plan` — assemble the plan(JSON)

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: all helpers above.
- Produces: `build_plan(index: dict) -> dict` returning:
  ```
  {
    "schema": "dotmd-plan/v1",
    "generated_at": "<ISO8601 Z>",
    "root": index["root"],
    "stats": {"tasks": int, "batches": int, "conflicts": int, "cycles": int},
    "batches": [{"level": int, "parallelizable": bool, "tasks": [str]}],
    "tasks": {task_id: {"title": str, "type": str, "parallel_flag": bool,
                        "depends_on": [str], "context": [ {...} ],
                        "level": None  # only when excluded by a cycle
                       }},
    "conflicts": [ {...} ],
    "cycles": [str],
    "warnings": [str],
  }
  ```
  - `parallel_flag` = OR of the `parallel` flag across all delegate edges pointing at the task.
  - `depends_on` = sorted prereq tasks.
  - `cycles` = `index["cycles"]` (copied) + `"a <-> b (task cycle)"` per mutual pair.
  - `warnings` = `"no @delegate directives found"` when no tasks; `"delegate target missing: <t>"` for each task whose index entry is missing or absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import build_plan


def test_build_plan_parallel_batch_with_conflict():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    plan = build_plan(idx)
    assert plan["schema"] == "dotmd-plan/v1"
    assert plan["stats"] == {"tasks": 2, "batches": 1, "conflicts": 1, "cycles": 0}
    assert plan["batches"] == [
        {"level": 0, "parallelizable": True, "tasks": ["a.md", "b.md"]}
    ]
    assert plan["tasks"]["a.md"]["parallel_flag"] is True
    assert plan["tasks"]["a.md"]["depends_on"] == []
    assert plan["tasks"]["a.md"]["context"] == [
        {"path": "shared/role.md", "type": "shared", "title": "shared/role.md"}
    ]
    assert plan["conflicts"][0]["shared"] == ["shared/role.md"]


def test_build_plan_chain_two_batches():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate")],
        "a.md": [_d("b.md", "delegate")],
        "b.md": [],
    })
    plan = build_plan(idx)
    assert [batch["tasks"] for batch in plan["batches"]] == [["b.md"], ["a.md"]]
    assert plan["tasks"]["a.md"]["depends_on"] == ["b.md"]
    assert plan["batches"][0]["parallelizable"] is False


def test_build_plan_mutual_cycle_excluded_and_reported():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    plan = build_plan(idx)
    assert plan["batches"] == []
    assert plan["stats"]["cycles"] == 1
    assert any("task cycle" in c for c in plan["cycles"])
    assert plan["tasks"]["a.md"]["level"] is None
    assert plan["tasks"]["b.md"]["level"] is None


def test_build_plan_no_delegates_warns():
    idx = _idx({"a.md": [_d("b.md", "include")], "b.md": []})
    plan = build_plan(idx)
    assert plan["stats"]["tasks"] == 0
    assert plan["tasks"] == {}
    assert "no @delegate directives found" in plan["warnings"]


def test_build_plan_missing_target_warns():
    idx = {
        "root": "/x",
        "files": {
            "SKILL.md": {"type": "skill", "title": "Root",
                         "deps": [{"to": "gone.md", "type": "delegate", "parallel": False}]},
            "gone.md": {"type": "agent", "title": "", "missing": True, "deps": []},
        },
        "cycles": [],
        "stats": {"files": 2},
    }
    plan = build_plan(idx)
    assert plan["tasks"]["gone.md"]["context"] == []
    assert any("gone.md" in w for w in plan["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k build_plan -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — add this import at the TOP of the file,
# directly under `from __future__ import annotations`:
#
#     from datetime import datetime, timezone
#
# Then append:

PLAN_SCHEMA = "dotmd-plan/v1"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _task_flags(index: dict) -> dict[str, bool]:
    """Map each task to OR of `--parallel` across its delegate edges."""
    flags: dict[str, bool] = {}
    for entry in index.get("files", {}).values():
        for dep in entry.get("deps", []):
            if dep.get("type") == "delegate":
                target = dep["to"]
                flags[target] = flags.get(target, False) or bool(dep.get("parallel"))
    return flags


def build_plan(index: dict) -> dict:
    """Build the dotmd-plan/v1 structure from a compact index."""
    files = index.get("files", {})
    tasks = _task_nodes(index)
    dag = _task_dag(index)
    flags = _task_flags(index)

    cycle_pairs = _task_cycles(dag)
    excluded: set[str] = set()
    for a, b in cycle_pairs:
        excluded.add(a)
        excluded.add(b)

    levels = _levels(dag, excluded=excluded)
    conflicts = _conflicts(index, levels)

    batches = [
        {"level": depth, "parallelizable": len(batch) > 1, "tasks": batch}
        for depth, batch in enumerate(levels)
    ]

    task_entries: dict[str, dict] = {}
    for task in sorted(tasks):
        entry = files.get(task, {})
        record: dict = {
            "title": entry.get("title", ""),
            "type": entry.get("type", "agent"),
            "parallel_flag": flags.get(task, False),
            "depends_on": sorted(dag.get(task, set())),
            "context": _context_of(index, task),
        }
        if task in excluded:
            record["level"] = None
        task_entries[task] = record

    cycles: list[str] = list(index.get("cycles", []))
    for a, b in cycle_pairs:
        cycles.append(f"{a} <-> {b} (task cycle)")

    warnings: list[str] = []
    if not tasks:
        warnings.append("no @delegate directives found")
    for task in sorted(tasks):
        entry = files.get(task)
        if entry is None or entry.get("missing"):
            warnings.append(f"delegate target missing: {task}")

    return {
        "schema": PLAN_SCHEMA,
        "generated_at": _utc_now(),
        "root": index.get("root", ""),
        "stats": {
            "tasks": len(tasks),
            "batches": len(batches),
            "conflicts": len(conflicts),
            "cycles": len(cycles),
        },
        "batches": batches,
        "tasks": task_entries,
        "conflicts": conflicts,
        "cycles": cycles,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k build_plan -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the whole plan test file**

Run: `python -m pytest tests/test_plan.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add build_plan assembling dotmd-plan/v1"
```

---

### Task 8: `render_ascii` — human-readable plan view

**Files:**
- Modify: `src/dotmd_parser/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: a plan dict from `build_plan`.
- Produces: `render_ascii(plan: dict) -> str` — a compact text view listing each batch (level, parallel marker, tasks), conflicts, and cycles.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
from dotmd_parser.plan import render_ascii


def test_render_ascii_shows_batches_and_conflicts():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    text = render_ascii(build_plan(idx))
    assert "Level 0" in text
    assert "a.md" in text and "b.md" in text
    assert "conflict" in text.lower()


def test_render_ascii_reports_cycles():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    text = render_ascii(build_plan(idx))
    assert "cycle" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k render_ascii -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/plan.py — append
def render_ascii(plan: dict) -> str:
    """Render a plan as a compact human-readable text view."""
    stats = plan.get("stats", {})
    lines: list[str] = [
        f"# dotmd plan — {stats.get('tasks', 0)} tasks, "
        f"{stats.get('batches', 0)} batches",
    ]
    for batch in plan.get("batches", []):
        marker = " (parallel)" if batch.get("parallelizable") else ""
        lines.append(f"Level {batch['level']}{marker}:")
        for task in batch.get("tasks", []):
            flag = " ‖" if plan["tasks"].get(task, {}).get("parallel_flag") else ""
            lines.append(f"  - {task}{flag}")
    conflicts = plan.get("conflicts", [])
    if conflicts:
        lines.append("")
        lines.append("Conflicts (warning — parallel kept):")
        for c in conflicts:
            pair = " & ".join(c["between"])
            lines.append(f"  - L{c['level']}: {pair} share {', '.join(c['shared'])}")
    cycles = plan.get("cycles", [])
    if cycles:
        lines.append("")
        lines.append("Cycles (error):")
        for c in cycles:
            lines.append(f"  - {c}")
    warnings = plan.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k render_ascii -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/plan.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: add render_ascii plan view"
```

---

### Task 9: Invariant tests (antichain + coverage)

**Files:**
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `build_plan`, `_task_dag`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
def test_invariant_batches_are_antichains_and_cover_all_tasks():
    idx = _idx({
        "SKILL.md": [
            _d("a.md", "delegate", True),
            _d("b.md", "delegate", True),
            _d("c.md", "delegate"),
        ],
        "a.md": [_d("shared.md", "include")],
        "b.md": [_d("a.md", "delegate")],   # b depends on a
        "c.md": [],
        "shared.md": [],
    })
    plan = build_plan(idx)
    dag = _task_dag(idx)

    # Every non-excluded task appears exactly once across batches.
    flat = [t for batch in plan["batches"] for t in batch["tasks"]]
    assert len(flat) == len(set(flat))
    excluded = {t for t, rec in plan["tasks"].items() if rec.get("level") is None}
    assert set(flat) == (set(plan["tasks"]) - excluded)

    # Antichain: no task shares a batch with one of its prereqs.
    for batch in plan["batches"]:
        members = set(batch["tasks"])
        for task in batch["tasks"]:
            assert not (dag[task] & members), f"{task} batched with a prereq"

    # Levels are contiguous and ascending.
    assert [b["level"] for b in plan["batches"]] == list(range(len(plan["batches"])))
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `python -m pytest tests/test_plan.py -k invariant -v`
Expected: PASS (the implementation already satisfies these; this test locks the invariant). If it FAILS, fix `_levels`/`build_plan` until it passes — do not weaken the test.

- [ ] **Step 3: Run the full plan test file**

Run: `python -m pytest tests/test_plan.py -v`
Expected: PASS (all tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_plan.py
git -c commit.gpgsign=false commit -m "test: lock batch antichain + coverage invariants"
```

---

### Task 10: Export `build_plan` from the package

**Files:**
- Modify: `src/dotmd_parser/__init__.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Produces: `from dotmd_parser import build_plan, render_ascii` works.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — append
def test_build_plan_is_exported_from_package():
    import dotmd_parser
    assert hasattr(dotmd_parser, "build_plan")
    assert hasattr(dotmd_parser, "render_ascii")
    assert "build_plan" in dotmd_parser.__all__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan.py -k exported -v`
Expected: FAIL with `AssertionError` (not yet exported).

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/__init__.py`, add after the `from dotmd_parser.digest import (...)` block:

```python
from dotmd_parser.plan import (
    build_plan,
    render_ascii,
)
```

And in the `__all__` list, add after the `# digest` group entries (`"deps_of",`):

```python
    # plan
    "build_plan",
    "render_ascii",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan.py -k exported -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/__init__.py tests/test_plan.py
git -c commit.gpgsign=false commit -m "feat: export build_plan/render_ascii from package"
```

---

### Task 11: CLI `plan` subcommand

**Files:**
- Modify: `src/dotmd_parser/cli.py`
- Test: `tests/test_cli_plan.py`

**Interfaces:**
- Consumes: `build_plan`, `render_ascii` from `dotmd_parser.plan`; existing `_load_or_build_index`, `_maybe_warn_empty`.
- Produces: `dotmd-parser plan <path> [--json] [--ascii] [--out FILE] [--no-cache] [--strict]`.
  - Default / `--json`: pretty JSON to stdout.
  - `--ascii` alone: ASCII only to stdout.
  - `--ascii --json`: ASCII then JSON to stdout.
  - `--out FILE`: JSON written to FILE (not stdout). With `--ascii`, ASCII still goes to stdout.
  - `--strict`: exit 1 when `stats.cycles` or `stats.conflicts` > 0.

**CLI output decision rule** (implement exactly):
```
print ASCII to stdout  iff  args.ascii
write JSON to FILE      iff  args.out
print JSON to stdout    iff  (not args.ascii or args.json) and not args.out
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_plan.py
import json

import pytest

from dotmd_parser.cli import run


def _make_skill(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "shared").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\n@delegate agents/a.md --parallel\n@delegate agents/b.md --parallel\n",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "a.md").write_text(
        "# A\n\n@include ../shared/role.md\n", encoding="utf-8"
    )
    (tmp_path / "agents" / "b.md").write_text(
        "# B\n\n@include ../shared/role.md\n", encoding="utf-8"
    )
    (tmp_path / "shared" / "role.md").write_text("# Role\n", encoding="utf-8")
    return tmp_path


def test_plan_json_to_stdout(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert plan["schema"] == "dotmd-plan/v1"
    assert plan["stats"]["tasks"] == 2
    assert plan["stats"]["conflicts"] == 1


def test_plan_ascii_only(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--ascii"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Level 0" in out
    # ascii-only: stdout must not be JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_plan_out_writes_file(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    out_file = tmp_path / "plan.json"
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--out", str(out_file)])
    assert exc.value.code == 0
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["stats"]["tasks"] == 2
    assert capsys.readouterr().out.strip() == ""  # nothing on stdout


def test_plan_strict_exits_zero_without_cycles(tmp_path):
    skill = _make_skill(tmp_path)
    # conflicts exist here, so --strict should exit 1
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--strict"])
    assert exc.value.code == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_plan.py -v`
Expected: FAIL — `plan` is not a known command (argparse error / SystemExit code 2 or similar).

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/cli.py`:

(a) Add the import near the other module imports (after the `from dotmd_parser.index_md import (...)` block):

```python
from dotmd_parser.plan import build_plan as _build_plan, render_ascii as _render_ascii
```

(b) Add the command handler (place it next to `cmd_tree`):

```python
def cmd_plan(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path, use_cache=not args.no_cache)
    plan = _build_plan(idx)

    if args.ascii:
        print(_render_ascii(plan))

    payload = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    elif (not args.ascii) or args.json:
        print(payload)

    if idx.get("stats", {}).get("files", 0) == 0:
        _maybe_warn_empty(args.path)

    stats = plan.get("stats", {})
    if args.strict and (stats.get("cycles") or stats.get("conflicts")):
        return 1
    return 0
```

(c) Register the subparser inside `_build_parser()` (after the `p_tree` block, before `p_resolve`):

```python
    p_plan = sub.add_parser("plan", help="Generate a parallel @delegate execution plan (JSON)")
    p_plan.add_argument("path", help="Directory or SKILL.md")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON to stdout (default behavior)")
    p_plan.add_argument("--ascii", action="store_true", help="Print a human-readable ASCII plan view")
    p_plan.add_argument("--out", help="Write JSON to a file instead of stdout")
    p_plan.add_argument("--no-cache", action="store_true", help="Force rebuild instead of using saved index")
    p_plan.add_argument("--strict", action="store_true", help="Exit 1 when cycles or conflicts are present")
    p_plan.set_defaults(func=cmd_plan)
```

(d) Add `"plan"` to the `known_cmds` set inside `run()`:

```python
    known_cmds = {"init", "index", "check", "affects", "deps", "digest", "tree", "resolve", "analyze", "inventory", "dotmd-index", "show", "plan"}
```

(e) Add one line to the module docstring subcommand list (under the `tree` line):

```python
- `plan    <path>`         Parallel @delegate execution plan (JSON).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_plan.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (all existing tests + new plan tests; no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py tests/test_cli_plan.py
git -c commit.gpgsign=false commit -m "feat: add plan CLI subcommand"
```

---

### Task 12: Docs — CHANGELOG + README

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.ja.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

At the top of `CHANGELOG.md` (above the `## [0.7.0]` entry), insert:

```markdown
## [0.8.0] - 2026-06-20

### Added
- **`plan` サブコマンド** — 依存グラフから `@delegate` の並列実行プランを
  静的生成。topological レベルを並列バッチ化し、各タスクに subtree の
  context を同梱した `dotmd-plan/v1` JSON を出力。同一バッチ内の共有依存を
  `conflicts[]`（警告のみ・並列維持）、相互到達タスクを `cycles[]` として
  事前検出する。`--ascii` で人間可読ビュー、`--strict` で CI ゲート、
  `--out` でファイル出力。`build_plan` / `render_ascii` を公開 API に追加。
  設計: `docs/superpowers/specs/2026-06-20-parallel-delegation-plan-design.md`
```

- [ ] **Step 2: Add a README section (English)**

In `README.md`, find the section documenting subcommands (near `affects` / `deps` / `tree`) and add:

```markdown
### `plan` — parallel delegation plan

Generate a static execution plan from the `@delegate` graph: topological
batches (parallel levels), per-task subtree context, plus conflict and cycle
pre-detection. Intended for a parent agent that fans out subagents.

```bash
dotmd-parser plan ./my-skill            # plan(JSON) to stdout
dotmd-parser plan ./my-skill --ascii    # human-readable view
dotmd-parser plan ./my-skill --out plan.json
dotmd-parser plan ./my-skill --strict   # exit 1 on cycles/conflicts (CI)
```

Each task in the JSON carries a `context` array (the subtree files to hand the
subagent). Same-batch shared dependencies are reported in `conflicts[]` as
warnings — the batch stays parallel. Mutual `@delegate` references are reported
in `cycles[]` and excluded from batches.
```

- [ ] **Step 3: Add a README section (Japanese)**

In `README.ja.md`, add the equivalent section near the other subcommands:

```markdown
### `plan` — 並列委譲プラン

`@delegate` グラフから実行プランを静的生成します。topological バッチ
（並列レベル）、各タスクの subtree context、競合・循環の事前検出を含み、
サブエージェントを fan-out する親エージェントが消費する想定です。

```bash
dotmd-parser plan ./my-skill            # plan(JSON) を stdout へ
dotmd-parser plan ./my-skill --ascii    # 人間可読ビュー
dotmd-parser plan ./my-skill --out plan.json
dotmd-parser plan ./my-skill --strict   # 循環/競合で exit 1 (CI)
```

各タスクは `context`（サブエージェントに渡す subtree ファイル）を持ちます。
同一バッチ内の共有依存は `conflicts[]` に警告として記録され、バッチは並列の
まま維持されます。相互 `@delegate` は `cycles[]` に記録しバッチから除外します。
```

- [ ] **Step 4: Verify the suite still passes**

Run: `python -m pytest -q`
Expected: PASS (docs changes don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md README.ja.md
git -c commit.gpgsign=false commit -m "docs: document plan subcommand (0.8.0)"
```

---

## Self-Review

**1. Spec coverage:**
- §3 module `plan.py` pure functions → Tasks 1–8. ✓
- §4 algorithm (reachable, task nodes, task DAG, levels, conflicts, cycles, context) → Tasks 1–7. ✓
- §5 JSON schema `dotmd-plan/v1` (all fields incl. `parallel_flag` OR-merge, `depends_on`, `context`, `level:null` on cycle, `warnings`) → Task 7. ✓
- §6 CLI (`--json`/`--ascii`/`--out`/`--no-cache`/`--strict`, output decision rule, `known_cmds`, docstring, `__init__` export) → Tasks 10–11. ✓
- §7 error handling (empty/no-delegate/missing/bad path) → Task 7 (warnings) + Task 11 (empty hint via `_maybe_warn_empty`, bad path via existing `_load_or_build_index`). ✓
- §8 implementation steps → Tasks 1–12. ✓
- §9 testing (unit, CLI, invariants, 80%+) → Tasks 1–9, 11. ✓
- ASCII renderer (§3/§6 `--ascii`) → Task 8. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases" placeholders; every code step shows full code and exact commands. ✓

**3. Type consistency:** Names used consistently across tasks — `_reachable`, `_task_nodes`, `_task_dag`, `_task_cycles`, `_levels(dag, excluded=...)`, `_conflicts(index, levels)`, `_context_of`, `_task_flags`, `build_plan`, `render_ascii`. `build_plan` consumes exactly these signatures. CLI uses `_build_plan`/`_render_ascii` aliases for the imported `build_plan`/`render_ascii`. `dotmd-plan/v1` field names match between Task 7 producer and Tasks 8/11 consumers. ✓

Note: bad-path handling (exit 2) is inherited from the existing `_load_or_build_index` → `build_index` → `build_graph` path; no dedicated task needed (matches how `affects`/`deps`/`tree` behave today).
