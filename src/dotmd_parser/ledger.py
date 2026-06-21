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

from dotmd_parser.index_md import extract_frontmatter

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


def active_tags(root: str | Path, file: str) -> set[str]:
    """Replay ledger events for `file` and return the active risk-tag set."""
    tags: set[str] = set()
    for event in read_events(root):
        if event.get("file") != file:
            continue
        action = event.get("action")
        tag = event.get("tag")
        if action == "add" and tag in RISK_TAGS:
            tags.add(tag)
        elif action == "clear":
            if tag == "all":
                tags.clear()
            else:
                tags.discard(tag)
    return tags


def static_tags(root: str | Path, file: str) -> set[str]:
    """Read `risk:` from the file's frontmatter; keep only known enum tags."""
    path = Path(root) / file
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    fm = extract_frontmatter(text)
    value = fm.get("risk")
    if value is None:
        return set()
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [v for v in value if isinstance(v, str)]
    else:
        candidates = []
    return {c for c in candidates if c in RISK_TAGS}


def all_active_tags(root: str | Path, file: str) -> set[str]:
    """Union of ledger-active tags and static frontmatter tags."""
    return active_tags(root, file) | static_tags(root, file)


def risk_level(tags: set[str]) -> str:
    """Map an active-tag set to a level: high | medium | none."""
    if tags & HIGH_TAGS:
        return "high"
    if tags:
        return "medium"
    return "none"
