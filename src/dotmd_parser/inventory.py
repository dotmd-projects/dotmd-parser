"""
dotmd-parser — filesystem inventory (API-free, lightweight).

Scans a directory and produces a compact summary of file composition
(extension counts, sizes, markdown ratio, binary ratio, largest files).
Useful to answer "what am I looking at?" before running `index`, `analyze`,
or other API-backed commands.

Skips:
- Hidden files and directories (leading dot: .git, .claude, .venv, .DS_Store)
- Office temporary lock files (leading ~$: ~$report.pptx)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


TEXT_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".vue",
    ".svelte",
    ".log",
}

BINARY_EXTENSIONS: set[str] = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".heic",
    ".bmp",
    ".tiff",
    ".tif",
    ".ico",
    ".mp3",
    ".mp4",
    ".m4a",
    ".wav",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".dat",
}

MARKDOWN_EXTENSIONS: set[str] = {".md", ".markdown"}

_MAX_LARGEST = 5


def _is_hidden(path: Path, root: Path) -> bool:
    """True if any path component (relative to root) starts with a dot."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def _is_office_lock(name: str) -> bool:
    return name.startswith("~$")


def _has_japanese(name: str) -> bool:
    """True if the filename contains CJK characters (Hiragana/Katakana/Kanji)."""
    for ch in name:
        cp = ord(ch)
        if (
            0x3040 <= cp <= 0x309F  # Hiragana
            or 0x30A0 <= cp <= 0x30FF  # Katakana
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
        ):
            return True
    return False


def inventory(path: str) -> dict[str, Any]:
    """Scan `path` recursively and return a compact inventory dict.

    Raises:
        ValueError: if `path` does not exist or is not a directory.
    """
    root = Path(path)
    if not root.exists():
        raise ValueError(f"path does not exist: {path}")
    if not root.is_dir():
        raise ValueError(f"path is not a directory: {path}")

    extensions: dict[str, dict[str, int]] = {}
    total_files = 0
    total_bytes = 0
    markdown_count = 0
    binary_count = 0
    has_japanese_names = False
    all_entries: list[dict[str, Any]] = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if _is_hidden(p, root):
            continue
        if _is_office_lock(p.name):
            continue

        try:
            size = p.stat().st_size
        except OSError:
            continue

        ext = p.suffix.lower()
        rel = str(p.relative_to(root))

        bucket = extensions.setdefault(ext, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += size

        total_files += 1
        total_bytes += size

        if ext in MARKDOWN_EXTENSIONS:
            markdown_count += 1
        if ext in BINARY_EXTENSIONS:
            binary_count += 1

        if not has_japanese_names and _has_japanese(p.name):
            has_japanese_names = True

        all_entries.append({"path": rel, "bytes": size, "ext": ext})

    largest = sorted(all_entries, key=lambda e: e["bytes"], reverse=True)[:_MAX_LARGEST]

    markdown_ratio = (markdown_count / total_files) if total_files else 0.0
    binary_ratio = (binary_count / total_files) if total_files else 0.0

    return {
        "path": str(root.resolve()),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "extensions": extensions,
        "markdown_count": markdown_count,
        "markdown_ratio": markdown_ratio,
        "binary_count": binary_count,
        "binary_ratio": binary_ratio,
        "has_japanese_names": has_japanese_names,
        "largest_files": largest,
    }


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    return f"{n / (1024 * 1024 * 1024):.1f}GB"


def format_inventory(inv: dict[str, Any]) -> str:
    """Render an inventory dict as a human-readable summary."""
    lines: list[str] = []
    lines.append(f"# inventory — {inv['path']}")
    lines.append(
        f"{inv['total_files']} files, {_human_bytes(inv['total_bytes'])} total"
    )
    lines.append(
        f"markdown: {inv['markdown_count']} ({inv['markdown_ratio'] * 100:.1f}%)  "
        f"binary: {inv['binary_count']} ({inv['binary_ratio'] * 100:.1f}%)"
    )
    if inv.get("has_japanese_names"):
        lines.append("locale: Japanese filenames detected")

    if inv["extensions"]:
        lines.append("")
        lines.append("## by extension")
        rows = sorted(
            inv["extensions"].items(),
            key=lambda kv: kv[1]["count"],
            reverse=True,
        )
        for ext, stats in rows:
            label = ext if ext else "(none)"
            lines.append(f"  {label:<12} {stats['count']:>5}  {_human_bytes(stats['bytes'])}")

    if inv["largest_files"]:
        lines.append("")
        lines.append("## largest files")
        for f in inv["largest_files"]:
            lines.append(f"  {_human_bytes(f['bytes']):>8}  {f['path']}")

    hint = suggest_next_command(inv)
    if hint:
        lines.append("")
        lines.append(f"note: {hint}")

    return "\n".join(lines)


def suggest_next_command(inv: dict[str, Any]) -> str | None:
    """Return a human-readable hint for `index`/`digest` callers, or None.

    Heuristics:
    - Empty folder → no suggestion.
    - .md-dominant folder (ratio ≥ 50%) → no suggestion.
    - No .md but binaries present → propose `analyze --apply`.
    - No .md and no binaries (text-only) → propose manual @include authoring.
    - Few .md in a mostly-binary large folder → propose `analyze --apply`.
    """
    total = inv.get("total_files", 0)
    md = inv.get("markdown_count", 0)
    binaries = inv.get("binary_count", 0)
    md_ratio = inv.get("markdown_ratio", 0.0)

    if total == 0:
        return None
    if md > 0 and md_ratio >= 0.5:
        return None

    if md == 0 and binaries > 0:
        return (
            "no .md files found — `index`/`digest` will produce an empty graph. "
            "Try `dotmd-parser analyze <path> --apply` to seed dependencies "
            "(requires ANTHROPIC_API_KEY), or add @include directives manually."
        )
    if md == 0 and binaries == 0:
        return (
            "no .md files found in this text-only folder. "
            "Add @include / @delegate directives to your entry files "
            "or create a deps.yml before running `index`/`digest`."
        )
    if md > 0 and md_ratio < 0.1 and total >= 20 and binaries > 0:
        return (
            f"only {md}/{total} files are .md — the graph may be sparse. "
            "Consider `dotmd-parser analyze <path> --apply` to capture "
            "dependencies from PDF/DOCX/PPTX as well."
        )
    return None
