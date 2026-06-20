from dotmd_parser.plan import _reachable


def _idx(files: dict) -> dict:
    """Build a minimal compact-index dict from a {rel: deps_list} map."""
    return {
        "root": "/x",
        "files": {
            rel: {"type": "agent", "title": rel, "deps": deps}
            for rel, deps in files.items()
        },
        "cycles": [],
        "stats": {"files": len(files)},
    }


def _d(to: str, type_: str = "include", parallel: bool = False) -> dict:
    return {"to": to, "type": type_, "parallel": parallel}


def test_reachable_follows_deps_and_excludes_start():
    idx = _idx({
        "a.md": [_d("b.md"), _d("c.md")],
        "b.md": [_d("d.md")],
        "c.md": [],
        "d.md": [],
    })
    assert _reachable(idx, "a.md") == {"b.md", "c.md", "d.md"}
    assert _reachable(idx, "b.md") == {"d.md"}
    assert _reachable(idx, "d.md") == set()


def test_reachable_is_cycle_safe():
    idx = _idx({"a.md": [_d("b.md")], "b.md": [_d("a.md")]})
    assert _reachable(idx, "a.md") == {"b.md"}  # start excluded even via cycle


from dotmd_parser.plan import _task_nodes


def test_task_nodes_collects_only_delegate_targets():
    idx = _idx({
        "SKILL.md": [
            _d("agents/a.md", "delegate", True),
            _d("agents/b.md", "delegate", True),
            _d("shared/role.md", "include"),
        ],
        "agents/a.md": [_d("shared/role.md", "include")],
        "agents/b.md": [_d("agents/c.md", "delegate")],
        "agents/c.md": [],
        "shared/role.md": [],
    })
    assert _task_nodes(idx) == {"agents/a.md", "agents/b.md", "agents/c.md"}


def test_task_nodes_empty_when_no_delegates():
    idx = _idx({"a.md": [_d("b.md", "include")], "b.md": []})
    assert _task_nodes(idx) == set()
