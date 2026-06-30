"""Tests for camoufox_versatile_mcp.js_engine_trace."""
from __future__ import annotations

from camoufox_versatile_mcp.js_engine_trace import (
    TRACE_OUTPUT_DIR, DEFAULT_TLLOG, DEFAULT_TLOPTIONS,
    TLLOG_SUBSYSTEMS, TRACE_LEVELS,
    prepare_tracelogger_env, start_tracelogger,
    format_trace_events, _normalize_trace_event,
)


def test_trace_output_dir():
    assert str(TRACE_OUTPUT_DIR).endswith("camoufox-trace/js_engine")


def test_default_tllog():
    assert "Interpreter" in DEFAULT_TLLOG
    assert "Ion" in DEFAULT_TLLOG


def test_trace_levels_keys():
    assert "functions" in TRACE_LEVELS
    assert "full" in TRACE_LEVELS


def test_tllog_subsystems_values():
    assert TLLOG_SUBSYSTEMS["ion"] == "Ion"
    assert TLLOG_SUBSYSTEMS["wasm"] == "Wasm"


def test_prepare_tracelogger_env():
    session = prepare_tracelogger_env(level="minimal")
    assert session.mode == "tracelogger"
    assert session.active is True
    assert "env_tllog" in session.config
    assert "env_tloptions" in session.config


def test_prepare_tracelogger_env_custom():
    session = prepare_tracelogger_env(
        level="wasm",
        custom_log="Wasm",
        custom_options="EnableActiveThread",
    )
    assert session.config["env_tllog"] == "Wasm"
    assert session.config["env_tloptions"] == "EnableActiveThread"


def test_start_tracelogger_alias():
    session = start_tracelogger(level="functions")
    assert session.mode == "tracelogger"
    assert session.active is True


def test_format_trace_events_empty():
    result = format_trace_events([], limit=100)
    assert result["total"] == 0
    assert result["unique_functions"] == 0


def test_format_trace_events_with_data():
    events = [
        {"type": "call", "functionName": "encrypt", "timestamp": 1000},
        {"type": "call", "functionName": "hash", "timestamp": 1500},
        {"type": "call", "functionName": "encrypt", "timestamp": 2000},
    ]
    result = format_trace_events(events, limit=100)
    assert result["total"] == 3
    assert result["unique_functions"] == 2


def test_normalize_trace_event():
    event = {
        "type": "call",
        "functionName": "testFn",
        "timestamp": 100,
        "args": ["a", "b"],
        "returnValue": "result",
    }
    normalized = _normalize_trace_event(event)
    assert normalized["name"] == "testFn"
    assert normalized["type"] == "call"
    assert normalized["has_return"] is True
