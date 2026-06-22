import json

import pytest

from dotmd_parser.cli import run


def _make_skill(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "shared").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\n@delegate agents/a.md --parallel\n@delegate agents/b.md --parallel\n",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "a.md").write_text(
        "# A\n\n@include ../shared/role.md\n", encoding="utf-8"
    )
    (tmp_path / "agents" / "b.md").write_text(
        "# B\n\n@include ../shared/role.md\n", encoding="utf-8"
    )
    (tmp_path / "shared" / "role.md").write_text("# Role\n", encoding="utf-8")
    return tmp_path


def test_plan_json_to_stdout(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert plan["schema"] == "dotmd-plan/v1"
    assert plan["stats"]["tasks"] == 2
    assert plan["stats"]["conflicts"] == 1


def test_plan_ascii_only(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--ascii"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Level 0" in out
    # ascii-only: stdout must not be JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_plan_out_writes_file(tmp_path, capsys):
    skill = _make_skill(tmp_path)
    out_file = tmp_path / "plan.json"
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--out", str(out_file)])
    assert exc.value.code == 0
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["stats"]["tasks"] == 2
    assert capsys.readouterr().out.strip() == ""  # nothing on stdout


def test_plan_strict_exits_one_when_conflicts_present(tmp_path):
    skill = _make_skill(tmp_path)
    # conflicts exist here, so --strict should exit 1
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(skill), "--strict"])
    assert exc.value.code == 1


def test_plan_strict_exits_zero_when_clean(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\n@delegate agents/a.md --parallel\n@delegate agents/b.md --parallel\n",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "agents" / "b.md").write_text("# B\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run(["plan", str(tmp_path), "--strict"])
    assert exc.value.code == 0
