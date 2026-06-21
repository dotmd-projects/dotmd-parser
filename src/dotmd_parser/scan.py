"""
dotmd-parser — prompt-injection scanner for @include-expanded content.

Pure, deterministic (stdlib `re` only). `scan_content` returns a list of
Finding dicts. Used by `parser.resolve` to inspect content pulled in via
`@include` (the untrusted supply-chain surface). No LLM, no network.

Finding shape:
    {"rule": str, "severity": "warning", "source": str,
     "line": int, "snippet": str, "message": str}
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^\s*```")
_ALLOW_RE = re.compile(r"<!--\s*dotmd-allow:\s*([^>]*?)\s*-->")


def _mask_code_fences(text: str) -> str:
    """Blank out lines inside ``` fenced blocks, preserving line count."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")          # blank the fence delimiter line too
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _suppressed_rules(text: str) -> set[str]:
    """Collect rule names from `<!-- dotmd-allow: a, b -->` comments."""
    suppressed: set[str] = set()
    for match in _ALLOW_RE.finditer(text):
        for name in match.group(1).split(","):
            name = name.strip()
            if name:
                suppressed.add(name)
    return suppressed
