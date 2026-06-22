"""
dotMD — .md skill dependency parser
Input:  Root directory of a skill (or path to SKILL.md)
Output: Dictionary with { nodes, edges, warnings }

Directives:
- @include  path/to/file.md              Inline expansion (content is embedded)
- @delegate path/to/agent.md [--parallel] Agent delegation (recorded, not expanded)
- @ref      path/to/file.md              Runtime reference (recorded, not expanded)

Additional features:
- resolve()            : Recursively expand @include directives and output final text
- parse_placeholders() : Detect {{variable}} placeholders
- dependents_of()      : Query reverse dependencies (impact scope)
- Custom node type mapping support
"""

import hashlib
import json
import re
from pathlib import Path

# Directive patterns
# @include  path/to/file.md
# @delegate path/to/agent.md [--parallel]
# @ref      path/to/file.md
DIRECTIVE_PATTERN = re.compile(
    r'^\s*@(include|delegate|ref)\s+([\w./_-]+\.md)(\s+--parallel)?\s*$',
    re.MULTILINE
)

# Legacy read reference patterns (kept for backward compatibility)
# Read `path/to/file.md` for ...
# See `path/to/file.md` for ...
# - `path/to/file.md` — description
# Only matches .md files with / in the path (to avoid false positives)
# NOTE: Prefer @ref over these legacy patterns in new files.
READ_REF_PATTERN = re.compile(
    r'(?:Read|See)\s+[`"\']([^`"\']*?/[^`"\']+\.md)[`"\']'
    r'|'
    r'^\s*-\s+[`"\']([^`"\']*?/[^`"\']+\.md)[`"\']',
    re.MULTILINE,
)

# Placeholder pattern: {{variableName}}
PLACEHOLDER_PATTERN = re.compile(r'\{\{(\w+)\}\}')

# Heading + inline formatting patterns (for description extraction)
H1_PATTERN = re.compile(r'^\s*#\s+(.+?)\s*$')
FRONTMATTER_PATTERN = re.compile(r'^---\s*\n.*?\n---\s*\n', re.DOTALL)
# Strip basic inline markdown (bold/italic/inline-code/links) for descriptions
_INLINE_CODE_PATTERN = re.compile(r'`([^`]+)`')
_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_BOLD_ITALIC_PATTERN = re.compile(r'\*{1,3}([^*]+)\*{1,3}')

MAX_DEPTH = 10
DESC_MAX_CHARS = 200
HASH_LENGTH = 16  # Truncate sha256 to first 16 hex chars for compactness

# ============================================================
# deps.yml parser (lightweight YAML — no PyYAML required)
# ============================================================

def parse_deps_yml(content: str) -> dict[str, list[str]]:
    """Parse deps.yml text (lightweight parser, no PyYAML required)."""
    result: dict[str, list[str]] = {}
    current_path = None
    in_includes = False

    for line in content.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        path_match = re.match(r'^-?\s*path:\s*(.+)$', stripped)
        if path_match:
            current_path = path_match.group(1).strip().strip('"').strip("'")
            result[current_path] = []
            in_includes = False
            continue

        if stripped == "includes:" or stripped == "includes: []":
            in_includes = stripped == "includes:"
            continue

        if in_includes and current_path and stripped.startswith("- "):
            include_path = stripped[2:].strip().strip('"').strip("'")
            if "  #" in include_path:
                include_path = include_path[:include_path.index("  #")].strip().strip('"').strip("'")
            if include_path:
                result[current_path].append(include_path)
            continue

        if ":" in stripped:
            in_includes = False

    return result

# Default node type mapping (path keyword -> type name)
# Order matters: first match wins
DEFAULT_TYPE_MAP = [
    ("agent", "agent"),
    ("shared", "shared"),
    ("prompt", "prompt"),
    ("reference", "reference"),
    ("asset", "template"),
    ("template", "template"),
]


def parse_directives(content: str) -> list[dict]:
    """Extract @include / @delegate / @ref directives from file content."""
    results = []
    for match in DIRECTIVE_PATTERN.finditer(content):
        results.append({
            "type": match.group(1),          # "include" or "delegate"
            "target": match.group(2),         # target path
            "parallel": bool(match.group(3)), # --parallel flag
        })
    return results


