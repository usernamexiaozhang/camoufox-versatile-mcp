"""VMP pipeline tools: detect_vmp / trace_vmp_for_sign / replay_vmp_offline.

Three-stage JSVMP analysis pipeline. All three are designed to be chained:

    detect_vmp() -> pick a hint -> trace_vmp_for_sign() -> replay_vmp_offline()

detect_vmp is read-only (no hooks). trace_vmp_for_sign uses a transparent
prototype-getter rewrite that is toString-safe (signature-safe). replay_vmp_offline
spawns a headless Node child process to run the captured VMP source without
needing a browser.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..server import mcp, browser_manager


_NODE_FALLBACK_PATHS = [
    r"D:\software_install\node\node.exe",
    r"C:\Program Files\nodejs\node.exe",
    "node",
]


def _find_node() -> str:
    """Return the first working Node executable."""
    for candidate in _NODE_FALLBACK_PATHS:
        try:
            res = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0:
                return candidate
        except Exception:
            continue
    return "node"  # PATH-resolved fallback


NODE_BIN = _find_node()
NODE_VERSION = ""
try:
    NODE_VERSION = subprocess.run(
        [NODE_BIN, "--version"], capture_output=True, text=True, timeout=5,
    ).stdout.strip()
except Exception:
    pass


# ---------------------------------------------------------------------------
# detect_vmp
# ---------------------------------------------------------------------------

_DETECT_VMP_JS = r"""
() => {
    const out = {
        scripts_analyzed: 0,
        wasm_scripts: 0,
        eval_count: 0,
        new_function_count: 0,
        big_string_array_count: 0,
        avg_identifier_length: 0,
        control_flow_switch_count: 0,
        large_object_literal_count: 0,
        evidence: [],
        hints: [],
    };

    // 1. Wasm presence (the strongest VM signal)
    try {
        const wasmScripts = Array.from(document.querySelectorAll('script'))
            .filter(s => /\.wasm(\b|$)/.test(s.textContent || '') ||
                        /\bWebAssembly\./.test(s.textContent || ''));
        out.wasm_scripts = wasmScripts.length;
        if (wasmScripts.length) {
            out.evidence.push(`found ${wasmScripts.length} script(s) referencing .wasm or WebAssembly`);
            out.hints.push("wasm");
        }
    } catch (e) {}

    // 2. Fetch all inline / external scripts (sample) and crunch features
    const scripts = Array.from(document.scripts || []);
    const inlineSrcs = scripts
        .filter(s => !s.src && s.textContent)
        .map(s => s.textContent);

    let totalIdentifiers = 0;
    let totalIdentChars = 0;
    let bigStringArrays = 0;
    let controlFlowSwitches = 0;
    let largeObjectLiterals = 0;

    for (const src of inlineSrcs) {
        out.scripts_analyzed += 1;

        out.eval_count += (src.match(/\beval\s*\(/g) || []).length;
        out.new_function_count += (src.match(/\bnew\s+Function\s*\(/g) || []).length;

        // Big "string array" / "decoder": const _0xabc = ['xxx','yyy', ...]  with >50 entries
        const stringArrayMatch = src.match(/=\s*\[["'](?:[^"'\\]|\\.)*["'](?:\s*,\s*["'][^"'\\]*["']){50,}/);
        if (stringArrayMatch) bigStringArrays += 1;

        // Control-flow flattening: many switch/case inside a function body
        const switchMatches = src.match(/\bswitch\s*\([^)]+\)\s*\{/g) || [];
        if (switchMatches.length > 5) controlFlowSwitches += 1;

        // Big object literal (often the VMP opcode dispatch table)
        const objMatch = src.match(/\{\s*(?:["']?[A-Za-z_$][\w$]*["']?\s*:\s*['"`]?[^,}\n]{0,200}['"`]?\s*,){50,}/);
        if (objMatch) largeObjectLiterals += 1;

        // Identifier length analysis (obfuscator renames to _0xabc, a1, b2)
        const idents = src.match(/\b[A-Za-z_$][\w$]{0,15}\b/g) || [];
        for (const id of idents) {
            if (id.length < 2) continue;
            // Skip common short JS keywords
            if (id in { if:1, do:1, in:1, of:1, to:1, is:1, it:1, be:1, on:1, at:1,
                       as:1, or:1, an:1, my:1, we:1, by:1, no:1, ok:1, so:1 }) continue;
            totalIdentifiers += 1;
            totalIdentChars += id.length;
        }
    }

    out.big_string_array_count = bigStringArrays;
    out.control_flow_switch_count = controlFlowSwitches;
    out.large_object_literal_count = largeObjectLiterals;
    if (totalIdentifiers > 0) {
        out.avg_identifier_length = +(totalIdentChars / totalIdentifiers).toFixed(2);
    }

    if (out.eval_count + out.new_function_count > 10) {
        out.evidence.push(`eval+new Function total ${out.eval_count + out.new_function_count} calls`);
    }
    if (bigStringArrays > 0) {
        out.evidence.push(`detected ${bigStringArrays} large string-array literal(s)`);
        out.hints.push("obfuscated");
    }
    if (controlFlowSwitches > 0) {
        out.evidence.push(`detected control-flow flattening in ${controlFlowSwitches} script(s)`);
        out.hints.push("obfuscated");
    }
    if (largeObjectLiterals > 0) {
        out.evidence.push(`detected ${largeObjectLiterals} large object literal(s) (likely opcode table)`);
        out.hints.push("vm_dispatch");
    }
    if (out.avg_identifier_length > 0 && out.avg_identifier_length < 3) {
        out.evidence.push(`avg identifier length ${out.avg_identifier_length} (heavy rename)`);
        out.hints.push("obfuscated");
    }

    // Final classification
    let vmp_type = "none";
    if (out.wasm_scripts > 0) {
        vmp_type = "wasm";
    } else if (out.hints.includes("vm_dispatch") || largeObjectLiterals > 0) {
        vmp_type = "vm_dispatch";
    } else if (out.hints.includes("obfuscated")) {
        vmp_type = "obfuscated";
    } else if (out.eval_count + out.new_function_count > 0) {
        vmp_type = "string_eval";
    }

    out.vmp_present = vmp_type !== "none";
    out.vmp_type = vmp_type;

    // Build concrete next-step advice
    if (vmp_type === "wasm") {
        out.recommendation = "use hook_function on WebAssembly.instantiate + fetch .wasm, run via Node";
    } else if (vmp_type === "vm_dispatch") {
        out.recommendation = "use hook_jsvmp_interpreter(mode='transparent') then trace_vmp_for_sign";
    } else if (vmp_type === "obfuscated") {
        out.recommendation = "use instrumentation(action='install', mode='ast') or hook_jsvmp_interpreter";
    } else if (vmp_type === "string_eval") {
        out.recommendation = "use hook_function on eval/new Function to capture generated code";
    } else {
        out.recommendation = "no VMP detected, regular hooking tools suffice";
    }

    return out;
}
"""


@mcp.tool()
async def detect_vmp() -> dict:
    """Detect what kind of JavaScript VM Protection the page is running.

    Returns a structured report with `vmp_type` in:
        - none: no VMP signals found
        - obfuscated: identifier rename + control-flow flattening + string arrays
        - string_eval: heavy use of `eval` / `new Function` to generate code
        - vm_dispatch: large object literal / opcode table / switch dispatch
        - wasm: WebAssembly / .wasm references

    This is purely read-only: no hooks are installed, no scripts are rewritten.
    """
    try:
        page = await browser_manager.get_active_page()
        try:
            result = await page.evaluate(_DETECT_VMP_JS)
        except Exception as exc:
            return {"error": f"detect_vmp evaluate failed: {exc}"}
        result["_meta"] = {
            "node_version": NODE_VERSION,
            "node_path": NODE_BIN,
        }
        return result
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# trace_vmp_for_sign
# ---------------------------------------------------------------------------

# A signature-safe tap that records what the VMP reads (gets/calls).
# Replaces prototype getters via Object.defineProperty, so `toString()` on
# the getter still returns the original code. No Proxy is used. The
# downstream VMP code is unaware of the tap unless it explicitly compares
# getter function identity (rare).
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
        # If the tap says already installed, no need to re-run.
        if trigger_js:
            try:
                await page.evaluate(f"(() => {{ try {{ {trigger_js} }} catch(e) {{ console.error('trigger error:', e.message) }} }})()")
            except Exception as exc:
                return {**tap_result, "trigger_error": str(exc)}
        if wait_ms > 0:
            await page.wait_for_timeout(wait_ms)
        # Pull the log + summarize
        try:
            log = await page.evaluate("() => window.__mcp_vmp_tap_log || []")
        except Exception as exc:
            return {**tap_result, "log_pull_error": str(exc)}
        # Condense: latest read per prop
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


# ---------------------------------------------------------------------------
# replay_vmp_offline
# ---------------------------------------------------------------------------

_NODE_RUNNER_TMPL = r"""
// Auto-generated by camoufox-versatile-mcp replay_vmp_offline
// Runs the captured VMP code in a Node-only environment and calls the entry point.
'use strict';
const fs = require('fs');
const vm = require('vm');

const code = `__VMP_CODE__`;
const entry = "__ENTRY_NAME__";
const inputJson = `__INPUT_JSON__`;
const timeoutMs = __TIMEOUT_MS__;

let output = null;
let error = null;
try {
    const sandbox = {
        console, setTimeout, clearTimeout, setInterval, clearInterval,
        Buffer, process, URL, URLSearchParams, TextEncoder, TextDecoder,
        btoa: (s) => Buffer.from(String(s), "binary").toString("base64"),
        atob: (s) => Buffer.from(String(s), "base64").toString("binary"),
    };
    vm.createContext(sandbox);
    // Run the VMP code in the sandbox.
    vm.runInContext(code, sandbox, {timeout: timeoutMs, displayErrors: true});
    const fn = sandbox[entry];
    if (typeof fn !== "function") {
        // Try to expose as global if VMP code didn't already
        if (typeof sandbox[entry] === "undefined") {
            error = `entry function "${entry}" not found in VMP code`;
        } else {
            error = `entry "${entry}" exists but is not a function (type=${typeof sandbox[entry]})`;
        }
    } else {
        const input = JSON.parse(inputJson);
        output = fn(input);
    }
} catch (e) {
    error = e && e.stack ? e.stack : String(e);
}

process.stdout.write(JSON.stringify({output, error}));
"""


# ---------------------------------------------------------------------------
# Convenience: get_tap_log (read the tap log without re-installing)
# ---------------------------------------------------------------------------


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
# (Optional helper) ensure wabt is available for disassembly
# ---------------------------------------------------------------------------

_WABT_PROBE = """const p = (() => { try { require.resolve('wabt'); return 'ok'; } catch(e) { return 'missing'; } })();
process.stdout.write(p);
"""


def _wabt_status() -> dict:
    """Check whether the npm `wabt` package is reachable from Node."""
    try:
        probe = subprocess.run(
            [NODE_BIN, "-e", _WABT_PROBE],
            capture_output=True, text=True, timeout=10,
        )
        out = (probe.stdout or "").strip()
        return {"installed": out == "ok", "raw": out, "stderr": (probe.stderr or "").strip()[:200]}
    except Exception as exc:
        return {"installed": False, "error": str(exc)}


def _ensure_wabt() -> tuple[bool, str]:
    """If wabt isn't reachable, try `npm install -g wabt` (best effort)."""
    status = _wabt_status()
    if status.get("installed"):
        return True, "already_installed"
    try:
        proc = subprocess.run(
            ["npm", "install", "-g", "wabt"],
            capture_output=True, text=True, timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        status2 = _wabt_status()
        if status2.get("installed"):
            return True, f"installed via npm (rc={proc.returncode})"
        return False, f"install failed: {(proc.stderr or proc.stdout)[:300]}"
    except Exception as exc:
        return False, f"install error: {exc}"


# ---------------------------------------------------------------------------
# disassemble_wasm
# ---------------------------------------------------------------------------

# Node-side runner: try wabt if available; otherwise just dump exports/imports
# via the built-in WebAssembly API and report that disassembly needs wabt.
_WASM_DIS_RUNNER = r"""
'use strict';
const fs = require('fs');

const wasmPath = process.argv[2];
const outPath = process.argv[3];
const wantWat = process.argv[4] === 'wat';

let bytes;
try {
    bytes = fs.readFileSync(wasmPath);
} catch (e) {
    process.stdout.write(JSON.stringify({error: 'read failed: ' + e.message}));
    process.exit(1);
}

// 1. Always-available fallback: parse header + exports/imports
let wasmMod;
try {
    wasmMod = new WebAssembly.Module(bytes);
} catch (e) {
    process.stdout.write(JSON.stringify({error: 'not a valid WebAssembly module: ' + e.message}));
    process.exit(2);
}

const imports = WebAssembly.Module.imports(wasmMod).map(i => ({
    module: i.module, name: i.name, kind: i.kind,
}));
const exportsList = WebAssembly.Module.exports(wasmMod).map(x => ({
    name: x.name, kind: x.kind,
}));
const customSections = WebAssembly.Module.customSections
    ? WebAssembly.Module.customSections(wasmMod, 'name').length
    : 0;

let wat = null;
let wabt_used = false;
let wabt_error = null;

if (wantWat) {
    try {
        const wabtMod = require('wabt');
        const wabt = wabtMod();
        const result = wabt.readWasm(bytes, {readDebugNames: true});
        const watText = result.toText({foldExprs: false, inlineExport: false});
        wat = watText;
        wabt_used = true;
        try { result.destroy(); } catch (e) {}
    } catch (e) {
        wabt_error = (e && e.message) ? e.message : String(e);
    }
}

// Suggest likely entry points: exported functions whose names look like
// "encrypt"/"sign"/"generate"/"main"/"call"/"compute" or are simply the first.
const ENTRY_HINTS = /encrypt|sign|generate|^main$|^call$|compute|do|run|exec|invoke|enc|dec|encode|decode|hash|digest|hmac/i;
let entry_candidates = exportsList
    .filter(e => e.kind === 'function' && ENTRY_HINTS.test(e.name))
    .map(e => e.name);
if (entry_candidates.length === 0) {
    entry_candidates = exportsList.filter(e => e.kind === 'function').slice(0, 5).map(e => e.name);
}

process.stdout.write(JSON.stringify({
    imports, exports: exportsList,
    custom_name_sections: customSections,
    wat, wabt_used, wabt_error,
    entry_candidates,
    size_bytes: bytes.length,
}));
"""


@mcp.tool()
async def disassemble_wasm(
    wasm_source: str,
    source_kind: str = "base64",
    generate_wat: bool = True,
    auto_install_wabt: bool = True,
) -> dict:
    """Disassemble a WebAssembly binary to text (wat) + summarize imports/exports.

    Args:
        wasm_source:
            - if source_kind=='base64': base64-encoded wasm bytes
            - if source_kind=='hex': hex-encoded wasm bytes
            - if source_kind=='url': a URL to fetch the wasm from (uses the
              active page so cookies / headers apply)
        source_kind: see above.
        generate_wat: if True, run wabt to produce WAT. Falls back to exports
            + imports only when wabt is unavailable.
        auto_install_wabt: if True and wabt is missing, attempt `npm i -g wabt`.

    Returns:
        dict with imports, exports, optional wat text, and entry-function
        candidates (for replay_vmp_offline).
    """
    try:
        # 1. Resolve bytes
        if source_kind == "url":
            page = await browser_manager.get_active_page()
            js = """
            async (url) => {
                const r = await fetch(url);
                if (!r.ok) return {error: 'fetch ' + r.status};
                const buf = new Uint8Array(await r.arrayBuffer());
                let bin = '';
                for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
                return {b64: btoa(bin), size: buf.length};
            }
            """
            res = await page.evaluate(js, [wasm_source])
            if not res or "error" in res:
                return {"error": res.get("error", "fetch failed") if res else "fetch returned nothing"}
            import base64
            raw = base64.b64decode(res["b64"])
            fetched_size = res.get("size", len(raw))
        elif source_kind == "base64":
            import base64
            raw = base64.b64decode(wasm_source)
            fetched_size = len(raw)
        elif source_kind == "hex":
            raw = bytes.fromhex(wasm_source)
            fetched_size = len(raw)
        else:
            return {"error": f"unknown source_kind '{source_kind}', use base64/hex/url"}

        # 2. Ensure wabt if asked
        wabt_note = None
        if generate_wat and auto_install_wabt:
            ok, msg = _ensure_wabt()
            wabt_note = msg

        # 3. Write to temp + run Node disassembler
        with tempfile.NamedTemporaryFile(
            "wb", suffix=".wasm", delete=False,
            prefix="vmp_wasm_", dir=tempfile.gettempdir(),
        ) as f:
            f.write(raw)
            wasm_path = f.name
        out_path = wasm_path + ".json"

        try:
            proc = subprocess.run(
                [NODE_BIN, "-e", f"require('fs').writeFileSync({json.dumps(out_path)},'')"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            pass

        runner_path = None
        with tempfile.NamedTemporaryFile(
            "w", suffix=".cjs", delete=False, encoding="utf-8",
            prefix="vmp_wasm_runner_", dir=tempfile.gettempdir(),
        ) as f:
            f.write(_WASM_DIS_RUNNER)
            runner_path = f.name

        try:
            proc = subprocess.run(
                [NODE_BIN, runner_path, wasm_path, out_path, "wat" if generate_wat else "no"],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
            )
        except subprocess.TimeoutExpired:
            return {"error": "wasm disassembler timed out (30s)",
                    "size_bytes": fetched_size, "wabt_note": wabt_note}

        stdout = proc.stdout.strip()
        if not stdout:
            return {"error": "no output from disassembler",
                    "stderr": (proc.stderr or "")[:500], "wabt_note": wabt_note,
                    "size_bytes": fetched_size}
        try:
            payload = json.loads(stdout)
        except Exception as exc:
            return {"error": f"failed to parse disassembler output: {exc}",
                    "raw_stdout": stdout[:1500], "stderr": (proc.stderr or "")[:500],
                    "size_bytes": fetched_size}

        result = {
            "size_bytes": fetched_size,
            "imports": payload.get("imports", []),
            "exports": payload.get("exports", []),
            "entry_candidates": payload.get("entry_candidates", []),
            "custom_name_sections": payload.get("custom_name_sections", 0),
            "wabt_used": payload.get("wabt_used", False),
            "wabt_error": payload.get("wabt_error"),
            "wabt_note": wabt_note,
        }
        if payload.get("wat"):
            # Truncate large wat to keep responses sane
            wat = payload["wat"]
            if len(wat) > 100_000:
                result["wat_truncated"] = True
                result["wat"] = wat[:100_000] + "\n;; ... (truncated, total " + str(len(wat)) + " chars)\n"
                result["wat_total_chars"] = len(wat)
            else:
                result["wat"] = wat
                result["wat_total_chars"] = len(wat)
        return result
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# capture_worker_js + capture_ws_payloads
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

    // Patch Worker constructor
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
            // If it's a blob: URL, fetch and store its text content
            if (typeof urlStr === 'string' && (urlStr.startsWith('blob:') || urlStr.startsWith('data:'))) {
                fetch(urlStr).then(r => r.text()).then(text => {
                    meta.source_preview = text.slice(0, 4000);
                    meta.source_size = text.length;
                    pushEvent({type: 'worker_source_loaded', id: meta.id, size: text.length});
                }).catch(e => pushEvent({type: 'worker_source_error', id: meta.id, error: e.message}));
            } else if (typeof urlStr === 'string' && /^https?:/.test(urlStr)) {
                // Skip cross-origin to avoid CORS mess, but record the URL
                meta.note = 'cross-origin: source not fetched';
            }
        } catch (e) { meta.note = 'fetch failed: ' + e.message; }
        const w = new OrigWorker(url, options);
        // Patch onmessage on the worker side is not possible without
        // entering the worker context; we record via main-world hook only.
        meta.worker_obj = w;
        return w;
    }
    PatchedWorker.prototype = OrigWorker.prototype;
    // Copy static constants if any (none in Worker)
    window.Worker = PatchedWorker;

    // Patch importScripts (only visible inside the worker; but if the page
    // also calls importScripts via something like eval, we'd see it here.)
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

    Use this when VMP fetches its bytecode over WebSocket (e.g. an onmessage
    pushes an opaque blob that is then assembled into a Worker or eval'd).

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


# ---------------------------------------------------------------------------
# auto_suggest_missing_props
# ---------------------------------------------------------------------------

# Common fingerprint props the VMPs we see tend to read. Each has a default
# value (what a vanilla browser returns) plus a small note.
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


@mcp.tool()
async def auto_suggest_missing_props(
    trace_log: list[dict] | None = None,
    failed_replay_error: str = "",
    supplied_env: dict | None = None,
) -> dict:
    """Given a trace log (or just an error string), guess which props a
    Node-side VMP replay is missing.

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
        a `sandbox_template` you can paste into your VMP replay code to
        cover the missing props.
    """
    try:
        supplied = supplied_env or {}
        # 1. Collect props mentioned in the trace log
        seen: dict[str, Any] = {}
        if trace_log:
            for entry in trace_log:
                if entry.get("type") == "get":
                    seen[entry.get("prop", "?")] = entry.get("value_preview")
                elif entry.get("type") == "call":
                    seen[entry.get("call", "?")] = entry.get("ret_preview")
        # 2. Add hints from the error message
        if failed_replay_error:
            for prop_name in _COMMON_FP:
                tail = prop_name.split(".")[-1]
                if tail and tail in failed_replay_error:
                    seen.setdefault(prop_name, "[from error msg]")
        # 3. Subtract what's already supplied
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
        # 4. Suggest a few always-relevant props the user might've forgotten
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
        # 5. Build a sandbox template
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


# ---------------------------------------------------------------------------
# Enhanced replay_vmp_offline: with auto_diff + supplied_env
# ---------------------------------------------------------------------------

# We rewrite the existing replay_vmp_offline to optionally:
#   - take expected_sign and auto-diff
#   - take supplied_env to inject navigator/screen into the sandbox
#   - return a structured diff on mismatch so callers can call
#     auto_suggest_missing_props automatically.
# Backwards compatibility: existing 4-arg calls keep working.


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
    # Try byte-level diff if both are strings
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
                cwd=str(Path(__file__).resolve().parent.parent.parent),
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
        # Diff
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
