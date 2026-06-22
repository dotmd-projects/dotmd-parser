"""
dotmd-parser — AI-powered dependency detection.

Adapted from dotmd-io/dotmd-tools/analyze.py. Scans a folder of markdown
files, asks Claude (via the public Anthropic API) to detect implicit
dependencies, then either prints the proposal, emits JSON, or applies the
result (adds `@include` headers to plain-text files, writes `deps.yml` for
binary files such as PDFs).

Why this lives in dotmd-parser
------------------------------
The core parser can only follow explicit `@include`/`@ref` directives.
Most real-world documentation repos don't use them yet. `analyze` bridges
that gap: run it once to seed directives / deps.yml, then `build_graph`
handles the rest.

Design notes
------------
- **stdlib only** for the API call (`urllib`). No `anthropic`/`requests`
  dependency added to the package.
- **PDF / docx** support is optional — imports `pdfplumber` / `python-docx`
  lazily, skips and warns when missing.
- **Prompts** are bundled under `dotmd_parser.templates.prompts/` and read
  via `importlib.resources` so they ship with the wheel.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Any

from dotmd_parser.parser import parse_deps_yml

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 4096
MAX_CONTENT_CHARS = 500
DEPS_FILENAME = "deps.yml"

TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".csv", ".html", ".xml",
    ".json", ".yaml", ".yml",
}


# ---------------------------------------------------------------------------
# .env loader (stdlib)
# ---------------------------------------------------------------------------

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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError as e:
        raise ImportError("pdfplumber is required for PDF input: pip install pdfplumber") from e
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                parts.append(page_text)
    return "\n".join(parts)


def _extract_text_from_docx(path: Path) -> str:
    try:
        import docx  # type: ignore
    except ImportError as e:
        raise ImportError("python-docx is required for docx input: pip install python-docx") from e
    doc = docx.Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text(path: Path) -> str | None:
    """Extract readable text from a file or return None when unsupported/unreadable."""
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXTENSIONS:
            return path.read_text(encoding="utf-8")
        if ext == ".pdf":
            return _extract_text_from_pdf(path)
        if ext == ".docx":
            return _extract_text_from_docx(path)
    except ImportError as e:
        print(f"warning: {e}")
    except (OSError, UnicodeDecodeError) as e:
        print(f"warning: cannot read {path.name}: {e}")
    return None


# ---------------------------------------------------------------------------
# Document scan
# ---------------------------------------------------------------------------

def scan_documents(directory: str | Path, extensions: list[str] | None = None) -> list[dict]:
    """
    Walk a directory and return text samples suitable for dependency analysis.

    Each entry: {"path": relative str, "content": full text, "summary": first N chars}.
    Hidden paths, node_modules, and deps.yml itself are skipped.
    """
    if extensions is None:
        extensions = [".md", ".txt"]

    root = Path(directory).resolve()
    documents: list[dict] = []

    for ext in extensions:
        for fp in sorted(root.rglob(f"*{ext}")):
            rel = fp.relative_to(root).as_posix()
            if any(part.startswith(".") for part in fp.relative_to(root).parts):
                continue
            if "node_modules" in rel:
                continue
            if fp.name == DEPS_FILENAME:
                continue
            text = extract_text(fp)
            if not text or not text.strip():
                continue
            documents.append({
                "path": rel,
                "content": text,
                "summary": text[:MAX_CONTENT_CHARS],
            })
    return documents


# ---------------------------------------------------------------------------
# deps.yml I/O
# ---------------------------------------------------------------------------

def load_deps_yml(directory: str | Path) -> dict[str, list[str]]:
    path = Path(directory).resolve() / DEPS_FILENAME
    if not path.exists():
        return {}
    return parse_deps_yml(path.read_text(encoding="utf-8"))


def save_deps_yml(
    directory: str | Path,
    deps: dict[str, list[str]],
    analysis: dict | None = None,
) -> str:
    """Merge `deps` into deps.yml at `directory` and write it back. Returns the path."""
    target = Path(directory).resolve() / DEPS_FILENAME
    existing = load_deps_yml(directory)
    for src, includes in deps.items():
        merged = list(existing.get(src, []))
        for inc in includes:
            if inc not in merged:
                merged.append(inc)
        existing[src] = merged

    reasons: dict[tuple[str, str], str] = {}
    if analysis:
        for edge in analysis.get("edges", []):
            reasons[(edge["from"], edge["to"])] = edge.get("reason", "")

    lines: list[str] = [
        "# dotmd dependency manifest — auto-generated, hand-editable",
        "files:",
    ]
    for src in sorted(existing):
        includes = existing[src]
        lines.append(f'  - path: "{src}"')
        if not includes:
            lines.append("    includes: []")
            continue
        lines.append("    includes:")
        for inc in includes:
            reason = reasons.get((src, inc), "")
            if reason:
                lines.append(f'      - "{inc}"  # {reason}')
            else:
                lines.append(f'      - "{inc}"')

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def _load_prompt_template(name: str) -> str:
    return (
        resources.files("dotmd_parser.templates.prompts")
        .joinpath(f"{name}.md")
        .read_text(encoding="utf-8")
    )


def _build_file_list_block(documents: list[dict]) -> str:
    return "\n\n".join(
        f"### {doc['path']}\n```\n{doc['summary']}\n```" for doc in documents
    )


def _call_claude(
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
        raise RuntimeError(f"Claude API error {e.code}: {e.read().decode('utf-8', 'replace')}") from e
    return body["content"][0]["text"]


def analyze_dependencies(
    directory: str | Path,
    api_key: str | None = None,
    extensions: list[str] | None = None,
    model: str | None = None,
    *,
    caller: Any = None,
) -> dict:
    """
    Ask Claude to infer dependencies between documents in `directory`.

    Parameters
    ----------
    api_key : str | None
        Anthropic API key. Falls back to `ANTHROPIC_API_KEY`.
    model : str | None
        Claude model ID. Falls back to env `CLAUDE_MODEL` or the library default.
    caller : callable | None
        Test hook that replaces the API call. Signature:
        `caller(prompt: str, system: str, model: str) -> str`.

    Returns
    -------
    {"documents": [...], "edges": [...], "shared_proposals": [...]}
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if caller is None and not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file "
            "or export it as an environment variable."
        )

    resolved_model = model or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)

    documents = scan_documents(directory, extensions=extensions)
    if not documents:
        return {"documents": [], "edges": [], "shared_proposals": []}

    template = _load_prompt_template("analyze-dependencies")
    file_list = _build_file_list_block(documents)
    full_prompt = template.replace("{{file_list}}", file_list)

    # Split out the first line as a terse system prompt (matches tools/ convention).
    first, _, rest = full_prompt.partition("\n")
    system_prompt = first.strip() or "You analyze markdown dependencies."
    user_prompt = rest.strip() or full_prompt

    if caller is not None:
        raw = caller(user_prompt, system_prompt, resolved_model)
    else:
        raw = _call_claude(user_prompt, system_prompt, api_key, resolved_model)

    # Prefer fenced ```json blocks, fall back to raw JSON body.
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    payload = match.group(1) if match else raw.strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude response was not valid JSON:\n{raw}") from e

    return {
        "documents": [{"path": d["path"], "summary": d["summary"]} for d in documents],
        "edges": parsed.get("edges", []),
        "shared_proposals": parsed.get("shared_proposals", []),
    }


