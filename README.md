# dotmd-parser

[![PyPI version](https://img.shields.io/pypi/v/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![Python](https://img.shields.io/pypi/pyversions/dotmd-parser)](https://pypi.org/project/dotmd-parser/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

![dotmd-parser demo](assets/demo.gif)

> [日本語版 README はこちら](README.ja.md)

Dependency graph parser for `.md` skill files — built for AI agent prompt engineering with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and similar tools.

## Why dotmd-parser?

As AI agent projects grow, `.md` files start referencing each other via `@include`, `@delegate`, and `@ref` directives. Without tooling, you're left manually tracing dependencies to answer basic questions:

- *"Which files break if I edit `shared/role.md`?"*
- *"Is there a circular reference hiding in my skill tree?"*
- *"What `{{variables}}` are still unresolved after expansion?"*

**dotmd-parser** solves this by parsing your `.md` files into a dependency graph — automatically detecting directives, runtime references, and template placeholders. One function call gives you the full picture.

## Comparison

| Capability | Manual / grep | dotmd-parser |
|---|---|---|
| Find `@include` / `@delegate` / `@ref` references | `grep -r "@include"` — flat list, no context | Structured graph with node types and edge metadata |
| Detect circular references | Hope you notice before the agent loops | Automatic detection with full cycle path in warnings |
| Reverse dependency ("what breaks?") | Manually trace every file | `dependents_of(graph, "shared/role.md")` — one call |
| Expand `@include` to final text | Copy-paste by hand | `resolve("SKILL.md", variables={...})` — recursive expansion |
| Find unresolved `{{variables}}` | `grep "{{" *.md` — noisy, no dedup | Deduplicated list per node and after expansion |
| Missing file detection | Runtime failure | Warnings at parse time with exact paths |

## Installation

```bash
pip install dotmd-parser
```

## Quick Start

```python
from dotmd_parser import build_graph, resolve, dependents_of, summary
```

### build_graph — Build a dependency graph

```python
graph = build_graph("./my-skill/")
# or from a specific file
graph = build_graph("./my-skill/SKILL.md")
```

Returns:

```json
{
  "nodes": [
    {"id": "/abs/path/to/SKILL.md", "type": "skill", "missing": false, "placeholders": []}
  ],
  "edges": [
    {"from": "...", "to": "...", "type": "include", "parallel": false}
  ],
  "warnings": []
}
```

**Custom node type mapping:**

By default, node types are inferred from path keywords (`agent`, `shared`, `prompt`, `reference`, `asset`, `template`). You can override this with the `type_map` parameter:

```python
graph = build_graph("./my-skill/", type_map=[
    ("helper", "utility"),
    ("core", "foundation"),
])
```

**deps.yml support:**

If a `deps.yml` file exists in the root directory, its dependencies are automatically merged into the graph:

```yaml
- path: agents/planner.md
  includes:
    - shared/role.md
    - shared/tools.md
```

### resolve — Expand @include directives

Recursively expands `@include` directives into final text. `@delegate` and `@ref` lines are left as-is.

```python
result = resolve("./prompts/main.md", variables={"name": "Alice"})

print(result["content"])       # Fully expanded text
print(result["placeholders"])  # Unresolved {{variable}} names
print(result["warnings"])      # Circular refs, missing files, etc.
```

### dependents_of — Reverse dependency query

```python
# "If I change shared/role.md, what else breaks?"
affected = dependents_of(graph, "/abs/path/to/shared/role.md")
```

### summary — Human-readable overview

```python
print(summary(graph))
# Nodes: 5  (agent:1, prompt:1, shared:2, skill:1)
# Edges: 4  (include:2, ref:1, read-ref:1)
# Warnings: 0
# Placeholders: name, role
```

## Supported Directives

| Directive | Edge type | Expanded by `resolve()`? | Description |
|---|---|---|---|
| `@include path/to/file.md` | `include` | Yes | Inline expansion — file content is inserted at this position |
| `@delegate path/to/agent.md` | `delegate` | No | Agent delegation — recorded in graph, left as-is in output |
| `@delegate path/to/agent.md --parallel` | `delegate` | No | Parallel delegation with `--parallel` flag |
| `@ref path/to/file.md` | `ref` | No | Runtime reference — recorded in graph, left as-is in output |
| `` Read `path/to/file.md` `` | `read-ref` | No | Legacy runtime reference (same as `@ref`, kept for backward compatibility) |

## Utility Functions

Lower-level parsing functions are also exported for custom use:

```python
from dotmd_parser import parse_directives, parse_read_refs, parse_placeholders, parse_deps_yml
```

| Function | Description |
|---|---|
| `parse_directives(content)` | Extract `@include` / `@delegate` / `@ref` directives from text |
| `parse_read_refs(content)` | Extract legacy `Read`/`See`/list-style `.md` references (deduplicated) |
| `parse_placeholders(content)` | Extract `{{variable}}` placeholder names (deduplicated) |
| `parse_deps_yml(content)` | Parse `deps.yml` text into `{path: [includes]}` dict (no PyYAML required) |

## CLI

```bash
# Installed as a command
dotmd-parser ./my-skill/

# Or via Python module
python -m dotmd_parser.parser ./my-skill/
```

Outputs a human-readable summary followed by the full JSON graph.

## Development

```bash
git clone https://github.com/dotmd-projects/dotmd-parser.git
cd dotmd-parser
pip install -e .
pip install pytest
pytest tests/ -v
```

## License

MIT
