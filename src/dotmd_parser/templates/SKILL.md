---
name: dotmd-parser
description: Analyze and index .md dependency graphs for Claude Code skills and plugins. Use BEFORE reading many markdown files in a skill/plugin directory — read `.claude/dotmd-index.json` first to understand topology in one shot, avoid grep-scanning, and catch circular references or broken `@include`/`@ref` links. For documentation folders that don't use explicit directives yet, run `dotmd-parser analyze` to detect implicit dependencies with Claude and seed `@include` / `deps.yml`. Trigger when the workspace contains `SKILL.md`, `deps.yml`, a `.claude/skills/` tree, a plain docs folder of `.md`, or when the user asks about dependencies / impact of editing a markdown file.
license: MIT
version: 0.4.1
---

# dotmd-parser — .md dependency index for Claude Code

This skill turns any folder of `.md` files (Claude Code skills, plugins, prompt
packs, documentation sets) into a compact dependency index so you can reason
about the file tree **without reading every file**.

## When to use

Invoke this skill when any of the following is true:

- The user asks "what depends on X?" / "what breaks if I change Y?"
- You are about to modify a `.md` file that may be `@include`d or `@ref`d by others
- You are onboarding into an unfamiliar skill/plugin directory
- A skill install from GitHub needs to be audited before use
- You need to detect circular references or broken links before running a workflow

## Decision tree — which command first?

```
Unsure what the folder contains? Run an API-free scan first:
  dotmd-parser inventory <path>   # extension counts, sizes, markdown ratio

Then branch based on the result:

Does the folder already contain @include / @ref / @delegate directives
or a deps.yml?
│
├─ YES → go straight to "Token-efficient workflow" below.
│
└─ NO  → the parser cannot discover implicit deps. Seed them first:
         dotmd-parser analyze <path>          # dry-run proposal
         dotmd-parser analyze <path> --apply  # inject directives & deps.yml
         …then continue with the workflow below.
```

## Token-efficient workflow

**Before reading 3+ markdown files in the same directory, do this instead:**

```bash
# 1. Build the index (once per session, or after edits)
dotmd-parser index <path>

# 2. Read the compact summary — replaces grep -r / cat of many files
dotmd-parser digest <path>

# 3. For specific questions, use targeted queries
dotmd-parser affects <path> <file.md>   # reverse deps
dotmd-parser deps    <path> <file.md>   # direct deps
dotmd-parser tree    <path>             # ASCII topology
```

The index is saved to `<path>/.claude/dotmd-index.json` and re-used on
subsequent calls; it is only rebuilt when a tracked file's SHA-256 changes.

## Output format (what to expect)

`dotmd-parser digest` produces text like:

```
# dotmd index — 4 files
Edges: 3 (delegate:1, include:2)
Health: OK

## Files
- [skill] SKILL.md — Receipt Analysis Skill
  deps: include→shared/role.md, include→shared/account-items.md, delegate→agents/receipt-classifier.md
- [agent] agents/receipt-classifier.md — Receipt Classifier Agent
- [shared] shared/role.md
  You are an expert accounting assistant.

Placeholders: accountItems, taxCode
```

One line per file with type, path, title, first-paragraph description, and
direct dependencies. For most questions this is the only thing you need to
read — far cheaper than globbing and `cat`ing every `.md`.

## Directives the parser understands

| Directive                         | Meaning                                    | Expanded? |
|-----------------------------------|--------------------------------------------|-----------|
| `@include path/to/file.md`        | Inline expansion at runtime                | yes       |
| `@delegate path/to/agent.md [--parallel]` | Agent delegation                   | no        |
| `@ref path/to/file.md`            | Runtime reference                          | no        |
| `` Read `path/to/file.md` ``      | Legacy runtime reference                   | no        |

Placeholders (`{{name}}`) are also extracted and reported.

## deps.yml — dependencies for files you can't edit

Not every source is a plain `.md` — PDFs, Word docs, specs, PPTX. You can't
write `@include` into them, but they *do* depend on supporting documents.
A `deps.yml` at the folder root records those edges and is merged into the
graph automatically:

```yaml
# deps.yml
files:
  - path: "specs/manual.pdf"
    includes:
      - "shared/terminology.md"  # reason: common glossary
      - "shared/legal.md"
  - path: "README.md"
    includes:
      - "CONTRIBUTING.md"
```

`dotmd-parser analyze --apply` produces this file automatically for any
binary source it discovers. Hand-editing is expected afterwards.

## `analyze` — seed a fresh documentation folder

When you land on a doc repo with no directives, let Claude infer them:

```bash
# Requires ANTHROPIC_API_KEY in the environment or a local .env file.
# Dry-run (no changes) — prints the proposed edges and shared-part extractions.
dotmd-parser analyze ./docs/

# Apply the proposal: prepend @include lines to .md/.txt, write deps.yml for .pdf/.docx
dotmd-parser analyze ./docs/ --apply

# Machine-readable output
dotmd-parser analyze ./docs/ --json

# Scan more file types (optional deps: pdfplumber, python-docx)
dotmd-parser analyze ./docs/ --ext md --ext pdf --ext docx
```

After running with `--apply`, the normal workflow (`index → digest → affects`)
is immediately useful because directives now exist in the tree.

## Health check before editing

Before editing shared markdown, run:

```bash
dotmd-parser check <path>   # exits non-zero on cycles/missing
dotmd-parser affects <path> <file-you-plan-to-edit>
```

- `check` surfaces circular `@include` chains and dangling references.
- `affects` lists every file that transitively depends on the target, so you
  know the blast radius of your edit.

## Programmatic API

For advanced use, import the Python package:

```python
from dotmd_parser import build_index, save_index, load_index, digest, affects

idx = build_index("./my-skill/")
save_index(idx, "./my-skill/")
print(digest(idx))
print(affects(idx, "shared/role.md"))
```

## Optional: keep the index fresh automatically

The index rebuilds automatically on every `digest`/`affects`/`deps`/`tree`
call when any tracked file's SHA-256 changes, so manual `index` runs are
rarely required. If you want the on-disk file (`.claude/dotmd-index.json`)
to stay current without an explicit query, add a **PostToolUse hook** to
`~/.claude/settings.json` or the project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "command": "dotmd-parser index \"$CLAUDE_PROJECT_DIR\" >/dev/null 2>&1 || true",
        "description": "Refresh dotmd-parser index after markdown edits"
      }
    ]
  }
}
```

The command is idempotent and exits fast when nothing has changed, so the
overhead per edit is negligible. The trailing `|| true` prevents a broken
index from blocking an edit.

For CI, run `dotmd-parser check <path>` as a pre-commit / pre-merge gate —
it exits non-zero on cycles or missing references.

## Installation

```bash
pip install dotmd-parser          # 0.4.0 or newer
dotmd-parser init                 # drop SKILL.md into ./.claude/skills/dotmd-parser/
```

Or grab the pre-built skill archive from Releases (no pip required):

```bash
mkdir -p .claude/skills
curl -L https://github.com/dotmd-projects/dotmd-parser/releases/latest/download/skill.tar.gz \
  | tar -xz -C .claude/skills/
```

This drops `.claude/skills/dotmd-parser/SKILL.md` into the project. A
Windows-friendly `skill.zip` is attached to the same release.
