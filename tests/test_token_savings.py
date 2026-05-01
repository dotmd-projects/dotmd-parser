"""
dotmd-parser — token-savings measurement.

Measures how many tokens Claude would consume to understand a folder,
under three strategies:

    1. naive       — read every .md file in the folder (baseline)
    2. dotmd-index — read only `<root>/dotmd-index.md`
    3. digest      — read `dotmd-parser digest` output

Tokens are approximated at ~4 chars/token (Claude's tokenizer averages
3.5–4.0 chars/token on English; CJK is denser at 2–3, so the estimate
is conservative for Japanese-heavy folders). For exact counts, install
`tiktoken` and the helper below switches to GPT-4 BPE (close enough as a
proxy for Claude's tokenizer family).

Run with `pytest -s tests/test_token_savings.py` to see the report
table; the assertions only enforce that:

- dotmd-index.md is **smaller** than the naive baseline once the folder
  has more than ~30 files (frontmatter overhead amortizes).
- digest is always smaller than the naive baseline regardless of size.
"""
from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from dotmd_parser.digest import digest as _digest
from dotmd_parser.index import build_index
from dotmd_parser.index_md import (
    DEFAULT_INDEX_FILENAME,
    generate_index_md,
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    """Return an approximate token count for `text`.

    Uses tiktoken's cl100k_base when available (close to Claude's
    tokenizer for our purposes); falls back to 4 chars/token otherwise.
    """
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _naive_read_all_md(root: Path) -> str:
    """Concatenate every .md file (skipping hidden + the index itself)."""
    parts: list[str] = []
    for p in sorted(root.rglob("*.md")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.name == DEFAULT_INDEX_FILENAME:
            continue
        try:
            parts.append(f"### {rel.as_posix()}\n" + p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n\n".join(parts)


def _measure(root: Path) -> dict:
    naive_text = _naive_read_all_md(root)
    index_md = generate_index_md(str(root))
    digest_text = _digest(build_index(str(root)))
    naive_tokens = _count_tokens(naive_text)
    index_tokens = _count_tokens(index_md)
    digest_tokens = _count_tokens(digest_text)
    md_count = sum(1 for p in root.rglob("*.md")
                   if p.is_file()
                   and p.name != DEFAULT_INDEX_FILENAME
                   and not any(part.startswith(".") for part in p.relative_to(root).parts))
    return {
        "files": md_count,
        "naive_bytes": len(naive_text.encode("utf-8")),
        "naive_tokens": naive_tokens,
        "index_bytes": len(index_md.encode("utf-8")),
        "index_tokens": index_tokens,
        "digest_bytes": len(digest_text.encode("utf-8")),
        "digest_tokens": digest_tokens,
    }


def _format_report(label: str, m: dict) -> str:
    naive = m["naive_tokens"] or 1
    return textwrap.dedent(f"""
        ─── {label} ({m['files']} markdown files) ───
        strategy        bytes        tokens       ratio
        naive read all  {m['naive_bytes']:>9}    {m['naive_tokens']:>9}    1.00x  (baseline)
        dotmd-index.md  {m['index_bytes']:>9}    {m['index_tokens']:>9}    {m['index_tokens']/naive:>4.2f}x
        digest          {m['digest_bytes']:>9}    {m['digest_tokens']:>9}    {m['digest_tokens']/naive:>4.2f}x
    """).strip()


# ---------------------------------------------------------------------------
# Synthetic folder generators
# ---------------------------------------------------------------------------

_LOREM_PARAGRAPH = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum."
)


def _shared_body(i: int, paragraphs: int) -> str:
    """Realistic shared-context content (~`paragraphs * ~500 chars`)."""
    body_paras = "\n\n".join(
        f"### Section {j+1}\n\n{_LOREM_PARAGRAPH}" for j in range(paragraphs)
    )
    return (
        f"# Topic {i}\n\n"
        f"Reference content for topic {i}. Domain rules, worked examples, "
        f"edge cases, and prompt fragments live here. They are reused by "
        f"multiple agents through `@include`.\n\n"
        f"{body_paras}\n\n"
        f"Use `{{{{topic_{i}_var}}}}` as the parameter.\n"
    )


def _agent_body(i: int, paragraphs: int) -> str:
    body_paras = "\n\n".join(
        f"### Step {j+1}\n\n{_LOREM_PARAGRAPH}" for j in range(paragraphs)
    )
    return (
        f"# Agent {i}\n\n"
        f"Responsible for sub-task #{i}. Reads context, applies rules, "
        f"returns structured output. The agent body documents inputs, "
        f"outputs, error modes, and recovery procedures.\n\n"
        f"{body_paras}\n"
    )


def _build_skill(root: Path, n_shared: int, n_agents: int, *, paragraphs: int = 4) -> None:
    """Create a realistic skill folder.

    Each shared / agent file gets `paragraphs` Lorem paragraphs (~500
    chars each) so the synthetic content matches the size profile of
    real-world skill / docs files (1.5–3 KB per file). With small
    `paragraphs` values the workload is closer to a tightly written
    prompt pack.
    """
    includes = "\n".join(f"@include shared/topic_{i:03d}.md" for i in range(n_shared))
    delegates = "\n".join(f"@delegate agents/agent_{i:02d}.md" for i in range(n_agents))
    (root / "SKILL.md").write_text(
        f"---\nname: synthetic-{n_shared}-{n_agents}\n---\n"
        f"# Synthetic Skill ({n_shared} shared, {n_agents} agents)\n\n"
        f"This skill exercises the parser against a generated tree.\n\n"
        f"{includes}\n\n{delegates}\n",
        encoding="utf-8",
    )
    (root / "shared").mkdir(exist_ok=True)
    for i in range(n_shared):
        (root / "shared" / f"topic_{i:03d}.md").write_text(
            _shared_body(i, paragraphs), encoding="utf-8"
        )
    (root / "agents").mkdir(exist_ok=True)
    for i in range(n_agents):
        (root / "agents" / f"agent_{i:02d}.md").write_text(
            _agent_body(i, paragraphs), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Pretty output is gated by the env var so CI logs aren't spammed.
PRINT_REPORT = os.environ.get("DOTMD_TOKEN_REPORT") not in (None, "", "0", "false")


class TestTokenSavings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reports: list[str] = []

    def tearDown(self):
        if PRINT_REPORT and self.reports:
            print()
            for r in self.reports:
                print(r)
        self.tmp.cleanup()

    def test_tiny_folder_overhead_is_acceptable(self):
        """For tiny folders (<5 files), the index.md may be larger than
        the raw content because of frontmatter overhead. We don't assert
        compression, only that the inflation stays bounded."""
        _build_skill(self.root, n_shared=2, n_agents=1)
        m = _measure(self.root)
        self.reports.append(_format_report("tiny (2 shared / 1 agent)", m))
        # Inflation no worse than 5x even on the worst tiny case
        self.assertLess(m["index_tokens"], m["naive_tokens"] * 5)

    def test_medium_folder_index_is_smaller(self):
        """At ~30 files the frontmatter overhead amortizes and the
        index becomes the more economical read."""
        _build_skill(self.root, n_shared=25, n_agents=5)
        m = _measure(self.root)
        self.reports.append(_format_report("medium (25 shared / 5 agents)", m))
        self.assertLess(m["index_tokens"], m["naive_tokens"])

    def test_large_folder_significant_savings(self):
        """At 100+ files we expect index.md to use less than half the
        tokens of reading every file."""
        _build_skill(self.root, n_shared=100, n_agents=10)
        m = _measure(self.root)
        self.reports.append(_format_report("large (100 shared / 10 agents)", m))
        self.assertLess(m["index_tokens"], m["naive_tokens"] * 0.5)

    def test_digest_always_compresses(self):
        """`digest` (the existing token-efficient summary) should beat
        the naive baseline at every folder size."""
        for nshared, nagents in [(2, 1), (25, 5), (100, 10)]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                _build_skill(root, n_shared=nshared, n_agents=nagents)
                m = _measure(root)
                self.assertLess(
                    m["digest_tokens"],
                    m["naive_tokens"],
                    msg=f"digest > naive for ({nshared}, {nagents}): {m}",
                )

    def test_real_demo_folder(self):
        """Measure the bundled demo/my-skill (small but real)."""
        demo = Path(__file__).resolve().parents[1] / "demo" / "my-skill"
        if not demo.exists():
            self.skipTest("demo/my-skill not present")
        m = _measure(demo)
        self.reports.append(_format_report(f"real folder: {demo.name}", m))
        # No assertion — this is a measurement print only.


if __name__ == "__main__":
    os.environ.setdefault("DOTMD_TOKEN_REPORT", "1")
    unittest.main(verbosity=2)
