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


from dotmd_parser.plan import _task_dag, _task_cycles


def test_task_dag_links_prereqs_via_subtree():
    # SKILL delegates a; a delegates b. So b is a prereq of a.
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate")],
        "a.md": [_d("b.md", "delegate")],
        "b.md": [],
    })
    dag = _task_dag(idx)
    assert dag == {"a.md": {"b.md"}, "b.md": set()}


def test_task_dag_independent_tasks_have_no_prereqs():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared.md", "include")],
        "b.md": [_d("shared.md", "include")],
        "shared.md": [],
    })
    dag = _task_dag(idx)
    assert dag == {"a.md": set(), "b.md": set()}


def test_task_cycles_detects_mutual_pairs():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    dag = _task_dag(idx)
    cycles = _task_cycles(dag)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a.md", "b.md"}


def test_task_cycles_empty_for_acyclic():
    dag = {"a.md": {"b.md"}, "b.md": set()}
    assert _task_cycles(dag) == []


from dotmd_parser.plan import _levels


def test_levels_independent_tasks_one_batch():
    dag = {"a.md": set(), "b.md": set()}
    assert _levels(dag) == [["a.md", "b.md"]]


def test_levels_chain_two_batches():
    dag = {"a.md": {"b.md"}, "b.md": set()}
    # b has no prereqs -> level 0; a depends on b -> level 1
    assert _levels(dag) == [["b.md"], ["a.md"]]


def test_levels_excludes_cycle_members():
    dag = {"a.md": {"b.md"}, "b.md": {"a.md"}, "c.md": set()}
    assert _levels(dag, excluded={"a.md", "b.md"}) == [["c.md"]]


def test_levels_empty_when_all_excluded():
    dag = {"a.md": {"b.md"}, "b.md": {"a.md"}}
    assert _levels(dag, excluded={"a.md", "b.md"}) == []


from dotmd_parser.plan import _conflicts


def test_conflicts_reports_shared_dependency_in_batch():
    idx = _idx({
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    conflicts = _conflicts(idx, [["a.md", "b.md"]])
    assert conflicts == [
        {"level": 0, "between": ["a.md", "b.md"], "shared": ["shared/role.md"]}
    ]


def test_conflicts_none_when_no_overlap():
    idx = _idx({
        "a.md": [_d("x.md", "include")],
        "b.md": [_d("y.md", "include")],
        "x.md": [],
        "y.md": [],
    })
    assert _conflicts(idx, [["a.md", "b.md"]]) == []


def test_conflicts_ignore_shared_task_nodes():
    # a and b both reach task c -> c is a task, so not counted as a conflict.
    idx = _idx({
        "a.md": [_d("c.md", "delegate")],
        "b.md": [_d("c.md", "delegate")],
        "c.md": [],
    })
    # batch is [a, b]; their only shared reachable is task c -> excluded
    assert _conflicts(idx, [["a.md", "b.md"]]) == []


from dotmd_parser.plan import _context_of


def test_context_of_lists_subtree_files_sorted():
    idx = {
        "root": "/x",
        "files": {
            "a.md": {"type": "agent", "title": "A",
                     "deps": [{"to": "shared/role.md", "type": "include"},
                              {"to": "shared/acc.md", "type": "include"}]},
            "shared/role.md": {"type": "shared", "title": "Role", "deps": []},
            "shared/acc.md": {"type": "shared", "title": "Accounts", "deps": []},
        },
        "cycles": [],
        "stats": {"files": 3},
    }
    assert _context_of(idx, "a.md") == [
        {"path": "shared/acc.md", "type": "shared", "title": "Accounts"},
        {"path": "shared/role.md", "type": "shared", "title": "Role"},
    ]


def test_context_of_skips_missing_files():
    idx = {
        "root": "/x",
        "files": {
            "a.md": {"type": "agent", "title": "A",
                     "deps": [{"to": "gone.md", "type": "include"}]},
        },
        "cycles": [],
        "stats": {"files": 1},
    }
    assert _context_of(idx, "a.md") == []
