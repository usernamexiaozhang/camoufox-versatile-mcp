"""VMP offline replay and environment suggestion tools."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from camoufox_versatile_mcp.server import mcp, browser_manager
from ._node import NODE_BIN, NODE_VERSION


# Common fingerprint props the VMPs we see tend to read.
_COMMON_FP = {
    "navigator.userAgent": {"default": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
                           "type": "string", "hint": "likely feeds UA-derived token"},
    "navigator.platform": {"default": "Win32", "type": "string"},
    "navigator.language": {"default": "en-US", "type": "string"},
    "navigator.languages": {"default": ["en-US", "en"], "type": "array"},
    "navigator.hardwareConcurrency": {"default": 16, "type": "number"},
    "navigator.deviceMemory": {"default": 8, "type": "number"},
    "navigator.maxTouchPoints": {"default": 0, "type": "number"},
    "navigator.vendor": {"default": "", "type": "string"},
    "navigator.webdriver": {"default": False, "type": "boolean",
                            "hint": "often read by anti-bot, should be False"},
    "navigator.cookieEnabled": {"default": True, "type": "boolean"},
    "screen.width": {"default": 1920, "type": "number"},
    "screen.height": {"default": 1080, "type": "number"},
    "screen.availWidth": {"default": 1920, "type": "number"},
    "screen.availHeight": {"default": 1040, "type": "number"},
    "screen.colorDepth": {"default": 24, "type": "number"},
    "screen.pixelDepth": {"default": 24, "type": "number"},
    "window.devicePixelRatio": {"default": 1, "type": "number"},
    "Date.now": {"default": 0, "type": "number", "hint": "stamp used; pass actual value at call time"},
    "Math.random": {"default": 0.5, "type": "number", "hint": "deterministic only if seeded"},
    "document.cookie": {"default": "", "type": "string", "hint": "session cookies"},
    "location.href": {"default": "about:blank", "type": "string"},
    "history.length": {"default": 1, "type": "number"},
    "navigator.plugins": {"default": [], "type": "array"},
    "navigator.mimeTypes": {"default": [], "type": "array"},
    "Intl.DateTimeFormat().resolvedOptions().timeZone": {"default": "America/New_York", "type": "string"},
    "canvas.toDataURL": {"default": "data:image/png;base64,...", "type": "string",
                         "hint": "often used for canvas fingerprint"},
    "WebGLRenderingContext.getParameter": {"default": 0, "type": "number",
                                          "hint": "GPU vendor/renderer strings"},
}


_ENHANCED_NODE_RUNNER_TMPL = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const code = `__VMP_CODE__`;
const entry = "__ENTRY_NAME__";
const inputJson = `__INPUT_JSON__`;
const timeoutMs = __TIMEOUT_MS__;
const suppliedEnvJson = `__SUPPLIED_ENV_JSON__`;

let output = null;
let error = null;
try {
    let sandbox = {
        console, setTimeout, clearTimeout, setInterval, clearInterval,
        Buffer, process, URL, URLSearchParams, TextEncoder, TextDecoder,
        btoa: (s) => Buffer.from(String(s), "binary").toString("base64"),
        atob: (s) => Buffer.from(String(s), "base64").toString("binary"),
    };
    // Inject caller-supplied fingerprint (navigator, screen, document...)
    try {
        const supplied = JSON.parse(suppliedEnvJson);
        if (supplied && typeof supplied === "object") {
            for (const k of Object.keys(supplied)) {
                sandbox[k] = supplied[k];
            }
        }
    } catch (e) { error = "supplied_env parse: " + e.message; }
    if (!error) {
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox, {timeout: timeoutMs, displayErrors: true});
        const fn = sandbox[entry];
        if (typeof fn !== "function") {
            if (sandbox[entry] === undefined) {
                error = `entry function "${entry}" not found in VMP code`;
            } else {
                error = `entry "${entry}" exists but is not a function (type=${typeof sandbox[entry]})`;
            }
        } else {
            const input = JSON.parse(inputJson);
            output = fn(input);
        }
    }
} catch (e) {
    error = e && e.stack ? e.stack : String(e);
}

process.stdout.write(JSON.stringify({output, error}));
"""


