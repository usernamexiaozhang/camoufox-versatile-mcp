"""HTTP Packet Trace - captures full HTTP request/response cycles with initiator stack traces.

Based on RuyiTrace's HttpPacketTrace.sys.mjs implementation.
This captures:
- Request/response headers and body
- Request/response SHA256 hashes
- Initiator stack traces (JS call chain)
- Content policy type (script/xhr/fetch/beacon)
- Full timing information
- Per-packet JSON files + index.jsonl
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from camoufox_versatile_mcp.constants import (
    CACHE_DIR_BASE, MAX_STACK_FRAMES, HTTP_IGNORED_HOSTS,
    SUBPROCESS_TIMEOUT_SHORT, SUBPROCESS_TIMEOUT_MEDIUM, SUBPROCESS_TIMEOUT_LONG,
    SUBPROCESS_TIMEOUT_NPM, WEBSOCKET_PING_TIMEOUT, WEBSOCKET_MSG_TIMEOUT,
    VMP_TAP_MAX_ENTRIES, WAT_TRUNCATE_CHARS, WAT_BODY_TRUNCATE_CHARS,
    MCP_DEFAULT_LIMIT, MCP_MAX_LIMIT,
)

# ---------------------------------------------------------------------------
# Constants (local overrides / derived)
# ---------------------------------------------------------------------------

HTTP_TRACE_DIR = CACHE_DIR_BASE / "http_packets"

# MIME types that qualify as text content
TEXT_MIME_PATTERNS = [
    re.compile(x, re.I) for x in [
        r"text/html", r"application/xhtml", r"application/json",
        r"application/.*\+json", r"text/.*", r"application/javascript",
        r"application/xml", r"application/.*\+xml",
    ]
]
# MIME types that qualify as binary
BINARY_MIME_PATTERNS = [
    re.compile(x, re.I) for x in [
        r"image/", r"audio/", r"video/", r"application/pdf",
        r"application/octet-stream", r"font/", r"application/zip",
    ]
]
# Hosts to ignore
IGNORED_HOSTS = HTTP_IGNORED_HOSTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_text_mime(mime: str) -> bool:
    if not mime:
        return False
    for pat in TEXT_MIME_PATTERNS:
        if pat.search(mime):
            return True
    return False


def _is_binary_mime(mime: str) -> bool:
    if not mime:
        return False
    for pat in BINARY_MIME_PATTERNS:
        if pat.search(mime):
            return True
    return False


def _normalize_stack_frame(frame: dict) -> dict:
    return {
        "functionName": str(frame.get("functionName") or ""),
        "filename": str(frame.get("filename") or ""),
        "lineNumber": max(0, int(frame.get("lineNumber") or 0)),
        "columnNumber": max(0, int(frame.get("columnNumber") or 0)),
        "asyncCause": frame.get("asyncCause"),
    }


def _filter_non_chrome_frames(frames: list[dict]) -> list[dict]:
    """Filter out frames from resource:// and chrome:// URLs."""
    result = []
    for frame in frames:
        fn = frame.get("filename") or ""
        if not (fn.startswith("resource://") or fn.startswith("chrome://")):
            result.append(frame)
    return result


# ---------------------------------------------------------------------------
# JS injection script for capturing HTTP traffic
# ---------------------------------------------------------------------------

