"""
dotmd-parser — persistent index module.

Saves a compact, token-efficient dependency index to
`<root>/.claude/dotmd-index.json` so that Claude Code (and other tools) can
answer dependency questions without scanning every `.md` file.

Key goals:
- **Relative paths** everywhere (the raw graph uses absolute paths).
- **Truncated sha256** for cache invalidation without bloat.
- **Inline descriptions** (first H1 + first paragraph) so consumers can tell
  what each file is about without reading it.
- **Flat "files" map** keyed by relative path — O(1) lookup, shorter JSON.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dotmd_parser.parser import build_graph

INDEX_SCHEMA = 1
DEFAULT_INDEX_DIR = ".claude"
DEFAULT_INDEX_FILE = "dotmd-index.json"


def default_index_path(root: str | Path) -> Path:
    """Return the default on-disk index location: `<root>/.claude/dotmd-index.json`."""
    return Path(root) / DEFAULT_INDEX_DIR / DEFAULT_INDEX_FILE


def _rel(base: Path, target: str | Path) -> str:
    """Return `target` as a POSIX-style path relative to `base`, falling back to absolute."""
    try:
        return Path(target).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return Path(target).as_posix()


def compact_graph(graph: dict, root: str | Path) -> dict:
    """
    Convert a `build_graph()` result into the on-disk index format.

    - Absolute paths are rewritten relative to `root`.
    - Per-file dependencies are grouped into a `deps` list (to, type).
    - Empty metadata fields are omitted to keep the JSON small.
    """
    base = Path(root).resolve()
    if base.is_file():
        base = base.parent

    edges_by_from: dict[str, list[dict]] = {}
    for edge in graph["edges"]:
        from_rel = _rel(base, edge["from"])
        to_rel = _rel(base, edge["to"])
        entry = {"to": to_rel, "type": edge["type"]}
        if edge.get("parallel"):
            entry["parallel"] = True
        edges_by_from.setdefault(from_rel, []).append(entry)

    files: dict[str, dict] = {}
    missing: list[str] = []
    for node in graph["nodes"]:
        rel = _rel(base, node["id"])
        entry: dict = {"type": node["type"]}
        if node.get("missing"):
            entry["missing"] = True
            missing.append(rel)
        for key in ("title", "desc", "hash"):
            value = node.get(key) or ""
            if value:
                entry[key] = value
        if node.get("size"):
            entry["size"] = node["size"]
        if node.get("placeholders"):
            entry["placeholders"] = list(node["placeholders"])
        if rel in edges_by_from:
            entry["deps"] = edges_by_from[rel]
        files[rel] = entry

    cycles = [w for w in graph["warnings"] if w["type"] == "circular"]
    edge_types: dict[str, int] = {}
    for edge in graph["edges"]:
        edge_types[edge["type"]] = edge_types.get(edge["type"], 0) + 1

    warnings: list[dict] = []
    for w in graph["warnings"]:
        warnings.append({
            "type": w["type"],
            "path": _rel(base, w["path"]),
            "message": w["message"],
        })

    return {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "root": str(base),
        "stats": {
            "files": len(files),
            "edges": len(graph["edges"]),
            "edge_types": edge_types,
            "cycles": len(cycles),
            "missing": len(missing),
        },
        "files": files,
        "cycles": [c["message"] for c in cycles],
        "missing": missing,
        "warnings": warnings,
    }


def build_index(root: str | Path, type_map: list[tuple[str, str]] | None = None) -> dict:
    """Convenience: run `build_graph` then `compact_graph`."""
    graph = build_graph(str(root), type_map=type_map)
    return compact_graph(graph, root)


def save_index(
    index: dict,
    root: str | Path,
    out_path: str | Path | None = None,
) -> Path:
    """
    Write the index to disk. Returns the resolved output path.

    Defaults to `<root>/.claude/dotmd-index.json`. The parent directory is
    created if missing.
    """
    target = Path(out_path) if out_path else default_index_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_index(path: str | Path) -> dict:
    """Load a previously saved index from disk."""
    content = Path(path).read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"Unsupported or missing index schema in {path}")
    return data


def _hash_of(base: Path, rel: str) -> str:
    """Compute the current truncated sha256 for a file under `base`. Empty string on error."""
    from dotmd_parser.parser import hash_content  # local import avoids circulars
    path = base / rel
    try:
        return hash_content(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def needs_rebuild(index: dict, root: str | Path) -> bool:
    """
    Return True when any tracked (non-missing) file's hash differs from the
    one recorded in the index, or when a recorded file has vanished.
    """
    base = Path(root).resolve()
    if base.is_file():
        base = base.parent
    for rel, entry in index.get("files", {}).items():
        if entry.get("missing"):
            continue
        path = base / rel
        if not path.exists():
            return True
        recorded = entry.get("hash", "")
        if recorded and recorded != _hash_of(base, rel):
            return True
    return False


def build_scoped_index(root: str | Path, scope: str) -> dict:
    """Build an index for files under `<root>/<scope>` with paths rewritten
    to be relative to `root` (not `scope`).

    Intended for incremental re-indexing of large documentation repos where
    only one subfolder changed. Pair with `merge_index()` to splice the
    result into an existing full-root index.

    Raises:
        ValueError: if `<root>/<scope>` does not exist or is not a directory.
    """
    base = Path(root).resolve()
    scope_norm = scope.replace("\\", "/").strip("/")
    if not scope_norm:
        raise ValueError("scope cannot be empty")
    scope_dir = base / scope_norm
    if not scope_dir.exists():
        raise ValueError(f"scope not found: {scope}")
    if not scope_dir.is_dir():
        raise ValueError(f"scope is not a directory: {scope}")

    sub_idx = build_index(str(scope_dir))
    prefixed_files: dict[str, dict] = {}
    for rel, entry in sub_idx["files"].items():
        new_rel = f"{scope_norm}/{rel}"
        new_entry = dict(entry)
        if "deps" in new_entry:
            new_entry["deps"] = [
                {**dep, "to": f"{scope_norm}/{dep['to']}"}
                for dep in new_entry["deps"]
            ]
        prefixed_files[new_rel] = new_entry

    sub_idx["files"] = prefixed_files
    sub_idx["root"] = str(base)
    sub_idx["missing"] = [f"{scope_norm}/{m}" for m in sub_idx.get("missing", [])]
    sub_idx["stats"]["files"] = len(prefixed_files)
    # scope is recorded so merge_index can locate which slice to replace
    sub_idx["scope"] = scope_norm
    return sub_idx


def merge_index(existing: dict, new: dict, scope: str) -> dict:
    """Replace the `scope` slice of `existing` with entries from `new`.

    - Files under `<scope>/` in `existing` are removed.
    - All files in `new` (which must be pre-prefixed) are added.
    - Files outside the scope are preserved untouched.
    - Stats are recomputed. Warnings/cycles are union-merged.
    """
    scope_norm = scope.replace("\\", "/").strip("/")
    prefix = f"{scope_norm}/"

    merged_files: dict[str, dict] = {}
    for rel, entry in existing.get("files", {}).items():
        if not rel.startswith(prefix):
            merged_files[rel] = entry
    for rel, entry in new.get("files", {}).items():
        merged_files[rel] = entry

    edge_types: dict[str, int] = {}
    edge_count = 0
    for entry in merged_files.values():
        for dep in entry.get("deps", []):
            edge_count += 1
            edge_types[dep["type"]] = edge_types.get(dep["type"], 0) + 1

    missing = [rel for rel, entry in merged_files.items() if entry.get("missing")]

    # Union-merge of cycles/warnings; dedupe by message.
    cycles_set: list[str] = []
    for c in existing.get("cycles", []) + new.get("cycles", []):
        if c not in cycles_set:
            cycles_set.append(c)

    warnings_out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for w in existing.get("warnings", []) + new.get("warnings", []):
        key = (w.get("type", ""), w.get("path", ""), w.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        warnings_out.append(w)

    return {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "root": existing.get("root") or new.get("root"),
        "stats": {
            "files": len(merged_files),
            "edges": edge_count,
            "edge_types": edge_types,
            "cycles": len(cycles_set),
            "missing": len(missing),
        },
        "files": merged_files,
        "cycles": cycles_set,
        "missing": missing,
        "warnings": warnings_out,
    }


def changed_files(index: dict, root: str | Path) -> list[str]:
    """Return the relative paths whose content no longer matches the index."""
    base = Path(root).resolve()
    if base.is_file():
        base = base.parent
    changed: list[str] = []
    for rel, entry in index.get("files", {}).items():
        if entry.get("missing"):
            continue
        path = base / rel
        if not path.exists():
            changed.append(rel)
            continue
        recorded = entry.get("hash", "")
        if recorded and recorded != _hash_of(base, rel):
            changed.append(rel)
    return changed
