"""
dotmd-parser — Dependency graph parser for .md skill files.

Parse @include/@delegate/@ref directives, build dependency graphs, resolve
templates, and produce a token-efficient on-disk index for AI agents.

API:
    from dotmd_parser import build_graph, resolve, dependents_of, summary
    from dotmd_parser import build_index, save_index, load_index
    from dotmd_parser import digest, tree, affects
"""

__version__ = "0.8.0"

from dotmd_parser.parser import (
    build_graph,
    resolve,
    dependents_of,
    parse_directives,
    parse_read_refs,
    parse_placeholders,
    parse_description,
    parse_deps_yml,
    hash_content,
    summary,
)
from dotmd_parser.index import (
    build_index,
    build_scoped_index,
    compact_graph,
    merge_index,
    save_index,
    load_index,
    needs_rebuild,
    changed_files,
    default_index_path,
)
from dotmd_parser.digest import (
    digest,
    tree,
    affects,
    deps_of,
)
from dotmd_parser.analyze import (
    analyze_dependencies,
    apply_analysis,
    apply_analysis_from_file,
    estimate_cost,
    format_cost_estimate,
    format_host_agent_plan,
    generate_directives,
    format_proposal,
    scan_documents,
    save_deps_yml,
    load_deps_yml,
    MODEL_PRICING,
)
from dotmd_parser.inventory import (
    inventory,
    format_inventory,
    suggest_next_command,
    TEXT_EXTENSIONS,
    BINARY_EXTENSIONS,
    MARKDOWN_EXTENSIONS,
)
from dotmd_parser.index_md import (
    generate_index_md,
    write_index_md,
    extract_frontmatter,
    DEFAULT_INDEX_FILENAME,
    INDEX_MD_SCHEMA,
)
from dotmd_parser.ledger import (
    append_event,
    read_events,
    active_tags,
    static_tags,
    all_active_tags,
    risk_level,
    risk_report,
    default_ledger_path,
    RISK_TAGS,
    HIGH_TAGS,
)
from dotmd_parser.checks import (
    run_checks,
    summarize,
    exit_code,
    format_text,
    format_json,
    format_sarif,
    CHECK_SCHEMA,
)
from dotmd_parser.openrag import push_to_openrag
from dotmd_parser.scan import (
    scan_content,
    DEFAULT_RULES,
    OPTIONAL_RULES,
    ALL_RULES,
)
from dotmd_parser.plan import (
    build_plan,
    render_ascii,
)

__all__ = [
    "__version__",
    # parser
    "build_graph",
    "resolve",
    "dependents_of",
    "parse_directives",
    "parse_read_refs",
    "parse_placeholders",
    "parse_description",
    "parse_deps_yml",
    "hash_content",
    "summary",
    # index
    "build_index",
    "build_scoped_index",
    "compact_graph",
    "merge_index",
    "save_index",
    "load_index",
    "needs_rebuild",
    "changed_files",
    "default_index_path",
    # digest
    "digest",
    "tree",
    "affects",
    "deps_of",
    # analyze
    "analyze_dependencies",
    "apply_analysis",
    "apply_analysis_from_file",
    "estimate_cost",
    "format_cost_estimate",
    "format_host_agent_plan",
    "generate_directives",
    "format_proposal",
    "scan_documents",
    "save_deps_yml",
    "load_deps_yml",
    "MODEL_PRICING",
    # inventory
    "inventory",
    "format_inventory",
    "suggest_next_command",
    "TEXT_EXTENSIONS",
    "BINARY_EXTENSIONS",
    "MARKDOWN_EXTENSIONS",
    # index_md
    "generate_index_md",
    "write_index_md",
    "extract_frontmatter",
    "DEFAULT_INDEX_FILENAME",
    "INDEX_MD_SCHEMA",
    # ledger
    "append_event",
    "read_events",
    "active_tags",
    "static_tags",
    "all_active_tags",
    "risk_level",
    "risk_report",
    "default_ledger_path",
    "RISK_TAGS",
    "HIGH_TAGS",
    # checks
    "run_checks",
    "summarize",
    "exit_code",
    "format_text",
    "format_json",
    "format_sarif",
    "CHECK_SCHEMA",
    # openrag
    "push_to_openrag",
    # scan
    "scan_content",
    "DEFAULT_RULES",
    "OPTIONAL_RULES",
    "ALL_RULES",
    # plan
    "build_plan",
    "render_ascii",
]
