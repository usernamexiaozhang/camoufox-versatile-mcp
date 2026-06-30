"""VMP tracing tools - transparent tap, Worker capture, WebSocket capture."""
from __future__ import annotations

from typing import Any

from camoufox_versatile_mcp.server import mcp, browser_manager


_TRANSPARENT_TAP_JS = r"""
(function(opts) {
    opts = opts || {};
    if (window.__mcp_vmp_tap_installed) return {status: "already_installed"};
    window.__mcp_vmp_tap_installed = true;
    window.__mcp_vmp_tap_log = window.__mcp_vmp_tap_log || [];

    const targetProps = new Set((opts.properties || []).map(p => p));
    const maxEntries = opts.max_entries || 2000;
    const trackCalls = opts.track_calls !== false;

    const _Error = Error;
    const _JSON_stringify = JSON.stringify;
    const _FP_toString = Function.prototype.toString;
    const _Object_defineProperty = Object.defineProperty;
    const _Object_getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    const _Object_getPrototypeOf = Object.getPrototypeOf;

    function preview(v) {
        if (v === null) return "null";
        if (v === undefined) return "undefined";
        const t = typeof v;
        if (t === "function") return "[Function " + (v.name || "") + "]";
        if (t === "object") {
            try {
                const s = _JSON_stringify(v);
                return s && s.length > 200 ? s.substring(0, 200) + "..." : s;
            } catch (e) { return "[object]"; }
        }
        const ss = String(v);
        return ss.length > 200 ? ss.substring(0, 200) + "..." : ss;
    }

    function push(entry) {
        const log = window.__mcp_vmp_tap_log;
        if (log.length >= maxEntries) log.shift();
        log.push(entry);
    }

    // Define a getter wrapper on a prototype for a specific property name.
    function tapPrototypeGetter(obj, propName) {
        if (!obj || typeof obj !== "object" && typeof obj !== "function") return false;
        const proto = _Object_getPrototypeOf(obj);
        if (!proto) return false;
        const desc = _Object_getOwnPropertyDescriptor(proto, propName);
        if (!desc || !desc.get) return false;
        const orig = desc.get;
        if (orig.__mcp_vmp_tapped) return true;
        const wrapped = function() {
            let val;
            try { val = orig.call(this); } catch (e) {
                push({type: "get", prop: propName, error: e.message});
                throw e;
            }
            push({type: "get", prop: propName, value_preview: preview(val),
                  value_type: typeof val});
            return val;
        };
        // toString safe: clone the original source string
        try {
            wrapped.toString = function() { return _FP_toString.call(orig); };
        } catch (e) {}
        try {
            _Object_defineProperty(proto, propName, {
                get: wrapped, configurable: true, enumerable: desc.enumerable,
            });
            Object.defineProperty(wrapped, "__mcp_vmp_tapped", {value: true});
            return true;
        } catch (e) {
            return false;
        }
    }

    // Tap a few high-signal objects' prototype getters.
    const probeObjects = ["navigator", "screen", "history", "location"];
    const probeProps = ["userAgent", "platform", "language", "languages",
                        "hardwareConcurrency", "deviceMemory", "webdriver",
                        "vendor", "width", "height", "availWidth", "availHeight",
                        "colorDepth", "pixelDepth"];
    const targets = (opts.properties && opts.properties.length)
        ? opts.properties
        : probeProps;
    const tappedProps = new Set();
    for (const objName of probeObjects) {
        let obj;
        try { obj = eval(objName); } catch (e) { continue; }
        for (const prop of targets) {
            if (tappedProps.has(prop)) continue;
            if (tapPrototypeGetter(obj, prop)) tappedProps.add(prop);
        }
    }

    // Tap a few common call sites (Date.now, Math.floor, etc.).
    if (trackCalls) {
        try {
            const _Date_now = Date.now;
            Date.now = function() {
                const r = _Date_now.call(Date);
                push({type: "call", call: "Date.now", args: [], ret_preview: preview(r)});
                return r;
            };
            Date.now.toString = function() { return _FP_toString.call(_Date_now); };
        } catch (e) {}
    }

    return {
        status: "tapped",
        tapped_properties: Array.from(tappedProps),
        max_entries: maxEntries,
        data_location: "window.__mcp_vmp_tap_log",
    };
})
"""


