"""VMP detection - reads page scripts and classifies protection type."""
from __future__ import annotations

from camoufox_versatile_mcp.server import mcp, browser_manager
from ._node import NODE_BIN, NODE_VERSION


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
