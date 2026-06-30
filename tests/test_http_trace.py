"""Tests for camoufox_versatile_mcp.http_trace."""
from __future__ import annotations

from camoufox_versatile_mcp.http_trace import (
    HTTP_TRACE_DIR, MAX_STACK_FRAMES, IGNORED_HOSTS,
    _sha256, _is_text_mime, _is_binary_mime,
    _normalize_stack_frame, _filter_non_chrome_frames,
    HttpTraceSession,
)
import tempfile
from pathlib import Path


def test_sha256():
    assert len(_sha256("hello")) == 64
    assert _sha256("hello") == _sha256("hello")
    assert _sha256("") == ""
    assert _sha256(None) == ""


def test_is_text_mime():
    assert _is_text_mime("application/json")
    assert _is_text_mime("text/html")
    assert _is_text_mime("application/javascript")
    assert _is_text_mime("text/css")
    assert not _is_text_mime("")
    assert not _is_text_mime(None)


def test_is_binary_mime():
    assert _is_binary_mime("image/png")
    assert _is_binary_mime("audio/mpeg")
    assert _is_binary_mime("application/pdf")
    assert _is_binary_mime("font/woff2")
    assert not _is_binary_mime("application/json")


def test_normalize_stack_frame():
    frame = _normalize_stack_frame({
        "functionName": "testFn",
        "filename": "test.js",
        "lineNumber": "10",
        "columnNumber": "5",
        "asyncCause": "promise",
    })
    assert frame["functionName"] == "testFn"
    assert frame["lineNumber"] == 10
    assert frame["columnNumber"] == 5
    assert frame["asyncCause"] == "promise"


def test_filter_non_chrome_frames():
    frames = [
        {"filename": "https://example.com/app.js"},
        {"filename": "resource://foo/bar"},
        {"filename": "chrome://global/content"},
        {"filename": "https://cdn.example.com/lib.js"},
    ]
    result = _filter_non_chrome_frames(frames)
    assert len(result) == 2
    assert all(not f["filename"].startswith("resource://") for f in result)
    assert all(not f["filename"].startswith("chrome://") for f in result)


def test_http_trace_session_init():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="test123",
            output_dir=Path(tmpdir) / "trace",
        )
        assert session.session_id == "test123"
        assert session.active is False
        assert session.capture_body is False
        assert session.entries == []


def test_http_trace_session_add_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="test456",
            output_dir=Path(tmpdir) / "trace",
        )
        entry = {
            "url": "https://example.com/api",
            "method": "GET",
            "status": 200,
            "resourceType": "xhr",
            "responseBodyKind": "json",
        }
        session.add_entry(entry)
        assert len(session.entries) == 1
        assert session.entries[0]["url"] == "https://example.com/api"


def test_http_trace_session_filter():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="test789",
            output_dir=Path(tmpdir) / "trace",
        )
        session.entries = [
            {"url": "https://a.com/api", "method": "GET", "status": 200, "resourceType": "xhr"},
            {"url": "https://a.com/data", "method": "POST", "status": 201, "resourceType": "xhr"},
            {"url": "https://b.com/img.png", "method": "GET", "status": 200, "resourceType": "image"},
        ]
        results = session.filter(method="GET")
        assert len(results) == 2
        assert all(e["method"] == "GET" for e in results)
