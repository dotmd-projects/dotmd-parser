# Changelog

All notable changes to dotmd-parser are documented here. This project
follows [Semantic Versioning](https://semver.org/).

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
