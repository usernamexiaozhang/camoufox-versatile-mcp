"""Tests for camoufox_versatile_mcp.tools.vmp_pipeline submodules."""
from __future__ import annotations

from camoufox_versatile_mcp.tools.vmp_pipeline import (
    detect_vmp, trace_vmp_for_sign, replay_vmp_offline,
    auto_suggest_missing_props, disassemble_wasm,
    capture_worker_js, capture_ws_payloads, get_tap_log,
    NODE_BIN, NODE_VERSION,
)


def test_node_bin_is_string():
    assert isinstance(NODE_BIN, str)
    assert len(NODE_BIN) > 0


def test_node_version_format():
    # Version should be like "v20.x.x" or empty string
    if NODE_VERSION:
        assert NODE_VERSION.startswith("v")


def test_auto_suggest_missing_props_no_args():
    """Should return suggestions even with no input."""
    result = auto_suggest_missing_props()
    assert "suggestions" in result
    assert "sandbox_template" in result


def test_auto_suggest_missing_props_filters_supplied():
    """Supplied env should not appear in suggestions."""
    result = auto_suggest_missing_props(
        supplied_env={"navigator.userAgent": "test"}
    )
    names = [s["prop"] for s in result["suggestions"]]
    assert "navigator.userAgent" not in names


def test_auto_suggest_missing_props_includes_always_suggest():
    """Always-suggest props should appear when not supplied."""
    result = auto_suggest_missing_props(supplied_env={})
    names = [s["prop"] for s in result["suggestions"]]
    # Date.now and screen.width are in ALWAYS_SUGGEST
    assert "Date.now" in names
    assert "screen.width" in names


def test_auto_suggest_missing_props_from_error():
    """Failed replay error should hint at missing props."""
    result = auto_suggest_missing_props(
        failed_replay_error="ReferenceError: navigator is not defined"
    )
    names = [s["prop"] for s in result["suggestions"]]
    # navigator should be suggested from the error message
    assert any("navigator" in n for n in names)
