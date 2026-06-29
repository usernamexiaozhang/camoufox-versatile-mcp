from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager
from ..utils.js_helpers import render_trace_template, render_persistent_trace_template


@mcp.tool()
async def hook_function(function_path: str, mode: str = "intercept", hook_code: str = "",
                        position: str = "before", non_overridable: bool = False,
                        persistent: bool = False, log_args: bool = True,
                        log_return: bool = True, log_stack: bool = False,
                        max_captures: int = 50) -> dict:
    """Hook or trace a function."""
    if mode == "trace":
        return await _trace_function(function_path, persistent, log_args, log_return, log_stack, max_captures)
    elif mode == "intercept":
        return await _hook_function(function_path, hook_code, position, non_overridable)
    else:
        return {"error": f"unknown mode: {mode}. Use 'intercept' or 'trace'"}


async def _trace_function(function_path: str, persistent: bool, log_args: bool,
                          log_return: bool, log_stack: bool, max_captures: int) -> dict:
    try:
        if persistent:
            trace_js = render_persistent_trace_template(function_path=function_path, max_captures=max_captures,
                                                       log_args=log_args, log_return=log_return, log_stack=log_stack)
            trace_name = f"trace:{function_path}"
            await browser_manager.add_persistent_script(trace_name, trace_js)
            page = await browser_manager.get_active_page()
            await page.evaluate(trace_js)
            return {"status": "tracing", "target": function_path, "persistent": True}
        else:
            page = await browser_manager.get_active_page()
            trace_js = render_trace_template(function_path=function_path, max_captures=max_captures,
                                            log_args=log_args, log_return=log_return, log_stack=log_stack)
            await page.evaluate(trace_js)
            return {"status": "tracing", "target": function_path, "persistent": False}
    except Exception as e:
        return {"error": str(e)}


async def _hook_function(function_path: str, hook_code: str, position: str, non_overridable: bool) -> dict:
    try:
        page = await browser_manager.get_active_page()
        escaped_hook = hook_code.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        freeze_code = ""
        if non_overridable:
            freeze_code = "\n    try { Object.defineProperty(parent, fn, {value: parent[fn], writable: false, configurable: false}); } catch(e) {}"

        if position == "before":
            js = f"""(() => {{ const path = {repr(function_path)}; const parts = path.split('.'); let parent = window; for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }} const fn = parts[parts.length - 1]; const _orig = parent[fn]; if (typeof _orig !== 'function') return; const wrapper = function(...args) {{ const __this = this; (function() {{ {escaped_hook} }}).call(__this); return _orig.apply(this, args); }}; wrapper.toString = function() {{ return _orig.toString(); }}; parent[fn] = wrapper;{freeze_code} }})();"""
        elif position == "after":
            js = f"""(() => {{ const path = {repr(function_path)}; const parts = path.split('.'); let parent = window; for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }} const fn = parts[parts.length - 1]; const _orig = parent[fn]; if (typeof _orig !== 'function') return; const wrapper = function(...args) {{ const __this = this; const __result = _orig.apply(this, args); (function() {{ {escaped_hook} }}).call(__this); return __result; }}; wrapper.toString = function() {{ return _orig.toString(); }}; parent[fn] = wrapper;{freeze_code} }})();"""
        elif position == "replace":
            js = f"""(() => {{ const path = {repr(function_path)}; const parts = path.split('.'); let parent = window; for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }} const fn = parts[parts.length - 1]; const wrapper = function(...args) {{ const __this = this; {escaped_hook} }}; parent[fn] = wrapper;{freeze_code} }})();"""
        else:
            return {"error": f"Invalid position: {position}. Use 'before', 'after', or 'replace'."}
        await page.evaluate(js)
        return {"status": "hooked", "target": function_path, "position": position, "non_overridable": non_overridable}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def inject_hook_preset(preset: str, persistent: bool = True) -> dict:
    """Inject a pre-built hook template for common reverse engineering tasks."""
    preset_map = {
        "xhr": "xhr_hook.js", "fetch": "fetch_hook.js", "crypto": "crypto_hook.js",
        "websocket": "websocket_hook.js", "debugger_bypass": "debugger_trap.js",
        "cookie": "cookie_hook.js", "runtime_probe": "runtime_probe.js",
    }
    if preset not in preset_map:
        return {"error": f"Unknown preset: {preset}. Available: {list(preset_map.keys())}"}
    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        hook_file = os.path.join(hooks_dir, preset_map[preset])
        with open(hook_file, "r", encoding="utf-8") as f:
            hook_js = f.read()
        if persistent:
            script_name = f"preset:{preset}"
            await browser_manager.add_persistent_script(script_name, hook_js)
            page = await browser_manager.get_active_page()
            await page.evaluate(hook_js)
        else:
            page = await browser_manager.get_active_page()
            await page.add_init_script(script=hook_js)
        browser_manager._init_scripts.append(f"preset:{preset}")
        return {"status": "injected", "preset": preset, "persistent": persistent}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def remove_hooks(keep_persistent: bool = False) -> dict:
    """Remove installed hooks and restore original objects."""
    try:
        page = await browser_manager.get_active_page()
        warnings: list[str] = []
        restored: list[str] = []

        uninstall_js = r"""
        (function() {
          var out = { uninstalled: [], errors: [] };
          if (typeof window.__mcp_jsvmp_uninstall === 'function') {
            try {
              var r = window.__mcp_jsvmp_uninstall();
              out.uninstalled.push({ hook: 'jsvmp_proxy', restored: (r && r.restored) || [] });
            } catch (e) { out.errors.push('jsvmp_uninstall: ' + e.message); }
          }
          if (typeof window.__mcp_transparent_uninstall === 'function') {
            try {
              var r = window.__mcp_transparent_uninstall();
              out.uninstalled.push({ hook: 'jsvmp_transparent', restored: (r && r.restored) || [] });
            } catch (e) { out.errors.push('transparent_uninstall: ' + e.message); }
          }
          return out;
        })();
        """
        try:
            in_page = await page.evaluate(uninstall_js)
            for item in (in_page.get("uninstalled") or []):
                hook = item.get("hook")
                items = item.get("restored") or []
                if items:
                    restored.extend([f"{hook}:{n}" for n in items])
                else:
                    restored.append(hook)
            for err in (in_page.get("errors") or []):
                warnings.append(f"in-page uninstall: {err}")
        except Exception as e:
            warnings.append(f"in-page uninstall eval failed: {e}")

        cleared_init = len(browser_manager._init_scripts)
        browser_manager._init_scripts.clear()
        cleared_persistent = 0
        if not keep_persistent:
            cleared_persistent = len(browser_manager._persistent_scripts)
            browser_manager._persistent_scripts.clear()

        return {
            "status": "hooks_removed", "restored_objects": restored,
            "cleared_init_scripts": cleared_init,
            "cleared_persistent_scripts": cleared_persistent if not keep_persistent else 0,
            "persistent_kept": keep_persistent,
            "warnings": warnings if warnings else None,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_console_logs(level: str | None = None, keyword: str | None = None,
                            clear: bool = False) -> list[dict]:
    """Get console output collected from the page."""
    try:
        logs = list(browser_manager._console_logs)
        if level:
            logs = [l for l in logs if l["level"] == level]
        if keyword:
            logs = [l for l in logs if keyword in (l.get("text") or "")]
        if clear:
            browser_manager._console_logs.clear()
        return logs
    except Exception as e:
        return [{"error": str(e)}]
