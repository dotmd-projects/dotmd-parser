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
