import json

from dotmd_parser.checks import (
    _circular_findings, _missing_findings, _graph_warning_findings,
    _placeholder_findings, _conflicting_directive_findings, _orphan_findings,
    run_checks, summarize, exit_code, format_text, format_json, format_sarif,
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


def test_orphan_findings_skips_hidden_and_node_modules(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.md").write_text("# h", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.md").write_text("# d", encoding="utf-8")
    (tmp_path / "real-orphan.md").write_text("# o", encoding="utf-8")
    idx = _idx(files={"SKILL.md": {"type": "skill"}}, root=str(tmp_path))
    res = _orphan_findings(idx, str(tmp_path))
    assert [f["path"] for f in res] == ["real-orphan.md"]


def test_run_checks_integrates_and_respects_orphan_flag(tmp_path):
    (tmp_path / "SKILL.md").write_text("# s", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# o", encoding="utf-8")
    idx = _idx(
        files={"SKILL.md": {"type": "skill", "placeholders": ["x"]}},
        missing=["gone.md"], root=str(tmp_path),
    )
    without = run_checks(idx, root=str(tmp_path), enable_orphans=False)
    rules_without = {f["rule"] for f in without}
    assert "missing-reference" in rules_without
    assert "unresolved-placeholder" in rules_without
    assert "orphan-file" not in rules_without

    with_orphans = run_checks(idx, root=str(tmp_path), enable_orphans=True)
    assert "orphan-file" in {f["rule"] for f in with_orphans}


def test_summarize_counts_by_severity():
    findings = [
        {"rule": "circular", "severity": "error", "path": "", "message": "", "line": None},
        {"rule": "unresolved-placeholder", "severity": "warning", "path": "a", "message": "", "line": None},
        {"rule": "conflicting-directive", "severity": "warning", "path": "a", "message": "", "line": None},
    ]
    assert summarize(findings) == {"errors": 1, "warnings": 2}


def test_exit_code_matrix():
    err = [{"rule": "x", "severity": "error", "path": "", "message": "", "line": None}]
    warn = [{"rule": "x", "severity": "warning", "path": "", "message": "", "line": None}]
    clean: list[dict] = []
    assert exit_code(err, "error") == 1
    assert exit_code(err, "warning") == 1
    assert exit_code(err, "never") == 0
    assert exit_code(warn, "error") == 0
    assert exit_code(warn, "warning") == 1
    assert exit_code(warn, "never") == 0
    assert exit_code(clean, "warning") == 0


def test_format_text_has_summary_and_lines():
    idx = _idx(files={"a.md": {"type": "agent"}}, missing=["gone.md"], edges=2)
    findings = run_checks(idx)
    text = format_text(findings, idx)
    lines = text.splitlines()
    assert "errors:1 warnings:0" in lines[0]
    assert any("[ERROR]" in ln and "missing-reference" in ln for ln in lines)


def test_format_json_shape():
    idx = _idx(files={"a.md": {"type": "agent", "placeholders": ["v"]}}, edges=0)
    findings = run_checks(idx)
    payload = json.loads(format_json(findings, idx))
    assert payload["schema"] == "dotmd-check/v1"
    assert payload["stats"]["warnings"] == 1
    assert payload["stats"]["errors"] == 0
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["rule"] == "unresolved-placeholder"


def test_format_sarif_shape_and_locations():
    idx = _idx(
        files={"a.md": {"type": "agent"}},
        missing=["gone.md"],
        cycles=["Circular reference: /x/a.md -> /x/a.md"],
    )
    findings = run_checks(idx)
    sarif = json.loads(format_sarif(findings, idx))
    assert sarif["version"] == "2.1.0"
    driver = sarif["runs"][0]["tool"]["driver"]
    assert driver["name"] == "dotmd-parser"
    rule_ids = {r["id"] for r in driver["rules"]}
    assert {"missing-reference", "circular"} <= rule_ids

    results = sarif["runs"][0]["results"]
    by_rule = {r["ruleId"]: r for r in results}
    # missing-reference has a path -> has a location
    assert by_rule["missing-reference"]["level"] == "error"
    assert (by_rule["missing-reference"]["locations"][0]["physicalLocation"]
            ["artifactLocation"]["uri"] == "gone.md")
    # circular has path "" -> no locations key
    assert "locations" not in by_rule["circular"]


def test_checks_api_is_exported():
    import dotmd_parser
    for name in ("run_checks", "summarize", "exit_code",
                 "format_text", "format_json", "format_sarif", "CHECK_SCHEMA"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name


def test_run_checks_no_double_count_of_cycles_and_missing():
    # circular + missing appear in BOTH index["cycles"]/["missing"] AND
    # index["warnings"]; run_checks must count each exactly once.
    idx = _idx(
        files={"a.md": {"type": "agent"}},
        cycles=["Circular reference: /x/a.md -> /x/a.md"],
        missing=["gone.md"],
        warnings=[
            {"type": "circular", "path": "a.md", "message": "Circular reference: /x/a.md -> /x/a.md"},
            {"type": "missing", "path": "gone.md", "message": "referenced file does not exist"},
        ],
    )
    findings = run_checks(idx)
    rules = [f["rule"] for f in findings]
    assert rules.count("circular") == 1
    assert rules.count("missing-reference") == 1


def test_run_checks_promotes_depth_and_read_error_to_errors():
    idx = _idx(warnings=[
        {"type": "depth_exceeded", "path": "deep.md", "message": "max depth 10 exceeded"},
        {"type": "read_error", "path": "bad.md", "message": "could not read"},
    ])
    findings = run_checks(idx)
    by_rule = {f["rule"]: f for f in findings}
    assert by_rule["depth-exceeded"]["severity"] == "error"
    assert by_rule["read-error"]["severity"] == "error"
