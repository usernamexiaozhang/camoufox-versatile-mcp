"""WebAssembly disassembly and analysis tools."""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from camoufox_versatile_mcp.server import mcp, browser_manager
from camoufox_versatile_mcp.constants import (
    SUBPROCESS_TIMEOUT_MEDIUM, SUBPROCESS_TIMEOUT_LONG,
    SUBPROCESS_TIMEOUT_NPM, SUBPROCESS_TIMEOUT_SHORT,
    WAT_TRUNCATE_CHARS, WAT_BODY_TRUNCATE_CHARS,
)
from ._node import NODE_BIN


_WABT_PROBE = """const p = (() => { try { require.resolve('wabt'); return 'ok'; } catch(e) { return 'missing'; } })();
process.stdout.write(p);
"""


def _wabt_status() -> dict:
    """Check whether the npm `wabt` package is reachable from Node."""
    try:
        probe = subprocess.run(
            [NODE_BIN, "-e", _WABT_PROBE],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_MEDIUM,
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
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_NPM,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
        )
        status2 = _wabt_status()
        if status2.get("installed"):
            return True, f"installed via npm (rc={proc.returncode})"
        return False, f"install failed: {(proc.stderr or proc.stdout)[:300]}"
    except Exception as exc:
        return False, f"install error: {exc}"


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
            raw = base64.b64decode(res["b64"])
            fetched_size = res.get("size", len(raw))
        elif source_kind == "base64":
            raw = base64.b64decode(wasm_source)
            fetched_size = len(raw)
        elif source_kind == "hex":
            raw = bytes.fromhex(wasm_source)
            fetched_size = len(raw)
        else:
            return {"error": f"unknown source_kind '{source_kind}', use base64/hex/url"}

        wabt_note = None
        if generate_wat and auto_install_wabt:
            ok, msg = _ensure_wabt()
            wabt_note = msg

        with tempfile.NamedTemporaryFile(
            "wb", suffix=".wasm", delete=False,
            prefix="vmp_wasm_", dir=tempfile.gettempdir(),
        ) as f:
            f.write(raw)
            wasm_path = f.name
        out_path = wasm_path + ".json"

        try:
            subprocess.run(
                [NODE_BIN, "-e", f"require('fs').writeFileSync({json.dumps(out_path)},'')"],
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT,
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
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_LONG,
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
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

        result: dict[str, Any] = {
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
            wat = payload["wat"]
            if len(wat) > WAT_TRUNCATE_CHARS:
                result["wat_truncated"] = True
                result["wat"] = wat[:WAT_TRUNCATE_CHARS] + "\n;; ... (truncated, total " + str(len(wat)) + " chars)\n"
                result["wat_total_chars"] = len(wat)
            else:
                result["wat"] = wat
                result["wat_total_chars"] = len(wat)
        return result
    except Exception as exc:
        return {"error": str(exc)}
