"""JS Engine Tracing - SpiderMonkey tracelogger + DevTools CDP ExecutionTracer.

Uses three independent trace layers, all from standard Firefox (no RuyiTrace C++ patches needed):

Layer 1: SpiderMonkey tracelogger (TLLOG/TLOPTIONS env vars)
  - C-level opcode/bytecode tracing via built-in tracelogger
  - Output: tl-data.json (flat format or graph format)

Layer 2: DevTools CDP ExecutionTracer (Firefox native)
  - JS function enter/exit via Firefox's built-in tracer
  - Controlled via CDP WebSocket (remote debugging port)
  - Output: JSON traces with call stacks, args, return values

Layer 3: JS runtime instrumentation (Page.evaluate)
  - Source-level hook injection via page evaluation
  - Best for understanding high-level JS logic
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from camoufox_versatile_mcp.constants import (
    CACHE_DIR_BASE,
    DEFAULT_TLLOG, DEFAULT_TLOPTIONS,
    TLLOG_SUBSYSTEMS, TRACE_LEVELS,
    SUBPROCESS_TIMEOUT_SHORT, SUBPROCESS_TIMEOUT_MEDIUM, SUBPROCESS_TIMEOUT_LONG,
    WEBSOCKET_PING_TIMEOUT, WEBSOCKET_MSG_TIMEOUT,
    MAX_EVENTS_PER_SESSION,
)
from camoufox_versatile_mcp.deprecation import log_deprecated_call

# ---------------------------------------------------------------------------
# Derived paths
# ---------------------------------------------------------------------------

TRACE_OUTPUT_DIR = CACHE_DIR_BASE / "js_engine"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TracerSession:
    session_id: str
    mode: str  # "tracelogger" | "cdp" | "instrumented"
    started_at: float
    trace_dir: Path
    pid: Optional[int] = None
    cdp_port: int = 9222
    cdp_ws_url: Optional[str] = None
    tl_log_file: Optional[Path] = None
    tl_dump_file: Optional[Path] = None
    active: bool = False
    config: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Directory management
# ---------------------------------------------------------------------------

def ensure_trace_dir() -> Path:
    TRACE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return TRACE_OUTPUT_DIR


# ---------------------------------------------------------------------------
# Layer 1: SpiderMonkey tracelogger (C-level tracing)
# ---------------------------------------------------------------------------

def prepare_tracelogger_env(
    level: str = "functions",
    custom_log: str | None = None,
    custom_options: str | None = None,
    trace_dir: Path | None = None,
) -> TracerSession:
    """Prepare SpiderMonkey tracelogger environment variables.

    This does NOT start a live trace.  It configures TLLOG/TLOPTIONS/TLFILENAME
    environment variables and records them in a TracerSession so callers can
    either:

    1. Spawn a standalone ``js`` shell with these env vars to get C-level
       opcode/bytecode tracing (the SpiderMonkey tracelogger's primary use case).
    2. Pass the session to a browser launch that reads ``from_options.env`` to
       propagate the vars into Firefox.

    For browser-side tracing use ``start_cdp_tracer`` (DevTools ExecutionTracer)
    or ``install_js_tracer`` (source-level instrumentation).

    Returns:
        TracerSession with env vars recorded in ``config["env_*"]`` fields.
    """
    if trace_dir is None:
        trace_dir = ensure_trace_dir()

    session_id = str(uuid.uuid4())[:8]
    session_dir = trace_dir / f"tl_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    log_subsystems = custom_log or TRACE_LEVELS.get(level, DEFAULT_TLLOG)
    options = custom_options or DEFAULT_TLOPTIONS

    log_file = session_dir / "tl-data.json"
    dump_file = session_dir / "tl-dump-flat.txt"

    env = os.environ.copy()
    env["TLLOG"] = log_subsystems
    env["TLOPTIONS"] = options
    env["TLFILENAME"] = str(log_file)

    session = TracerSession(
        session_id=session_id,
        mode="tracelogger",
        started_at=time.time(),
        trace_dir=session_dir,
        tl_log_file=log_file,
        tl_dump_file=dump_file,
        active=True,
        config={
            "level": level,
            "log": log_subsystems,
            "options": options,
            "env_tllog": log_subsystems,
            "env_tloptions": options,
            "env_tlfilename": str(log_file),
        },
    )
    return session


def start_tracelogger(level: str = "functions", custom_log: str | None = None,
                       custom_options: str | None = None,
                       trace_dir: Path | None = None) -> TracerSession:
    """Deprecated alias for ``prepare_tracelogger_env``.

    .. deprecated:: 1.1.0
       The name was misleading (no process is actually started).  Use
       ``prepare_tracelogger_env`` instead.  This alias will be removed in v1.2.0.
    """
    log_deprecated_call("start_tracelogger", "prepare_tracelogger_env", removed_in="1.2.0")
    return prepare_tracelogger_env(level=level, custom_log=custom_log,
                                   custom_options=custom_options, trace_dir=trace_dir)


def parse_tracelogger_dump(dump_path: Path, max_lines: int = 5000) -> dict:
    """Parse a SpiderMonkey tracelogger flat dump file.

    The dump is a plain text format with entries like:
    [timestamp] <thread> OPERATION: details
    """
    if not dump_path.exists():
        return {"error": f"File not found: {dump_path}"}

    operations: dict[str, int] = {}
    samples = []
    entry_count = 0

    with open(dump_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if entry_count >= max_lines:
                break
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Parse operation type
            parts = line.split(" ", 2)
            if len(parts) >= 2:
                op = parts[1] if parts[1] else parts[0]
                operations[op] = operations.get(op, 0) + 1

            samples.append(line)
            entry_count += 1

    return {
        "format": "tracelogger_flat",
        "file": str(dump_path),
        "total_entries": entry_count,
        "sample_lines": samples[:100],
        "operation_counts": dict(sorted(operations.items(), key=lambda x: -x[1])[:50]),
        "truncated": entry_count >= max_lines,
    }


# ---------------------------------------------------------------------------
# Layer 2: CDP DevTools ExecutionTracer
# ---------------------------------------------------------------------------

async def _find_firefox_cdp_port(browser_pid: int | None = None) -> int | None:
    """Try to find Firefox CDP port from its remote debugging info.

    Firefox exposes CDP via --remote-debugging-port. We check the known
    ports and also try to get the browser PID from the environment.
    """
    import socket

    for port in (9222, 9223, 9322, 9422):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(("localhost", port))
            sock.close()
            if result == 0:
                return port
        except Exception:
            pass
    return None


async def _get_cdp_targets(port: int) -> list[dict]:
    """Get available CDP targets (pages/iframes) from Firefox."""
    import urllib.request
    import urllib.error

    try:
        url = f"http://localhost:{port}/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=SUBPROCESS_TIMEOUT_SHORT) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


async def _cdp_send(ws_url: str, method: str, params: dict | None = None, id: int = 1) -> dict | None:
    """Send a raw CDP command over WebSocket."""
    try:
        import websockets
        async with websockets.connect(ws_url, ping_timeout=WEBSOCKET_PING_TIMEOUT) as ws:
            msg = {"id": id, "method": method}
            if params:
                msg["params"] = params
            await ws.send(json.dumps(msg))
            resp = await asyncio.wait_for(ws.recv(), timeout=WEBSOCKET_MSG_TIMEOUT)
            return json.loads(resp)
    except Exception:
        return None


async def start_cdp_tracer(
    page_target_id: str | None = None,
    trace_values: bool = False,
    trace_dom_events: bool = True,
    trace_dom_mutations: bool = False,
    trace_function_return: bool = False,
    max_records: int = 50000,
    max_depth: int = 50,
    cdp_port: int = 9222,
) -> TracerSession:
    """Start Firefox DevTools native tracer via CDP.

    Uses Firefox's built-in ExecutionTracer (JavaScriptTracer) via CDP protocol.
    This is the same tracer that RuyiTrace's TraceConsole.exe wraps, but we
    access it directly via DevTools WebSocket API.

    The CDP WebSocket for each page is at:
    ws://localhost:{port}/devtools/page/{targetId}
    """
    session_id = str(uuid.uuid4())[:8]
    trace_dir = ensure_trace_dir() / f"cdp_{session_id}"
    trace_dir.mkdir(parents=True, exist_ok=True)

    session = TracerSession(
        session_id=session_id,
        mode="cdp",
        started_at=time.time(),
        trace_dir=trace_dir,
        cdp_port=cdp_port,
        active=True,
        config={
            "trace_values": trace_values,
            "trace_dom_events": trace_dom_events,
            "trace_dom_mutations": trace_dom_mutations,
            "trace_function_return": trace_function_return,
            "max_records": max_records,
            "max_depth": max_depth,
        },
    )

    targets = await _get_cdp_targets(cdp_port)
    if not targets:
        session.config["error"] = "No CDP targets found. Is Firefox running with --remote-debugging-port?"
        return session

    target = None
    if page_target_id:
        for t in targets:
            if t.get("id") == page_target_id or t.get("targetId") == page_target_id:
                target = t
                break
    else:
        for t in targets:
            if t.get("type") == "page":
                target = t
                break

    if not target:
        session.config["error"] = f"No suitable target found. Available: {[t.get('id',t.get('targetId','?')) for t in targets]}"
        return session

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        session.config["error"] = "Target has no WebSocket URL"
        return session

    session.cdp_ws_url = ws_url

    params = {
        "maxRecords": max_records,
        "maxDepth": max_depth,
        "traceValues": trace_values,
        "traceDOMEvents": trace_dom_events,
        "traceDOMMutations": trace_dom_mutations,
        "traceFunctionReturn": trace_function_return,
    }

    result = await _cdp_send(ws_url, "Tracer.startTracing", params)
    if result and result.get("result") is not None:
        session.config["started"] = True
        session.config["target_id"] = target.get("id") or target.get("targetId")
    else:
        session.config["cdp_response"] = result

    return session


async def stop_cdp_tracer(session: TracerSession) -> dict:
    """Stop the CDP tracer and return the accumulated trace data."""
    if not session.cdp_ws_url:
        return {"error": "No active CDP session"}

    result = await _cdp_send(session.cdp_ws_url, "Tracer.stopTracing", {})
    session.active = False

    if result:
        return {"result": result.get("result"), "session_id": session.session_id}
    return {"result": None, "session_id": session.session_id}


# ---------------------------------------------------------------------------
# Layer 3: JS instrumentation (source-level, no CDP needed)
# ---------------------------------------------------------------------------

JS_INSTRUMENTED_TRACER_TEMPLATE = """
(function() {
  var _tracer = window.__mcp_js_engine_tracer;
  if (!_tracer) {
    _tracer = {
      _callId: 0,
      _maxDepth: {max_depth},
      _traceValues: {trace_values},
      _active: false,
      _events: [],
      _callStack: [],
      _filter: {filter_pattern},
      _enter: function(fn, ctx, args, loc) {{
        if (!this._active) return;
        if (this._callStack.length >= this._maxDepth) return;
        var depth = this._callStack.length;
        var callId = ++this._callId;
        var argValues = this._traceValues ? Array.prototype.slice.call(args) : null;
        this._callStack.push({{id: callId, depth: depth, fn: fn, loc: loc, args: argValues}});
        this._events.push({{
          t: Date.now(),
          k: 'enter',
          id: callId,
          depth: depth,
          fn: fn || 'anonymous',
          loc: loc,
          nargs: args ? args.length : 0
        }});
      }},
      _exit: function(fn, ctx, ret, isThrow) {{
        if (!this._active) return;
        var frame = this._callStack.pop();
        if (!frame) return;
        this._events.push({{
          t: Date.now(),
          k: isThrow ? 'throw' : 'exit',
          id: frame.id,
          depth: frame.depth,
          fn: fn || frame.fn || 'anonymous',
          ret: this._traceValues ? (isThrow ? String(ret) : ret) : null,
          isThrow: isThrow
        }});
      }},
      start: function() {{ this._active = true; this._events = []; this._callStack = []; this._callId = 0; }},
      stop: function() {{ this._active = false; return this.getEvents(); }},
      getEvents: function() {{ return this._events; }},
      clear: function() {{ this._events = []; this._callStack = []; this._callId = 0; }}
    };
    window.__mcp_js_engine_tracer = _tracer;
  }
  return _tracer;
})();
"""


def build_instrumentation_script(
    trace_values: bool = False,
    max_depth: int = 50,
    filter_pattern: str = "",
) -> str:
    """Build the JS instrumentation script for source-level tracing."""
    return JS_INSTRUMENTED_TRACER_TEMPLATE.format(
        max_depth=max_depth,
        trace_values="true" if trace_values else "false",
        filter_pattern=f"/{filter_pattern}/" if filter_pattern else "null",
    )


def install_js_tracer(
    page,  # playwright Page object
    trace_values: bool = False,
    max_depth: int = 50,
    filter_pattern: str = "",
) -> dict:
    """Install JS engine tracer into a page via evaluate."""
    script = build_instrumentation_script(trace_values, max_depth, filter_pattern)
    try:
        result = page.evaluate(script)
        return {"status": "installed", "tracer": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Utility: dump and format traces
# ---------------------------------------------------------------------------

def format_trace_events(events: list[dict], limit: int = 200) -> dict:
    """Format a list of trace events into a readable summary."""
    if not events:
        return {"total": 0, "summary": "No events captured"}

    by_kind: dict[str, int] = {}
    by_fn: dict[str, int] = {}
    depth_hist: dict[int, int] = {}

    for e in events:
        kind = e.get("k", "?")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        fn = e.get("fn", "?")
        by_fn[fn] = by_fn.get(fn, 0) + 1
        depth = e.get("depth", 0)
        depth_hist[depth] = depth_hist.get(depth, 0) + 1

    hot_functions = dict(sorted(by_fn.items(), key=lambda x: -x[1])[:30])

    return {
        "total_events": len(events),
        "by_kind": by_kind,
        "hot_functions": hot_functions,
        "depth_histogram": depth_hist,
        "sample_events": events[:limit],
        "truncated": len(events) > limit,
    }
