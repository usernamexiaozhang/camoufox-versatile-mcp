"""Tests for camoufox_versatile_mcp.tools.js_engine_trace_mcp."""
from __future__ import annotations

import asyncio
from camoufox_versatile_mcp.tools.js_engine_trace_mcp import (
    _normalize_body_kind,
    _build_http_summary,
    _page_http_handlers,
    _trace_sessions,
    _http_sessions,
)
from camoufox_versatile_mcp.http_trace import HttpTraceSession
import tempfile
from pathlib import Path


def test_normalize_body_kind():
    assert _normalize_body_kind("application/json") == "json"
    assert _normalize_body_kind("text/html; charset=utf-8") == "html"
    assert _normalize_body_kind("application/javascript") == "javascript"
    assert _normalize_body_kind("image/png") == "image"
    assert _normalize_body_kind("text/css") == "css"
    assert _normalize_body_kind("font/woff2") == "font"
    assert _normalize_body_kind("application/octet-stream") == "other"
    assert _normalize_body_kind("") == "other"


def test_build_http_summary_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="sumtest",
            output_dir=Path(tmpdir) / "trace",
        )
        session.started_at = 1000.0
        result = _build_http_summary(session, limit=100)
        assert result["by_resource_type"] == {}
        assert result["total_requests"] == 0
        assert result["slow_requests_ms"] == []


def test_build_http_summary_with_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="sumtest2",
            output_dir=Path(tmpdir) / "trace",
        )
        session.started_at = 1000.0
        session.entries = [
            {
                "url": "https://a.com/api.js",
                "method": "GET",
                "status": 200,
                "resourceType": "script",
                "responseBodyKind": "javascript",
                "duration": 500,
            },
            {
                "url": "https://a.com/v1/data",
                "method": "POST",
                "status": 201,
                "resourceType": "xhr",
                "responseBodyKind": "json",
                "duration": 3000,
            },
        ]
        result = _build_http_summary(session, limit=100)
        assert result["by_resource_type"]["script"] == 1
        assert result["by_resource_type"]["xhr"] == 1
        assert result["by_method"]["GET"] == 1
        assert result["by_method"]["POST"] == 1
        assert len(result["slow_requests_ms"]) == 1
        assert result["slow_requests_ms"][0]["duration"] == 3000
        assert len(result["javascript_requests"]) == 1
        assert len(result["api_requests"]) == 1


def test_http_summary_slow_detection_threshold():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = HttpTraceSession(
            session_id="slowtest",
            output_dir=Path(tmpdir) / "trace",
        )
        session.started_at = 1000.0
        # Exactly 1000ms should not be considered slow (> 1000)
        session.entries = [
            {"url": "https://x.com/fast", "method": "GET", "status": 200, "duration": 1000, "resourceType": "xhr", "responseBodyKind": "json"},
            {"url": "https://x.com/slow", "method": "GET", "status": 200, "duration": 1001, "resourceType": "xhr", "responseBodyKind": "json"},
        ]
        result = _build_http_summary(session, limit=100)
        assert len(result["slow_requests_ms"]) == 1
        assert result["slow_requests_ms"][0]["duration"] == 1001
