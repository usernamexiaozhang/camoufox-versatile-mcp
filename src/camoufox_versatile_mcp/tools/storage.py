from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager


@mcp.tool()
async def cookies(action: str, domain: str | None = None, cookies_list: list[dict] | None = None,
                 name: str | None = None) -> dict | list:
    """Cookie management."""
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context

        if action == "get":
            all_cookies = await ctx.cookies()
            if domain:
                all_cookies = [c for c in all_cookies if domain in c.get("domain", "")]
            return all_cookies
        elif action == "set":
            if not cookies_list:
                return {"error": "cookies_list is required for action='set'"}
            await ctx.add_cookies(cookies_list)
            return {"status": "set", "count": len(cookies_list)}
        elif action == "delete":
            all_cookies = await ctx.cookies()
            to_keep = []
            deleted = 0
            for c in all_cookies:
                should_delete = False
                if name and c["name"] == name:
                    should_delete = True
                if domain and domain in c.get("domain", ""):
                    should_delete = True
                if not name and not domain:
                    should_delete = True
                if should_delete:
                    deleted += 1
                else:
                    to_keep.append(c)
            await ctx.clear_cookies()
            if to_keep:
                await ctx.add_cookies(to_keep)
            return {"status": "deleted", "count": deleted}
        else:
            return {"error": f"unknown action: {action}. Use get/set/delete"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_storage(storage_type: str = "local") -> dict:
    """Get the contents of localStorage or sessionStorage."""
    try:
        page = await browser_manager.get_active_page()
        if storage_type == "local":
            data = await page.evaluate("""() => {
                const obj = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    obj[key] = localStorage.getItem(key);
                }
                return obj;
            }""")
        elif storage_type == "session":
            data = await page.evaluate("""() => {
                const obj = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    obj[key] = sessionStorage.getItem(key);
                }
                return obj;
            }""")
        else:
            return {"error": f"Invalid storage_type: {storage_type}. Use 'local' or 'session'."}
        return {"storage_type": storage_type, "data": data, "count": len(data)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_state(save_path: str) -> dict:
    """Export the complete browser state to a JSON file."""
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        await ctx.storage_state(path=save_path)
        return {"status": "exported", "path": save_path}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def import_state(state_path: str) -> dict:
    """Import browser state from a JSON file by creating a new context."""
    try:
        await browser_manager._ensure_browser()
        ctx = await browser_manager.browser.new_context(storage_state=state_path)
        ctx_name = f"imported_{len(browser_manager.contexts)}"
        browser_manager.contexts[ctx_name] = ctx
        page = await ctx.new_page()
        browser_manager._attach_listeners(page)
        browser_manager.pages[ctx_name] = page
        browser_manager.active_page_name = ctx_name
        return {"status": "imported", "context": ctx_name, "path": state_path}
    except Exception as e:
        return {"error": str(e)}
