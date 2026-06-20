"""
dotmd-parser — guidance health checks (deterministic CI gate).

Consumes a compact index (from `index.build_index` / `index.load_index`) and
produces a flat list of Finding dicts, rendered as text / JSON / SARIF. Pure,
stdlib-only, no LLM. The raw graph / parser are not touched.

Finding shape:
    {"rule": str, "severity": "error"|"warning", "path": str,
     "message": str, "line": int | None}
"""

from __future__ import annotations

CHECK_SCHEMA = "dotmd-check/v1"

_GRAPH_WARNING_RULES = {
    "depth_exceeded": "depth-exceeded",
    "read_error": "read-error",
}


def _finding(rule: str, severity: str, path: str, message: str,
             line: int | None = None) -> dict:
    return {"rule": rule, "severity": severity, "path": path,
            "message": message, "line": line}


def _circular_findings(index: dict) -> list[dict]:
    """One error finding per recorded cycle message (path unknown → '')."""
    return [
        _finding("circular", "error", "", msg)
        for msg in index.get("cycles", [])
    ]


def _missing_findings(index: dict) -> list[dict]:
    """One error finding per missing referenced file."""
    return [
        _finding("missing-reference", "error", rel,
                 "referenced file does not exist")
        for rel in index.get("missing", [])
    ]


def _graph_warning_findings(index: dict) -> list[dict]:
    """Promote depth_exceeded / read_error graph warnings to error findings."""
    out: list[dict] = []
    for warning in index.get("warnings", []):
        rule = _GRAPH_WARNING_RULES.get(warning.get("type", ""))
        if rule is None:
            continue
        out.append(_finding(rule, "error", warning.get("path", ""),
                            warning.get("message", "")))
    return out


def _placeholder_findings(index: dict) -> list[dict]:
    """One warning finding per unresolved {{var}} (sorted by path, var)."""
    out: list[dict] = []
    files = index.get("files", {})
    for rel in sorted(files):
        for var in sorted(files[rel].get("placeholders", []) or []):
            out.append(_finding(
                "unresolved-placeholder", "warning", rel,
                f"unresolved placeholder: {{{{{var}}}}}",
            ))
    return out
