from dotmd_parser.plan import _reachable


def _idx(files: dict) -> dict:
    """Build a minimal compact-index dict from a {rel: deps_list} map."""
    def infer_type(rel: str) -> str:
        if rel.startswith("shared/"):
            return "shared"
        if rel.endswith("SKILL.md"):
            return "skill"
        if rel.startswith("agents/"):
            return "agent"
        return "agent"

    return {
        "root": "/x",
        "files": {
            rel: {"type": infer_type(rel), "title": rel, "deps": deps}
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


from dotmd_parser.plan import build_plan


def test_build_plan_parallel_batch_with_conflict():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    plan = build_plan(idx)
    assert plan["schema"] == "dotmd-plan/v1"
    assert plan["stats"] == {"tasks": 2, "batches": 1, "conflicts": 1, "cycles": 0}
    assert plan["batches"] == [
        {"level": 0, "parallelizable": True, "tasks": ["a.md", "b.md"]}
    ]
    assert plan["tasks"]["a.md"]["parallel_flag"] is True
    assert plan["tasks"]["a.md"]["depends_on"] == []
    assert plan["tasks"]["a.md"]["context"] == [
        {"path": "shared/role.md", "type": "shared", "title": "shared/role.md"}
    ]
    assert plan["conflicts"][0]["shared"] == ["shared/role.md"]


def test_build_plan_chain_two_batches():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate")],
        "a.md": [_d("b.md", "delegate")],
        "b.md": [],
    })
    plan = build_plan(idx)
    assert [batch["tasks"] for batch in plan["batches"]] == [["b.md"], ["a.md"]]
    assert plan["tasks"]["a.md"]["depends_on"] == ["b.md"]
    assert plan["batches"][0]["parallelizable"] is False


def test_build_plan_mutual_cycle_excluded_and_reported():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    plan = build_plan(idx)
    assert plan["batches"] == []
    assert plan["stats"]["cycles"] == 1
    assert any("task cycle" in c for c in plan["cycles"])
    assert plan["tasks"]["a.md"]["level"] is None
    assert plan["tasks"]["b.md"]["level"] is None


def test_build_plan_three_node_cycle_all_excluded():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("c.md", "delegate")],
        "c.md": [_d("a.md", "delegate")],
    })
    plan = build_plan(idx)
    assert plan["batches"] == []
    assert plan["stats"]["cycles"] >= 1
    for t in ("a.md", "b.md", "c.md"):
        assert plan["tasks"][t]["level"] is None


def test_build_plan_no_delegates_warns():
    idx = _idx({"a.md": [_d("b.md", "include")], "b.md": []})
    plan = build_plan(idx)
    assert plan["stats"]["tasks"] == 0
    assert plan["tasks"] == {}
    assert "no @delegate directives found" in plan["warnings"]


def test_build_plan_missing_target_warns():
    idx = {
        "root": "/x",
        "files": {
            "SKILL.md": {"type": "skill", "title": "Root",
                         "deps": [{"to": "gone.md", "type": "delegate", "parallel": False}]},
            "gone.md": {"type": "agent", "title": "", "missing": True, "deps": []},
        },
        "cycles": [],
        "stats": {"files": 2},
    }
    plan = build_plan(idx)
    assert plan["tasks"]["gone.md"]["context"] == []
    assert any("gone.md" in w for w in plan["warnings"])


from dotmd_parser.plan import render_ascii


def test_render_ascii_shows_batches_and_conflicts():
    idx = _idx({
        "SKILL.md": [_d("a.md", "delegate", True), _d("b.md", "delegate", True)],
        "a.md": [_d("shared/role.md", "include")],
        "b.md": [_d("shared/role.md", "include")],
        "shared/role.md": [],
    })
    text = render_ascii(build_plan(idx))
    assert "Level 0" in text
    assert "a.md" in text and "b.md" in text
    assert "conflict" in text.lower()


def test_render_ascii_reports_cycles():
    idx = _idx({
        "a.md": [_d("b.md", "delegate")],
        "b.md": [_d("a.md", "delegate")],
    })
    text = render_ascii(build_plan(idx))
    assert "cycle" in text.lower()


def test_invariant_batches_are_antichains_and_cover_all_tasks():
    idx = _idx({
        "SKILL.md": [
            _d("a.md", "delegate", True),
            _d("b.md", "delegate", True),
            _d("c.md", "delegate"),
        ],
        "a.md": [_d("shared.md", "include")],
        "b.md": [_d("a.md", "delegate")],   # b depends on a
        "c.md": [],
        "shared.md": [],
    })
    plan = build_plan(idx)
    dag = _task_dag(idx)

    # Every non-excluded task appears exactly once across batches.
    flat = [t for batch in plan["batches"] for t in batch["tasks"]]
    assert len(flat) == len(set(flat))
    excluded = {t for t, rec in plan["tasks"].items() if rec.get("level") is None}
    assert set(flat) == (set(plan["tasks"]) - excluded)

    # Antichain: no task shares a batch with one of its prereqs.
    for batch in plan["batches"]:
        members = set(batch["tasks"])
        for task in batch["tasks"]:
            assert not (dag[task] & members), f"{task} batched with a prereq"

    # Levels are contiguous and ascending.
    assert [b["level"] for b in plan["batches"]] == list(range(len(plan["batches"])))


def test_build_plan_is_exported_from_package():
    import dotmd_parser
    assert hasattr(dotmd_parser, "build_plan")
    assert hasattr(dotmd_parser, "render_ascii")
    assert "build_plan" in dotmd_parser.__all__
