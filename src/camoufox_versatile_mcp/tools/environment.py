from __future__ import annotations

import importlib
from typing import Any

from ..server import mcp, browser_manager


@mcp.tool()
async def check_environment() -> dict:
    """One-stop self-check of MCP environment, dependencies, and browser state."""
    recommendations: list[str] = []

    try:
        mod = importlib.import_module("camoufox_versatile_mcp")
        version = getattr(mod, "__version__", "unknown")
        parts = tuple(int(x) for x in version.split(".") if x.isdigit())
        version_ok = parts >= (1, 0, 0)
    except Exception:
        version = "unknown"
        version_ok = False
    if not version_ok:
        recommendations.append(f"MCP version is {version}, need >= 1.0.0.")

    deps: dict[str, dict] = {}
    for dep in ("esprima", "playwright", "camoufox_captcha"):
        try:
            m = importlib.import_module(dep)
            deps[dep] = {"installed": True, "version": getattr(m, "__version__", "unknown"), "ok": True}
        except ImportError:
            deps[dep] = {"installed": False, "version": None, "ok": False}
            if dep == "camoufox_captcha":
                recommendations.append("camoufox_captcha not installed. Run: pip install camoufox-captcha")

    browser_state: dict[str, Any] = {"running": False}
    try:
        if browser_manager.browser is not None:
            browser_state["running"] = True
            ctx = browser_manager.contexts.get("default")
            pages = ctx.pages if ctx else []
            browser_state["page_count"] = len(pages)
            browser_state["persistent_scripts_count"] = len(browser_manager._persistent_scripts)
            browser_state["active_captures"] = browser_manager._capturing
            browser_state["captured_requests_count"] = len(browser_manager._network_requests)
            has_residuals = (
                browser_state["persistent_scripts_count"] > 0
                or browser_state["captured_requests_count"] > 0
            )
            browser_state["has_residuals"] = has_residuals
            if has_residuals:
                recommendations.append("Browser has residual state. Consider reset_browser_state().")
    except Exception as e:
        browser_state["error"] = str(e)

    overall_ok = version_ok and all(d["ok"] for d in deps.values() if d.get("installed"))

    return {
        "mcp": {"version": version, "version_ok": version_ok},
        "deps": deps,
        "browser": browser_state,
        "overall_ok": overall_ok,
        "recommendations": recommendations,
    }
