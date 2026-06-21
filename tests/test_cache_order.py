import subprocess
from dotmd_parser.cache_order import git_change_counts, order_key


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )


def test_git_change_counts_reflects_commit_frequency(tmp_path):
    _git(tmp_path, "init")
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    # a.md committed once, b.md committed 3 times
    a.write_text("a0\n", encoding="utf-8")
    b.write_text("b0\n", encoding="utf-8")
    _git(tmp_path, "add", "a.md", "b.md")
    _git(tmp_path, "commit", "-m", "c1")
    for i in range(2):
        b.write_text(f"b{i+1}\n", encoding="utf-8")
        _git(tmp_path, "add", "b.md")
        _git(tmp_path, "commit", "-m", f"cb{i}")
    counts = git_change_counts(tmp_path)
    assert counts.get("a.md") == 1
    assert counts.get("b.md") == 3


def test_git_change_counts_non_repo_returns_empty(tmp_path):
    (tmp_path / "x.md").write_text("x\n", encoding="utf-8")
    assert git_change_counts(tmp_path) == {}


def test_order_key_low_count_first_then_alpha():
    counts = {"hot.md": 5, "cold.md": 0}
    keys = sorted(["hot.md", "cold.md", "warm.md"], key=lambda r: order_key(r, counts))
    # cold.md (0), warm.md (0, alpha after cold), hot.md (5)
    assert keys == ["cold.md", "warm.md", "hot.md"]
