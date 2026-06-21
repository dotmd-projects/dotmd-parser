"""
dotmd-parser — Memory-as-Governance risk ledger.

An append-only JSONL event log of per-file risk tags, combined with reverse
dependencies (`digest.affects`) and static frontmatter tags to produce a
pre-edit risk report. Pure stdlib. The parser/index are not touched.

Event schema (one JSON object per line):
    {"ts": <ISO8601 Z>, "file": <root-relative POSIX>,
     "action": "add"|"clear", "tag": <enum>|"all", "note"?: <str>}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RISK_TAGS = ("fix-failed", "fragile", "security-sensitive", "deprecated")
HIGH_TAGS = frozenset({"fix-failed", "security-sensitive"})
LEDGER_DIR = ".claude"
LEDGER_FILE = "dotmd-ledger.jsonl"


def default_ledger_path(root: str | Path) -> Path:
    """Return `<root>/.claude/dotmd-ledger.jsonl`."""
    return Path(root) / LEDGER_DIR / LEDGER_FILE


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def append_event(
    root: str | Path,
    file: str,
    action: str,
    tag: str,
    note: str | None = None,
    ts: str | None = None,
) -> Path:
    """Append one event to the ledger; create `.claude/` if missing."""
    if action not in ("add", "clear"):
        raise ValueError(f"action must be 'add' or 'clear', got {action!r}")
    if action == "add" and tag not in RISK_TAGS:
        raise ValueError(f"unknown risk tag {tag!r}; choose from {RISK_TAGS}")
    if action == "clear" and tag != "all" and tag not in RISK_TAGS:
        raise ValueError(f"unknown clear tag {tag!r}; choose from {RISK_TAGS} or 'all'")

    event: dict = {
        "ts": ts or _utc_now(),
        "file": file,
        "action": action,
        "tag": tag,
    }
    if note:
        event["note"] = note

    path = default_ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def read_events(root: str | Path) -> list[dict]:
    """Read all ledger events; [] if absent; skip malformed lines."""
    path = default_ledger_path(root)
    if not path.exists():
        return []
    events: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed ledger line: {line[:80]}", file=sys.stderr)
    return events
