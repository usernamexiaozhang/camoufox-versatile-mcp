"""conftest.py - pytest fixtures for camoufox-versatile-mcp tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def sample_http_entry():
    """A sample HTTP trace entry for testing."""
    return {
        "url": "https://example.com/api/data",
        "method": "POST",
        "status": 200,
        "resourceType": "xhr",
        "responseBodyKind": "json",
        "duration": 1500,
        "ts": 1234567890,
        "seq": 1,
    }


@pytest.fixture
def sample_js_trace_event():
    """A sample JS engine trace event."""
    return {
        "type": "call",
        "functionName": "generateSign",
        "timestamp": 1234567890000,
        "args": ["input_data"],
        "returnValue": "abc123sign",
    }


@pytest.fixture
def sample_page_props():
    """Sample browser property values."""
    return {
        "navigator.userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0)",
        "navigator.platform": "Win32",
        "screen.width": 1920,
        "screen.height": 1080,
        "Date.now": 1234567890000,
    }
