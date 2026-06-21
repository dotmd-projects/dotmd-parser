# Changelog

All notable changes to dotmd-parser are documented here. This project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Memory-as-Governance リスク台帳** — 追記専用 JSONL（`.claude/dotmd-ledger.jsonl`）に
  per-file リスクタグ（fix-failed / fragile / security-sensitive / deprecated）を記録
  （`ledger add` / `ledger clear`、replay で状態導出）。`risk <path> <file>` が逆依存
  （affects）件数と active タグ・レベル（high/medium/none）を返し、`--fail-on high|any|never`
  で CI / PreToolUse フックのゲートに使える。frontmatter `risk:` の静的タグも統合。
  `risk_report` 等を公開 API に追加。
  設計: `docs/superpowers/specs/2026-06-21-memory-governance-design.md`

## [0.7.0] - 2026-05-13

### Added
- **Orchestrator マルチシグナル検出** (`build_graph`):
  - `.claude/skills/<name>/skill.md` (Claude Code plugin convention) を directory 引数で auto-detect
  - 既存の root `SKILL.md` 検出は完全に後方互換 (root 優先)
  - 4 つの主要 orchestrator 配置パターンをカバー
  - 関連 spec: dotmd-io `docs/superpowers/specs/2026-05-13-orchestration-graph-design.md`

## [0.6.2] — 2026-05-02

Focus: **fix the OpenRAG bridge to actually work against the real
SDK on PyPI.** Two bugs surfaced when first installing
`dotmd-parser[openrag]` end-to-end on a fresh interpreter.

### Fixed

- **Import path** — was `from openrag import OpenRAGClient`, the real
  module is `openrag_sdk` (the PyPI distribution is `openrag-sdk` with
  the hyphen, but the importable package name uses an underscore).
- **`successful_files` type** — the SDK's `IngestTaskStatus` exposes
  this as an `int` (count) but `_normalize_response` was wrapping it
  in `list(...)`, which crashed at runtime. The exports record now
  carries `total_files` / `successful_files` / `failed_files` as ints
  and exposes the `filename` instead of a fictitious `document_id`
  (the real SDK has no such field).
- **Version pin** — `openrag-sdk>=0.4.0,<0.5.0` was based on the GitHub
  repo's release tag, but the PyPI package only ships up to `0.3.1`.
  Loosened to `openrag-sdk>=0.3.1` to match reality.

### Changed

- The `exports.openrag` frontmatter slot now records:
  `task_id`, `status`, `filename`, `total_files`, `successful_files`,
  `failed_files`, `pushed_at`, `base_url`.
  `document_id` is no longer present (the SDK never returned it).
- CLI's "Pushed to OpenRAG" log line now reports
  `(task_id=…, successful=N, failed=M)` instead of `document_id`.

### Tests

- 263 tests total, all passing. `_FakeIngestResponse` now mirrors the
  real `IngestTaskStatus` shape.

---

## [0.6.1] — 2026-05-02

Focus: **multi-folder aggregation.** When a project has multiple
subfolders that each maintain their own `dotmd-index.md` (monorepos,
docs trees, etc.), running the parent in aggregate mode rolls them up
into a single navigable index without duplicating content.

### Added

- **`--aggregate` flag** on `dotmd-index` — discovers descendant
  `dotmd-index.md` artifacts, references them in a `## Sub-Indexes`
  body section, and records their relative path / `content_hash` /
  `generated_at` / stats under `aggregates[]` in the parent's
  frontmatter. User-authored `dotmd-index.md` files (no
  `generated_by: dotmd-parser`) are silently skipped.
- **`generate_index_md(..., aggregate=True)`** library parameter for
  the same behavior.
- New chunk id `sub-indexes` with anchor `#sub-indexes`.

### Tests

- 258 tests total (+9 vs. 0.6.0), all passing.
- New module: `test_aggregate.py`.

### Compatibility

Non-breaking. Default behavior of `dotmd-index` is unchanged
(`aggregate=False`).

---

## [0.6.0] — 2026-05-02

Focus: **single-file folder overview + OpenRAG bridge.** Spotted while
trying to onboard Claude into unfamiliar `.md` folders without burning
tokens reading every file: there was no way to produce a *single*,
durable artifact that captured the inventory + dependency graph + chunk
boundaries together.

### Added

- **`dotmd-index` subcommand** — generates `<root>/dotmd-index.md`, a
  self-contained Markdown artifact that combines `inventory()` + 
  `build_index()` into one file Claude can read instead of grep-scanning.
  Includes RAG-friendly chunk markers (`<!-- chunk:id -->`) and a
  `chunks[]` frontmatter manifest.
  - Frontmatter `content_hash` (sha256 over filesystem state) makes
    re-runs idempotent.
  - Refuses to overwrite hand-written `dotmd-index.md` files unless
    `--force` is passed (frontmatter `generated_by: dotmd-parser` check).
  - Flags: `--stdout`, `--force`, `--no-folder-map`, `--no-deps`,
    `--max-files N`.
