"""
dotmd-parser — cache-affine ordering helpers.

Estimates per-file change frequency from git history (with a safe fallback)
and provides an ordering key that puts low-frequency files first, so the
`dotmd-index.md` body prefix stays stable across regenerations (KV-cache
friendly). Also a prefix-stability metric. Pure stdlib; git via subprocess.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_change_counts(root: str | Path) -> dict[str, int]:
    """Return {rel_posix: commit_count} from git history; {} when unavailable."""
    if shutil.which("git") is None:
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false",
             "log", "--format=", "--name-only", "--relative", "--", "."],
            capture_output=True,
            text=True,
        )
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        rel = line.strip()
        if rel:
            counts[rel] = counts.get(rel, 0) + 1
    return counts


def order_key(rel: str, counts: dict[str, int]) -> tuple[int, str]:
    """Sort key: low change-count first, path-ascending tiebreak."""
    return (counts.get(rel, 0), rel)


def prefix_stability(old_text: str, new_text: str) -> dict:
    """Measure how much of `new_text`'s leading lines match `old_text`."""
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    common = 0
    for old_line, new_line in zip(old_lines, new_lines):
        if old_line == new_line:
            common += 1
        else:
            break
    total_new = len(new_lines)
    return {
        "common_prefix_lines": common,
        "new_lines": total_new,
        "ratio": round(common / max(total_new, 1), 4),
    }