# ---------------------------------------------------------------------------
# Applying the proposal
# ---------------------------------------------------------------------------

def is_text_editable(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in TEXT_EXTENSIONS


def generate_directives(analysis: dict) -> dict[str, list[str]]:
    """Convert edges + shared proposals into `{src: ["@include|@ref target", ...]}`.

    Each edge's `kind` ("include" | "ref") selects the directive; unknown or
    missing kind falls back to "include". Shared proposals are always @include.
    """
    directives: dict[str, list[str]] = {}
    for edge in analysis.get("edges", []):
        directive = "@ref" if edge.get("kind") == "ref" else "@include"
        entry = f"{directive} {edge['to']}"
        bucket = directives.setdefault(edge["from"], [])
        if entry not in bucket:
            bucket.append(entry)
    for proposal in analysis.get("shared_proposals", []):
        target = proposal.get("name")
        if not target:
            continue
        for user_file in proposal.get("used_by", []):
            entry = f"@include {target}"
            bucket = directives.setdefault(user_file, [])
            if entry not in bucket:
                bucket.append(entry)
    return directives


def apply_directives(directory: str | Path, directives: dict[str, list[str]]) -> list[str]:
    """Prepend new @include lines to text files. Returns list of modified files."""
    root = Path(directory).resolve()
    modified: list[str] = []
    for file_rel, includes in directives.items():
        if not is_text_editable(file_rel):
            continue
        fp = root / file_rel
        if not fp.exists():
            continue
        content = fp.read_text(encoding="utf-8")
        existing = {
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith(("@include ", "@delegate ", "@ref "))
        }
        new = [d for d in includes if d not in existing]
        if not new:
            continue
        fp.write_text("\n".join(new) + "\n\n" + content, encoding="utf-8")
        modified.append(file_rel)
    return modified


def apply_analysis(directory: str | Path, analysis: dict) -> dict:
    """
    Apply the analysis result: inject `@include` into text files, write
    `deps.yml` for binary (pdf/docx) sources.
    """
    directives = generate_directives(analysis)
    modified = apply_directives(directory, directives)

    binary_deps: dict[str, list[str]] = {
        src: [d.removeprefix("@include ") for d in entries]
        for src, entries in directives.items()
        if not is_text_editable(src)
    }
    deps_yml_path = save_deps_yml(directory, binary_deps, analysis) if binary_deps else None

    return {"modified_files": modified, "deps_yml": deps_yml_path}


# ---------------------------------------------------------------------------
# Cost estimation (dry-run)
# ---------------------------------------------------------------------------

# Approximate public pricing (USD per 1M tokens). Update when Anthropic
# adjusts the pricing page. Consumers should treat these as estimates.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"input_per_mtok": 15.0, "output_per_mtok": 75.0},
    "claude-opus-4-5": {"input_per_mtok": 15.0, "output_per_mtok": 75.0},
    "claude-sonnet-4-6": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-sonnet-4-5": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-haiku-4-5": {"input_per_mtok": 0.80, "output_per_mtok": 4.0},
}

