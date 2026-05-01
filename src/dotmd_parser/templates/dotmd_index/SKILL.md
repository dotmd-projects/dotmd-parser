---
name: dotmd-index
description: Build (or read) a single `dotmd-index.md` artifact at a folder's root that summarizes everything inside — file inventory, dependency graph, placeholders, and chunk markers for RAG. Use this BEFORE grep-scanning or Read-ing many files when entering an unfamiliar folder. Triggers when the workspace contains a `dotmd-index.md` file, when the user asks "what's in this folder?", "give me an overview of <path>", or when you are about to read 3+ documents in the same directory. Pairs with OpenRAG (https://github.com/langflow-ai/openrag) — the artifact is RAG-ingestion-friendly via `--push-openrag`.
license: MIT
version: 0.6.2
---

# dotmd-index — single-file folder overview for Claude

This sub-skill of `dotmd-parser` produces a self-contained Markdown
artifact (`<root>/dotmd-index.md`) that lets Claude understand a folder
from **one file** instead of scanning every document inside it.

## Decision tree

```
Are you entering an unfamiliar folder, or about to grep/Read 3+ files?
│
├─ Does <root>/dotmd-index.md already exist?
│  ├─ YES → Read it. That single file lists every file, every title,
│  │        every dependency, and every unresolved placeholder.
│  │
│  └─ NO  → Run `dotmd-parser dotmd-index <root>` to generate it,
│           then read it. Subsequent runs skip the write when the
│           folder hasn't changed (content_hash idempotency).
│
└─ Need RAG-style retrieval over the same folder?
   └─ Run `dotmd-parser dotmd-index <root> --push-openrag` to ingest
      the artifact into OpenRAG. The frontmatter records the document_id
      under `exports.openrag` for traceability.
```

## When to use this sub-skill

Invoke `dotmd-index` when any of the following is true:

- The user asks "what's in this folder?" / "summarize <path>"
- You are landing on an unfamiliar repository and want a one-shot overview
- You are about to read 3+ markdown files from the same directory
- A team member shipped a docs folder and you need to know what depends on what
- You need RAG-ready chunks of the folder for OpenRAG / OpenSearch

Do NOT invoke when:

- You only need filesystem stats (use `dotmd-parser inventory` instead)
- You only need the dependency graph (use `dotmd-parser digest` instead)
- The folder has zero `.md` and you need to seed dependencies first
  (run `dotmd-parser analyze --plan` first, then come back)

## Output shape

`dotmd-index.md` looks like this:

```markdown
---
schema: dotmd-index/v1
generated_by: dotmd-parser
generator_version: 0.6.0
generated_at: 2026-05-02T12:34:56Z
root: /abs/path/to/folder
content_hash: "sha256:abc123..."     # idempotency key
analysis_backend: none                # claude-api | host-agent | openrag | none
stats:
  files: 42
  markdown: 18
  binary: 12
  total_bytes: 8400000
  edges: 7
  cycles: 0
  missing: 0
chunks:                               # RAG ingestion hints
  - id: summary
    anchor: "#summary"
    tokens_est: 80
  - id: files
    anchor: "#files"
    tokens_est: 1200
  - id: deps
    anchor: "#dependency-tree"
    tokens_est: 400
exports:                              # populated by --push-openrag
  openrag:
    document_id: doc-abc
    base_url: http://localhost:3000
    pushed_at: 2026-05-02T12:35:00Z
---

# Folder Index

<!-- chunk:summary -->
## Summary
- Files: 42  (markdown: 18, binary: 12)
- ...

<!-- chunk:folder-map -->
## Folder Map
```
my-skill/
├── agents/
│   └── classifier.md
├── shared/
│   └── role.md
└── SKILL.md
```

<!-- chunk:files -->
## Files
### Markdown (18)
- [skill] SKILL.md — Receipt Analysis Skill
  deps: include→shared/role.md, delegate→agents/classifier.md
- ...

<!-- chunk:deps -->
## Dependency Tree
SKILL.md
├── [include] shared/role.md
└── [delegate] agents/classifier.md
```

The `<!-- chunk:id -->` HTML markers + the `chunks[]` frontmatter let any
RAG ingester split the file deterministically without re-implementing
chunking.

## Commands

