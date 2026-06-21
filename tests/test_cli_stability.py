import json
import subprocess
import pytest
from dotmd_parser.cli import run


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )


def test_stability_text(tmp_path, capsys):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("a\nb\nc\n", encoding="utf-8")
    new.write_text("a\nb\nX\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["stability", str(old), str(new)])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "prefix stable" in out
    assert "2/" in out  # 2 common prefix lines


def test_stability_json(tmp_path, capsys):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("a\nb\n", encoding="utf-8")
    new.write_text("a\nb\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(["stability", str(old), str(new), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["ratio"] == 1.0


def test_stability_missing_file_exits_2(tmp_path):
    old = tmp_path / "old.md"
    old.write_text("a\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["stability", str(old), str(tmp_path / "nope.md")])
    assert e.value.code == 2


def test_stability_missing_old_file_exits_2(tmp_path):
    new = tmp_path / "new.md"
    new.write_text("a\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["stability", str(tmp_path / "nope.md"), str(new)])
    assert e.value.code == 2


def test_dotmd_index_order_cache_runs(tmp_path, capsys):
    _git(tmp_path, "init")
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "cold.md").write_text("c\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    with pytest.raises(SystemExit) as e:
        run(["dotmd-index", str(tmp_path), "--order", "cache", "--stdout"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "## Files" in out
