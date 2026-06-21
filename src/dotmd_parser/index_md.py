"""
dotmd-parser — single-file folder overview (`dotmd-index.md`).

Generates a Markdown artifact at `<root>/dotmd-index.md` that combines
`inventory()` (filesystem stats) and `build_index()` (dependency graph)
into one self-contained file. Claude (or any RAG ingester) can read this
single file instead of grep/Read-ing every document in the folder.

Design points
-------------
- **Frontmatter** is YAML-shaped but emitted/parsed by a tiny in-house
  helper so we keep the project's stdlib-only stance. The shape is
  intentionally small (scalars, one-level nested dicts, list of flat dicts).
- **content_hash** excludes timestamps so re-runs are idempotent and writes
  are skipped when the folder contents haven't changed.
- **chunk markers** (`<!-- chunk:id -->`) bracket each section so RAG tools
  can split on them deterministically.
- **safety valve**: refuses to overwrite an existing `dotmd-index.md` whose
  frontmatter doesn't say `generated_by: dotmd-parser` (use `force=True`
  to override).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotmd_parser import __version__
from dotmd_parser.cache_order import git_change_counts, order_key
from dotmd_parser.digest import tree as _dep_tree
from dotmd_parser.index import build_index
from dotmd_parser.inventory import (
    BINARY_EXTENSIONS,
    MARKDOWN_EXTENSIONS,
    inventory,
)

INDEX_MD_SCHEMA = "dotmd-index/v1"
DEFAULT_INDEX_FILENAME = "dotmd-index.md"
HASH_PREFIX = "sha256:"
HASH_LENGTH = 32

_BINARY_HINT_EXT = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp3", ".mp4", ".mov", ".wav", ".zip", ".tar", ".gz", ".7z",
}


# ---------------------------------------------------------------------------
# YAML helpers (minimal — only handles the shapes we emit)
# ---------------------------------------------------------------------------

def _yaml_scalar(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if not s:
        return '""'
    if any(c in s for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "<", ">", "!", "%", "@")):
        return '"' + s.replace('"', '\\"') + '"'
    if s != s.strip():
        return '"' + s + '"'
    return s


def _yaml_dump(data: dict, indent: int = 0) -> str:
    pad = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            if not value:
                lines.append(f"{pad}{key}: {{}}")
                continue
            lines.append(f"{pad}{key}:")
            lines.append(_yaml_dump(value, indent + 1))
            continue
        if isinstance(value, list):
            if not value:
                lines.append(f"{pad}{key}: []")
                continue
            lines.append(f"{pad}{key}:")
            for item in value:
                if isinstance(item, dict):
                    items = list(item.items())
                    if not items:
                        lines.append(f"{pad}  - {{}}")
                        continue
                    first_k, first_v = items[0]
                    lines.append(f"{pad}  - {first_k}: {_yaml_scalar(first_v)}")
                    for k, v in items[1:]:
                        lines.append(f"{pad}    {k}: {_yaml_scalar(v)}")
                else:
                    lines.append(f"{pad}  - {_yaml_scalar(item)}")
            continue
        lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
    return "\n".join(lines)


def _parse_scalar(text: str) -> Any:
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~"):
        return None
    if text.lstrip("-").isdigit():
        try:
            return int(text)
        except ValueError:
            pass
    return text


def _parse_dict_block(lines: list[str], start: int, base_indent: int) -> tuple[dict, int]:
    """Parse a contiguous block of YAML key:value pairs at `base_indent`.

    Recursively descends into nested dicts and lists when a key's value is
    blank and the following line has a deeper indent.
    """
    out: dict[str, Any] = {}
    i = start
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent < base_indent:
            break
        if indent > base_indent:
            i += 1
            continue
        stripped = raw.strip()
        if ":" not in stripped:
            i += 1
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "{}":
            out[key] = {}
            i += 1
            continue
        if value == "[]":
            out[key] = []
            i += 1
            continue
        if value:
            out[key] = _parse_scalar(value)
            i += 1
            continue
        # Empty value — descend into a nested dict or list
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            out[key] = None
            i += 1
            continue
        next_indent = len(lines[j]) - len(lines[j].lstrip())
        if next_indent <= base_indent:
            out[key] = None
            i += 1
            continue
        next_stripped = lines[j].lstrip()
        if next_stripped.startswith("- "):
            items, consumed = _parse_list_block(lines, j, next_indent)
            out[key] = items
            i = consumed
            continue
        sub, consumed = _parse_dict_block(lines, j, next_indent)
        out[key] = sub
        i = consumed
    return out, i


def _parse_list_block(lines: list[str], start: int, base_indent: int) -> tuple[list, int]:
    out: list[Any] = []
    i = start
    current: dict | None = None
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent < base_indent:
            break
        stripped = raw.lstrip()
        if indent == base_indent and stripped.startswith("- "):
            after = stripped[2:].strip()
            if ":" in after:
                k, _, v = after.partition(":")
                current = {k.strip(): _parse_scalar(v.strip())}
                out.append(current)
            else:
                out.append(_parse_scalar(after))
                current = None
            i += 1
            continue
        if current is not None and indent > base_indent and ":" in stripped:
            k, _, v = stripped.partition(":")
            current[k.strip()] = _parse_scalar(v.strip())
            i += 1
            continue
        i += 1
    return out, i


def extract_frontmatter(md: str) -> dict:
    """Return the YAML frontmatter dict from `md`, or `{}` if none.

    Supports the shapes emitted by `_yaml_dump`: scalars, nested dicts
    (arbitrary depth), and lists of flat dicts.
    """
    if not md.startswith("---\n"):
        return {}
    end_marker = md.find("\n---", 4)
    if end_marker == -1:
        return {}
    block = md[4:end_marker]
    lines = block.splitlines()
    out, _ = _parse_dict_block(lines, 0, 0)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _is_hidden_rel(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def _walk_files(root: Path):
    """Yield (rel_path_posix, size) for non-hidden, non-lock files."""
    for p in sorted(root.rglob("*")):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if _is_hidden_rel(rel):
            continue
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if p.name.startswith("~$"):
            continue
        if p.name == DEFAULT_INDEX_FILENAME:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        yield rel.as_posix(), size


def _compute_content_hash(root: Path, idx: dict, order: str = "alpha") -> str:
    """Stable hash over (rel_path, size, file_content_hash) tuples.

    Excludes timestamps and absolute paths so re-runs match when content
    is unchanged. Skips the index.md itself so writing the artifact never
    invalidates its own hash. The body `order` is folded in (only for
    non-default order) so switching order triggers a rewrite.
    """
    h = hashlib.sha256()
    files_idx = idx.get("files", {})
    for rel_str, size in _walk_files(root):
        content_h = files_idx.get(rel_str, {}).get("hash", "")
        h.update(f"{rel_str}|{size}|{content_h}".encode("utf-8"))
    if order != "alpha":
        h.update(f"|order={order}".encode("utf-8"))
    return f"{HASH_PREFIX}{h.hexdigest()[:HASH_LENGTH]}"


def _classify(ext: str) -> str:
    if ext in MARKDOWN_EXTENSIONS:
        return "markdown"
    if ext in BINARY_EXTENSIONS:
        return "binary"
    return "text"


# ---------------------------------------------------------------------------
# Body sections
# ---------------------------------------------------------------------------

def _summary_section(inv: dict, idx: dict) -> str:
    stats = idx.get("stats", {})
    lines = [
        "## Summary",
        "",
        f"- Files: {inv['total_files']}  (markdown: {inv['markdown_count']}, binary: {inv['binary_count']})",
        f"- Total size: {_human_bytes(inv['total_bytes'])}",
        f"- Health: cycles={stats.get('cycles', 0)}, missing={stats.get('missing', 0)}",
    ]
    edge_types = stats.get("edge_types") or {}
    if edge_types:
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(edge_types.items()))
        lines.append(f"- Edges: {stats.get('edges', 0)}  ({parts})")
    if inv["extensions"]:
        top = sorted(
            inv["extensions"].items(),
            key=lambda kv: kv[1]["count"],
            reverse=True,
        )[:5]
        ext_str = ", ".join(f"{ext or '(none)'} ({s['count']})" for ext, s in top)
        lines.append(f"- Top extensions: {ext_str}")
    return "\n".join(lines)


def _folder_map_section(root: Path, max_depth: int = 3) -> str:
    """Render an ASCII directory tree, depth-limited."""
    lines = ["## Folder Map", ""]

    def walk(dir_path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(
                [c for c in dir_path.iterdir() if not c.name.startswith(".") and not c.name.startswith("~$")],
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return
        for i, child in enumerate(children):
            if child.name == DEFAULT_INDEX_FILENAME:
                continue
            is_last = i == len(children) - 1
            connector = "└── " if is_last else "├── "
            label = child.name + ("/" if child.is_dir() else "")
            lines.append(f"{prefix}{connector}{label}")
            if child.is_dir():
                ext = "    " if is_last else "│   "
                walk(child, prefix + ext, depth + 1)

    lines.append("```")
    lines.append(root.name + "/")
    walk(root, "", 1)
    lines.append("```")
    return "\n".join(lines)


def _files_section(
    root: Path,
    inv: dict,
    idx: dict,
    max_files: int,
    order: str = "alpha",
    counts: dict | None = None,
) -> str:
    lines = ["## Files", ""]
    files_idx = idx.get("files", {})
    md_entries: list[tuple[str, dict]] = []
    other_entries: list[tuple[str, int]] = []

    for rel, size in _walk_files(root):
        ext = Path(rel).suffix.lower()
        if ext in MARKDOWN_EXTENSIONS:
            md_entries.append((rel, files_idx.get(rel, {})))
        else:
            other_entries.append((rel, size))

    if order == "cache":
        ck = counts or {}
        md_entries.sort(key=lambda e: order_key(e[0], ck))
        other_entries.sort(key=lambda e: order_key(e[0], ck))

    total_listed = 0
    omitted = 0

    if md_entries:
        lines.append(f"### Markdown ({len(md_entries)})")
        lines.append("")
        for rel, entry in md_entries:
            if total_listed >= max_files:
                omitted += 1
                continue
            total_listed += 1
            ftype = entry.get("type", "markdown")
            title = entry.get("title", "")
            desc = entry.get("desc", "")
            header = f"- [{ftype}] {rel}"
            if title:
                header += f" — {title}"
            lines.append(header)
            if desc:
                lines.append(f"  {desc}")
            deps = entry.get("deps") or []
            if deps:
                dep_str = ", ".join(
                    f"{d['type']}→{d['to']}" + (" (parallel)" if d.get("parallel") else "")
                    for d in deps
                )
                lines.append(f"  deps: {dep_str}")
        lines.append("")

    if other_entries:
        lines.append(f"### Other ({len(other_entries)})")
        lines.append("")
        for rel, size in other_entries:
            if total_listed >= max_files:
                omitted += 1
                continue
            total_listed += 1
            ext = Path(rel).suffix.lower() or "(no-ext)"
            kind = _classify(ext)
            lines.append(f"- [{kind}] {rel} — {_human_bytes(size)}")
        lines.append("")

    if omitted:
        lines.append(f"_…{omitted} files omitted (max_files={max_files}). See frontmatter `stats.files` for the true total._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _deps_section(idx: dict) -> str:
    if not idx.get("files"):
        return ""
    edges = idx.get("stats", {}).get("edges", 0)
    if not edges:
        return ""
    lines = ["## Dependency Tree", "", "```"]
    lines.append(_dep_tree(idx))
    lines.append("```")
    return "\n".join(lines)


def _discover_child_indexes(root: Path) -> list[dict]:
    """Find descendant `dotmd-index.md` artifacts authored by dotmd-parser.

    The target's own `<root>/dotmd-index.md` is skipped. Files that lack
    `generated_by: dotmd-parser` in their frontmatter are silently
    ignored — those are user-authored documents that happen to share the
    name and must not be aggregated.
    """
    out: list[dict] = []
    target_self = (root / DEFAULT_INDEX_FILENAME).resolve()
    for p in sorted(root.rglob(DEFAULT_INDEX_FILENAME)):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if _is_hidden_rel(rel):
            continue
        if p.resolve() == target_self:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = extract_frontmatter(text)
        if fm.get("generated_by") != "dotmd-parser":
            continue
        stats = fm.get("stats") or {}
        out.append({
            "path": rel.as_posix(),
            "content_hash": fm.get("content_hash", ""),
            "generated_at": fm.get("generated_at", ""),
            "files": stats.get("files", 0),
            "edges": stats.get("edges", 0),
            "cycles": stats.get("cycles", 0),
            "missing": stats.get("missing", 0),
            "root": fm.get("root", ""),
        })
    return out


def _subindexes_section(aggregates: list[dict]) -> str:
    lines = ["## Sub-Indexes", ""]
    lines.append(
        "Descendant `dotmd-index.md` artifacts discovered under this folder. "
        "Read the relevant child for full file listings — this section "
        "intentionally only summarizes."
    )
    lines.append("")
    for entry in aggregates:
        files = entry.get("files", 0)
        edges = entry.get("edges", 0)
        cycles = entry.get("cycles", 0)
        missing = entry.get("missing", 0)
        health = "OK" if (cycles == 0 and missing == 0) else f"cycles:{cycles} missing:{missing}"
        line = f"- `{entry['path']}` — {files} files, {edges} edges, {health}"
        gen_at = entry.get("generated_at")
        if gen_at:
            line += f"  _(generated {gen_at})_"
        lines.append(line)
    return "\n".join(lines)


def _placeholders_section(idx: dict) -> tuple[str, list[str]]:
    placeholders: set[str] = set()
    for entry in idx.get("files", {}).values():
        for p in entry.get("placeholders", []) or []:
            placeholders.add(p)
    if not placeholders:
        return "", []
    sorted_p = sorted(placeholders)
    lines = ["## Placeholders", "", "Unresolved `{{...}}` variables across the folder:", ""]
    for p in sorted_p:
        lines.append(f"- `{{{{{p}}}}}`")
    return "\n".join(lines), sorted_p


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_index_md(
    root: str | Path,
    *,
    max_files: int = 200,
    include_folder_map: bool = True,
    folder_map_depth: int = 3,
    include_deps_tree: bool = True,
    aggregate: bool = False,
    analysis_backend: str = "none",
    extra_frontmatter: dict | None = None,
    order: str = "alpha",
) -> str:
    """Return a Markdown string (frontmatter + body) summarizing `root`.

    Parameters
    ----------
    root : str | Path
        Directory to summarize.
    max_files : int
        Cap on the number of files listed in the body. The frontmatter's
        `stats.files` always reflects the true total.
    include_folder_map : bool
        Include a depth-limited ASCII directory tree.
    folder_map_depth : int
        Depth limit for the folder map.
    include_deps_tree : bool
        Include an ASCII dependency tree (only rendered if any edges exist).
    aggregate : bool
        When True, scan descendants for `dotmd-index.md` artifacts authored
        by dotmd-parser and reference them in a `## Sub-Indexes` section
        plus `aggregates[]` frontmatter. Files lacking
        `generated_by: dotmd-parser` are silently skipped.
    analysis_backend : str
        Recorded in frontmatter; one of "none" | "claude-api" | "host-agent" |
        "openrag". Set by callers that ran an analysis pass first.
    extra_frontmatter : dict | None
        Merged into the top-level frontmatter (e.g. `{"exports": {...}}`).
    """
    base = Path(root)
    if not base.exists():
        raise ValueError(f"path does not exist: {root}")
    if not base.is_dir():
        raise ValueError(f"path is not a directory: {root}")
    base = base.resolve()

    inv = inventory(str(base))
    idx = build_index(str(base))

    counts = git_change_counts(base) if order == "cache" else {}
    content_hash = _compute_content_hash(base, idx, order)

    chunks: list[dict] = []
    body_parts: list[str] = []

    body_parts.append("# Folder Index")
    body_parts.append("")
    body_parts.append(
        "_Auto-generated by dotmd-parser. Read this file first to learn what's "
        "in this folder without scanning every file._"
    )

    body_parts.append("")
    body_parts.append("<!-- chunk:summary -->")
    summary_text = _summary_section(inv, idx)
    body_parts.append(summary_text)
    chunks.append({"id": "summary", "anchor": "#summary", "tokens_est": _approx_tokens(summary_text)})

    if include_folder_map and inv["total_files"]:
        body_parts.append("")
        body_parts.append("<!-- chunk:folder-map -->")
        fm_text = _folder_map_section(base, max_depth=folder_map_depth)
        body_parts.append(fm_text)
        chunks.append({"id": "folder-map", "anchor": "#folder-map", "tokens_est": _approx_tokens(fm_text)})

    body_parts.append("")
    body_parts.append("<!-- chunk:files -->")
    files_text = _files_section(base, inv, idx, max_files=max_files, order=order, counts=counts)
    body_parts.append(files_text)
    chunks.append({"id": "files", "anchor": "#files", "tokens_est": _approx_tokens(files_text)})

    if include_deps_tree:
        deps_text = _deps_section(idx)
        if deps_text:
            body_parts.append("")
            body_parts.append("<!-- chunk:deps -->")
            body_parts.append(deps_text)
            chunks.append({"id": "deps", "anchor": "#dependency-tree", "tokens_est": _approx_tokens(deps_text)})

    aggregates: list[dict] = _discover_child_indexes(base) if aggregate else []
    if aggregates:
        body_parts.append("")
        body_parts.append("<!-- chunk:sub-indexes -->")
        sub_text = _subindexes_section(aggregates)
        body_parts.append(sub_text)
        chunks.append({
            "id": "sub-indexes",
            "anchor": "#sub-indexes",
            "tokens_est": _approx_tokens(sub_text),
        })

    placeholders_text, placeholder_list = _placeholders_section(idx)
    if placeholders_text:
        body_parts.append("")
        body_parts.append("<!-- chunk:placeholders -->")
        body_parts.append(placeholders_text)
        chunks.append({
            "id": "placeholders",
            "anchor": "#placeholders",
            "tokens_est": _approx_tokens(placeholders_text),
        })

    body = "\n".join(body_parts).rstrip() + "\n"

    fm: dict[str, Any] = {
        "schema": INDEX_MD_SCHEMA,
        "generator_version": __version__,
        "generated_by": "dotmd-parser",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "root": str(base),
        "content_hash": content_hash,
        "analysis_backend": analysis_backend,
        "stats": {
            "files": inv["total_files"],
            "markdown": inv["markdown_count"],
            "binary": inv["binary_count"],
            "total_bytes": inv["total_bytes"],
            "edges": idx.get("stats", {}).get("edges", 0),
            "cycles": idx.get("stats", {}).get("cycles", 0),
            "missing": idx.get("stats", {}).get("missing", 0),
        },
        "chunks": chunks,
    }
    if placeholder_list:
        fm["placeholders"] = placeholder_list
    if aggregate:
        fm["aggregates"] = aggregates
    if extra_frontmatter:
        fm.update(extra_frontmatter)

    return f"---\n{_yaml_dump(fm)}\n---\n\n{body}"


def write_index_md(
    root: str | Path,
    md: str | None = None,
    *,
    force: bool = False,
    filename: str = DEFAULT_INDEX_FILENAME,
    **generate_kwargs: Any,
) -> tuple[Path, bool]:
    """Write `md` (or freshly generated content) to `<root>/<filename>`.

    Returns
    -------
    (path, written) : tuple
        `written` is False when the existing file already had the same
        `content_hash` and was therefore left untouched.

    Raises
    ------
    ValueError
        If the existing target lacks `generated_by: dotmd-parser` in its
        frontmatter and `force` is False.
    """
    base = Path(root)
    if not base.exists():
        raise ValueError(f"path does not exist: {root}")
    if not base.is_dir():
        raise ValueError(f"path is not a directory: {root}")
    base = base.resolve()
    target = base / filename

    new_md = md if md is not None else generate_index_md(str(base), **generate_kwargs)
    new_fm = extract_frontmatter(new_md)
    new_hash = new_fm.get("content_hash", "")

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        existing_fm = extract_frontmatter(existing)
        owner = existing_fm.get("generated_by")
        if owner != "dotmd-parser" and not force:
            raise ValueError(
                f"refusing to overwrite {target}: it does not look like a "
                f"dotmd-parser artifact (generated_by={owner!r}). "
                "Pass force=True (or --force on the CLI) to override."
            )
        if owner == "dotmd-parser" and existing_fm.get("content_hash") == new_hash and new_hash:
            return target, False

    target.write_text(new_md, encoding="utf-8")
    return target, True


def _approx_tokens(text: str) -> int:
    """Rough Claude-tokenizer estimate (~4 chars per token)."""
    return max(1, len(text) // 4)