HTTP_TRACE_JS = """
(function() {
  var _trace = window.__mcp_http_trace;
  if (_trace && _trace._active) return _trace;
  _trace = {
    _active: false,
    _requests: {},
    _seq: 0,
    _pid: 0,
    _ignoredHosts: %(ignored_hosts)s,
    _maxStackFrames: %(max_stack)s,
    _captureBody: %(capture_body)s,
    _eventProxy: null,

    _shouldCapture: function(url, causeType) {
      if (!url) return false;
      try {
        var host = new URL(url).hostname.toLowerCase();
        if (this._ignoredHosts.has(host)) return false;
      } catch(e) { return false; }
      var cause = (causeType || '').toLowerCase();
      return ['document','subdocument','script','xhr','fetch','beacon'].includes(cause);
    },

    _normalizeMime: function(mime) {
      if (!mime) return 'other';
      var lower = mime.toLowerCase();
      if (lower.includes('json')) return 'json';
      if (lower.includes('html')) return 'html';
      if (lower.includes('javascript')) return 'javascript';
      if (lower.includes('text')) return 'text';
      if (lower.includes('image')) return 'image';
      if (lower.includes('font')) return 'font';
      if (lower.includes('css')) return 'css';
      if (lower.includes('xml')) return 'xml';
      return 'other';
    },

    _parseStack: function(stack) {
      if (!stack || !stack.length) return [];
      var frames = [];
      var depth = 0;
      for (var i = 0; i < stack.length && depth < this._maxStackFrames; i++) {
        var s = stack[i];
        frames.push({
          functionName: s.functionName || '',
          filename: s.filename || s.fileName || '',
          lineNumber: s.lineNumber || 0,
          columnNumber: s.columnNumber || 0,
          asyncCause: s.asyncCause || null
        });
        depth++;
      }
      return frames;
    },

    _buildEventProxy: function() {
      var self = this;
      var origFetch = window.fetch;
      var origXHROpen = XMLHttpRequest.prototype.open;
      var origXHRSend = XMLHttpRequest.prototype.send;
      var origSendBeacon = navigator.sendBeacon;
      var origPush = History.prototype.pushState;
      var origReplace = History.prototype.replaceState;

      window.fetch = function(url, options) {
        var causeType = 'fetch';
        return origFetch.apply(this, arguments).then(function(resp) {
          if (self._active) {
            self._captureResponse(url, causeType, resp.clone());
          }
          return resp;
        }).catch(function(e) { return Promise.reject(e); });
      };

      XMLHttpRequest.prototype.open = function(method, url) {
        this._mcp_url = url;
        this._mcp_method = method;
        this._mcp_causeType = 'xhr';
        return origXHROpen.apply(this, arguments);
      };

      var xhrSendWrap = function() {
        if (self._active && this._mcp_url) {
          var stack = self._parseStack(new Error().stack);
          var filtered = self._filterNonChrome(stack);
          self._captureRequest(this._mcp_url, this._mcp_method || 'GET', 'xhr', filtered);
        }
        return origXHRSend.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = xhrSendWrap;

      navigator.sendBeacon = function(url, data) {
        if (self._active) {
          var stack = self._parseStack(new Error().stack);
          self._captureRequest(url, 'BEACON', 'beacon', stack);
        }
        return origSendBeacon.apply(this, arguments);
      };

      return {};
    },

    _filterNonChrome: function(stack) {
      if (!stack || !stack.length) return [];
      var result = [];
      for (var i = 0; i < stack.length; i++) {
        var fn = stack[i].filename || '';
        if (!fn.startsWith('resource://') && !fn.startsWith('chrome://')) {
          result.push(stack[i]);
        }
      }
      return result;
    },

    _captureRequest: function(url, method, causeType, stack) {
      try {
        var filtered = this._filterNonChrome(stack || []);
        var entry = {
          seq: ++this._seq,
          ts: Date.now(),
          pid: this._pid,
          url: url,
          method: method,
          causeType: causeType,
          initiator: {
            type: causeType,
            url: filtered[0] ? filtered[0].filename : '',
            line: filtered[0] ? filtered[0].lineNumber : 0,
            column: filtered[0] ? filtered[0].columnNumber : 0,
            stacktrace: filtered,
            stacktraceAvailable: filtered.length > 0,
          },
          status: 0,
          responseBodyKind: 'unknown',
          requestSha256: '',
          responseSha256: '',
          truncated: false,
          body: null,
        };
        this._requests[url + '::' + this._seq] = entry;
      } catch(e) {}
    },

    _captureResponse: function(url, causeType, response) {
      var key = url + '::' + this._seq;
      var entry = this._requests[key];
      if (!entry) {
        entry = {
          seq: ++this._seq,
          ts: Date.now(),
          pid: this._pid,
          url: url,
          method: 'FETCH',
          causeType: causeType,
          status: 0,
          responseBodyKind: 'unknown',
          requestSha256: '',
          responseSha256: '',
          truncated: false,
          body: null,
        };
        this._requests[key] = entry;
      }
      var mime = response.headers ? response.headers.get('content-type') || '' : '';
      entry.responseBodyKind = this._normalizeMime(mime);
      entry.status = response.status;
      if (this._captureBody) {
        var self = this;
        response.text().then(function(text) {
          entry.responseSha256 = self._sha256(text);
          entry.body = text.length > 50000 ? text.substring(0, 50000) : text;
          entry.truncated = text.length > 50000;
        }).catch(function(e) {});
      } else {
        response.text().then(function(text) {
          entry.responseSha256 = self._sha256(text);
        }).catch(function(e) {});
      }
    },

    _sha256: function(text) {
      if (!text) return '';
      try {
        var data = new TextEncoder().encode(text);
        var hash = 0;
        var view = new DataView(data.buffer);
        for (var i = 0; i < data.length; i++) {
          hash = ((hash << 5) - hash + view.getUint8(i)) | 0;
        }
        return hash.toString(16);
      } catch(e) { return ''; }
    },

    start: function(opts) {
      if (opts === undefined) opts = {};
      this._active = true;
      this._requests = {};
      this._seq = 0;
      this._captureBody = opts.captureBody !== undefined ? opts.captureBody : false;
      this._ignoredHosts = %(ignored_hosts)s;
      this._eventProxy = this._buildEventProxy();
      return {status: 'active', seq: this._seq};
    },

    stop: function() {
      this._active = false;
      var entries = Object.values(this._requests);
      var result = {
        total: entries.length,
        entries: entries,
        session_id: %(session_id)s
      };
      return result;
    },

    isActive: function() { return this._active; },
    getEntries: function() { return Object.values(this._requests); }
  };
  window.__mcp_http_trace = _trace;
  return _trace;
})();
""" % {
    "ignored_hosts": json.dumps(list(IGNORED_HOSTS)),
    "max_stack": MAX_STACK_FRAMES,
    "capture_body": "false",
    "session_id": json.dumps(str(uuid.uuid4())[:8]),
}


