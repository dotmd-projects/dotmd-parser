# Cache-Affine Index Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--order cache` for `dotmd-index.md` that lists low-change-frequency files first (git-history based, with fallback) for prefix/KV-cache stability, plus a `stability` command to measure prefix stability — `--order alpha` (default) stays byte-identical to today.

**Architecture:** A new pure module `src/dotmd_parser/cache_order.py` provides `git_change_counts` (git log file counts, `{}` fallback), `order_key`, and `prefix_stability`. `index_md` gains an `order` param that threads through `generate_index_md` → `_files_section` (sort) and `_compute_content_hash` (folds `order` so switching re-writes). The CLI adds `dotmd-index --order` and a `stability` command. `parser.py`/`index.py` unchanged.

**Tech Stack:** Python 3 stdlib only (`subprocess`, `shutil`, `hashlib`), pytest, argparse, git (invoked as a subprocess; absence handled).

## Global Constraints

- **stdlib only** — no third-party dependency. git is invoked via `subprocess`; its absence/failure must be handled (return `{}`), never crash.
- **Do not modify** `parser.py` or `index.py`. New code in `cache_order.py`; edits limited to `index_md.py` (order threading) + `cli.py` (flags/command) + `__init__.py` (exports).
- **Type annotations** on every signature; `from __future__ import annotations` at top of the new module.
- **Immutability** — sort produces ordering for output; do not mutate the input index.
- **Backward compatibility (critical):** default `--order alpha` must keep `dotmd-index.md` output AND its `content_hash` byte-for-byte identical to today. The `order` marker is folded into the hash ONLY when `order != "alpha"`.
- **Frequency source:** `git_change_counts(root)` runs `git -C <root> log --format= --name-only --relative -- .`; returns `{rel_posix: commit_count}`. Returns `{}` when git is absent (`shutil.which`), the dir is not a repo (`returncode != 0`), or on `OSError`. Files not in git → count 0 (most stable → front).
- **Sort key:** `order_key(rel, counts) = (counts.get(rel, 0), rel)` — low count first, path-ascending tiebreak.
- **Scope:** only the `## Files` section ordering changes under `cache`. Folder map, dependency tree, and frontmatter ordering are unchanged.
- **content_hash rule:** `_compute_content_hash(root, idx, order="alpha")` appends `b"|order=cache"` to the hash input iff `order != "alpha"`. So alpha hash is unchanged; cache hash is distinct (switching order forces a rewrite; same-order regeneration stays idempotent).
- **prefix_stability:** `prefix_stability(old_text, new_text) -> {"common_prefix_lines": int, "new_lines": int, "ratio": float}`; `ratio = round(common / max(new_lines, 1), 4)`; counts leading equal lines, stops at first mismatch.
- **Commit signing:** SSH signing key not readable in sandbox. Commit with `git -c commit.gpgsign=false commit -m "..."`.
- **Branch:** work on `feat/cache-affine-order` (already created; design spec commit `081ab42` is its first commit). Do NOT branch off or switch.
- **Canonical test command:** `PYTHONPATH=src ./.venv/bin/python -m pytest` (the editable install is stale — you MUST use this exact form; never bare `python`/`pytest`).
- **Reference — current `index_md` code you edit:**
  - `_compute_content_hash(root: Path, idx: dict) -> str` iterates `_walk_files(root)` and hashes `f"{rel}|{size}|{content_h}"` per file, returns `f"{HASH_PREFIX}{h.hexdigest()[:HASH_LENGTH]}"`.
  - `_files_section(root, inv, idx, max_files)` builds `md_entries: list[(rel, entry)]` and `other_entries: list[(rel, size)]` from `_walk_files(root)` (path-sorted), then renders them in that order.
  - `generate_index_md(root, *, max_files=200, include_folder_map=True, folder_map_depth=3, include_deps_tree=True, aggregate=False, analysis_backend="none", extra_frontmatter=None)` calls `_compute_content_hash(base, idx)` and `_files_section(base, inv, idx, max_files=max_files)`.
  - `write_index_md(root, md=None, *, force=False, filename=..., **generate_kwargs)` forwards `**generate_kwargs` to `generate_index_md` — so a new `order` kwarg flows through without changing `write_index_md`'s signature.

---