# Fallback when an unknown model id is passed — use Sonnet-class pricing.
_FALLBACK_PRICING = {"input_per_mtok": 3.0, "output_per_mtok": 15.0}

# Approximate chars-per-token. Claude's tokenizer runs ~3.5-4 chars/token for
# English and ~2-3 for Japanese/CJK; 4 is a reasonable safe default.
_CHARS_PER_TOKEN = 4

# Fixed prompt scaffolding (system + template + schema) — roughly constant
# regardless of how many files are included. Estimated.
_BASE_PROMPT_TOKENS = 800


def estimate_cost(
    directory: str | Path,
    model: str | None = None,
    extensions: list[str] | None = None,
    max_output_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Estimate the cost of running `analyze_dependencies` on `directory`.

    Uses the public per-million-token pricing table in `MODEL_PRICING`.
    No API call is made. Results are approximate.
    """
    resolved_model = model or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
    pricing = MODEL_PRICING.get(resolved_model, _FALLBACK_PRICING)
    pricing_note = None
    if resolved_model not in MODEL_PRICING:
        pricing_note = (
            f"unknown model '{resolved_model}'; using Sonnet-class pricing as a fallback"
        )

    documents = scan_documents(directory, extensions=extensions)

    input_chars = sum(len(doc["summary"]) for doc in documents)
    # Each file block adds ~30 chars of framing (### path + fences).
    input_chars += len(documents) * 30
    input_tokens = (input_chars // _CHARS_PER_TOKEN) + _BASE_PROMPT_TOKENS if documents else 0
    output_tokens = max_output_tokens if documents else 0

    input_usd = (input_tokens / 1_000_000) * pricing["input_per_mtok"]
    output_usd = (output_tokens / 1_000_000) * pricing["output_per_mtok"]
    total_usd = input_usd + output_usd

    result: dict[str, Any] = {
        "model": resolved_model,
        "documents": len(documents),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_usd": round(input_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": round(total_usd, 6),
        "pricing_source": "approximate; verify at https://anthropic.com/pricing",
    }
    if pricing_note:
        result["pricing_note"] = pricing_note
    return result


def format_cost_estimate(est: dict[str, Any]) -> str:
    """Pretty-print an estimate dict for the CLI."""
    lines = [
        "=" * 60,
        "Dry-run cost estimate",
        "=" * 60,
        f"  model:       {est['model']}",
        f"  documents:   {est['documents']}",
        f"  input:       {est['input_tokens']:,} tokens  →  ${est['input_usd']:.4f}",
        f"  output:      ~{est['output_tokens']:,} tokens  →  ${est['output_usd']:.4f}",
        f"  total:       ${est['total_usd']:.4f}",
    ]
    if "pricing_note" in est:
        lines.append(f"  note:        {est['pricing_note']}")
    lines += [
        "",
        est.get("pricing_source", ""),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Host-agent mode — emit a plan instead of calling the Claude API
# ---------------------------------------------------------------------------

def _list_document_paths(
    directory: str | Path,
    extensions: list[str] | None = None,
) -> list[dict]:
    """List eligible document paths without extracting text.

    Used by `format_host_agent_plan`: the host agent will read the files
    itself, so we don't need to parse PDFs / DOCX here (which would require
    optional dependencies).
    """
    if extensions is None:
        extensions = [".md", ".txt", ".pdf", ".docx"]
    root = Path(directory).resolve()
    out: list[dict] = []
    for ext in extensions:
        for fp in sorted(root.rglob(f"*{ext}")):
            rel = fp.relative_to(root).as_posix()
            if any(part.startswith(".") for part in fp.relative_to(root).parts):
                continue
            if "node_modules" in rel:
                continue
            if fp.name == DEPS_FILENAME:
                continue
            out.append({"path": rel, "ext": ext})
    return out


def format_host_agent_plan(
    directory: str | Path,
    extensions: list[str] | None = None,
) -> str:
    """Produce a Markdown instruction pack for a host agent (e.g. Claude Code).

    The pack contains: the files to analyze, the analysis task (from the
    bundled prompt template), the expected JSON schema, and the command to
    apply the resulting JSON. No API call is made.
    """
    directory_str = str(Path(directory).resolve())
    documents = _list_document_paths(directory, extensions=extensions)

    if not documents:
        return (
            f"# dotmd-parser — host agent plan\n\n"
            f"Target: `{directory_str}`\n\n"
            "No documents found. Nothing to analyze.\n"
            "Run `dotmd-parser inventory <path>` to see what's in the folder.\n"
        )

    template = _load_prompt_template("analyze-dependencies")
    file_list_block = "\n".join(
        f"### {d['path']}\n(read this file from `{directory_str}/{d['path']}`)"
        for d in documents
    )
    full_prompt = template.replace("{{file_list}}", file_list_block)

    files_listed = "\n".join(f"- `{d['path']}`" for d in documents)

    plan_lines = [
        "# dotmd-parser — host agent plan",
        "",
        f"Target directory: `{directory_str}`",
        f"Documents discovered: {len(documents)}",
        "",
        "## Instructions for the host agent",
        "",
        "This is a no-API-key alternative to `dotmd-parser analyze`. "
        "Execute the task below yourself (read the files, infer "
        "dependencies, emit JSON), then feed the JSON back via "
        "`dotmd-parser analyze <path> --apply-from <json>`.",
        "",
        "## Files to analyze",
        "",
        files_listed,
        "",
        "## Analysis task",
        "",
        "```",
        full_prompt.strip(),
        "```",
        "",
        "## Expected output",
        "",
        "A JSON object with this shape:",
        "",
        "```json",
        "{",
        '  "documents": [{"path": "...", "summary": "..."}],',
        '  "edges": [{"from": "...", "to": "...", "reason": "..."}],',
        '  "shared_proposals": [',
        '    {"name": "shared/...", "content_summary": "...",',
        '     "used_by": ["..."], "reason": "..."}',
        "  ]",
        "}",
        "```",
        "",
        "## Apply the result",
        "",
        "Save the JSON to a file (e.g. `analysis.json`), then run:",
        "",
        "```bash",
        f'dotmd-parser analyze "{directory_str}" --apply-from analysis.json',
        "```",
        "",
        "This will inject `@include` lines into text files and write "
        "`deps.yml` for any binary sources (PDF/DOCX/PPTX).",
        "",
    ]
    return "\n".join(plan_lines)


def apply_analysis_from_file(directory: str | Path, json_path: str | Path) -> dict:
    """Load a pre-computed analysis JSON and apply it.

    Raises:
        FileNotFoundError: if `json_path` does not exist.
        ValueError: if the file is not valid JSON or the schema is wrong.
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"analysis JSON not found: {json_path}")
    try:
        analysis = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {json_path}: {e}") from e

    # Normalize: accept minimal payloads (edges/shared_proposals only).
    analysis.setdefault("documents", [])
    analysis.setdefault("edges", [])
    analysis.setdefault("shared_proposals", [])
    return apply_analysis(directory, analysis)