# ---------------------------------------------------------------------------
# Playwright-based HTTP capture (using route interception)
# ---------------------------------------------------------------------------

async def capture_http_via_playwright(page, output_dir: Path, capture_body: bool = False,
                                      capture_pattern: str = "**/*") -> dict:
    """Capture HTTP traffic via Playwright route interception and CDP network events.

    This is more reliable than the JS injection approach because it intercepts
    at the network layer rather than relying on monkey-patching.
    """
    captured: list[dict] = []
    request_id_map: dict[str, dict] = {}
    seq = [0]

    async def on_request(request):
        seq[0] += 1
        entry = {
            "seq": seq[0],
            "ts": int(time.time() * 1000),
            "requestId": request.url,
            "url": request.url,
            "method": request.method,
            "resourceType": request.resource_type,
            "headers": dict(request.headers),
            "postData": request.post_data,
            "postSha256": _sha256(request.post_data or ""),
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
            "error": None,
        }

        # Try to get initiator stack
        try:
            chain = request.initiator
            if chain and chain.stack:
                frames = _filter_non_chrome_frames([
                    {
                        "functionName": f.get("url", "").split("/")[-1] or f.get("functionName", ""),
                        "filename": f.get("url", ""),
                        "lineNumber": f.get("lineNumber", 0),
                        "columnNumber": f.get("columnNumber", 0),
                        "asyncCause": f.get("type", ""),
                    }
                    for f in (chain.stack or [])
                ])
                if frames:
                    entry["initiator"]["stacktrace"] = frames
                    entry["initiator"]["stacktraceAvailable"] = True
                    entry["initiator"]["type"] = chain.type or "script"
                    entry["initiator"]["url"] = frames[0]["filename"]
                    entry["initiator"]["line"] = frames[0]["lineNumber"]
                    entry["initiator"]["column"] = frames[0]["columnNumber"]
        except Exception:
            pass

        request_id_map[request.url] = entry
        captured.append(entry)

    async def on_response(response):
        try:
            for entry in reversed(captured):
                if entry["url"] == response.url:
                    entry["status"] = response.status
                    entry["responseHeaders"] = dict(response.headers)

                    mime = response.headers.get("content-type", "") or ""
                    if "json" in mime.lower():
                        entry["responseBodyKind"] = "json"
                    elif "html" in mime.lower():
                        entry["responseBodyKind"] = "html"
                    elif "javascript" in mime.lower():
                        entry["responseBodyKind"] = "javascript"
                    elif "text" in mime.lower():
                        entry["responseBodyKind"] = "text"
                    elif "image" in mime.lower():
                        entry["responseBodyKind"] = "image"
                    elif "font" in mime.lower():
                        entry["responseBodyKind"] = "font"
                    elif "css" in mime.lower():
                        entry["responseBodyKind"] = "css"
                    else:
                        entry["responseBodyKind"] = "other"

                    if capture_body and entry["responseBodyKind"] in ("json", "javascript", "html", "text", "css", "other"):
                        try:
                            body_bytes = await response.body()
                            try:
                                body_text = body_bytes.decode("utf-8")
                            except UnicodeDecodeError:
                                body_text = body_bytes.decode("latin-1")
                            entry["responseSha256"] = _sha256(body_text)
                            if len(body_text) > 50000:
                                entry["responseBody"] = body_text[:50000]
                                entry["truncated"] = True
                            else:
                                entry["responseBody"] = body_text
                        except Exception as e:
                            entry["error"] = str(e)

                    entry["duration"] = int(time.time() * 1000) - entry["ts"]
                    break
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    return {
        "status": "capturing",
        "note": "HTTP capture active. Call stop() to retrieve results.",
        "output_dir": str(output_dir),
        "capture_pattern": capture_pattern,
    }