def _diff_outputs(replayed: Any, expected: Any) -> dict:
    """Compute a structured diff between two sign outputs."""
    if replayed == expected:
        return {"match": True}
    info: dict[str, Any] = {"match": False}
    info["replayed_preview"] = str(replayed)[:200]
    info["expected_preview"] = str(expected)[:200]
    info["replayed_type"] = type(replayed).__name__
    info["expected_type"] = type(expected).__name__
    if isinstance(replayed, str) and isinstance(expected, str):
        common = 0
        for a, b in zip(replayed, expected):
            if a == b:
                common += 1
            else:
                break
        info["common_prefix_len"] = common
        info["replayed_len"] = len(replayed)
        info["expected_len"] = len(expected)
        info["first_diff_idx"] = common
    return info


@mcp.tool()
async def replay_vmp_offline(
    vmp_code: str,
    entry: str,
    input: dict,
    timeout_ms: int = 5000,
    expected_sign: str = "",
    auto_diff: bool = False,
    supplied_env: dict | None = None,
) -> dict:
    """Run captured VMP code in headless Node and call the entry function.

    Useful for verifying you've correctly extracted the signing algorithm:
    call the VMP's signing function with the inputs a real request would
    use, and check that the output matches what the browser actually sent.

    Args:
        vmp_code: The JavaScript source that defines the VMP / sign function.
        entry: The name of the function exported on global that we should call.
        input: A dict to pass to the entry function. Will be JSON-serialized
            and re-parsed inside the Node sandbox.
        timeout_ms: VM timeout. JS that runs longer triggers a V8 timeout error.
        expected_sign: optional reference sign captured from the browser. When
            set and auto_diff is True, the response includes a structured diff
            (match / common_prefix_len / first_diff_idx).
        auto_diff: if True and expected_sign is set, compute a structured diff.
        supplied_env: optional dict merged into the sandbox before the VMP
            code runs. Use this to feed navigator.userAgent, screen.width,
            Date.now(), document.cookie etc. (see auto_suggest_missing_props).

    Returns:
        dict with status, output, elapsed_ms, optional diff, and optional
        suggestion hint when outputs don't match.
    """
    try:
        supplied_json = json.dumps(supplied_env or {})
        runner_src = (
            _ENHANCED_NODE_RUNNER_TMPL
            .replace("__VMP_CODE__", vmp_code.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
            .replace("__ENTRY_NAME__", entry.replace('"', '\\"'))
            .replace("__INPUT_JSON__", json.dumps(input).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
            .replace("__SUPPLIED_ENV_JSON__", supplied_json.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
            .replace("__TIMEOUT_MS__", str(int(timeout_ms)))
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".cjs", delete=False, encoding="utf-8",
            prefix="vmp_runner_", dir=tempfile.gettempdir(),
        ) as f:
            f.write(runner_src)
            runner_path = f.name
        started = time.time()
        try:
            proc = subprocess.run(
                [NODE_BIN, runner_path],
                capture_output=True, text=True, timeout=timeout_ms / 1000 + 10,
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "error": f"Node process timed out after {timeout_ms} ms",
                "node_path": NODE_BIN, "node_version": NODE_VERSION,
            }
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if not stdout:
            return {
                "status": "error",
                "error": "Node produced no output",
                "stderr": stderr, "returncode": proc.returncode,
                "node_path": NODE_BIN, "node_version": NODE_VERSION,
            }
        try:
            payload = json.loads(stdout)
        except Exception as exc:
            return {
                "status": "error",
                "error": f"failed to parse Node output: {exc}",
                "raw_stdout": stdout[:2000], "stderr": stderr[:1000],
                "node_path": NODE_BIN, "node_version": NODE_VERSION,
            }
        if payload.get("error"):
            return {
                "status": "error",
                "error": payload["error"],
                "node_path": NODE_BIN, "node_version": NODE_VERSION,
                "elapsed_ms": elapsed_ms,
                "hint": "call auto_suggest_missing_props with this error string",
            }
        result: dict[str, Any] = {
            "status": "ok",
            "output": payload.get("output"),
            "node_path": NODE_BIN, "node_version": NODE_VERSION,
            "elapsed_ms": elapsed_ms,
        }
        if auto_diff and expected_sign:
            result["diff"] = _diff_outputs(payload.get("output"), expected_sign)
            if not result["diff"].get("match", False):
                result["suggestion"] = (
                    "outputs differ. Run auto_suggest_missing_props + "
                    "compare_env, then re-call with supplied_env populated."
                )
        return result
    except Exception as exc:
        return {"error": str(exc), "node_path": NODE_BIN, "node_version": NODE_VERSION}


@mcp.tool()
async def auto_suggest_missing_props(
    trace_log: list[dict] | None = None,
    failed_replay_error: str = "",
    supplied_env: dict | None = None,
) -> dict:
    """Guess which props a Node-side VMP replay is missing.

    This combines two signals:
      1. Every property the VMP read in the browser (from trace_vmp_for_sign)
      2. A curated set of high-frequency fingerprint properties that VMPs
         commonly read but that our default Node sandbox doesn't supply

    Args:
        trace_log: the entries returned by trace_vmp_for_sign/get_tap_log
            (optional). If absent, only the curated list is returned.
        failed_replay_error: the error string from a previous failed
            replay_vmp_offline call (e.g. "ReferenceError: navigator is
            not defined"). Used to extract additional hints.
        supplied_env: dict of props you've already fed to replay. We won't
            suggest these.

    Returns:
        dict with `suggestions` (list of {prop, type, default, hint}) and
        a `sandbox_template` you can paste into your VMP replay code.
    """
    try:
        supplied = supplied_env or {}
        seen: dict[str, Any] = {}
        if trace_log:
            for entry in trace_log:
                if entry.get("type") == "get":
                    seen[entry.get("prop", "?")] = entry.get("value_preview")
                elif entry.get("type") == "call":
                    seen[entry.get("call", "?")] = entry.get("ret_preview")
        if failed_replay_error:
            for prop_name in _COMMON_FP:
                tail = prop_name.split(".")[-1]
                if tail and tail in failed_replay_error:
                    seen.setdefault(prop_name, "[from error msg]")
        suggestions: list[dict] = []
        for prop, last_seen in seen.items():
            if prop in supplied:
                continue
            entry = _COMMON_FP.get(prop, {})
            suggestions.append({
                "prop": prop,
                "last_seen_in_browser": last_seen,
                "default_value": entry.get("default"),
                "type": entry.get("type", "unknown"),
                "hint": entry.get("hint", ""),
            })
        ALWAYS_SUGGEST = ["navigator.userAgent", "screen.width", "Date.now",
                          "navigator.webdriver", "document.cookie",
                          "window.devicePixelRatio"]
        for prop in ALWAYS_SUGGEST:
            if prop in supplied:
                continue
            if any(s["prop"] == prop for s in suggestions):
                continue
            entry = _COMMON_FP.get(prop, {})
            suggestions.append({
                "prop": prop, "last_seen_in_browser": None,
                "default_value": entry.get("default"),
                "type": entry.get("type", "unknown"),
                "hint": entry.get("hint", "high-frequency fingerprint prop"),
            })
        sandbox_template = (
            "const sandbox = {\n"
            "  navigator: { userAgent: '...', platform: 'Win32', language: 'en-US',\n"
            "                languages: ['en-US','en'], hardwareConcurrency: 16,\n"
            "                deviceMemory: 8, maxTouchPoints: 0, vendor: '',\n"
            "                webdriver: false, cookieEnabled: true },\n"
            "  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,\n"
            "            colorDepth: 24, pixelDepth: 24 },\n"
            "  document: { cookie: '' },\n"
            "  location: { href: 'https://target/' },\n"
            "  history: { length: 1 },\n"
            "  devicePixelRatio: 1,\n"
            "};\n"
            "// vm.createContext(sandbox);\n"
        )
        return {
            "suggestions": suggestions,
            "supplied_count": len(supplied),
            "sandbox_template": sandbox_template,
            "note": "Run compare_env() in the browser to get accurate values, "
                    "then merge them into the supplied_env dict.",
        }
    except Exception as exc:
        return {"error": str(exc)}
