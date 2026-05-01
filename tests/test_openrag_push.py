"""
dotmd-parser — OpenRAG push integration tests.

The push helper sends a generated `dotmd-index.md` to a running OpenRAG
instance (https://github.com/langflow-ai/openrag) via its async Python SDK
(`openrag-sdk`). The SDK is an optional dependency.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from dotmd_parser.cli import run as cli_run
from dotmd_parser.index_md import (
    DEFAULT_INDEX_FILENAME,
    extract_frontmatter,
    write_index_md,
)
from dotmd_parser.openrag import push_to_openrag


def _w(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cli_run(argv)
            rc = 0
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Mock OpenRAG SDK
# ---------------------------------------------------------------------------

class _FakeIngestResponse:
    """Mirrors openrag-sdk's IngestTaskStatus (returned by ingest(wait=True))."""

    def __init__(self, task_id="task-1", status="completed", filename="dotmd-index.md"):
        self.task_id = task_id
        self.status = status
        self.filename = filename
        self.total_files = 1
        self.successful_files = 1   # int (count), not a list
        self.failed_files = 0


class _FakeDocuments:
    def __init__(self, parent: "_FakeOpenRAGClient"):
        self.parent = parent

    async def ingest(self, file_path=None, file=None, filename=None, wait=True):
        self.parent.ingested_paths.append(file_path)
        return _FakeIngestResponse()


class _FakeOpenRAGClient:
    """Drop-in replacement for openrag-sdk's OpenRAGClient (async ctx mgr)."""

    instances: list["_FakeOpenRAGClient"] = []

    def __init__(self, *, api_key=None, base_url=None):
        self.api_key = api_key
        self.base_url = base_url
        self.ingested_paths: list[str] = []
        self.documents = _FakeDocuments(self)
        _FakeOpenRAGClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


# ---------------------------------------------------------------------------
# push_to_openrag()
# ---------------------------------------------------------------------------

class TestPushToOpenRAG(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _w(self.root / "SKILL.md", "# Demo\n")
        # Pre-generate dotmd-index.md
        write_index_md(str(self.root))
        self.md_path = self.root / DEFAULT_INDEX_FILENAME
        _FakeOpenRAGClient.instances.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_calls_ingest_with_md_path(self):
        result = push_to_openrag(
            str(self.md_path),
            base_url="http://localhost:3000",
            api_key="test-key",
            _client_cls=_FakeOpenRAGClient,
        )
        self.assertEqual(len(_FakeOpenRAGClient.instances), 1)
        client = _FakeOpenRAGClient.instances[0]
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "http://localhost:3000")
        self.assertIn(str(self.md_path), client.ingested_paths)
        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["successful_files"], 1)

    def test_returns_exports_shape(self):
        result = push_to_openrag(
            str(self.md_path),
            base_url="https://rag.example.com",
            _client_cls=_FakeOpenRAGClient,
        )
        for key in (
            "task_id",
            "status",
            "filename",
            "total_files",
            "successful_files",
            "failed_files",
            "pushed_at",
            "base_url",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["base_url"], "https://rag.example.com")
        # pushed_at should be ISO-8601 UTC
        self.assertTrue(result["pushed_at"].endswith("Z"))

    def test_raises_when_md_path_missing(self):
        with self.assertRaises(ValueError):
            push_to_openrag(
                "/nonexistent/path/dotmd-index.md",
                _client_cls=_FakeOpenRAGClient,
            )

    def test_raises_friendly_import_error_when_sdk_missing(self):
        # No _client_cls injection → falls through to real import,
        # which fails because openrag-sdk isn't installed.
        with self.assertRaises(ImportError) as ctx:
            push_to_openrag(str(self.md_path))
        self.assertIn("openrag-sdk", str(ctx.exception).lower())
        self.assertIn("pip install", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# CLI --push-openrag
# ---------------------------------------------------------------------------

class TestCliPushOpenRAG(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _w(self.root / "SKILL.md", "# Demo\n")
        _FakeOpenRAGClient.instances.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_flag_writes_file_then_pushes(self):
        with mock.patch("dotmd_parser.openrag._import_openrag_client_cls", return_value=_FakeOpenRAGClient):
            rc, _, err = _invoke([
                "dotmd-index", str(self.root),
                "--push-openrag",
                "--openrag-url", "http://localhost:3000",
            ])
        self.assertEqual(rc, 0, msg=err)
        target = self.root / DEFAULT_INDEX_FILENAME
        self.assertTrue(target.exists())
        fm = extract_frontmatter(target.read_text(encoding="utf-8"))
        self.assertIn("exports", fm)
        self.assertIn("openrag", fm["exports"])
        self.assertEqual(fm["exports"]["openrag"]["task_id"], "task-1")
        self.assertEqual(fm["exports"]["openrag"]["status"], "completed")
        # Verify the SDK was actually called
        self.assertEqual(len(_FakeOpenRAGClient.instances), 1)

    def test_push_flag_friendly_error_when_sdk_missing(self):
        # Default — SDK is not installed in this venv
        rc, _, err = _invoke([
            "dotmd-index", str(self.root),
            "--push-openrag",
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn("openrag-sdk", err.lower())


if __name__ == "__main__":
    unittest.main()