### Task 1: cache_order — git_change_counts + order_key

**Files:**
- Create: `src/dotmd_parser/cache_order.py`
- Test: `tests/test_cache_order.py`

**Interfaces:**
- Produces:
  - `git_change_counts(root: str | Path) -> dict[str, int]` — per-file commit counts via git; `{}` on no-git/non-repo/error.
  - `order_key(rel: str, counts: dict[str, int]) -> tuple[int, str]` — `(counts.get(rel, 0), rel)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_order.py
import subprocess
from dotmd_parser.cache_order import git_change_counts, order_key


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", *args],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/cache_order.py
"""
dotmd-parser — cache-affine ordering helpers.

Estimates per-file change frequency from git history (with a safe fallback)
and provides an ordering key that puts low-frequency files first, so the
`dotmd-index.md` body prefix stays stable across regenerations (KV-cache
friendly). Also a prefix-stability metric. Pure stdlib; git via subprocess.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_change_counts(root: str | Path) -> dict[str, int]:
    """Return {rel_posix: commit_count} from git history; {} when unavailable."""
    if shutil.which("git") is None:
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--format=", "--name-only", "--relative", "--", "."],
            capture_output=True,
            text=True,
        )
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        rel = line.strip()
        if rel:
            counts[rel] = counts.get(rel, 0) + 1
    return counts


def order_key(rel: str, counts: dict[str, int]) -> tuple[int, str]:
    """Sort key: low change-count first, path-ascending tiebreak."""
    return (counts.get(rel, 0), rel)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/cache_order.py tests/test_cache_order.py
git -c commit.gpgsign=false commit -m "feat: add git_change_counts and order_key"
```

---

### Task 2: cache_order — prefix_stability

**Files:**
- Modify: `src/dotmd_parser/cache_order.py`
- Test: `tests/test_cache_order.py`

**Interfaces:**
- Produces: `prefix_stability(old_text: str, new_text: str) -> dict` — `{"common_prefix_lines": int, "new_lines": int, "ratio": float}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_order.py — append
from dotmd_parser.cache_order import prefix_stability


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -k prefix_stability -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/dotmd_parser/cache_order.py — append
def prefix_stability(old_text: str, new_text: str) -> dict:
    """Measure how much of `new_text`'s leading lines match `old_text`."""
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    common = 0
    for old_line, new_line in zip(old_lines, new_lines):
        if old_line == new_line:
            common += 1
        else:
            break
    total_new = len(new_lines)
    return {
        "common_prefix_lines": common,
        "new_lines": total_new,
        "ratio": round(common / max(total_new, 1), 4),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -k prefix_stability -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full cache_order test file**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cache_order.py tests/test_cache_order.py
git -c commit.gpgsign=false commit -m "feat: add prefix_stability metric"
```

---

### Task 3: index_md integration — order threading + sort + hash

**Files:**
- Modify: `src/dotmd_parser/index_md.py`
- Test: `tests/test_index_md_order.py`

**Interfaces:**
- Consumes: `git_change_counts`, `order_key` from `dotmd_parser.cache_order`.
- Produces: `generate_index_md(..., order="alpha", ...)` reorders the Files section when `order=="cache"` and folds `order` into `content_hash`. `_compute_content_hash(root, idx, order="alpha")` and `_files_section(root, inv, idx, max_files, order="alpha", counts=None)` gain the params.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_index_md_order.py
import subprocess
from dotmd_parser.index_md import generate_index_md, extract_frontmatter


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", *args],
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
    # alphabetical: cold.md before hot.md (independent of frequency here, but
    # also before SKILL.md? markdown section lists by path: cold, hot, SKILL)
    assert body.index("cold.md") < body.index("hot.md") < body.index("SKILL.md")


def test_order_changes_content_hash_but_alpha_stable(tmp_path):
    repo = _repo(tmp_path)
    alpha1 = extract_frontmatter(generate_index_md(str(repo)))["content_hash"]
    alpha2 = extract_frontmatter(generate_index_md(str(repo), order="alpha"))["content_hash"]
    cache = extract_frontmatter(generate_index_md(str(repo), order="cache"))["content_hash"]
    assert alpha1 == alpha2          # alpha idempotent
    assert cache != alpha1           # cache distinct -> switching triggers rewrite
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_index_md_order.py -v`
Expected: FAIL — `generate_index_md` has no `order` kwarg (TypeError).

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/index_md.py`:

