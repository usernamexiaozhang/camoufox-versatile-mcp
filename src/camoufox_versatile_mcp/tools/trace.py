"""Engine-level property access tracing tools."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ..server import mcp, browser_manager
from ..property_trace import (
    CONTROL_DIR, TRACES_DIR,
    list_session_files, load_events,
    build_summary, build_timeline, build_sequence,
    filter_events, write_control_all, cleanup_traces,
)


def _is_trace_enabled() -> bool:
    if not CONTROL_DIR.exists():
        return False
    return len(list(CONTROL_DIR.glob("control-*.cmd"))) > 0


@mcp.tool()
async def trace_property_access(duration: int = 10, mode: str = "summary",
                                filter_object: Optional[str] = None, search_query: Optional[str] = None,
                                limit: int = 1000, bucket_ms: int = 500,
                                collect_values: bool = False) -> dict:
    """Engine-level DOM property access tracing (JSVMP-undetectable)."""
    if not _is_trace_enabled():
        return {
            "error": "engine_trace_not_available",
            "message": "当前浏览器不支持引擎层 DOM 属性追踪，需要 camoufox-reverse 定制版浏览器。",
            "install_guide": "https://github.com/WhiteNightShadow/camoufox-reverse/releases",
        }

    if duration > 0:
        cleanup_traces()
        write_control_all("off")
        await asyncio.sleep(0.5)
        write_control_all("on")
        await asyncio.sleep(0.3)
        await asyncio.sleep(duration)
        write_control_all("off")
        await asyncio.sleep(0.5)
    else:
        write_control_all("off")
        await asyncio.sleep(0.5)

    events: list[dict] = []
    for f in list_session_files():
        events.extend(load_events(f))

    if not events:
        return {"mode": "error", "reason": "No trace events captured during the window."}

    events = filter_events(events, filter_object, search_query)

    if mode == "summary":
        result = build_summary(events, duration)
    elif mode == "timeline":
        result = build_timeline(events, duration, bucket_ms)
    elif mode in ("sequence", "search"):
        result = build_sequence(events, limit)
    else:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}

    if collect_values and result.get("by_property"):
        values = await _collect_property_values(result["by_property"])
        result["values"] = values

    return result


@mcp.tool()
async def list_trace_files(limit: int = 20) -> dict:
    """List all trace files on disk."""
    if not TRACES_DIR.exists():
        return {"files": [], "total": 0, "traces_dir": str(TRACES_DIR)}

    all_files = []
    for f in TRACES_DIR.glob("*.jsonl"):
        try:
            parts = f.stem.split("_")
            file_pid = int(parts[0]) if parts else -1
            session_id = int(parts[1]) if len(parts) > 1 else -1
        except (IndexError, ValueError):
            continue

        size_kb = f.stat().st_size / 1024
        all_files.append({"path": str(f), "pid": file_pid, "session_id": session_id,
                        "size_kb": round(size_kb, 1), "mtime": f.stat().st_mtime})

    all_files.sort(key=lambda x: x["mtime"], reverse=True)
    return {"traces_dir": str(TRACES_DIR), "total": len(all_files),
            "returned": min(len(all_files), limit), "files": all_files[:limit]}


@mcp.tool()
async def query_trace_file(file_path: str, mode: str = "summary",
                           filter_object: Optional[str] = None, search_query: Optional[str] = None,
                           limit: int = 1000, bucket_ms: int = 500) -> dict:
    """Query a specific historical trace file."""
    path = Path(file_path)
    if not path.exists():
        return {"mode": "error", "reason": f"File not found: {file_path}"}

    events = load_events(path)
    events = filter_events(events, filter_object, search_query)

    duration_s = 0
    if events:
        duration_s = (events[-1].get("t", 0) // 1000) + 1

    if mode == "summary":
        return build_summary(events, duration_s)
    elif mode == "timeline":
        return build_timeline(events, duration_s, bucket_ms)
    elif mode in ("sequence", "search"):
        return build_sequence(events, limit)
    else:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}


async def _collect_property_values(by_property: list[dict]) -> dict:
    from ..property_trace import CACHE_DIR
    values_dir = CACHE_DIR / "values"
    values_dir.mkdir(parents=True, exist_ok=True)

    path_to_js = {
        "navigator.userAgent": "navigator.userAgent",
        "navigator.platform": "navigator.platform",
        "navigator.language": "navigator.language",
        "navigator.languages": "JSON.stringify(navigator.languages)",
        "navigator.hardwareConcurrency": "navigator.hardwareConcurrency",
        "navigator.maxTouchPoints": "navigator.maxTouchPoints",
        "screen.rect": "JSON.stringify({w:screen.width,h:screen.height})",
        "screen.colorDepth": "screen.colorDepth",
        "window.innerWidth": "window.innerWidth",
        "window.innerHeight": "window.innerHeight",
        "window.devicePixelRatio": "window.devicePixelRatio",
        "document.cookie.get": "document.cookie",
        "history.length": "history.length",
    }

    paths = [p["path"] for p in by_property]
    js_parts = []
    for path in paths:
        js_expr = path_to_js.get(path)
        if js_expr:
            safe_key = path.replace(".", "_").replace("-", "_")
            js_parts.append(f'try{{r.{safe_key}={js_expr}}}catch(e){{r.{safe_key}="ERROR:"+e.message}}')

    if not js_parts:
        return {}

    js_code = "(() => { var r = {}; " + ";".join(js_parts) + "; return r; })()"

    try:
        page = await browser_manager.get_active_page()
        raw = await page.evaluate(js_code)
    except Exception as e:
        return {"error": f"evaluate_js failed: {e}"}

    values = {}
    for path in paths:
        safe_key = path.replace(".", "_").replace("-", "_")
        val = raw.get(safe_key)
        if val is None:
            continue
        val_str = str(val)
        if len(val_str) > 500:
            filename = f"{safe_key}.txt"
            filepath = values_dir / filename
            filepath.write_text(val_str, encoding="utf-8")
            values[path] = f"[file:{filepath}] ({len(val_str)} chars)"
        else:
            values[path] = val

    return values
