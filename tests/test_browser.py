"""Tests for camoufox_versatile_mcp.browser."""
from __future__ import annotations

from camoufox_versatile_mcp.browser import (
    detect_host_os, detect_system_locale,
    _build_from_options,
    BrowserManager,
)


def test_detect_host_os():
    result = detect_host_os()
    assert result in ("windows", "macos", "linux")


def test_detect_system_locale_returns_string():
    result = detect_system_locale()
    assert isinstance(result, str)
    assert "-" in result or result == "en-US"


def test_build_from_options_no_exception():
    kwargs = {"os": "windows", "locale": "en-US"}
    opts = _build_from_options(kwargs, headless=False, env_overrides={"TEST_VAR": "123"})
    assert "env" in opts
    assert opts["env"]["TEST_VAR"] == "123"


def test_build_from_options_no_overrides():
    kwargs = {"os": "windows"}
    opts = _build_from_options(kwargs, headless=True, env_overrides=None)
    assert "env" in opts


def test_browser_manager_init():
    bm = BrowserManager()
    assert bm.browser is None
    assert bm.pages == {}
    assert bm.contexts == {}
    assert bm.active_page_name is None


def test_browser_manager_default_config():
    assert BrowserManager.default_config == {}