(a) Add the import near the other `from dotmd_parser...` imports at the top:

```python
from dotmd_parser.cache_order import git_change_counts, order_key
```

(b) Change `_compute_content_hash` to accept and fold `order`:

```python
def _compute_content_hash(root: Path, idx: dict, order: str = "alpha") -> str:
    """Stable hash over (rel_path, size, file_content_hash) tuples.

    Excludes timestamps and absolute paths so re-runs match when content
    is unchanged. Skips the index.md itself so writing the artifact never
    invalidates its own hash. The body `order` is folded in (only for
    non-default order) so switching order triggers a rewrite.
    """
    h = hashlib.sha256()
    files_idx = idx.get("files", {})
    for rel_str, size in _walk_files(root):
        content_h = files_idx.get(rel_str, {}).get("hash", "")
        h.update(f"{rel_str}|{size}|{content_h}".encode("utf-8"))
    if order != "alpha":
        h.update(f"|order={order}".encode("utf-8"))
    return f"{HASH_PREFIX}{h.hexdigest()[:HASH_LENGTH]}"
```

(c) Change `_files_section` to accept `order`/`counts` and sort when `cache`:

```python
def _files_section(root, inv, idx, max_files, order="alpha", counts=None):
    lines = ["## Files", ""]
    files_idx = idx.get("files", {})
    md_entries: list[tuple[str, dict]] = []
    other_entries: list[tuple[str, int]] = []

    for rel, size in _walk_files(root):
        ext = Path(rel).suffix.lower()
        if ext in MARKDOWN_EXTENSIONS:
            md_entries.append((rel, files_idx.get(rel, {})))
        else:
            other_entries.append((rel, size))

    if order == "cache":
        ck = counts or {}
        md_entries.sort(key=lambda e: order_key(e[0], ck))
        other_entries.sort(key=lambda e: order_key(e[0], ck))

    total_listed = 0
    omitted = 0
```

(Leave the rest of `_files_section` — the `if md_entries:` rendering block onward — exactly as it is.)

(d) In `generate_index_md`, add the `order` keyword param and thread it. Change the signature to include `order: str = "alpha"` (add it alongside the other keyword-only params), then update the two call sites:

```python
    inv = inventory(str(base))
    idx = build_index(str(base))

    counts = git_change_counts(base) if order == "cache" else {}
    content_hash = _compute_content_hash(base, idx, order)
```

and:

```python
    body_parts.append("<!-- chunk:files -->")
    files_text = _files_section(base, inv, idx, max_files=max_files, order=order, counts=counts)
```

