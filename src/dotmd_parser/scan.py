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


DEFAULT_RULES = ("role-spoof", "instruction-override")
OPTIONAL_RULES = ("delimiter-spoof", "tool-exfil")
ALL_RULES = DEFAULT_RULES + OPTIONAL_RULES

_ROLE_LINE_RE = re.compile(r"^\s*(System|Assistant|Human|User|AI)\s*:")
_CHAT_TOKENS = ("<|im_start|>", "<|im_end|>", "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>")
_OVERRIDE_RE = re.compile(
    r"(?i)\b(ignore|disregard|forget)\b[^\n]{0,200}?\b(previous|above|prior|earlier|all)\b"
    r"[^\n]{0,200}?\b(instruction|instructions|prompt|prompts|context|rule|rules)\b"
)
_NEW_INSTR_RE = re.compile(r"(?i)\bnew\s+instructions?\s*:")
_DELIM_DASHES_RE = re.compile(r"^\s*---\s*$")
_DELIM_HEADING_RE = re.compile(r"(?i)^#{1,6}\s*(system|instructions?|prompt)\b")
_EXFIL_RE = re.compile(
    r"(?i)\b(print|reveal|show|repeat|output|display)\b[^\n]{0,200}?"
    r"\b(system prompt|your instructions|the prompt above|previous prompt)\b"
)


def _match_role_spoof(line: str, lineno: int) -> str | None:
    if _ROLE_LINE_RE.search(line):
        return "possible role impersonation"
    for token in _CHAT_TOKENS:
        if token in line:
            return f"chat-template token {token!r}"
    return None


def _match_instruction_override(line: str, lineno: int) -> str | None:
    if _OVERRIDE_RE.search(line) or _NEW_INSTR_RE.search(line):
        return "possible instruction override"
    return None


def _match_delimiter_spoof(line: str, lineno: int) -> str | None:
    if lineno > 1 and _DELIM_DASHES_RE.match(line):
        return "delimiter spoofing ('---')"
    if _DELIM_HEADING_RE.match(line):
        return "system-like heading"
    return None


def _match_tool_exfil(line: str, lineno: int) -> str | None:
    if _EXFIL_RE.search(line):
        return "possible prompt exfiltration"
    return None


_RULE_MATCHERS = {
    "role-spoof": _match_role_spoof,
    "instruction-override": _match_instruction_override,
    "delimiter-spoof": _match_delimiter_spoof,
    "tool-exfil": _match_tool_exfil,
}


def scan_content(text: str, source: str = "", rules: list[str] | None = None) -> list[dict]:
    """Scan `text` for injection patterns; return Findings sorted by (line, rule)."""
    active = list(rules) if rules is not None else list(DEFAULT_RULES)
    suppressed = _suppressed_rules(text)
    if "all" in suppressed:
        return []
    active = [r for r in active if r in _RULE_MATCHERS and r not in suppressed]
    if not active:
        return []

    masked_lines = _mask_code_fences(text).split("\n")
    findings: list[dict] = []
    for idx, line in enumerate(masked_lines):
        lineno = idx + 1
        for rule in active:
            message = _RULE_MATCHERS[rule](line, lineno)
            if message:
                findings.append({
                    "rule": rule,
                    "severity": "warning",
                    "source": source,
                    "line": lineno,
                    "snippet": line.strip()[:120],
                    "message": message,
                })
    findings.sort(key=lambda f: (f["line"], f["rule"]))
    return findings
