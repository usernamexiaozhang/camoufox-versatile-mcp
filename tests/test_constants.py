"""Tests for camoufox_versatile_mcp.constants."""
from __future__ import annotations

from camoufox_versatile_mcp.constants import (
    MAX_LOG_SIZE, MAX_BODY_SIZE,
    NAV_TIMEOUT_MS, CAPTCHA_TURNSTILE_TIMEOUT_MS,
    HTTP_IGNORED_HOSTS, MAX_STACK_FRAMES,
    TRACE_LEVELS, TLLOG_SUBSYSTEMS,
    NODE_FALLBACK_PATHS,
    VMP_TAP_MAX_ENTRIES,
)


def test_browser_limits():
    assert MAX_LOG_SIZE == 2000
    assert MAX_BODY_SIZE == 200_000


def test_timeout_values():
    assert NAV_TIMEOUT_MS == 30_000
    assert CAPTCHA_TURNSTILE_TIMEOUT_MS == 15_000


def test_http_trace_constants():
    assert MAX_STACK_FRAMES == 64
    assert "mozilla.org" in HTTP_IGNORED_HOSTS


def test_trace_levels():
    assert "functions" in TRACE_LEVELS
    assert "full" in TRACE_LEVELS
    assert "wasm" in TRACE_LEVELS
    assert "minimal" in TRACE_LEVELS
    assert "bytecode" in TRACE_LEVELS


def test_tllog_subsystems():
    assert "interpreter" in TLLOG_SUBSYSTEMS
    assert "ion" in TLLOG_SUBSYSTEMS
    assert "wasm" in TLLOG_SUBSYSTEMS
    assert TLLOG_SUBSYSTEMS["ion"] == "Ion"


def test_node_fallback_paths():
    assert len(NODE_FALLBACK_PATHS) > 0
    assert "node" in NODE_FALLBACK_PATHS


def test_vmp_tap_max_entries():
    assert VMP_TAP_MAX_ENTRIES == 2000