(Find the existing `files_text = _files_section(base, inv, idx, max_files=max_files)` line and replace it with the above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_index_md_order.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite (backward-compat check)**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS — existing `index_md` tests still pass (alpha default unchanged; `content_hash` for alpha is byte-identical because the `order != "alpha"` guard skips the fold).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/index_md.py tests/test_index_md_order.py
git -c commit.gpgsign=false commit -m "feat: thread order into index_md (cache-affine Files sort)"
```

---

### Task 4: export cache_order API from the package

**Files:**
- Modify: `src/dotmd_parser/__init__.py`
- Test: `tests/test_cache_order.py`

**Interfaces:**
- Produces: `from dotmd_parser import git_change_counts, order_key, prefix_stability` works; all in `__all__`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_order.py — append
def test_cache_order_api_is_exported():
    import dotmd_parser
    for name in ("git_change_counts", "order_key", "prefix_stability"):
        assert hasattr(dotmd_parser, name), name
        assert name in dotmd_parser.__all__, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -k exported -v`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/__init__.py`, add after the `from dotmd_parser.index_md import (...)` block:

```python
from dotmd_parser.cache_order import (
    git_change_counts,
    order_key,
    prefix_stability,
)
```

And in the `__all__` list, add after the `# index_md` group's last entry (`"INDEX_MD_SCHEMA",`):

```python
    # cache_order
    "git_change_counts",
    "order_key",
    "prefix_stability",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cache_order.py -k exported -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotmd_parser/__init__.py tests/test_cache_order.py
git -c commit.gpgsign=false commit -m "feat: export cache_order API from package"
```

---

### Task 5: CLI — `dotmd-index --order` + `stability` command

**Files:**
- Modify: `src/dotmd_parser/cli.py`
- Test: `tests/test_cli_stability.py`

**Interfaces:**
- Consumes: `prefix_stability` from `dotmd_parser.cache_order`; existing `_generate_index_md`/`_write_index_md` (which forward `order` via kwargs).
- Produces: `dotmd-parser dotmd-index <path> --order alpha|cache`, and `dotmd-parser stability <old> <new> [--json]`.

**Current code being modified** (`cmd_dotmd_index` builds `gen_kwargs` in `cli.py`):
```python
    gen_kwargs = {
        "include_folder_map": not args.no_folder_map,
        "include_deps_tree": not args.no_deps,
        "max_files": args.max_files,
        "aggregate": args.aggregate,
    }
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_stability.py
import json
import subprocess
import pytest
from dotmd_parser.cli import run


def _git(d, *args):
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t", *args],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_stability.py -v`
Expected: FAIL — `stability` not a known command / `--order` not recognized.

- [ ] **Step 3: Write minimal implementation**

In `src/dotmd_parser/cli.py`:

(a) Add `"order": args.order` to the `gen_kwargs` dict in `cmd_dotmd_index`:

```python
    gen_kwargs = {
        "include_folder_map": not args.no_folder_map,
        "include_deps_tree": not args.no_deps,
        "max_files": args.max_files,
        "aggregate": args.aggregate,
        "order": args.order,
    }
```

(b) Add the `stability` handler (place near `cmd_dotmd_index`):

```python
def cmd_stability(args: argparse.Namespace) -> int:
    from dotmd_parser.cache_order import prefix_stability
    try:
        old_text = Path(args.old).read_text(encoding="utf-8")
        new_text = Path(args.new).read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    result = prefix_stability(old_text, new_text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"prefix stable: {result['common_prefix_lines']}/{result['new_lines']} "
            f"lines ({result['ratio']})"
        )
    return 0
```

(c) Add the `--order` argument to the `p_idxmd` parser (find the `p_idxmd.add_argument("--aggregate", ...)` block and add after it):

```python
    p_idxmd.add_argument(
        "--order",
        choices=["alpha", "cache"],
        default="alpha",
        help="Files-section order: alpha (default) or cache (low-change-frequency first)",
    )
```

(d) Register the `stability` subparser (place after the `p_idxmd` block, before `p_show`):

```python
    p_stab = sub.add_parser("stability", help="Measure prefix stability between two index files")
    p_stab.add_argument("old", help="Old/baseline file")
    p_stab.add_argument("new", help="New file")
    p_stab.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    p_stab.set_defaults(func=cmd_stability)
```

(e) Add `"stability"` to the `known_cmds` set in `run()`:

```python
    known_cmds = {"init", "index", "check", "affects", "deps", "digest", "tree", "resolve", "analyze", "inventory", "dotmd-index", "show", "stability"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_cli_stability.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (existing + new; no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/dotmd_parser/cli.py tests/test_cli_stability.py
git -c commit.gpgsign=false commit -m "feat: add dotmd-index --order and stability CLI"
```

---

### Task 6: Docs — CHANGELOG + README

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.ja.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

At the top of `CHANGELOG.md` (above the most recent `## [...]` entry; if an `## [Unreleased]` section already exists, add the bullet under its `### Added` instead of duplicating the heading):

```markdown
## [Unreleased]

### Added
- **`dotmd-index --order cache`（キャッシュ親和ソート）** — Files セクションを
  変更頻度の低い順（git 履歴ベース、非リポは fallback）に並べ、LLM のプレフィックス
  安定化＝KV キャッシュ無効化抑制を狙う opt-in。既定 `--order alpha` は現状と完全同一。
  `order` は content_hash に織り込まれ、切替時のみ再生成される。新 `stability <old> <new>`
  でプレフィックス安定率を計測。`git_change_counts` / `prefix_stability` を公開 API に追加。
  設計: `docs/superpowers/specs/2026-06-21-cache-affine-order-design.md`
```

- [ ] **Step 2: Add a README section (English)**

In `README.md`, near the `dotmd-index` docs, add:

```markdown
### Cache-affine order (`--order cache`)

`dotmd-index --order cache` lists the `## Files` section with the
least-frequently-changed files first (estimated from git history), so the
generated `dotmd-index.md` keeps a stable prefix across regenerations — better
KV-cache reuse for LLMs that read it. Default `--order alpha` is unchanged.

```bash
dotmd-parser dotmd-index ./skill --order cache
dotmd-parser dotmd-index ./skill --order cache --stdout
```

Measure the effect with `stability` (compare two generations):

```bash
dotmd-parser stability old-index.md new-index.md          # prefix stable: 42/50 lines (0.84)
dotmd-parser stability old-index.md new-index.md --json
```

Outside a git repo (or for untracked files) frequency is treated as 0, so
`cache` degrades gracefully to alphabetical order.
```

- [ ] **Step 3: Add a README section (Japanese)**

In `README.ja.md`, add the equivalent:

```markdown
### キャッシュ親和ソート（`--order cache`）

`dotmd-index --order cache` は `## Files` セクションを変更頻度の低い順
（git 履歴から推定）に並べ、再生成しても `dotmd-index.md` のプレフィックスが
安定するようにします（読み手 LLM の KV キャッシュ再利用に有利）。既定の
`--order alpha` は従来どおりです。

```bash
dotmd-parser dotmd-index ./skill --order cache
dotmd-parser dotmd-index ./skill --order cache --stdout
```

効果は `stability`（2 世代を比較）で計測できます:

```bash
dotmd-parser stability old-index.md new-index.md          # prefix stable: 42/50 lines (0.84)
dotmd-parser stability old-index.md new-index.md --json
```

git リポジトリ外（または未追跡ファイル）では頻度 0 として扱われ、`cache` は
アルファベット順に穏当に縮退します。
```

- [ ] **Step 4: Verify the suite still passes**

Run: `PYTHONPATH=src ./.venv/bin/python -m pytest -q`
Expected: PASS (docs changes don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md README.ja.md
git -c commit.gpgsign=false commit -m "docs: document cache-affine order and stability"
```

---

## Self-Review

**1. Spec coverage:**
- §3 module `cache_order.py` (git_change_counts, order_key, prefix_stability) → Tasks 1–2. ✓
- §4 git frequency (`--relative`, `{}` fallback via which/returncode/OSError) → Task 1. ✓
- §5 order_key (low count first, alpha tiebreak) → Task 1. ✓
- §6 index_md integration (order param, Files-section sort, content_hash folds order only when ≠ alpha) → Task 3. ✓
- §7 prefix_stability shape/ratio → Task 2. ✓
- §8 CLI (`dotmd-index --order`, `stability` command, exports, known_cmds) → Tasks 4–5. ✓
- §9 error handling (alpha byte-identical; git absent → {} → graceful; stability missing file → exit 2; unknown order → argparse exit 2) → Tasks 1/3/5. ✓
- §10 steps → Tasks 1–6. ✓
- §11 testing (git counts via tmp repo + non-repo {}, order_key, prefix_stability, cache reorders Files, alpha unchanged, cache hash ≠ alpha, CLI stability + dotmd-index --order, 80%+) → Tasks 1–5. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step has complete code; every command is exact with expected output. ✓

**3. Type consistency:** Names consistent across tasks — `git_change_counts(root)`, `order_key(rel, counts)`, `prefix_stability(old_text, new_text)`, `_compute_content_hash(root, idx, order)`, `_files_section(root, inv, idx, max_files, order, counts)`, `generate_index_md(..., order=...)`. The `prefix_stability` dict keys (`common_prefix_lines`/`new_lines`/`ratio`) match between producer (Task 2) and consumers (Task 5 CLI text/json + the CLI test assertions). `gen_kwargs["order"]` flows to `generate_index_md`/`write_index_md` (the latter forwards via `**generate_kwargs`, unchanged). CLI `--order` choices `{alpha,cache}` match the `_files_section`/hash logic. ✓

Note: `write_index_md` needs no signature change — it already forwards `**generate_kwargs` to `generate_index_md`, so the new `order` kwarg threads through both the `--stdout` path (calls `_generate_index_md` directly) and the file-write path (calls `_write_index_md`). The CLI adds `order` to the single `gen_kwargs` dict used by both paths.
