# tests/test_cli_ledger.py
import json
import pytest
from dotmd_parser.cli import run


def _skill(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "SKILL.md").write_text("# Root\n\n@include shared/role.md\n", encoding="utf-8")
    (tmp_path / "shared" / "role.md").write_text("# Role\n", encoding="utf-8")
    return tmp_path


def test_ledger_add_then_risk_text(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit) as e1:
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fix-failed"])
    assert e1.value.code == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as e2:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "never"])
    assert e2.value.code == 0
    out = capsys.readouterr().out
    assert "fix-failed" in out
    assert "affects" in out


def test_ledger_add_unknown_tag_exits_2(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit) as e:
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "bogus"])
    # argparse choices rejects -> exit 2
    assert e.value.code == 2


def test_ledger_clear_removes_tag(tmp_path, capsys):
    skill = _skill(tmp_path)
    for args in (["ledger", "add", str(skill), "shared/role.md", "--tag", "fix-failed"],
                 ["ledger", "clear", str(skill), "shared/role.md", "--tag", "fix-failed"]):
        with pytest.raises(SystemExit):
            run(args)
        capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["risk", str(skill), "shared/role.md", "--json", "--fail-on", "never"])
    report = json.loads(capsys.readouterr().out)
    assert report["active_tags"] == []
    assert report["level"] == "none"


def test_risk_json_shape(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fragile"])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["risk", str(skill), "shared/role.md", "--json", "--fail-on", "never"])
    report = json.loads(capsys.readouterr().out)
    assert report["file"] == "shared/role.md"
    assert report["affects_count"] == 1   # SKILL.md includes role.md
    assert report["level"] == "medium"


def test_risk_fail_on_high_exit_code(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "security-sensitive"])
    capsys.readouterr()
    # high tag active -> --fail-on high exits 1
    with pytest.raises(SystemExit) as e_high:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "high"])
    assert e_high.value.code == 1
    capsys.readouterr()


def test_risk_fail_on_medium_vs_any(tmp_path, capsys):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fragile"])
    capsys.readouterr()
    # medium tag: --fail-on high -> 0; --fail-on any -> 1
    with pytest.raises(SystemExit) as e_high:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "high"])
    assert e_high.value.code == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as e_any:
        run(["risk", str(skill), "shared/role.md", "--fail-on", "any"])
    assert e_any.value.code == 1


def test_ledger_clear_requires_tag_or_all(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit) as e:
        run(["ledger", "clear", str(skill), "shared/role.md"])
    assert e.value.code == 2


def test_risk_accepts_skill_md_file_path(tmp_path, capsys):
    # `risk` documents accepting a SKILL.md path; ledger/frontmatter must
    # resolve under its parent dir (same root as `ledger add <dir>`).
    skill = _skill(tmp_path)
    with pytest.raises(SystemExit):
        run(["ledger", "add", str(skill), "shared/role.md", "--tag", "fix-failed"])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["risk", str(skill / "SKILL.md"), "shared/role.md",
             "--json", "--fail-on", "never"])
    report = json.loads(capsys.readouterr().out)
    assert "fix-failed" in report["active_tags"]
    assert report["level"] == "high"