def parse_read_refs(content: str) -> list[str]:
    """Extract Read/See/list-style .md references from file content (deduplicated, in order)."""
    seen = set()
    result = []
    for match in READ_REF_PATTERN.finditer(content):
        # Two alternation groups — take the first non-None match
        target = match.group(1) or match.group(2)
        if target and target not in seen:
            seen.add(target)
            result.append(target)
    return result


def parse_placeholders(content: str) -> list[str]:
    """Extract {{variable}} placeholders from file content (deduplicated, in order)."""
    seen = set()
    result = []
    for match in PLACEHOLDER_PATTERN.finditer(content):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _strip_inline_markdown(text: str) -> str:
    text = _INLINE_CODE_PATTERN.sub(r'\1', text)
    text = _LINK_PATTERN.sub(r'\1', text)
    text = _BOLD_ITALIC_PATTERN.sub(r'\1', text)
    return text.strip()


def parse_description(content: str, max_chars: int = DESC_MAX_CHARS) -> dict:
    """
    Extract a compact summary from markdown content for token-efficient indexing.

    Returns:
        {"title": "First H1 or empty", "desc": "First paragraph, trimmed"}

    Skips YAML front-matter and directive lines. Strips inline markdown
    (bold/italic/inline-code/links) from the description.
    """
    # Drop YAML front-matter if present
    body = FRONTMATTER_PATTERN.sub('', content, count=1)

    title = ""
    for line in body.splitlines():
        m = H1_PATTERN.match(line)
        if m:
            title = _strip_inline_markdown(m.group(1))
            break

    # First paragraph: contiguous non-empty lines that are not headings/directives
    paragraph_lines: list[str] = []
    in_para = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_para:
                break
            continue
        if stripped.startswith("#"):
            continue
        if DIRECTIVE_PATTERN.match(line):
            continue
        paragraph_lines.append(stripped)
        in_para = True

    desc = _strip_inline_markdown(" ".join(paragraph_lines))
    if len(desc) > max_chars:
        desc = desc[: max_chars - 1].rstrip() + "…"
    return {"title": title, "desc": desc}


