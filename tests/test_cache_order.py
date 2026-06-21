import subprocess
from dotmd_parser.cache_order import git_change_counts, order_key, prefix_stability


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


def test_prefix_stability_identical():
    text = "a\nb\nc\n"
    res = prefix_stability(text, text)
    assert res["common_prefix_lines"] == res["new_lines"]
    assert res["ratio"] == 1.0


def test_prefix_stability_partial():
    old = "a\nb\nc\nd\n"
    new = "a\nb\nX\nd\n"
    res = prefix_stability(old, new)
    assert res["common_prefix_lines"] == 2  # a, b match; then X != c
    assert res["new_lines"] == len(new.split("\n"))
    assert 0.0 < res["ratio"] < 1.0


def test_prefix_stability_no_common():
    res = prefix_stability("x\ny\n", "a\nb\n")
    assert res["common_prefix_lines"] == 0
    assert res["ratio"] == 0.0


def test_git_change_counts_handles_non_ascii_filename(tmp_path):
    _git(tmp_path, "init")
    f = tmp_path / "日本語.md"
    f.write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    counts = git_change_counts(tmp_path)
    assert counts.get("日本語.md") == 1


def test_cache_order_api_is_exported():
    import dotmd_parser
    for name in ("git_change_counts", "order_key", "prefix_stability"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name
