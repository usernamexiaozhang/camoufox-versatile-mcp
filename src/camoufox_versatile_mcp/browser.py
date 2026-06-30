"""BrowserManager - manages Camoufox browser lifecycle, contexts, and pages."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os as _os
import platform
import time
from collections import deque
from typing import Any

from playwright.async_api import Page, BrowserContext

_logger = logging.getLogger(__name__)

MAX_LOG_SIZE = 2000
MAX_BODY_SIZE = 200_000


def detect_host_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "windows"


def detect_system_locale() -> str:
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = _os.environ.get(var, "")
        if val and val not in ("C", "POSIX"):
            return val.split(".")[0].replace("_", "-")
    return "en-US"


def _build_from_options(kwargs: dict[str, Any], headless: bool, env_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a merged from_options dict for camoufox launch.

    Applies env_overrides on top of whatever launch_options() produces from
    the current kwargs.  If launch_options() already set env vars, they are
    preserved; env_overrides are layered on top.
    """
    from camoufox.utils import launch_options as _cfx_launch_options
    filtered = {k: v for k, v in kwargs.items() if k != "headless"}
    opts = _cfx_launch_options(headless=headless, **filtered)
    env = dict(opts.get("env", {}))
    if env_overrides:
        env.update(env_overrides)
    opts["env"] = env
    return opts


