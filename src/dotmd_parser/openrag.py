"""
dotmd-parser — OpenRAG push integration (optional).

Sends a generated `dotmd-index.md` to a running OpenRAG instance
(https://github.com/langflow-ai/openrag) using its async Python SDK
(`openrag-sdk`). The SDK is an *optional* dependency — install it with:

    pip install dotmd-parser[openrag]

OpenRAG's `documents.ingest()` accepts a file path, parses the markdown
through Docling, and stores it in OpenSearch. Once ingested, the file
becomes searchable from any client (including OpenRAG's own MCP server,
which Claude Code can talk to directly).

Design points
-------------
- **Lazy import** so the package itself never fails to import when the
  optional SDK is missing.
- **Friendly error** with the exact `pip install` command when the SDK is
  missing.
- **Async-to-sync wrapper** (`asyncio.run`) so the rest of dotmd-parser's
  synchronous CLI keeps working unchanged.
- **`_client_cls` test hook** mirrors `analyze.py`'s `caller=` parameter.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _import_openrag_client_cls():
    """Lazy SDK import. Raise a helpful ImportError when missing.

    Note: the PyPI package is `openrag-sdk` (with hyphen) and the importable
    module is `openrag_sdk` (with underscore). The module exposes
    `OpenRAGClient` from `openrag_sdk.client`.
    """
    try:
        from openrag_sdk import OpenRAGClient  # type: ignore
    except ImportError as e:
        raise ImportError(
            "openrag-sdk is not installed. "
            "Install it with: pip install dotmd-parser[openrag]\n"
            "(or directly: pip install openrag-sdk)"
        ) from e
    return OpenRAGClient


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_base_url(explicit: str | None) -> str:
    return (
        explicit
        or os.environ.get("OPENRAG_URL")
        or "http://localhost:3000"
    )


def _normalize_response(raw: Any, base_url: str) -> dict:
    """Coerce an SDK ingest response into our `exports.openrag` dict shape.

    With `wait=True`, openrag-sdk returns an `IngestTaskStatus` carrying
    `task_id`, `status`, and per-file counters (`total_files`,
    `successful_files`, `failed_files`). With `wait=False` it returns a
    leaner `IngestResponse` (task_id + optional status + filename).
    Both shapes are accepted here; missing fields default to neutral values.
    """
    return {
        "task_id": _attr(raw, "task_id", "") or "",
        "status": _attr(raw, "status", "") or "",
        "filename": _attr(raw, "filename", "") or "",
        "total_files": int(_attr(raw, "total_files", 0) or 0),
        "successful_files": int(_attr(raw, "successful_files", 0) or 0),
        "failed_files": int(_attr(raw, "failed_files", 0) or 0),
        "pushed_at": _now_iso(),
        "base_url": base_url,
    }


def _attr(obj: Any, name: str, default: Any) -> Any:
    """Read attribute or dict key; tolerate either response shape."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def _push_async(
    md_path: Path,
    base_url: str,
    api_key: str | None,
    client_cls,
) -> dict:
    kwargs: dict[str, Any] = {"base_url": base_url}
    if api_key is not None:
        kwargs["api_key"] = api_key
    async with client_cls(**kwargs) as client:
        raw = await client.documents.ingest(file_path=str(md_path), wait=True)
    return _normalize_response(raw, base_url)


def push_to_openrag(
    md_path: str | Path,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    _client_cls: Any = None,
) -> dict:
    """Push `md_path` to OpenRAG and return a record for `exports.openrag`.

    Parameters
    ----------
    md_path : str | Path
        Path to the file to ingest (typically `<root>/dotmd-index.md`).
    base_url : str | None
        OpenRAG endpoint. Falls back to `OPENRAG_URL` then localhost.
    api_key : str | None
        OpenRAG API key. Falls back to `OPENRAG_API_KEY` (handled inside the SDK).
    _client_cls : type | None
        Test hook — replaces `OpenRAGClient` with a fake.

    Returns
    -------
    dict
        Suitable for storing under `exports.openrag` in dotmd-index.md
        frontmatter.

    Raises
    ------
    ValueError : if `md_path` does not exist.
    ImportError : if `openrag-sdk` is not installed and `_client_cls` is None.
    RuntimeError : on an SDK / network failure (re-raised by asyncio).
    """
    p = Path(md_path)
    if not p.exists():
        raise ValueError(f"file not found: {md_path}")

    client_cls = _client_cls or _import_openrag_client_cls()
    resolved_url = _resolve_base_url(base_url)
    return asyncio.run(_push_async(p, resolved_url, api_key, client_cls))
