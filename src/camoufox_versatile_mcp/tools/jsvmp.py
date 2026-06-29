from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager


@mcp.tool()
async def hook_jsvmp_interpreter(script_url: str = "", persistent: bool = True, mode: str = "proxy",
                                 track_calls: bool = True, track_props: bool = True,
                                 track_reflect: bool = True, proxy_objects: list[str] | None = None,
                                 max_entries: int = 10000) -> dict:
    """Install a JSVMP runtime probe."""
    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        page = await browser_manager.get_active_page()

        if mode == "transparent":
            hook_path = os.path.join(hooks_dir, "jsvmp_transparent_hook.js")
            if not os.path.exists(hook_path):
                return {"error": "jsvmp_transparent_hook.js not found"}
            with open(hook_path, "r", encoding="utf-8") as f:
                template = f.read()
            hook_js = (template
                .replace("{{SCRIPT_URL}}", script_url.replace('"', '\\"').replace("'", "\\'"))
                .replace("{{MAX_ENTRIES}}", str(max_entries)))
            if persistent:
                await browser_manager.add_persistent_script(f"jsvmp_transparent:{script_url or 'all'}", hook_js)
            page_already_loaded = page.url and page.url != "about:blank"
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {"status": "partial", "mode": "transparent", "warning": f"Evaluate failed: {e}", "persistent": persistent}
            result = {"status": "instrumented", "mode": "transparent",
                     "script_url": script_url or "(all)", "persistent": persistent, "data_location": "window.__mcp_jsvmp_log"}
            if page_already_loaded:
                result["warnings"] = ["Hooks installed on already-loaded page."]
            return result

        elif mode == "proxy":
            if proxy_objects is None:
                proxy_objects = ["navigator", "screen", "history", "localStorage", "sessionStorage", "performance"]
            with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r", encoding="utf-8") as f:
                template = f.read()
            hook_js = (template
                .replace("{{SCRIPT_URL}}", script_url.replace('"', '\\"').replace("'", "\\'"))
                .replace("{{MAX_ENTRIES}}", str(max_entries))
                .replace("{{TRACK_CALLS}}", "true" if track_calls else "false")
                .replace("{{TRACK_PROPS}}", "true" if track_props else "false")
                .replace("{{TRACK_REFLECT}}", "true" if track_reflect else "false")
                .replace("'{{PROXY_OBJECTS}}'", json.dumps(json.dumps(proxy_objects))))
            if persistent:
                await browser_manager.add_persistent_script(f"jsvmp_probe:{script_url or 'all'}", hook_js)
            page_already_loaded = page.url and page.url != "about:blank"
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {"status": "partial", "mode": "proxy", "warning": f"Evaluate failed: {e}", "persistent": persistent}
            result = {"status": "instrumented", "mode": "proxy",
                     "script_url": script_url or "(all)", "persistent": persistent,
                     "data_location": "window.__mcp_jsvmp_log", "warning": "proxy mode is detectable by RS/AK-style anti-bot."}
            if page_already_loaded:
                result.setdefault("warnings", [])
                if isinstance(result.get("warning"), str):
                    result["warnings"].append(result.pop("warning"))
                result["warnings"].append("Hooks installed on already-loaded page.")
            return result
        else:
            return {"error": f"unknown mode '{mode}', use 'proxy' or 'transparent'"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def compare_env(properties: list[str] | None = None) -> dict:
    """Collect browser environment fingerprint data for comparison with Node.js/jsdom."""
    try:
        page = await browser_manager.get_active_page()
        custom_props_js = ""
        if properties:
            custom_props_js = f"""
            const customProps = {json.dumps(properties)};
            for (const prop of customProps) {{
                try {{
                    const val = eval(prop);
                    result.custom[prop] = {{
                        value: typeof val === 'object' ? JSON.stringify(val).substring(0, 500) : String(val),
                        type: typeof val
                    }};
                }} catch(e) {{
                    result.custom[prop] = {{ value: null, error: e.message }};
                }}
            }}"""

        result = await page.evaluate(f"""() => {{
            const result = {{ navigator: {{}}, screen: {{}}, canvas: {{}}, webgl: {{}},
                             audio: {{}}, timing: {{}}, misc: {{}}, custom: {{}} }};
            const navProps = ['userAgent', 'platform', 'language', 'languages',
                'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints',
                'vendor', 'cookieEnabled', 'webdriver'];
            for (const p of navProps) {{
                try {{ result.navigator[p] = {{ value: String(navigator[p]), type: typeof navigator[p] }}; }}
                catch(e) {{ result.navigator[p] = {{ value: null, error: e.message }}; }}
            }}
            const screenProps = ['width', 'height', 'availWidth', 'availHeight', 'colorDepth'];
            for (const p of screenProps) {{
                try {{ result.screen[p] = {{ value: String(screen[p]), type: typeof screen[p] }}; }}
                catch(e) {{ result.screen[p] = {{ value: null, error: e.message }}; }}
            }}
            result.screen.devicePixelRatio = {{ value: window.devicePixelRatio, type: 'number' }};
            result.timing.timezoneOffset = {{ value: new Date().getTimezoneOffset(), type: 'number' }};
            result.timing.timezone = {{ value: Intl.DateTimeFormat().resolvedOptions().timeZone, type: 'string' }};
            {custom_props_js}
            return result;
        }}""")
        return result
    except Exception as e:
        return {"error": str(e)}
