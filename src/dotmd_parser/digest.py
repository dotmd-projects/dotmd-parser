"""
dotmd-parser — token-efficient digest for Claude Code consumption.

The raw graph from `build_graph()` contains absolute paths and per-edge
records that cost unnecessary tokens when fed to an LLM. This module turns
an index (from `index.compact_graph`) into compact, human-readable views:

- `digest(index)` — one-screen summary (skill topology + health).
- `tree(index, root_rel)` — ASCII dependency tree.
- `affects(index, target_rel)` — reverse-dependency list.
- `deps(index, source_rel)` — forward-dependency list.
"""

from __future__ import annotations


def _entry_label(rel: str, entry: dict) -> str:
    parts = [rel]
    title = entry.get("title")
    if title:
        parts.append(f"— {title}")
    if entry.get("missing"):
        parts.append("[MISSING]")
    return " ".join(parts)


def _iter_deps(index: dict, rel: str):
    entry = index.get("files", {}).get(rel, {})
    for dep in entry.get("deps", []):
        yield dep["to"], dep.get("type", "include"), bool(dep.get("parallel"))


def digest(index: dict, max_desc: int = 80) -> str:
    """
    Return a compact text summary of the index — intended to be pasted into
    a Claude Code context window in place of reading many `.md` files.

    Token budget target: ≤ ~500 tokens for a typical skill (< 30 files).
    """
    files: dict = index.get("files", {})
    stats: dict = index.get("stats", {})

    lines: list[str] = []
    lines.append(f"# dotmd index — {stats.get('files', len(files))} files")
    edge_types = stats.get("edge_types", {})
    if edge_types:
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(edge_types.items()))
        lines.append(f"Edges: {stats.get('edges', 0)} ({parts})")
    cycles = stats.get("cycles", 0)
    missing = stats.get("missing", 0)
    health = "OK" if (cycles == 0 and missing == 0) else f"cycles:{cycles} missing:{missing}"
    lines.append(f"Health: {health}")

    if cycles:
        lines.append("")
        lines.append("## Cycles")
        for c in index.get("cycles", []):
            lines.append(f"- {c}")

    if missing:
        lines.append("")
        lines.append("## Missing")
        for m in index.get("missing", []):
            lines.append(f"- {m}")

    # One line per file: type | path | title — trimmed desc
    lines.append("")
    lines.append("## Files")
    for rel in sorted(files):
        entry = files[rel]
        ftype = entry.get("type", "?")
        title = entry.get("title", "")
        desc = entry.get("desc", "")
        if len(desc) > max_desc:
            desc = desc[: max_desc - 1].rstrip() + "…"
        flag = " [MISSING]" if entry.get("missing") else ""
        header = f"- [{ftype}] {rel}{flag}"
        if title:
            header += f" — {title}"
        lines.append(header)
        if desc:
            lines.append(f"  {desc}")
        deps = entry.get("deps", [])
        if deps:
            dep_strs = [f"{d['type']}→{d['to']}" for d in deps]
            lines.append(f"  deps: {', '.join(dep_strs)}")

    placeholders: set[str] = set()
    for entry in files.values():
        for p in entry.get("placeholders", []) or []:
            placeholders.add(p)
    if placeholders:
        lines.append("")
        lines.append(f"Placeholders: {', '.join(sorted(placeholders))}")

    return "\n".join(lines)


def tree(index: dict, root_rel: str | None = None, max_depth: int = 6) -> str:
    """
    Render an ASCII dependency tree starting from `root_rel` (defaults to the
    first `skill` node, or the first file).
    """
    files: dict = index.get("files", {})
    if not files:
        return "(empty index)"

    if root_rel is None:
        skill_nodes = [rel for rel, e in files.items() if e.get("type") == "skill"]
        root_rel = skill_nodes[0] if skill_nodes else next(iter(files))

    lines: list[str] = []
    seen_on_path: set[str] = set()

    def walk(rel: str, prefix: str, is_last: bool, depth: int) -> None:
        entry = files.get(rel, {})
        connector = "└── " if is_last else "├── "
        label = _entry_label(rel, entry)
        if rel in seen_on_path:
            lines.append(f"{prefix}{connector}{label}  [cycle]")
            return
        lines.append(f"{prefix}{connector}{label}")
        if depth >= max_depth:
            return
        children = list(_iter_deps(index, rel))
        if not children:
            return
        seen_on_path.add(rel)
        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, (child, kind, parallel) in enumerate(children):
            tag = f"[{kind}{'‖' if parallel else ''}]"
            child_prefix = new_prefix
            is_last_child = i == len(children) - 1
            connector = "└── " if is_last_child else "├── "
            if child not in files:
                lines.append(f"{child_prefix}{connector}{tag} {child} [MISSING]")
                continue
            lines.append(f"{child_prefix}{connector}{tag} {_entry_label(child, files[child])}")
            grand_prefix = child_prefix + ("    " if is_last_child else "│   ")
            seen_on_path.add(child)
            grandchildren = list(_iter_deps(index, child))
            for j, (gc, gk, gp) in enumerate(grandchildren):
                walk(gc, grand_prefix, j == len(grandchildren) - 1, depth + 2)
            seen_on_path.discard(child)
        seen_on_path.discard(rel)

    lines.append(_entry_label(root_rel, files.get(root_rel, {})))
    children = list(_iter_deps(index, root_rel))
    seen_on_path.add(root_rel)
    for i, (child, kind, parallel) in enumerate(children):
        tag = f"[{kind}{'‖' if parallel else ''}]"
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        if child not in files:
            lines.append(f"{connector}{tag} {child} [MISSING]")
            continue
        lines.append(f"{connector}{tag} {_entry_label(child, files[child])}")
        prefix = "    " if is_last else "│   "
        grandchildren = list(_iter_deps(index, child))
        seen_on_path.add(child)
        for j, (gc, gk, gp) in enumerate(grandchildren):
            walk(gc, prefix, j == len(grandchildren) - 1, 2)
        seen_on_path.discard(child)
    seen_on_path.discard(root_rel)
    return "\n".join(lines)


def affects(index: dict, target_rel: str) -> list[str]:
    """
    Return the relative paths that depend (directly or transitively) on
    `target_rel` — i.e. files that may break if it changes.
    """
    reverse: dict[str, list[str]] = {}
    for src, entry in index.get("files", {}).items():
        for dep in entry.get("deps", []):
            reverse.setdefault(dep["to"], []).append(src)

    visited: set[str] = set()
    queue = [target_rel]
    result: list[str] = []
    while queue:
        current = queue.pop(0)
        for parent in reverse.get(current, []):
            if parent not in visited:
                visited.add(parent)
                result.append(parent)
                queue.append(parent)
    return result


def deps_of(index: dict, source_rel: str) -> list[dict]:
    """Return the direct dependencies (deps array) of the given file."""
    return list(index.get("files", {}).get(source_rel, {}).get("deps", []))