class BrowserManager:
    default_config: dict[str, Any] = {}

    def __init__(self) -> None:
        self.browser = None
        self.contexts: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}
        self.active_page_name: str | None = None
        self._cm = None
        self._console_logs: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._network_requests: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._request_id_counter = 0
        self._capturing = False
        self._capture_pattern: str = "**/*"
        self._capture_body = False
        self._init_scripts: list[str] = []
        self._persistent_scripts: list[dict] = []
        self._persistent_traces: dict[str, list] = {}
        self._nav_responses: list[dict] = []
        self._route_handlers: dict[str, Any] = {}

    async def launch(self, config: dict | None = None) -> dict:
        if self.browser is not None:
            pages_info = {}
            for name, p in self.pages.items():
                try:
                    pages_info[name] = p.url
                except Exception:
                    _logger.debug("Could not read page URL for status: %s", e)
            return {
                "status": "already_running", "active_page": self.active_page_name,
                "pages": pages_info, "contexts": list(self.contexts.keys()),
                "capturing": self._capturing,
            }

        from camoufox.async_api import AsyncCamoufox
        cfg = {**self.default_config, **(config or {})}
        kwargs: dict[str, Any] = {}

        # --- Basic options -------------------------------------------------
        if cfg.get("proxy"):
            kwargs["proxy"] = cfg["proxy"]

        os_type = cfg.get("os", "auto")
        host_os = detect_host_os()
        os_type = os_type if os_type != "auto" else host_os
        kwargs["os"] = os_type

        if cfg.get("humanize"):
            kwargs["humanize"] = True
        if cfg.get("geoip"):
            kwargs["geoip"] = True
        if cfg.get("block_images"):
            kwargs["block_images"] = True
        if cfg.get("block_webrtc"):
            kwargs["block_webrtc"] = True

        # Cloudflare anti-sandbox flags. Defaults match what camoufox's own
        # test.py smoke test relies on; without these, CF serves a hard
        # challenge with no solvable iframe.
        if cfg.get("i_know_what_im_doing"):
            kwargs["i_know_what_im_doing"] = True
        if cfg.get("disable_coop"):
            kwargs["disable_coop"] = True
        if cfg.get("force_scope_access"):
            kwargs["config"] = {**(kwargs.get("config") or {}), "forceScopeAccess": True}

        locale = cfg.get("locale", "auto")
        if locale == "auto":
            locale = detect_system_locale()
        kwargs["locale"] = locale

        headless = cfg.get("headless", False)
        kwargs["headless"] = headless

        # --- from_options: accumulate env patches from all features -----
        env_patches: dict[str, str] = {}

        # Feature: custom trace_env overrides
        for k, v in (cfg.get("trace_env") or {}).items():
            env_patches[k] = str(v)

        # Feature: remote debugging port
        remote_debugging_port = cfg.get("remote_debugging_port", 0)
        if remote_debugging_port:
            env_patches["MOZ_REMOTE_DEBUGGING_PORT"] = str(remote_debugging_port)
            kwargs["remote_debugging_port"] = remote_debugging_port

        # Feature: property trace (enable_trace)
        if cfg.get("enable_trace"):
            from .property_trace import build_property_trace_config, ensure_dirs, cleanup_old_traces, cleanup_traces, CACHE_DIR
            ensure_dirs()
            cleanup_old_traces(keep_days=7)
            cleanup_traces()
            values_dir = CACHE_DIR / "values"
            if values_dir.exists():
                for f in values_dir.glob("*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
            trace_config = build_property_trace_config()

            # Merge into existing CAMOU_CONFIG if present
            merged_config: dict[str, Any] = {"propertyTrace": trace_config}
            for key in sorted(env_patches.keys()):
                if key.startswith("CAMOU_CONFIG"):
                    try:
                        merged_config = _json.loads(env_patches[key])
                        merged_config["propertyTrace"] = trace_config
                        del env_patches[key]
                        break
                    except (ValueError, TypeError):
                        pass
            env_patches["CAMOU_CONFIG"] = _json.dumps(merged_config)
            env_patches["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"

        # Apply accumulated env patches
        if env_patches:
            kwargs["from_options"] = _build_from_options(kwargs, headless, env_patches)

        # --- Launch --------------------------------------------------------
        self._cm = AsyncCamoufox(**kwargs)
        self.browser = await self._cm.__aenter__()

        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.contexts["default"] = ctx

        if os_type != host_os:
            from .utils.js_helpers import get_font_fallback_script
            await ctx.add_init_script(get_font_fallback_script())

        for script_info in self._persistent_scripts:
            await ctx.add_init_script(script=script_info["content"])

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        self._attach_listeners(page)
        self.pages["default"] = page
        self.active_page_name = "default"

        return {
            "status": "launched", "headless": headless, "os": os_type, "locale": locale,
            "pages": list(self.pages.keys()),
        }

    async def _ensure_browser(self) -> None:
        if self.browser is None:
            await self.launch()

    async def add_persistent_script(self, name: str, content: str) -> None:
        for s in self._persistent_scripts:
            if s["name"] == name:
                s["content"] = content
                break
        else:
            self._persistent_scripts.append({"name": name, "content": content})
        for ctx in self.contexts.values():
            await ctx.add_init_script(script=content)

    def remove_persistent_script(self, name: str) -> bool:
        before = len(self._persistent_scripts)
        self._persistent_scripts = [s for s in self._persistent_scripts if s["name"] != name]
        return len(self._persistent_scripts) < before

    def _attach_listeners(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("request", self._on_request)
        page.on("response", self._on_response_async)
        page.on("response", self._on_response_for_nav)

    def _on_console(self, msg) -> None:
        text = msg.text
        if text and text.startswith("__MCP_TRACE__:"):
            try:
                import json
                payload = json.loads(text[len("__MCP_TRACE__:"):])
                path = payload.pop("__path__", "unknown")
                self._persistent_traces.setdefault(path, []).append(payload)
            except Exception:
                pass
            return
        self._console_logs.append({
            "level": msg.type, "text": text,
            "timestamp": int(time.time() * 1000),
            "location": str(msg.location) if hasattr(msg, "location") else None,
        })

    def _on_request(self, req) -> None:
        if not self._capturing:
            return
        import fnmatch
        if not fnmatch.fnmatch(req.url, self._capture_pattern):
            return
        self._request_id_counter += 1
        entry = {
            "id": self._request_id_counter, "url": req.url, "method": req.method,
            "resource_type": req.resource_type, "request_headers": dict(req.headers),
            "request_post_data": req.post_data, "timestamp": int(time.time() * 1000),
            "status": None, "response_headers": None, "response_body": None, "duration": None,
        }
        self._network_requests.append(entry)

    def _on_response_async(self, resp) -> None:
        if not self._capturing:
            return
        for entry in reversed(self._network_requests):
            if entry["url"] == resp.url and entry["status"] is None:
                entry["status"] = resp.status
                entry["response_headers"] = dict(resp.headers)
                entry["duration"] = int(time.time() * 1000) - entry["timestamp"]
                if self._capture_body:
                    asyncio.ensure_future(self._fetch_response_body(resp, entry))
                break

    async def _fetch_response_body(self, resp, entry: dict) -> None:
        try:
            body_bytes = await resp.body()
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = body_bytes.decode("latin-1")
            if len(body_text) > MAX_BODY_SIZE:
                entry["response_body"] = body_text[:MAX_BODY_SIZE]
                entry["response_body_truncated"] = True
                entry["response_body_total_size"] = len(body_text)
            else:
                entry["response_body"] = body_text
        except Exception as e:
            _logger.debug("Failed to fetch response body: %s", e)
            entry["response_body"] = None

    def _on_response_for_nav(self, resp) -> None:
        try:
            self._nav_responses.append({
                "url": resp.url, "status": resp.status,
                "resource_type": getattr(resp.request, "resource_type", None) if resp.request else None,
                "ts": int(time.time() * 1000),
            })
            if len(self._nav_responses) > 100:
                self._nav_responses = self._nav_responses[-100:]
        except Exception as e:
            _logger.debug("Failed to append nav response: %s", e)

    def reset_nav_responses(self) -> None:
        self._nav_responses = []

    async def create_context(self, name: str, cookies: list[dict] | None = None) -> dict:
        await self._ensure_browser()
        ctx = await self.browser.new_context()
        if cookies:
            await ctx.add_cookies(cookies)
        for script_info in self._persistent_scripts:
            await ctx.add_init_script(script=script_info["content"])
        self.contexts[name] = ctx
        page = await ctx.new_page()
        self._attach_listeners(page)
        self.pages[name] = page
        self.active_page_name = name
        return {"status": "created", "context": name}

    async def get_active_page(self) -> Page:
        await self._ensure_browser()
        if self.active_page_name and self.active_page_name in self.pages:
            return self.pages[self.active_page_name]
        raise RuntimeError("No active page available. Call launch_browser first.")

    async def close(self) -> dict:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.browser = None
        self.contexts.clear()
        self.pages.clear()
        self.active_page_name = None
        self._cm = None
        self._console_logs.clear()
        self._network_requests.clear()
        self._request_id_counter = 0
        self._capturing = False
        self._capture_body = False
        self._init_scripts.clear()
        self._persistent_scripts.clear()
        self._persistent_traces.clear()
        self._nav_responses.clear()
        self._route_handlers.clear()
        return {"status": "closed"}
