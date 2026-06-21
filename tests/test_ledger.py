import json
import pytest
from dotmd_parser.ledger import (
    default_ledger_path, append_event, read_events, RISK_TAGS, HIGH_TAGS,
)


def test_constants():
    assert RISK_TAGS == ("fix-failed", "fragile", "security-sensitive", "deprecated")
    assert HIGH_TAGS == frozenset({"fix-failed", "security-sensitive"})


def test_default_ledger_path(tmp_path):
    p = default_ledger_path(tmp_path)
    assert p == tmp_path / ".claude" / "dotmd-ledger.jsonl"


def test_append_then_read_roundtrip(tmp_path):
    append_event(tmp_path, "shared/role.md", "add", "fix-failed",
                 note="retry hung", ts="2026-06-21T00:00:00Z")
    append_event(tmp_path, "shared/role.md", "clear", "fix-failed",
                 ts="2026-06-22T00:00:00Z")
    events = read_events(tmp_path)
    assert len(events) == 2
    assert events[0] == {"ts": "2026-06-21T00:00:00Z", "file": "shared/role.md",
                         "action": "add", "tag": "fix-failed", "note": "retry hung"}
    assert events[1] == {"ts": "2026-06-22T00:00:00Z", "file": "shared/role.md",
                         "action": "clear", "tag": "fix-failed"}
    # .claude/ auto-created
    assert (tmp_path / ".claude" / "dotmd-ledger.jsonl").exists()


def test_append_rejects_unknown_add_tag(tmp_path):
    with pytest.raises(ValueError):
        append_event(tmp_path, "a.md", "add", "bogus")


def test_read_events_absent_is_empty(tmp_path):
    assert read_events(tmp_path) == []


def test_read_events_skips_malformed_lines(tmp_path):
    path = default_ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"t","file":"a.md","action":"add","tag":"fragile"}\n'
        'NOT JSON\n'
        '{"ts":"t2","file":"b.md","action":"add","tag":"deprecated"}\n',
        encoding="utf-8",
    )
    events = read_events(tmp_path)
    assert [e["file"] for e in events] == ["a.md", "b.md"]
