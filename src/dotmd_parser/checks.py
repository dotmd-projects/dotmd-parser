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

import json
from pathlib import Path

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


_EXPLICIT_DIRECTIVE_TYPES = {"include", "ref", "delegate"}


def _conflicting_directive_findings(index: dict) -> list[dict]:
    """Warn when a source reaches one target via ≥2 distinct explicit types."""
    out: list[dict] = []
    files = index.get("files", {})
    for rel in sorted(files):
        by_target: dict[str, set[str]] = {}
        for dep in files[rel].get("deps", []):
            dtype = dep.get("type", "")
            if dtype not in _EXPLICIT_DIRECTIVE_TYPES:
                continue
            target = dep.get("to", "")
            if not target:
                continue
            by_target.setdefault(target, set()).add(dtype)
        for target in sorted(by_target):
            types = by_target[target]
            if len(types) >= 2:
                joined = ", ".join(sorted(types))
                out.append(_finding(
                    "conflicting-directive", "warning", rel,
                    f"{target} is referenced by multiple directive types ({joined})",
                ))
    return out


def _orphan_findings(index: dict, root: str | None) -> list[dict]:
    """Warn about .md files on disk that no graph node references."""
    if root is None:
        return []
    base = Path(root)
    if base.is_file():
        base = base.parent
    if not base.is_dir():
        return []
    node_set = set(index.get("files", {}).keys())
    out: list[dict] = []
    for path in sorted(base.rglob("*.md")):
        rel_path = path.relative_to(base)
        if any(part.startswith(".") for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        if "node_modules" in rel:
            continue
        if not path.is_file():
            continue
        if rel not in node_set:
            out.append(_finding("orphan-file", "warning", rel,
                               "file is not referenced by any node"))
    return out


def run_checks(index: dict, root: str | None = None,
               enable_orphans: bool = False) -> list[dict]:
    """Run all enabled checks and return a flat list of findings."""
    findings: list[dict] = []
    findings += _circular_findings(index)
    findings += _missing_findings(index)
    findings += _graph_warning_findings(index)
    findings += _placeholder_findings(index)
    findings += _conflicting_directive_findings(index)
    if enable_orphans:
        findings += _orphan_findings(index, root)
    return findings


def summarize(findings: list[dict]) -> dict:
    """Count findings by severity."""
    errors = sum(1 for f in findings if f.get("severity") == "error")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    return {"errors": errors, "warnings": warnings}


def exit_code(findings: list[dict], fail_on: str) -> int:
    """Map findings to a CI exit code per the fail_on threshold."""
    counts = summarize(findings)
    if fail_on == "never":
        return 0
    if fail_on == "warning":
        return 1 if (counts["errors"] or counts["warnings"]) else 0
    # default: "error"
    return 1 if counts["errors"] else 0


def format_text(findings: list[dict], index: dict) -> str:
    """Render findings as a backward-compatible text summary + detail lines."""
    stats = index.get("stats", {})
    counts = summarize(findings)
    lines = [
        f"{stats.get('files', 0)} files, {stats.get('edges', 0)} edges — "
        f"errors:{counts['errors']} warnings:{counts['warnings']}"
    ]
    for f in findings:
        loc = f.get("path") or "-"
        lines.append(
            f"  [{f['severity'].upper()}] {f['rule']}: {loc} — {f['message']}"
        )
    return "\n".join(lines)


def format_json(findings: list[dict], index: dict) -> str:
    """Render findings as dotmd-check/v1 JSON."""
    stats = index.get("stats", {})
    counts = summarize(findings)
    payload = {
        "schema": CHECK_SCHEMA,
        "root": index.get("root", ""),
        "stats": {
            "files": stats.get("files", 0),
            "edges": stats.get("edges", 0),
            "errors": counts["errors"],
            "warnings": counts["warnings"],
        },
        "findings": findings,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _camel(rule_id: str) -> str:
    parts = rule_id.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def format_sarif(findings: list[dict], index: dict) -> str:
    """Render findings as SARIF 2.1.0 JSON (for GitHub code scanning)."""
    from dotmd_parser import __version__  # local import avoids cycle at import time

    rules: dict[str, dict] = {}
    results: list[dict] = []
    for f in findings:
        rule_id = f["rule"]
        rules.setdefault(rule_id, {"id": rule_id, "name": _camel(rule_id)})
        result: dict = {
            "ruleId": rule_id,
            "level": f["severity"],
            "message": {"text": f["message"]},
        }
        if f.get("path"):
            physical: dict = {"artifactLocation": {"uri": f["path"]}}
            if f.get("line"):
                physical["region"] = {"startLine": f["line"]}
            result["locations"] = [{"physicalLocation": physical}]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "dotmd-parser",
                "informationUri": "https://github.com/dotmd-projects/dotmd-parser",
                "version": __version__,
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=2)