def hash_content(content: str, length: int = HASH_LENGTH) -> str:
    """Return a truncated sha256 hex digest of the given content."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return digest[:length]


def _empty_node(node_id: str, node_type: str, missing: bool) -> dict:
    """Create a node dict with all standard fields initialized."""
    return {
        "id": node_id,
        "type": node_type,
        "missing": missing,
        "placeholders": [],
        "title": "",
        "desc": "",
        "hash": "",
        "size": 0,
    }


def build_graph(root_path: str, type_map: list[tuple[str, str]] | None = None) -> dict:
    """
    Build a dependency graph starting from a root SKILL.md (or any .md file / directory).
    If deps.yml exists, it is merged into the graph.

    Args:
        root_path: Path to a directory, SKILL.md, or any .md file.
        type_map:  Custom mapping for node type inference.
                   List of [(path_keyword, type_name), ...].
                   Uses default mapping if None.

    Returns:
    {
      "nodes": [{"id": "...", "type": "...", "missing": false, "placeholders": ["var1"]}],
      "edges": [{"from": "...", "to": "...", "type": "include", "parallel": false}],
      "warnings": [{"type": "circular|missing|depth_exceeded", "path": "...", "message": "..."}]
    }
    """
    root = Path(root_path)
    mapping = type_map if type_map is not None else DEFAULT_TYPE_MAP

    # Record deps.yml path for when a directory is given
    base_dir = root if root.is_dir() else root.parent

    # If a directory is given, look for SKILL.md
    has_skill_md = True
    if root.is_dir():
        candidate = root / "SKILL.md"
        if not candidate.exists():
            # Case-insensitive search at root
            candidates = list(root.glob("*.md"))
            skill_files = [f for f in candidates if f.name.upper() == "SKILL.MD"]
            if skill_files:
                candidate = skill_files[0]
            else:
                # Sprint 13 Phase 36: Claude Code plugin convention fallback
                # .claude/skills/<name>/skill.md パターンを検出
                plugin_skills = list(root.glob(".claude/skills/*/skill.md"))
                if plugin_skills:
                    # 最初の plugin skill を採用 (複数ある場合は名前順)
                    plugin_skills.sort()
                    candidate = plugin_skills[0]
                else:
                    has_skill_md = False
                    candidate = None

        if candidate:
            root = candidate

    nodes = {}   # id -> node dict (for deduplication)
    edges = []
    warnings = []
    visited_stack = []  # DFS stack for circular reference detection

    def _infer_node_type(path: Path) -> str:
        """Infer node type from path."""
        path_lower = str(path).lower()
        name = path.name.lower()
        if name == "skill.md":
            return "skill"
        for keyword, node_type in mapping:
            if keyword in path_lower:
                return node_type
        return "reference"

    def _resolve(current_file: Path, target: str) -> Path:
        """Resolve a relative path to an absolute path."""
        return (current_file.parent / target).resolve()

    def _walk(file_path: Path, depth: int):
        rel = str(file_path)

        # Depth limit check
        if depth > MAX_DEPTH:
            warnings.append({
                "type": "depth_exceeded",
                "path": rel,
                "message": f"Maximum depth {MAX_DEPTH} exceeded"
            })
            return

        # Circular reference check
        if rel in visited_stack:
            cycle_path = " -> ".join(visited_stack + [rel])
            warnings.append({
                "type": "circular",
                "path": rel,
                "message": f"Circular reference: {cycle_path}"
            })
            return

        # File existence check
        if not file_path.exists():
            warnings.append({
                "type": "missing",
                "path": rel,
                "message": f"Referenced file does not exist: {rel}"
            })
            # Add node (recorded as missing)
            if rel not in nodes:
                nodes[rel] = _empty_node(rel, _infer_node_type(file_path), missing=True)
            return

        # Register node
        if rel not in nodes:
            nodes[rel] = _empty_node(rel, _infer_node_type(file_path), missing=False)

        # If already visited, only add edges (don't recurse into node content)
        if rel in [n["id"] for n in nodes.values() if not n.get("_unvisited", True)]:
            pass

        nodes[rel]["_unvisited"] = False

        # Read file
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            warnings.append({
                "type": "read_error",
                "path": rel,
                "message": str(e)
            })
            return

        # Enrich node with metadata (placeholders, title/desc, hash, size)
        nodes[rel]["placeholders"] = parse_placeholders(content)
        meta = parse_description(content)
        nodes[rel]["title"] = meta["title"]
        nodes[rel]["desc"] = meta["desc"]
        nodes[rel]["hash"] = hash_content(content)
        nodes[rel]["size"] = len(content.encode("utf-8"))

        # Extract directives & recurse
        visited_stack.append(rel)
        for directive in parse_directives(content):
            target_path = _resolve(file_path, directive["target"])
            target_rel = str(target_path)

            # Add edge (with dedup check)
            edge = {
                "from": rel,
                "to": target_rel,
                "type": directive["type"],
                "parallel": directive["parallel"],
            }
            if edge not in edges:
                edges.append(edge)

            if directive["type"] == "ref":
                # @ref: record edge and node, but do not recurse
                if target_rel not in nodes:
                    is_missing = not target_path.exists()
                    nodes[target_rel] = _empty_node(
                        target_rel, _infer_node_type(target_path), missing=is_missing
                    )
                    if is_missing:
                        warnings.append({
                            "type": "missing",
                            "path": target_rel,
                            "message": f"@ref target does not exist: {directive['target']}",
                        })
            else:
                _walk(target_path, depth + 1)

        # Detect Read references (runtime dependencies — no recursion)
        for read_target in parse_read_refs(content):
            # Relative path from file's parent directory -> absolute path
            target_path = _resolve(file_path, read_target)
            target_rel = str(target_path)

            # If not resolved, search up ancestor directories
            if not target_path.exists():
                search_dir = file_path.parent
                while search_dir != search_dir.parent:
                    alt_path = (search_dir / read_target).resolve()
                    if alt_path.exists():
                        target_path = alt_path
                        target_rel = str(alt_path)
                        break
                    search_dir = search_dir.parent

            # Add edge (with dedup check)
            edge = {
                "from": rel,
                "to": target_rel,
                "type": "read-ref",
                "parallel": False,
            }
            if edge not in edges:
                edges.append(edge)

            # Register node (no recursion)
            if target_rel not in nodes:
                is_missing = not target_path.exists()
                nodes[target_rel] = _empty_node(
                    target_rel, _infer_node_type(target_path), missing=is_missing
                )
                if is_missing:
                    warnings.append({
                        "type": "missing",
                        "path": target_rel,
                        "message": f"Read reference target does not exist: {read_target}",
                    })

        visited_stack.pop()

    if has_skill_md and root:
        _walk(root.resolve(), 0)
    elif not has_skill_md:
        # No SKILL.md — may still work with deps.yml only
        pass

    # Load and merge deps.yml
    deps_yml_path = base_dir / "deps.yml"
    if deps_yml_path.exists():
        try:
            deps_content = deps_yml_path.read_text(encoding="utf-8")
            deps = parse_deps_yml(deps_content)

            for from_file, includes in deps.items():
                from_path = (base_dir / from_file).resolve()
                from_rel = str(from_path)

                # Register node
                if from_rel not in nodes:
                    node_type = _infer_node_type(from_path)
                    is_missing = not from_path.exists()
                    nodes[from_rel] = _empty_node(
                        from_rel,
                        node_type if node_type != "reference" else "document",
                        missing=is_missing,
                    )

                for to_file in includes:
                    to_path = (base_dir / to_file).resolve()
                    to_rel = str(to_path)

                    # Register target node
                    if to_rel not in nodes:
                        is_missing = not to_path.exists()
                        node_type = _infer_node_type(to_path)
                        nodes[to_rel] = _empty_node(
                            to_rel,
                            node_type if node_type != "reference" else "document",
                            missing=is_missing,
                        )
                        if is_missing:
                            warnings.append({
                                "type": "missing",
                                "path": to_rel,
                                "message": f"deps.yml target does not exist: {to_file}",
                            })

                    # Register edge (with dedup check)
                    edge = {
                        "from": from_rel,
                        "to": to_rel,
                        "type": "include",
                        "parallel": False,
                    }
                    if edge not in edges:
                        edges.append(edge)

        except Exception as e:
            warnings.append({
                "type": "read_error",
                "path": str(deps_yml_path),
                "message": f"Failed to read deps.yml: {e}",
            })

    # Neither SKILL.md nor deps.yml found
    if not nodes and not edges:
        if not has_skill_md:
            warnings.append({
                "type": "missing",
                "path": str(base_dir),
                "message": "Neither SKILL.md nor deps.yml found",
            })

    # Clean up _unvisited flag
    for node in nodes.values():
        node.pop("_unvisited", None)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "warnings": warnings,
    }


# ============================================================
# resolve() — @include expansion
# ============================================================

def resolve(
    file_path: str,
    variables: dict[str, str] | None = None,
    *,
    scan: bool = True,
    scan_rules: list[str] | None = None,
    on_injection: str = "warn",
) -> dict:
    """
    Recursively expand @include directives and generate final text.
    @delegate and @ref lines are left as-is (not expanded).

    Scans @include-pulled content (depth > 0) for injection patterns when
    `scan` is True. Findings are returned under the "injections" key. With
    `on_injection="block"`, a flagged file's expansion is replaced by a
    placeholder comment instead of being inlined.
    """
    from dotmd_parser.scan import scan_content  # local import avoids import cycle

    root = Path(file_path).resolve()
    warnings = []
    injections: list[dict] = []
    visited_stack = []

    def _expand(fp: Path, depth: int) -> str:
        rel = str(fp)

        if depth > MAX_DEPTH:
            warnings.append({"type": "depth_exceeded", "path": rel, "message": f"Maximum depth {MAX_DEPTH} exceeded"})
            return ""

        if rel in visited_stack:
            warnings.append({"type": "circular", "path": rel, "message": f"Circular reference: {' -> '.join(visited_stack + [rel])}"})
            return ""

        if not fp.exists():
            warnings.append({"type": "missing", "path": rel, "message": f"Referenced file does not exist: {rel}"})
            return ""

        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            warnings.append({"type": "read_error", "path": rel, "message": str(e)})
            return ""

        # Scan @include-pulled content only (root is depth 0 and trusted).
        if scan and depth > 0:
            found = scan_content(content, source=rel, rules=scan_rules)
            if found:
                injections.extend(found)
                if on_injection == "block":
                    rule_names = ", ".join(sorted({f["rule"] for f in found}))
                    return f"<!-- dotmd: blocked injection ({rule_names}) from {rel} -->"

        visited_stack.append(rel)

        # Replace @include lines with expanded content
        def _replace_include(match):
            directive_type = match.group(1)
            target = match.group(2)
            if directive_type != "include":
                # Keep @delegate and @ref as-is
                return match.group(0)
            target_path = (fp.parent / target).resolve()
            return _expand(target_path, depth + 1)

        result = DIRECTIVE_PATTERN.sub(_replace_include, content)
        visited_stack.pop()
        return result

    expanded = _expand(root, 0)

    # Variable substitution
    if variables:
        for key, value in variables.items():
            expanded = expanded.replace(f"{{{{{key}}}}}", value)

    # Detect unresolved placeholders
    remaining = parse_placeholders(expanded)

    return {
        "content": expanded,
        "placeholders": remaining,
        "warnings": warnings,
        "injections": injections,
    }


# ============================================================
# dependents_of() — reverse dependency query
# ============================================================

def dependents_of(graph: dict, target_id: str) -> list[str]:
    """
    Return nodes that (directly or indirectly) depend on the specified node.
    i.e., "files that would be affected if target_id is changed".

    Args:
        graph:     Return value of build_graph()
        target_id: id of the target node (absolute path string)

    Returns:
        List of dependent node ids (ordered from closest to root)
    """
    # Build reverse adjacency list
    reverse_adj: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        reverse_adj.setdefault(edge["to"], []).append(edge["from"])

    # BFS in reverse direction
    visited = set()
    queue = [target_id]
    result = []

    while queue:
        current = queue.pop(0)
        for parent in reverse_adj.get(current, []):
            if parent not in visited:
                visited.add(parent)
                result.append(parent)
                queue.append(parent)

    return result


# ============================================================
# summary() — human-readable graph overview
# ============================================================

def summary(graph: dict) -> str:
    """Return a human-readable text summary of the graph."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    warnings = graph["warnings"]

    by_type: dict[str, int] = {}
    for n in nodes:
        t = n["type"]
        by_type[t] = by_type.get(t, 0) + 1

    # Node type display (only types that exist)
    type_parts = [f"{k}:{v}" for k, v in sorted(by_type.items())]
    type_str = ", ".join(type_parts) if type_parts else "none"

    # Edge type counts
    edge_types: dict[str, int] = {}
    for e in edges:
        edge_types[e["type"]] = edge_types.get(e["type"], 0) + 1
    edge_parts = [f"{k}:{v}" for k, v in sorted(edge_types.items())]
    edge_str = ", ".join(edge_parts) if edge_parts else "none"

    # Placeholder summary
    all_placeholders: set[str] = set()
    for n in nodes:
        for p in n.get("placeholders", []):
            all_placeholders.add(p)

    lines = [
        f"Nodes: {len(nodes)}  ({type_str})",
        f"Edges: {len(edges)}  ({edge_str})",
        f"Warnings: {len(warnings)}",
    ]

    if all_placeholders:
        lines.append(f"Placeholders: {', '.join(sorted(all_placeholders))}")

    for w in warnings:
        lines.append(f"  [{w['type'].upper()}] {w['message']}")
    return "\n".join(lines)


def main():
    # Defer CLI to avoid circular imports at module load time
    from dotmd_parser.cli import run
    run()


if __name__ == "__main__":
    main()
