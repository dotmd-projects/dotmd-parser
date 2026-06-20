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
