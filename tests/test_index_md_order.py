import subprocess
from dotmd_parser.index_md import generate_index_md, extract_frontmatter


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )


def _repo(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    cold = tmp_path / "cold.md"
    hot = tmp_path / "hot.md"
    cold.write_text("cold0\n", encoding="utf-8")
    hot.write_text("hot0\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    for i in range(3):
        hot.write_text(f"hot{i+1}\n", encoding="utf-8")
        _git(tmp_path, "add", "hot.md")
        _git(tmp_path, "commit", "-m", f"h{i}")
    return tmp_path


def _files_body(md):
    return md.split("## Files", 1)[1]


def test_cache_order_puts_low_frequency_first(tmp_path):
    repo = _repo(tmp_path)
    body = _files_body(generate_index_md(str(repo), order="cache"))
    # cold.md (1 commit) must appear before hot.md (4 commits) in Files section
    assert body.index("cold.md") < body.index("hot.md")


def test_alpha_order_is_path_sorted_default(tmp_path):
    repo = _repo(tmp_path)
    body = _files_body(generate_index_md(str(repo)))  # default alpha
    # alphabetical path sort (case-sensitive ASCII): uppercase before lowercase.
    # SKILL.md (S=83) < cold.md (c=99) < hot.md (h=104)
    assert body.index("SKILL.md") < body.index("cold.md") < body.index("hot.md")


def test_order_changes_content_hash_but_alpha_stable(tmp_path):
    repo = _repo(tmp_path)
    alpha1 = extract_frontmatter(generate_index_md(str(repo)))["content_hash"]
    alpha2 = extract_frontmatter(generate_index_md(str(repo), order="alpha"))["content_hash"]
    cache = extract_frontmatter(generate_index_md(str(repo), order="cache"))["content_hash"]
    assert alpha1 == alpha2          # alpha idempotent
    assert cache != alpha1           # cache distinct -> switching triggers rewrite