- **`--push-openrag` flag** — after writing, ingest the artifact into a
  running OpenRAG instance (https://github.com/langflow-ai/openrag) via
  the optional `openrag-sdk` dependency. Records `document_id` /
  `pushed_at` / `base_url` under frontmatter `exports.openrag` for
  traceability. Companion flags: `--openrag-url`, `--openrag-api-key`.
- **`init --skill <id>` flag** — install a specific bundled skill
  (`dotmd-parser` or `dotmd-index`) into `.claude/skills/<id>/`.
- **`templates/dotmd_index/SKILL.md`** — Claude Code sub-skill that
  documents when to read `dotmd-index.md`, how to generate it, and how
  to combine it with OpenRAG's MCP server.

### Public API additions

- `dotmd_parser.generate_index_md(root, *, max_files, include_folder_map,
  include_deps_tree, folder_map_depth, analysis_backend, extra_frontmatter)`
- `dotmd_parser.write_index_md(root, md=None, *, force, filename, **kwargs)`
- `dotmd_parser.extract_frontmatter(md)` — minimal stdlib YAML reader
  that handles the shapes emitted by the in-house dumper (scalars,
  nested dicts, lists of flat dicts).
- `dotmd_parser.push_to_openrag(md_path, *, base_url, api_key,
  _client_cls)` — async-to-sync wrapper over `openrag-sdk`'s
  `OpenRAGClient.documents.ingest`.
- `dotmd_parser.DEFAULT_INDEX_FILENAME`, `INDEX_MD_SCHEMA`.

### Optional dependencies

`pyproject.toml` now exposes `[project.optional-dependencies]`:

```bash
pip install dotmd-parser[openrag]   # + openrag-sdk for --push-openrag
pip install dotmd-parser[pdf]       # + pdfplumber for analyze on PDFs
pip install dotmd-parser[docx]      # + python-docx for analyze on DOCX
pip install dotmd-parser[all]       # everything
```

### Tests

- 249 tests total (+43 vs. 0.5.0), all passing.
- New test modules: `test_index_md.py`, `test_cli_dotmd_index.py`,
  `test_openrag_push.py` (the latter mocks `OpenRAGClient` so it runs
  without `openrag-sdk` installed).

### Compatibility

Non-breaking. All existing commands, library APIs, and the
`.claude/dotmd-index.json` format behave as before. The new
`dotmd-index.md` artifact lives at the folder root and is independent of
the existing `.claude/dotmd-index.json`.

---

## [0.5.0] — 2026-04-20

Focus: **no-API-key workflows and better onboarding for non-.md repos.**
Spotted while trying to analyze a folder of PDFs/PPTX/XLSX with no
markdown — previously dotmd-parser would silently produce an empty
graph, and `analyze` required an ANTHROPIC_API_KEY with no dry-run.

### Added

- **`inventory` subcommand** — API-free filesystem report: extension
  counts, sizes, markdown ratio, binary ratio, Japanese filename
  detection, and largest-files preview. Useful as the first command
  when landing on an unfamiliar doc folder.
- **Non-.md folder warnings** — `index` and `digest` now emit a stderr
  hint when zero .md files are found, pointing to `inventory` /
  `analyze --apply`.
- **`analyze --plan`** — emit a host-agent prompt pack (Markdown) that
  Claude Code (or any host agent) can execute instead of calling the
  Claude API. Pairs with:
- **`analyze --apply-from <json>`** — apply a pre-computed analysis
  JSON from any source (the host agent, a cached run, etc.).
- **`analyze --dry-run`** — estimate document count, input/output
  tokens, and USD cost from `MODEL_PRICING` without hitting the API.
  Warns when an unknown model is passed.
- **`index --scope <subdir>`** — incrementally re-index a single
  subdirectory and merge into the existing root-level
  `.claude/dotmd-index.json`. Entries outside the scope are preserved;
  entries under the scope are replaced.

### Public API additions

- `dotmd_parser.inventory.inventory()`,
  `format_inventory()`,
  `suggest_next_command()`,
  `TEXT_EXTENSIONS`, `BINARY_EXTENSIONS`, `MARKDOWN_EXTENSIONS`
- `dotmd_parser.analyze.format_host_agent_plan()`,
  `apply_analysis_from_file()`,
  `estimate_cost()`, `format_cost_estimate()`,
  `MODEL_PRICING`
- `dotmd_parser.index.build_scoped_index()`,
  `merge_index()`

### Tests

- 206 tests total (+69 vs. 0.4.1), all passing.
- New test modules: `test_inventory.py`, `test_empty_warnings.py`,
  `test_host_agent_plan.py`, `test_cost_estimate.py`,
  `test_index_scope.py`.

### Compatibility

Non-breaking. All existing commands and library APIs behave as before.

---

## [0.4.1] — 2026-04-19

- Prior release. See git history.
