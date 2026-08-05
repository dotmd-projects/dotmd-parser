"""Shared LLM plumbing (stdlib-only) used by analyze.py and ontology.py."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 4096


def load_dotenv(env_path: str | Path | None = None) -> None:
    """Read a .env file and export its keys to os.environ (without overriding)."""
    path = Path(env_path) if env_path else Path.cwd() / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_prompt_template(name: str) -> str:
    return (
        resources.files("dotmd_parser.templates.prompts")
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
    )


def call_claude(
    prompt: str,
    system: str,
    api_key: str,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — URL is constant
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Claude API error {e.code}: {e.read().decode('utf-8', 'replace')}"
        ) from e
    return body["content"][0]["text"]


def extract_json(raw: str) -> dict:
    """Parse a Claude reply: prefer a fenced ```json block, fall back to raw body."""
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    payload = match.group(1) if match else raw.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude response was not valid JSON:\n{raw}") from e