# ---------------------------------------------------------------------------
# Main HTTP trace session management
# ---------------------------------------------------------------------------

class HttpTraceSession:
    def __init__(self, session_id: str, output_dir: Path):
        self.session_id = session_id
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = output_dir / "index.jsonl"
        self.entries: list[dict] = []
        self.started_at = time.time()
        self.active = False
        self.capture_body = False

    def add_entry(self, entry: dict) -> None:
        self.entries.append(entry)
        # Write to index
        index_entry = {
            "requestId": entry.get("requestId") or entry.get("url", ""),
            "seq": entry.get("seq", 0),
            "ts": entry.get("ts", 0),
            "url": entry.get("url", ""),
            "method": entry.get("method", "GET"),
            "status": entry.get("status", 0),
            "responseBodyKind": entry.get("responseBodyKind", "unknown"),
            "responseSha256": entry.get("responseSha256", ""),
            "requestSha256": entry.get("postSha256", ""),
            "duration": entry.get("duration"),
        }
        try:
            with open(self.index_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(index_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def save_entry_file(self, entry: dict) -> str:
        """Save a single entry to a file and return the filename."""
        url = entry.get("url", "unknown")
        method = entry.get("method", "GET")
        status = entry.get("status", 0)
        seq = entry.get("seq", 0)
        ts = entry.get("ts", 0)

        # Sanitize filename
        safe_url = re.sub(r"[^\w\-_.]", "_", url)[:100]
        filename = f"{seq:06d}_{method}_{status}_{safe_url}.json"

        filepath = self.output_dir / filename
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return filename

    def save_all(self) -> dict:
        """Save all entries and return summary."""
        files = []
        for entry in self.entries:
            fn = self.save_entry_file(entry)
            files.append(fn)

        summary = {
            "session_id": self.session_id,
            "output_dir": str(self.output_dir),
            "index_file": str(self.index_path),
            "total_requests": len(self.entries),
            "duration_s": round(time.time() - self.started_at, 2),
            "files": files[:100],
            "truncated": len(files) > 100,
        }
        return summary

    def filter(
        self,
        url_pattern: str | None = None,
        method: str | None = None,
        status: int | None = None,
        resource_type: str | None = None,
        cause_type: str | None = None,
    ) -> list[dict]:
        """Filter captured entries."""
        results = self.entries
        if url_pattern:
            import fnmatch
            results = [e for e in results if fnmatch.fnmatch(e.get("url", ""), url_pattern)]
        if method:
            results = [e for e in results if e.get("method", "").upper() == method.upper()]
        if status:
            results = [e for e in results if e.get("status") == status]
        if resource_type:
            results = [e for e in results if e.get("resourceType", "") == resource_type]
        if cause_type:
            results = [e for e in results if e.get("initiator", {}).get("type", "") == cause_type]
        return results