| Command | Purpose |
|---|---|
| `dotmd-parser dotmd-index <path>` | Generate `<path>/dotmd-index.md` |
| `dotmd-parser dotmd-index <path> --stdout` | Print to stdout instead of writing |
| `dotmd-parser dotmd-index <path> --force` | Overwrite a hand-written `dotmd-index.md` |
| `dotmd-parser dotmd-index <path> --no-folder-map` | Skip the ASCII folder tree |
| `dotmd-parser dotmd-index <path> --no-deps` | Skip the dependency tree |
| `dotmd-parser dotmd-index <path> --max-files 50` | Cap how many files appear in the body |
| `dotmd-parser dotmd-index <path> --aggregate` | Roll up descendant `dotmd-index.md` files into a parent `## Sub-Indexes` section |
| `dotmd-parser dotmd-index <path> --push-openrag` | Ingest the file into OpenRAG after writing |

## Idempotency & safety

- The artifact's `content_hash` is computed from the folder's filesystem
  state (paths, sizes, per-file content hashes), **excluding** the
  timestamp. Re-running the command on an unchanged folder writes
  nothing — you'll see `<path>/dotmd-index.md unchanged`.
- The command refuses to overwrite an existing `dotmd-index.md` that
  doesn't carry `generated_by: dotmd-parser` in its frontmatter, unless
  `--force` is passed. This protects hand-written files of the same name.
- The artifact itself is excluded from `content_hash` so writing it never
  invalidates its own hash.

## Aggregating multiple folders

For a monorepo or a `docs/` tree where each subfolder maintains its own
`dotmd-index.md`, run the parent in aggregate mode:

```bash
dotmd-parser dotmd-index ./project/ --aggregate
```

The parent file gains:

- A `## Sub-Indexes` section with one bullet per descendant artifact
  (file count, edge count, health, generated timestamp)
- An `aggregates[]` frontmatter array with each child's relative path,
  `content_hash`, `generated_at`, and stats — enough to detect when a
  child has gone stale without parsing the child again

Children are discovered by walking the tree for files named
`dotmd-index.md`. Files that don't carry `generated_by: dotmd-parser`
in their frontmatter are silently skipped, so a hand-written
`dotmd-index.md` in `manual/` won't pollute the roll-up.

This is intentionally a **reference**, not a merge — Claude reads the
parent to learn what subfolders exist, then drills into the relevant
child for full file listings. That keeps the parent token-efficient and
scales to large trees.

## OpenRAG integration

OpenRAG (https://github.com/langflow-ai/openrag) is a self-hosted RAG
platform built on Langflow + Docling + OpenSearch. It exposes a Python
SDK (`openrag-sdk`) and an MCP server. dotmd-parser pushes the artifact
through the SDK; once ingested, the same content becomes searchable from
Claude Code via OpenRAG's MCP server.

Setup:

```bash
pip install dotmd-parser[openrag]   # installs the optional SDK
export OPENRAG_URL=http://localhost:3000
export OPENRAG_API_KEY=...           # if your instance requires auth
```

Then:

```bash
dotmd-parser dotmd-index ./docs/ --push-openrag
# 1. Generates ./docs/dotmd-index.md
# 2. Calls OpenRAGClient.documents.ingest(file_path=...)
# 3. Records {document_id, base_url, pushed_at} under exports.openrag
```

The two surfaces complement each other:

- **dotmd-index.md** is the *map* (a single token-efficient overview)
- **OpenRAG** is the *search index* (full-content semantic retrieval)

For cross-folder queries, also register OpenRAG's MCP server with Claude
Code so the same content is reachable as a search tool, not just a file.

## API-key-free alternative

When you can't or don't want to install `openrag-sdk` or set an API key:

- `dotmd-index` itself works **with no API key** — it only reads the
  filesystem and runs the existing graph parser.
- For implicit-dependency analysis (e.g. inferring what a PDF depends on
  before generating the index), use `dotmd-parser analyze --plan` to
  emit a host-agent prompt pack, run it via Claude Code's host context
  (subscription LLM), then `--apply-from <json>` to fold the result back
  in. Re-run `dotmd-index` after that and the new edges show up in the
  artifact's "Dependency Tree" section.

## Programmatic API

```python
from dotmd_parser import generate_index_md, write_index_md, push_to_openrag

# Just the string
md = generate_index_md("./docs/")

# Write to <root>/dotmd-index.md (idempotent)
path, written = write_index_md("./docs/")

# After writing, push to OpenRAG
if written:
    export = push_to_openrag(str(path), base_url="http://localhost:3000")
    print(export["document_id"])
```

## Installation

```bash
pip install dotmd-parser              # core, no API key needed
pip install dotmd-parser[openrag]     # adds openrag-sdk for --push-openrag
```

To install this sub-skill into your project:

```bash
dotmd-parser init --skill dotmd-index   # writes .claude/skills/dotmd-index/SKILL.md
```

The `--skill` flag is in dotmd-parser 0.6.0+. For older versions or
manual installs, copy the file from the wheel's
`dotmd_parser/templates/dotmd_index/SKILL.md`.
