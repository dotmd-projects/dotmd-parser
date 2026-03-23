"""
dotmd-parser — .md ファイルの依存グラフパーサー

主要API:
    from dotmd_parser import build_graph, resolve, dependents_of, summary
"""

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
