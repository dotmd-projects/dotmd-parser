"""
dotmd-parser — Dependency graph parser for .md skill files.

Parse @include/@delegate directives, build dependency graphs,
and resolve templates for AI agent prompt engineering.

API:
    from dotmd_parser import build_graph, resolve, dependents_of, summary
"""

__version__ = "0.1.0"

from dotmd_parser.parser import (
    build_graph,
    resolve,
    dependents_of,
    parse_directives,
    parse_read_refs,
    parse_placeholders,
    parse_deps_yml,
    summary,
)

__all__ = [
    "build_graph",
    "resolve",
    "dependents_of",
    "parse_directives",
    "parse_read_refs",
    "parse_placeholders",
    "parse_deps_yml",
    "summary",
]
