"""
dotmd-parser — Dependency graph parser for .md skill files.

Parse @include/@delegate/@ref directives, build dependency graphs, resolve
templates, and produce a token-efficient on-disk index for AI agents.

API:
    from dotmd_parser import build_graph, resolve, dependents_of, summary
    from dotmd_parser import build_index, save_index, load_index
    from dotmd_parser import digest, tree, affects
"""

__version__ = "0.3.0"

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
    compact_graph,
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
    "compact_graph",
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
]
