from dotmd_parser.checks import (
    _circular_findings, _missing_findings, _graph_warning_findings,
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
