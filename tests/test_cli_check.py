import json

import pytest

from dotmd_parser.cli import run


def _skill_missing(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\n@include shared/gone.md\n", encoding="utf-8"
    )
    return tmp_path


def _skill_warn_only(tmp_path):
    # unresolved placeholder, no errors
    (tmp_path / "SKILL.md").write_text(
        "# Root\n\nUse {{company_id}} here.\n", encoding="utf-8"
    )
    return tmp_path


def _skill_orphan(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "extra.md").write_text("# Extra\n", encoding="utf-8")
    return tmp_path


def test_check_default_fails_on_missing(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill)])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "missing-reference" in out


def test_check_warning_only_passes_by_default(tmp_path):
    skill = _skill_warn_only(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill)])
    assert exc.value.code == 0


def test_check_fail_on_warning_fails_on_placeholder(tmp_path):
    skill = _skill_warn_only(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill), "--fail-on", "warning"])
    assert exc.value.code == 1


def test_check_fail_on_never_always_zero(tmp_path):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["check", str(skill), "--fail-on", "never"])
    assert exc.value.code == 0


def test_check_json_format(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "json", "--fail-on", "never"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "dotmd-check/v1"
    assert payload["stats"]["errors"] >= 1


def test_check_sarif_format(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "sarif", "--fail-on", "never"])
    sarif = json.loads(capsys.readouterr().out)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "dotmd-parser"


def test_check_orphans_opt_in(tmp_path, capsys):
    skill = _skill_orphan(tmp_path)
    # without --check orphans: clean (exit 0, no orphan finding)
    with pytest.raises(SystemExit) as exc1:
        run(["check", str(skill), "--fail-on", "warning"])
    assert exc1.value.code == 0
    # with --check orphans: extra.md is flagged -> warning -> exit 1
    with pytest.raises(SystemExit) as exc2:
        run(["check", str(skill), "--check", "orphans", "--fail-on", "warning"])
    assert exc2.value.code == 1
    assert "orphan-file" in capsys.readouterr().out


def test_check_out_writes_file(tmp_path, capsys):
    skill = _skill_missing(tmp_path)
    out_file = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        run(["check", str(skill), "--format", "json", "--out", str(out_file),
             "--fail-on", "never"])
    assert capsys.readouterr().out.strip() == ""
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["schema"] == "dotmd-check/v1"
