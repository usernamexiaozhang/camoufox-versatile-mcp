"""MCP tools for JS Engine Tracing and HTTP Packet Trace.

Combines three layers:
1. SpiderMonkey tracelogger (C-level, env vars)
2. DevTools CDP ExecutionTracer (native Firefox tracer via WebSocket)
3. JS instrumentation (source-level, no special browser needed)

All three layers work with standard Camoufox - no custom Firefox build needed.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from ..server import mcp, browser_manager
from ..js_engine_trace import (
    TRACE_OUTPUT_DIR,
    TRACE_LEVELS,
    TLLOG_SUBSYSTEMS,
    prepare_tracelogger_env,
    parse_tracelogger_dump,
    start_cdp_tracer,
    stop_cdp_tracer,
    install_js_tracer,
    format_trace_events,
    build_instrumentation_script,
    TracerSession,
)
from ..http_trace import (
    HTTP_TRACE_DIR,
    HttpTraceSession,
    capture_http_via_playwright,
)


# ---------------------------------------------------------------------------
# Session registry (in-memory)
# ---------------------------------------------------------------------------

_trace_sessions: dict[str, TracerSession] = {}
_http_sessions: dict[str, HttpTraceSession] = {}


# ---------------------------------------------------------------------------
# JS Engine Trace tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def js_engine_trace(action: str,
                            level: str = "functions",
                            max_depth: int = 50,
                            trace_values: bool = False,
                            trace_dom_events: bool = True,
                            trace_dom_mutations: bool = False,
                            trace_function_return: bool = False,
                            max_records: int = 50000,
                            cdp_port: int = 9222,
                            page_target_id: Optional[str] = None,
                            session_id: Optional[str] = None,
                            output_format: str = "summary",
                            limit: int = 200) -> dict:
    """JS Engine tracing via three independent layers.

    Actions:
    - install_js: Install source-level JS tracer (no special browser needed)
    - start_cdp: Start DevTools CDP ExecutionTracer (Firefox native tracer)
    - stop: Stop active trace session
    - read: Read and parse trace results
    - status: Show all active sessions

    Layers:
    - Layer 1 (install_js): Source-level via page.evaluate. Safe, works with all browsers.
    - Layer 2 (start_cdp): Native Firefox tracer via CDP WebSocket. Records JS function
      enter/exit, DOM events, mutations, and optional arg values.
    - Layer 3 (tracelogger via ``prepare_tracelogger_env``): C-level SpiderMonkey tracelogger.
      Configures TLLOG/TLOPTIONS env vars for use with the standalone ``js`` shell.
      Not used directly here; call ``prepare_tracelogger_env`` directly if needed.

    Examples:
    - js_engine_trace(action="install_js", max_depth=20, trace_values=True)
    - js_engine_trace(action="start_cdp", level="full", trace_values=True, trace_dom_mutations=True)
    - js_engine_trace(action="read", session_id="abc12345")
    """
    if action == "install_js":
        try:
            page = await browser_manager.get_active_page()
        except RuntimeError:
            return {"error": "Browser not launched. Call launch_browser first."}

        script = build_instrumentation_script(
            trace_values=trace_values,
            max_depth=max_depth,
            filter_pattern="",
        )
        try:
            page.evaluate(script)
        except Exception as e:
            return {"error": f"Failed to install tracer: {e}"}

        sid = str(uuid.uuid4())[:8]
        _trace_sessions[sid] = {
            "mode": "js_instrumented",
            "session_id": sid,
            "config": {"trace_values": trace_values, "max_depth": max_depth},
            "active": True,
        }
        return {
            "status": "installed",
            "mode": "js_instrumented",
            "session_id": sid,
            "note": "Source-level JS tracer installed. Start: __mcp_js_engine_tracer.start(), Stop: __mcp_js_engine_tracer.stop()",
            "instructions": {
                "start": "await page.evaluate('__mcp_js_engine_tracer.start()')",
                "stop": "await page.evaluate('__mcp_js_engine_tracer.stop()')",
                "get": "await page.evaluate('__mcp_js_engine_tracer.getEvents()')",
                "clear": "await page.evaluate('__mcp_js_engine_tracer.clear()')",
            },
        }

    elif action == "start_cdp":
        session = await start_cdp_tracer(
            page_target_id=page_target_id,
            trace_values=trace_values,
            trace_dom_events=trace_dom_events,
            trace_dom_mutations=trace_dom_mutations,
            trace_function_return=trace_function_return,
            max_records=max_records,
            max_depth=max_depth,
            cdp_port=cdp_port,
        )

        if session.config.get("error"):
            return {"error": session.config["error"], "mode": "cdp"}

        sid = session.session_id
        _trace_sessions[sid] = session

        return {
            "status": "started",
            "mode": "cdp",
            "session_id": sid,
            "trace_dir": str(session.trace_dir),
            "config": session.config,
            "note": "Firefox DevTools tracer active. Use action='read' to fetch trace data.",
        }

    elif action == "stop":
        if not session_id:
            return {"error": "session_id required for action='stop'"}
        if session_id not in _trace_sessions:
            return {"error": f"Session not found: {session_id}"}

        session = _trace_sessions[session_id]
        if isinstance(session, dict) and session.get("mode") == "js_instrumented":
            session["active"] = False
            return {"status": "stopped", "session_id": session_id, "mode": "js_instrumented"}
        elif isinstance(session, TracerSession) and session.mode == "cdp":
            result = await stop_cdp_tracer(session)
            return {"status": "stopped", **result}
        return {"error": f"Unknown session type: {session_id}"}

    elif action == "read":
        if not session_id:
            return {"error": "session_id required for action='read'"}
        if session_id not in _trace_sessions:
            return {"error": f"Session not found: {session_id}"}

        session = _trace_sessions[session_id]
        if isinstance(session, dict) and session.get("mode") == "js_instrumented":
            try:
                page = await browser_manager.get_active_page()
                events = await page.evaluate("window.__mcp_js_engine_tracer ? window.__mcp_js_engine_tracer.getEvents() : []")
                if output_format == "events":
                    return {"session_id": session_id, "mode": "js_instrumented", "events": events, "total": len(events)}
                return {"session_id": session_id, "mode": "js_instrumented", **format_trace_events(events, limit)}
            except Exception as e:
                return {"error": f"Failed to read trace: {e}"}
        elif isinstance(session, TracerSession):
            return {
                "session_id": session_id,
                "mode": session.mode,
                "active": session.active,
                "config": session.config,
                "note": "CDP trace data retrieved from DevTools WebSocket. Check DevTools Tracer panel for full data.",
            }
        return {"error": f"Unknown session type: {session_id}"}

    elif action == "status":
        sessions_info = []
        for sid, session in _trace_sessions.items():
            if isinstance(session, TracerSession):
                sessions_info.append({
                    "session_id": sid,
                    "mode": session.mode,
                    "active": session.active,
                    "started_at": session.started_at,
                    "trace_dir": str(session.trace_dir),
                })
            elif isinstance(session, dict):
                sessions_info.append({
                    "session_id": sid,
                    "mode": session.get("mode"),
                    "active": session.get("active", False),
                })
        return {"sessions": sessions_info, "total": len(sessions_info)}

    else:
        available = ["install_js", "start_cdp", "stop", "read", "status"]
        return {"error": f"Unknown action: {action}. Available: {available}"}


# ---------------------------------------------------------------------------
# HTTP Packet Trace helpers
# ---------------------------------------------------------------------------

# Global registry of active page listeners: maps page_id -> (session_id, on_request, on_response, pending_requests)
_page_http_handlers: dict[int, tuple] = {}


def _unregister_page_handlers(page, page_id: int) -> None:
    """Safely remove listeners from a page and clean up the registry entry."""
    info = _page_http_handlers.pop(page_id, None)
    if info is None:
        return
    _sid, h_req, h_resp, _pending = info
    if page is not None:
        try:
            page.remove_listener("request", h_req)
        except Exception:
            pass
        try:
            page.remove_listener("response", h_resp)
        except Exception:
            pass


def _normalize_body_kind(mime: str) -> str:
    """Map a content-type MIME string to a short category name."""
    mime = mime.lower()
    if "json" in mime:
        return "json"
    if "html" in mime:
        return "html"
    if "javascript" in mime:
        return "javascript"
    if "text" in mime:
        return "text"
    if "image" in mime:
        return "image"
    if "font" in mime:
        return "font"
    if "css" in mime:
        return "css"
    return "other"


def _build_http_summary(session: HttpTraceSession, limit: int) -> dict:
    """Build a structured summary from a completed HTTP trace session."""
    by_type: dict[str, int] = {}
    by_method: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    slow_requests: list[dict] = []
    js_requests: list[dict] = []
    api_requests: list[dict] = []

    for entry in session.entries:
        t = entry.get("resourceType", "other")
        m = entry.get("method", "?")
        s = str(entry.get("status", 0))
        k = entry.get("responseBodyKind", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        by_method[m] = by_method.get(m, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        by_kind[k] = by_kind.get(k, 0) + 1

        duration = entry.get("duration") or 0
        if duration > 1000:
            slow_requests.append({"url": entry.get("url", ""), "duration": duration, "status": entry.get("status")})

        url = entry.get("url", "")
        if ".js" in url.lower() or entry.get("resourceType") == "script":
            js_requests.append({"url": url, "status": entry.get("status"), "bodyKind": k})
        if "/api/" in url.lower() or "/v1/" in url.lower() or "/v2/" in url.lower():
            api_requests.append({"url": url, "method": m, "status": entry.get("status"), "bodyKind": k})

    slow_requests.sort(key=lambda x: -x["duration"])
    return {
        "by_resource_type": by_type,
        "by_method": by_method,
        "by_status": by_status,
        "by_body_kind": by_kind,
        "slow_requests_ms": slow_requests[:20],
        "javascript_requests": js_requests[:30],
        "api_requests": api_requests[:30],
        "sample_entries": session.entries[:limit],
    }


async def _http_trace_start(
    page,
    sid: str,
    session: HttpTraceSession,
    page_id: int,
    capture_body: bool,
) -> dict:
    """Start a new HTTP trace session."""
    captured_ref = [{"count": 0}]
    pending_requests: dict[str, dict] = {}

    async def on_request(request):
        captured_ref[0]["count"] += 1
        entry = {
            "seq": captured_ref[0]["count"],
            "ts": int(time.time() * 1000),
            "requestId": request.url,
            "url": request.url,
            "method": request.method,
            "resourceType": request.resource_type,
            "headers": dict(request.headers),
            "postData": request.post_data,
            "postSha256": "",
            "status": 0,
            "responseHeaders": {},
            "responseBody": None,
            "responseBodyKind": "unknown",
            "responseSha256": "",
            "truncated": False,
            "duration": None,
            "initiator": {
                "type": request.resource_type or "other",
                "url": "",
                "line": 0,
                "column": 0,
                "stacktrace": [],
                "stacktraceAvailable": False,
            },
        }
        if request.post_data:
            import hashlib
            entry["postSha256"] = hashlib.sha256(request.post_data.encode()).hexdigest()
        pending_requests[request.url] = entry

        try:
            chain = request.initiator
            if chain and chain.stack:
                filtered = [
                    {
                        "functionName": f.get("url", "").split("/")[-1] or f.get("functionName", ""),
                        "filename": f.get("url", ""),
                        "lineNumber": f.get("lineNumber", 0),
                        "columnNumber": f.get("columnNumber", 0),
                        "asyncCause": f.get("type", ""),
                    }
                    for f in (chain.stack or [])
                    if f.get("url", "").startswith("http")
                ]
                if filtered:
                    entry["initiator"]["stacktrace"] = filtered
                    entry["initiator"]["stacktraceAvailable"] = True
                    entry["initiator"]["type"] = chain.type or "script"
                    entry["initiator"]["url"] = filtered[0]["filename"]
                    entry["initiator"]["line"] = filtered[0]["lineNumber"]
                    entry["initiator"]["column"] = filtered[0]["columnNumber"]
        except Exception:
            pass

    async def on_response(response):
        entry = pending_requests.pop(response.url, None)
        if entry:
            entry["status"] = response.status
            entry["responseHeaders"] = dict(response.headers)
            mime = response.headers.get("content-type") or ""
            entry["responseBodyKind"] = _normalize_body_kind(mime)
            entry["duration"] = int(time.time() * 1000) - entry["ts"]
            session.add_entry(entry)

    page.on("request", on_request)
    page.on("response", on_response)

    _page_http_handlers[page_id] = (sid, on_request, on_response, pending_requests)
    session._page_id = page_id
    _http_sessions[sid] = session

    return {
        "status": "started",
        "session_id": sid,
        "output_dir": str(session.output_dir),
        "capture_body": capture_body,
        "note": "HTTP capture active. Navigate to trigger requests. Call action='stop' to retrieve.",
    }


async def _http_trace_stop(session_id: str | None) -> dict:
    """Stop an active HTTP trace session and flush pending requests."""
    if not session_id:
        if _http_sessions:
            session_id = list(_http_sessions.keys())[-1]
        else:
            return {"error": "No active HTTP sessions. Provide session_id."}

    if session_id not in _http_sessions:
        return {"error": f"Session not found: {session_id}"}

    session = _http_sessions[session_id]
    session.active = False

    flushed = 0
    page_id = getattr(session, "_page_id", None)
    if page_id is not None:
        info = _page_http_handlers.get(page_id)
        if info is not None:
            _sid, _h_req, _h_resp, pending = info
            for entry in pending.values():
                session.add_entry(entry)
                flushed += 1
        _unregister_page_handlers(None, page_id)

    summary = session.save_all()
    summary["flushed_pending"] = flushed
    return {"status": "stopped", **summary}


# ---------------------------------------------------------------------------
# HTTP Packet Trace MCP tool
# ---------------------------------------------------------------------------

@mcp.tool()
async def http_packet_trace(action: str,
                              capture_body: bool = False,
                              capture_pattern: str = "**/*",
                              url_pattern: Optional[str] = None,
                              method: Optional[str] = None,
                              status: Optional[int] = None,
                              resource_type: Optional[str] = None,
                              session_id: Optional[str] = None,
                              output_format: str = "summary",
                              limit: int = 100) -> dict:
    """HTTP Packet trace - captures full HTTP request/response cycles with initiator stack traces.

    Based on RuyiTrace's HttpPacketTrace implementation, capturing:
    - Request/response headers and body
    - Request/response SHA256 hashes
    - Initiator JS stack traces
    - Content policy type (script/xhr/fetch/beacon)
    - Full timing information

    Uses Playwright's route interception for reliable capture at the network layer.

    Actions:
    - start: Start HTTP packet capture on current page
    - stop: Stop capture and return all captured requests
    - read: Read/filter captured session
    - status: Show all HTTP trace sessions

    Examples:
    - http_packet_trace(action="start", capture_body=True)
    - http_packet_trace(action="stop")
    - http_packet_trace(action="read", url_pattern="*api*", method="POST")
    """
    if action == "start":
        try:
            page = await browser_manager.get_active_page()
        except RuntimeError:
            return {"error": "Browser not launched. Call launch_browser first."}

        page_id = id(page)
        if page_id in _page_http_handlers:
            _unregister_page_handlers(page, page_id)

        sid = session_id or str(uuid.uuid4())[:8]
        session = HttpTraceSession(
            session_id=sid,
            output_dir=HTTP_TRACE_DIR / f"session_{sid}",
        )
        session.active = True
        session.capture_body = capture_body

        return await _http_trace_start(page, sid, session, page_id, capture_body)

    elif action == "stop":
        return await _http_trace_stop(session_id)

    elif action == "read":
        if not session_id:
            if _http_sessions:
                session_id = list(_http_sessions.keys())[-1]
            else:
                return {"error": "No HTTP sessions. Provide session_id."}

        if session_id not in _http_sessions:
            return {"error": f"Session not found: {session_id}"}

        session = _http_sessions[session_id]

        if output_format == "entries":
            return {
                "session_id": session_id,
                "total": len(session.entries),
                "entries": session.entries[:limit],
            }

        summary = _build_http_summary(session, limit)
        return {
            "session_id": session_id,
            "output_dir": str(session.output_dir),
            "total_requests": len(session.entries),
            "duration_s": round(time.time() - session.started_at, 2),
            **summary,
        }

    elif action == "status":
        info = [
            {
                "session_id": sid,
                "active": s.active,
                "total": len(s.entries),
                "output_dir": str(s.output_dir),
                "started_at": s.started_at,
                "capture_body": s.capture_body,
            }
            for sid, s in _http_sessions.items()
        ]
        return {"sessions": info, "total": len(info)}

    else:
        return {"error": f"Unknown action: {action}. Available: start, stop, read, status"}


# ---------------------------------------------------------------------------
# Combo: trace JS engine + HTTP simultaneously
# ---------------------------------------------------------------------------

@mcp.tool()
async def trace_js_and_http(duration: int = 10,
                              trace_js: bool = True,
                              trace_http: bool = True,
                              capture_http_body: bool = False,
                              trace_values: bool = False,
                              max_depth: int = 50,
                              trace_dom_events: bool = True,
                              trace_dom_mutations: bool = False,
                              cdp_port: int = 9222) -> dict:
    """Combined JS engine + HTTP packet trace for the specified duration.

    This is the most comprehensive tracing mode, capturing:
    - JS function calls (enter/exit) with optional arg values
    - DOM events triggering JS
    - DOM mutations
    - All HTTP requests with initiator stacks

    Both layers start simultaneously, run for `duration` seconds, then stop automatically.

    Examples:
    - trace_js_and_http(duration=5, trace_http=True)
    - trace_js_and_http(duration=10, trace_js=True, trace_http=True, trace_values=True, capture_http_body=True)
    """
    if trace_js:
        js_result = await js_engine_trace(
            action="install_js",
            trace_values=trace_values,
            max_depth=max_depth,
        )
        if "error" in js_result:
            return {"js_trace_error": js_result["error"]}

        js_session_id = js_result.get("session_id")

        # Start the tracer
        try:
            page = await browser_manager.get_active_page()
            await page.evaluate("__mcp_js_engine_tracer.start()")
        except Exception as e:
            return {"error": f"Failed to start JS tracer: {e}"}
    else:
        js_session_id = None

    if trace_http:
        http_result = await http_packet_trace(
            action="start",
            capture_body=capture_http_body,
            session_id=None,
        )
        if "error" in http_result:
            return {"http_trace_error": http_result["error"]}
        http_session_id = http_result.get("session_id")
    else:
        http_session_id = None

    # Wait for duration
    await asyncio.sleep(duration)

    # Stop both
    js_events = []
    http_summary = {}

    if trace_js and js_session_id:
        try:
            page = await browser_manager.get_active_page()
            await page.evaluate("__mcp_js_engine_tracer.stop()")
            js_events = await page.evaluate("__mcp_js_engine_tracer.getEvents()")
        except Exception:
            pass

    if trace_http and http_session_id:
        http_result = await http_packet_trace(action="stop", session_id=http_session_id)
        http_summary = http_result

    return {
        "status": "completed",
        "duration_s": duration,
        "js_session_id": js_session_id,
        "http_session_id": http_session_id,
        "js_events_count": len(js_events),
        "js_trace_summary": format_trace_events(js_events, 50),
        "http_trace": http_summary,
    }
