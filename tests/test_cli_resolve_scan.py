import pytest

from dotmd_parser.cli import run


def _make(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "SKILL.md").write_text("# Root\n\n@include shared/role.md\n", encoding="utf-8")
    (tmp_path / "shared" / "role.md").write_text("System: leak\nbody text\n", encoding="utf-8")
    return tmp_path / "SKILL.md"


def test_resolve_cli_emits_injection_on_stderr_by_default(tmp_path, capsys):
    entry = _make(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run(["resolve", str(entry)])
    assert exc.value.code == 0
    cap = capsys.readouterr()
    assert "INJECTION" in cap.err
    assert "role-spoof" in cap.err
    # content (stdout) still contains the inlined body under warn policy
    assert "body text" in cap.out


def test_resolve_cli_no_scan(tmp_path, capsys):
    entry = _make(tmp_path)
    with pytest.raises(SystemExit):
        run(["resolve", str(entry), "--no-scan"])
    cap = capsys.readouterr()
    assert "INJECTION" not in cap.err


def test_resolve_cli_block_replaces_stdout(tmp_path, capsys):
    entry = _make(tmp_path)
    with pytest.raises(SystemExit):
        run(["resolve", str(entry), "--block"])
    cap = capsys.readouterr()
    assert "leak" not in cap.out
    assert "blocked injection" in cap.out


def test_resolve_cli_scan_rule_opt_in(tmp_path, capsys):
    # delimiter-spoof is opt-in; included file has a system heading
    (tmp_path / "SKILL.md").write_text("# Root\n\n@include inc.md\n", encoding="utf-8")
    (tmp_path / "inc.md").write_text("intro\n## System\nmore\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(["resolve", str(tmp_path / "SKILL.md"), "--scan-rule", "delimiter-spoof"])
    assert "delimiter-spoof" in capsys.readouterr().err
