from dotmd_parser.checks import (
    _circular_findings, _missing_findings, _graph_warning_findings,
    _placeholder_findings, _conflicting_directive_findings, _orphan_findings,
)


def _idx(files=None, cycles=None, missing=None, warnings=None, root="/x", edges=0):
    files = files or {}
    return {
        "schema": 1, "root": root,
        "stats": {"files": len(files), "edges": edges,
                  "cycles": len(cycles or []), "missing": len(missing or [])},
        "files": files,
        "cycles": cycles or [],
        "missing": missing or [],
        "warnings": warnings or [],
    }


def test_circular_findings():
    idx = _idx(cycles=["Circular reference: /x/a.md -> /x/b.md -> /x/a.md"])
    res = _circular_findings(idx)
    assert len(res) == 1
    assert res[0]["rule"] == "circular"
    assert res[0]["severity"] == "error"
    assert res[0]["path"] == ""
    assert "a.md" in res[0]["message"]


def test_missing_findings():
    idx = _idx(missing=["shared/gone.md"])
    res = _missing_findings(idx)
    assert res == [{
        "rule": "missing-reference", "severity": "error",
        "path": "shared/gone.md",
        "message": "referenced file does not exist", "line": None,
    }]


def test_graph_warning_findings_promotes_depth_and_read_error_only():
    idx = _idx(warnings=[
        {"type": "depth_exceeded", "path": "deep.md", "message": "max depth 10 exceeded"},
        {"type": "read_error", "path": "bad.md", "message": "could not read"},
        {"type": "missing", "path": "x.md", "message": "nope"},  # must be ignored here
        {"type": "circular", "path": "c.md", "message": "cycle"},  # must be ignored here
    ])
    res = _graph_warning_findings(idx)
    rules = sorted(f["rule"] for f in res)
    assert rules == ["depth-exceeded", "read-error"]
    assert all(f["severity"] == "error" for f in res)


def test_placeholder_findings_one_per_var_sorted():
    idx = _idx(files={
        "b.md": {"type": "shared"},
        "a.md": {"type": "agent", "placeholders": ["year", "company_id"]},
    })
    res = _placeholder_findings(idx)
    assert [(f["path"], f["message"]) for f in res] == [
        ("a.md", "unresolved placeholder: {{company_id}}"),
        ("a.md", "unresolved placeholder: {{year}}"),
    ]
    assert all(f["rule"] == "unresolved-placeholder" and f["severity"] == "warning"
               for f in res)


def test_placeholder_findings_empty_when_none():
    idx = _idx(files={"a.md": {"type": "agent"}})
    assert _placeholder_findings(idx) == []


def test_conflicting_directive_include_and_ref_same_target():
    idx = _idx(files={
        "SKILL.md": {"type": "skill", "deps": [
            {"to": "shared/role.md", "type": "include"},
            {"to": "shared/role.md", "type": "ref"},
        ]},
        "shared/role.md": {"type": "shared"},
    })
    res = _conflicting_directive_findings(idx)
    assert len(res) == 1
    assert res[0]["rule"] == "conflicting-directive"
    assert res[0]["severity"] == "warning"
    assert res[0]["path"] == "SKILL.md"
    assert "shared/role.md" in res[0]["message"]
    assert "include" in res[0]["message"] and "ref" in res[0]["message"]


def test_conflicting_directive_single_type_is_clean():
    idx = _idx(files={"SKILL.md": {"type": "skill", "deps": [
        {"to": "a.md", "type": "include"},
    ]}})
    assert _conflicting_directive_findings(idx) == []


def test_conflicting_directive_ignores_read_ref():
    # include + read-ref to same target: only one EXPLICIT type -> no conflict
    idx = _idx(files={"SKILL.md": {"type": "skill", "deps": [
        {"to": "a.md", "type": "include"},
        {"to": "a.md", "type": "read-ref"},
    ]}})
    assert _conflicting_directive_findings(idx) == []


def test_orphan_findings_flags_unreferenced_md(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s", encoding="utf-8")
    (tmp_path / "used.md").write_text("# u", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# o", encoding="utf-8")
    idx = _idx(files={"SKILL.md": {"type": "skill"}, "used.md": {"type": "reference"}},
               root=str(tmp_path))
    res = _orphan_findings(idx, str(tmp_path))
    assert [f["path"] for f in res] == ["orphan.md"]
    assert res[0]["rule"] == "orphan-file" and res[0]["severity"] == "warning"


def test_orphan_findings_none_root_returns_empty():
    idx = _idx(files={"SKILL.md": {"type": "skill"}})
    assert _orphan_findings(idx, None) == []
