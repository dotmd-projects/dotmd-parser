import json
import pytest
from dotmd_parser.ledger import (
    default_ledger_path, append_event, read_events, RISK_TAGS, HIGH_TAGS,
    active_tags, static_tags, all_active_tags, risk_level, risk_report,
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


def test_active_tags_add_then_clear(tmp_path):
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    append_event(tmp_path, "a.md", "add", "fragile", ts="t2")
    assert active_tags(tmp_path, "a.md") == {"fix-failed", "fragile"}
    append_event(tmp_path, "a.md", "clear", "fix-failed", ts="t3")
    assert active_tags(tmp_path, "a.md") == {"fragile"}


def test_active_tags_clear_all(tmp_path):
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    append_event(tmp_path, "a.md", "add", "deprecated", ts="t2")
    append_event(tmp_path, "a.md", "clear", "all", ts="t3")
    assert active_tags(tmp_path, "a.md") == set()


def test_active_tags_isolated_per_file(tmp_path):
    append_event(tmp_path, "a.md", "add", "fragile", ts="t1")
    append_event(tmp_path, "b.md", "add", "deprecated", ts="t2")
    assert active_tags(tmp_path, "a.md") == {"fragile"}
    assert active_tags(tmp_path, "b.md") == {"deprecated"}


def test_active_tags_empty_when_no_events(tmp_path):
    assert active_tags(tmp_path, "a.md") == set()


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_static_tags_from_frontmatter_list(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk:\n  - fragile\n  - bogus\n---\n# A\n")
    assert static_tags(tmp_path, "a.md") == {"fragile"}  # bogus dropped


def test_static_tags_from_frontmatter_scalar(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk: deprecated\n---\n# A\n")
    assert static_tags(tmp_path, "a.md") == {"deprecated"}


def test_static_tags_absent(tmp_path):
    _write(tmp_path, "a.md", "# A\nno frontmatter\n")
    assert static_tags(tmp_path, "a.md") == set()
    assert static_tags(tmp_path, "missing.md") == set()


def test_all_active_tags_union(tmp_path):
    _write(tmp_path, "a.md", "---\nrisk: fragile\n---\n# A\n")
    append_event(tmp_path, "a.md", "add", "fix-failed", ts="t1")
    assert all_active_tags(tmp_path, "a.md") == {"fragile", "fix-failed"}


def test_risk_level():
    assert risk_level({"fix-failed"}) == "high"
    assert risk_level({"security-sensitive", "fragile"}) == "high"
    assert risk_level({"fragile"}) == "medium"
    assert risk_level({"deprecated"}) == "medium"
    assert risk_level(set()) == "none"


def _idx_with_dep():
    # SKILL.md and agents/a.md both include shared/role.md → role.md affects both
    return {
        "root": "/x",
        "files": {
            "SKILL.md": {"type": "skill", "deps": [{"to": "shared/role.md", "type": "include"}]},
            "agents/a.md": {"type": "agent", "deps": [{"to": "shared/role.md", "type": "include"}]},
            "shared/role.md": {"type": "shared", "deps": []},
        },
        "stats": {"files": 3},
    }


def test_risk_report_combines_affects_and_tags(tmp_path):
    append_event(tmp_path, "shared/role.md", "add", "fix-failed", ts="t1")
    report = risk_report(_idx_with_dep(), tmp_path, "shared/role.md")
    assert report["file"] == "shared/role.md"
    assert sorted(report["affects"]) == ["SKILL.md", "agents/a.md"]
    assert report["affects_count"] == 2
    assert report["active_tags"] == ["fix-failed"]
    assert report["level"] == "high"
    assert report["events"][0]["tag"] == "fix-failed"


def test_risk_report_no_risk(tmp_path):
    report = risk_report(_idx_with_dep(), tmp_path, "shared/role.md")
    assert report["active_tags"] == []
    assert report["level"] == "none"
    assert report["events"] == []


def test_risk_report_recent_limit_newest_first(tmp_path):
    for i in range(7):
        append_event(tmp_path, "a.md", "add", "fragile", ts=f"t{i}")
    report = risk_report({"files": {}, "root": "/x"}, tmp_path, "a.md", recent=3)
    assert len(report["events"]) == 3
    assert [e["ts"] for e in report["events"]] == ["t6", "t5", "t4"]


# tests/test_ledger.py — append
def test_ledger_api_is_exported():
    import dotmd_parser
    for name in ("append_event", "active_tags", "all_active_tags", "static_tags",
                 "risk_report", "risk_level", "RISK_TAGS", "HIGH_TAGS",
                 "default_ledger_path"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name