@mcp.tool()
async def trace_vmp_for_sign(
    properties: list[str] | None = None,
    trigger_js: str = "",
    max_entries: int = 2000,
    track_calls: bool = True,
    clear_log: bool = True,
    wait_ms: int = 0,
) -> dict:
    """Install a signature-safe tap and record what the VMP reads.

    Args:
        properties: list of property names to watch on navigator/screen/history/
            location prototypes. If empty, uses a default high-signal set.
        trigger_js: optional JS expression to evaluate AFTER the tap is
            installed, to actually invoke the VMP (e.g. "sign(payload)").
        max_entries: cap on the recorded log.
        track_calls: also instrument Date.now / etc.
        clear_log: clear any previous tap log first.
        wait_ms: extra delay after trigger_js before reading the log.

    Returns:
        dict with the tap summary, count of recorded reads, and a
        condensed view of the VMP's reads (prop -> latest value preview).
    """
    try:
        page = await browser_manager.get_active_page()
        if clear_log:
            try:
                await page.evaluate("() => { window.__mcp_vmp_tap_log = []; }")
            except Exception:
                pass
        tap_result = await page.evaluate(
            _TRANSPARENT_TAP_JS,
            [{"properties": properties or [], "max_entries": max_entries,
              "track_calls": track_calls}],
        )
        if trigger_js:
            try:
                await page.evaluate(
                    f"(() => {{ try {{ {trigger_js} }} catch(e) {{ console.error('trigger error:', e.message) }} }})()"
                )
            except Exception as exc:
                return {**tap_result, "trigger_error": str(exc)}
        if wait_ms > 0:
            await page.wait_for_timeout(wait_ms)
        try:
            log = await page.evaluate("() => window.__mcp_vmp_tap_log || []")
        except Exception as exc:
            return {**tap_result, "log_pull_error": str(exc)}
        latest: dict[str, Any] = {}
        for entry in log:
            if entry.get("type") == "get":
                prop = entry.get("prop", "?")
                latest[prop] = entry.get("value_preview", entry.get("error", "?"))
        summary = {
            "total_entries": len(log),
            "distinct_props_read": len({e.get("prop") for e in log if e.get("type") == "get"}),
            "latest_per_prop": latest,
        }
        return {
            "tap": tap_result,
            "trigger_js": trigger_js or None,
            "log_size": len(log),
            "log_first_5": log[:5],
            "log_last_5": log[-5:] if len(log) > 5 else log,
            "summary": summary,
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def get_tap_log(limit: int = 200, clear: bool = False) -> dict:
    """Read what was recorded by trace_vmp_for_sign."""
    try:
        page = await browser_manager.get_active_page()
        log = await page.evaluate("() => window.__mcp_vmp_tap_log || []")
        truncated = len(log) > limit
        out = log[-limit:] if truncated else log
        if clear:
            await page.evaluate("() => { window.__mcp_vmp_tap_log = []; }")
        return {"entries": out, "total": len(log), "truncated": truncated,
                "data_location": "window.__mcp_vmp_tap_log"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Worker and WebSocket capture
# ---------------------------------------------------------------------------

_CAPTURE_WORKER_JS = r"""
(function(opts) {
    opts = opts || {};
    if (window.__mcp_worker_cap_installed) return {status: "already_installed"};
    window.__mcp_worker_cap_installed = true;
    window.__mcp_worker_cap = {
        workers: [],
        events: [],
        importsLog: [],
    };

    function pushEvent(ev) {
        const arr = window.__mcp_worker_cap.events;
        if (arr.length > (opts.max_events || 1000)) arr.shift();
        arr.push(ev);
    }

    const OrigWorker = window.Worker;
    function PatchedWorker(url, options) {
        let urlStr;
        if (typeof url === 'string') urlStr = url;
        else if (url instanceof URL) urlStr = url.href;
        else urlStr = '[object URL/Blob]';
        const meta = {url: urlStr, type: (options && options.type) || 'classic',
                      created_at: Date.now(), id: window.__mcp_worker_cap.workers.length};
        window.__mcp_worker_cap.workers.push(meta);
        pushEvent({type: 'worker_created', id: meta.id, url: urlStr, ts: Date.now()});
        try {
            if (typeof urlStr === 'string' && (urlStr.startsWith('blob:') || urlStr.startsWith('data:'))) {
                fetch(urlStr).then(r => r.text()).then(text => {
                    meta.source_preview = text.slice(0, 4000);
                    meta.source_size = text.length;
                    pushEvent({type: 'worker_source_loaded', id: meta.id, size: text.length});
                }).catch(e => pushEvent({type: 'worker_source_error', id: meta.id, error: e.message}));
            } else if (typeof urlStr === 'string' && /^https?:/.test(urlStr)) {
                meta.note = 'cross-origin: source not fetched';
            }
        } catch (e) { meta.note = 'fetch failed: ' + e.message; }
        const w = new OrigWorker(url, options);
        meta.worker_obj = w;
        return w;
    }
    PatchedWorker.prototype = OrigWorker.prototype;
    window.Worker = PatchedWorker;

    if (typeof window.importScripts === 'function' && !window.importScripts.__mcp_patched) {
        const _is = window.importScripts;
        window.importScripts = function(...urls) {
            window.__mcp_worker_cap.importsLog.push({urls, ts: Date.now()});
            return _is.apply(this, urls);
        };
        window.importScripts.__mcp_patched = true;
    }

    return {status: "installed", patch: "Worker constructor", max_events: opts.max_events || 1000};
})()
"""


_CAPTURE_WS_JS = r"""
(function(opts) {
    opts = opts || {};
    if (window.__mcp_ws_cap_installed) return {status: "already_installed"};
    window.__mcp_ws_cap_installed = true;
    window.__mcp_ws_cap = {
        connections: [],
        events: [],
    };
    function pushEvent(ev) {
        const arr = window.__mcp_ws_cap.events;
        if (arr.length > (opts.max_events || 1000)) arr.shift();
        arr.push(ev);
    }
    const OrigWS = window.WebSocket;
    function PatchedWS(url, protocols) {
        const meta = {url: String(url), protocols, opened_at: Date.now(),
                      id: window.__mcp_ws_cap.connections.length,
                      sent: [], received: []};
        window.__mcp_ws_cap.connections.push(meta);
        pushEvent({type: 'ws_created', id: meta.id, url: String(url), ts: Date.now()});
        const ws = new OrigWS(url, protocols);
        const _send = ws.send.bind(ws);
        ws.send = function(data) {
            let preview;
            try {
                if (data instanceof ArrayBuffer) preview = '[ArrayBuffer len=' + data.byteLength + ']';
                else if (data instanceof Blob) preview = '[Blob]';
                else preview = String(data).slice(0, 1000);
            } catch (e) { preview = '[unprintable]'; }
            meta.sent.push({preview, ts: Date.now()});
            pushEvent({type: 'ws_send', id: meta.id, preview, ts: Date.now()});
            return _send(data);
        };
        ws.addEventListener('message', (ev) => {
            let preview;
            try {
                if (ev.data instanceof ArrayBuffer) preview = '[ArrayBuffer len=' + ev.data.byteLength + ']';
                else if (ev.data instanceof Blob) preview = '[Blob]';
                else preview = String(ev.data).slice(0, 1000);
            } catch (e) { preview = '[unprintable]'; }
            meta.received.push({preview, ts: Date.now()});
            pushEvent({type: 'ws_recv', id: meta.id, preview, ts: Date.now()});
        });
        return ws;
    }
    PatchedWS.prototype = OrigWS.prototype;
    PatchedWS.CONNECTING = OrigWS.CONNECTING;
    PatchedWS.OPEN = OrigWS.OPEN;
    PatchedWS.CLOSING = OrigWS.CLOSING;
    PatchedWS.CLOSED = OrigWS.CLOSED;
    window.WebSocket = PatchedWS;
    return {status: "installed", max_events: opts.max_events || 1000};
})()
"""


@mcp.tool()
async def capture_worker_js(
    duration_ms: int = 3000,
    max_events: int = 1000,
    clear: bool = True,
) -> dict:
    """Patch the page's Worker constructor and record every Worker created.

    Use this when VMP bytecode is delivered via a Web Worker (e.g. the page
    does `new Worker(blob:...)` containing the VMP, then postMessage inputs
    and gets signs back). After the patch is installed, leave the page alone
    for `duration_ms` so VMP can spin up its workers.

    Returns:
        dict with workers[] (id, url, source_preview if blob:), events[].
    """
    try:
        page = await browser_manager.get_active_page()
        if clear:
            await page.evaluate("() => { window.__mcp_worker_cap = {workers:[],events:[],importsLog:[]}; }")
        install = await page.evaluate(_CAPTURE_WORKER_JS, [{"max_events": max_events}])
        await page.wait_for_timeout(duration_ms)
        data = await page.evaluate("() => window.__mcp_worker_cap || {workers:[],events:[]}")
        return {"patch": install, "duration_ms": duration_ms,
                "workers": data.get("workers", []),
                "events": data.get("events", []),
                "importsLog": data.get("importsLog", []),
                "worker_count": len(data.get("workers", []))}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
async def capture_ws_payloads(
    duration_ms: int = 3000,
    url_filter: str = "",
    max_events: int = 1000,
    clear: bool = True,
) -> dict:
    """Patch the page's WebSocket constructor and record every connection's send/recv.

    Use this when VMP fetches its bytecode over WebSocket.

    Args:
        duration_ms: how long to keep the patch alive after install.
        url_filter: if set, only record connections whose URL contains this string.
        max_events: ring buffer cap.

    Returns:
        dict with connections[] (url, sent[], received[]), events[].
    """
    try:
        page = await browser_manager.get_active_page()
        if clear:
            await page.evaluate("() => { window.__mcp_ws_cap = {connections:[],events:[]}; }")
        install = await page.evaluate(_CAPTURE_WS_JS, [{"max_events": max_events}])
        await page.wait_for_timeout(duration_ms)
        data = await page.evaluate("() => window.__mcp_ws_cap || {connections:[],events:[]}")
        conns = data.get("connections", [])
        if url_filter:
            conns = [c for c in conns if url_filter in c.get("url", "")]
            events = [e for e in data.get("events", []) if url_filter in (e.get("url", "") or "")]
        else:
            events = data.get("events", [])
        return {"patch": install, "duration_ms": duration_ms,
                "connections": conns, "events": events,
                "connection_count": len(conns)}
    except Exception as exc:
        return {"error": str(exc)}