# ---------------------------------------------------------------------------
# Human-readable formatter (same shape as dotmd-tools)
# ---------------------------------------------------------------------------

def format_proposal(analysis: dict) -> str:
    lines: list[str] = [
        "=" * 60,
        "Dependency analysis",
        "=" * 60,
        f"Files analyzed: {len(analysis['documents'])}",
        "",
    ]

    if analysis["edges"]:
        lines.append("--- Detected dependencies ---")
        for edge in analysis["edges"]:
            lines += [
                f"  {edge['from']}",
                f"    └── depends on: {edge['to']}",
                f"        reason: {edge.get('reason', '')}",
            ]
        lines.append("")

    if analysis["shared_proposals"]:
        lines.append("--- Shared part proposals ---")
        for prop in analysis["shared_proposals"]:
            lines += [
                f"  {prop.get('name', '(unnamed)')}",
                f"    content: {prop.get('content_summary', '')}",
                f"    used by: {', '.join(prop.get('used_by', []))}",
                f"    reason: {prop.get('reason', '')}",
            ]
        lines.append("")

    directives = generate_directives(analysis)
    text_directives = {k: v for k, v in directives.items() if is_text_editable(k)}
    binary_directives = {k: v for k, v in directives.items() if not is_text_editable(k)}
    if text_directives:
        lines.append("--- @include directives to insert ---")
        for src in sorted(text_directives):
            lines.append(f"  {src}:")
            for d in text_directives[src]:
                lines.append(f"    + {d}")
        lines.append("")
    if binary_directives:
        lines.append("--- deps.yml entries to write ---")
        for src in sorted(binary_directives):
            lines.append(f"  {src}:")
            for d in binary_directives[src]:
                lines.append(f"    → {d.removeprefix('@include ')}")
        lines.append("")

    if not analysis["edges"] and not analysis["shared_proposals"]:
        lines.append("No dependencies detected.")

    return "\n".join(lines)
