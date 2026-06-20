"""
dotmd-parser — static parallel-delegation plan generator.

Consumes a compact index (from `index.build_index` / `index.load_index`) and
produces an execution plan: parallel batches (topological levels) over
`@delegate` targets, with per-task subtree context, conflict detection
(same-batch shared dependencies — warning only), and cycle detection.

Pure functions, stdlib only. The raw graph / parser are not touched.
"""

from __future__ import annotations

from datetime import datetime, timezone


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


def _task_nodes(index: dict) -> set[str]:
    """Return the set of `@delegate` target paths (the plan's tasks)."""
    tasks: set[str] = set()
    for entry in index.get("files", {}).values():
        for dep in entry.get("deps", []):
            if dep.get("type") == "delegate":
                tasks.add(dep["to"])
    return tasks


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
