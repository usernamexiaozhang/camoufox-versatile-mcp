from __future__ import annotations

from ..server import mcp, browser_manager


def _build_error_response(error_msg: str) -> dict:
    hint = None
    if ("expected expression" in error_msg) and ("keyword" in error_msg):
        hint = "Wrap in IIFE if you need var/let/const/function declarations: (() => { var x = 1; return x; })()"
    elif "JSON.parse" in error_msg and "unexpected character" in error_msg:
        hint = "The expression likely returned a non-JSON-serializable value. Wrap result in plain object."
    elif "timeout" in error_msg.lower() or "exceeded" in error_msg.lower():
        hint = "If your expression returns a Promise, set await_promise=True. Otherwise simplify the expression."
    elif "target closed" in error_msg.lower() or "page closed" in error_msg.lower():
        hint = "Call launch_browser() + navigate() to establish a new session before running evaluate_js."
    return {"type": "error", "error": error_msg, "hint": hint}


@mcp.tool()
async def evaluate_js(expression: str, await_promise: bool = True) -> dict:
    """Execute an arbitrary JavaScript expression in the page context and return the result."""
    import json as _json
    import re as _re

    def _clean_str(s: str) -> tuple[str, list[str]]:
        warns: list[str] = []
        if not isinstance(s, str):
            return s, warns
        if s.startswith("\ufeff"):
            s = s.lstrip("\ufeff")
            warns.append("stripped BOM")
        try:
            s.encode("utf-8")
        except UnicodeEncodeError:
            s = s.encode("utf-8", "replace").decode("utf-8")
            warns.append("replaced invalid unicode")
        stripped = s.strip()
        if stripped != s and stripped:
            s = stripped
            warns.append("trimmed whitespace")
        return s, warns

    def _parse_smart(s: str, warns: list[str]) -> tuple:
        if not isinstance(s, str) or not s.strip():
            return s, None
        first_char = s.lstrip()[:1]
        if first_char not in '[{"':
            return s, None
        try:
            return _json.loads(s), None
        except Exception as e1:
            e1_msg = str(e1)[:100]
        cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        if cleaned != s:
            try:
                val = _json.loads(cleaned)
                warns.append("stripped control chars")
                return val, None
            except Exception:
                pass
        if s.startswith('"') and s.endswith('"'):
            try:
                unwrapped = _json.loads(s)
                if isinstance(unwrapped, str) and unwrapped.lstrip()[:1] in '[{"':
                    try:
                        val = _json.loads(unwrapped)
                        warns.append("unwrapped double-encoded JSON")
                        return val, None
                    except Exception:
                        pass
            except Exception:
                pass
        return s, f"all JSON parse strategies failed: {e1_msg}"

    try:
        page = await browser_manager.get_active_page()
        try:
            if await_promise:
                raw = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await (async () => {{ return {expression}; }})();
                        const t = typeof r;
                        if (r === undefined || r === null) {{
                            return {{ result: null, type: t, is_undefined: r === undefined }};
                        }}
                        if (t === 'symbol') {{
                            return {{ result: null, type: 'symbol', symbol_desc: r.toString() }};
                        }}
                        if (t === 'object' || t === 'function') {{
                            try {{
                                return {{ result: JSON.parse(JSON.stringify(r)), type: t }};
                            }} catch(e) {{
                                return {{ result: String(r), type: t, serialization_warning: e.message }};
                            }}
                        }}
                        return {{ result: r, type: t }};
                    }} catch(e) {{
                        return {{ error: e.message, type: 'error' }};
                    }}
                }}""")
            else:
                raw = await page.evaluate(f"""() => {{
                    try {{
                        const r = (() => {{ return {expression}; }})();
                        const t = typeof r;
                        if (r === undefined || r === null) {{
                            return {{ result: null, type: t, is_undefined: r === undefined }};
                        }}
                        if (t === 'symbol') {{
                            return {{ result: null, type: 'symbol', symbol_desc: r.toString() }};
                        }}
                        if (t === 'object' || t === 'function') {{
                            try {{
                                return {{ result: JSON.parse(JSON.stringify(r)), type: t }};
                            }} catch(e) {{
                                return {{ result: String(r), type: t, serialization_warning: e.message }};
                            }}
                        }}
                        return {{ result: r, type: t }};
                    }} catch(e) {{
                        return {{ error: e.message, type: 'error' }};
                    }}
                }}""")
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if any(kw in low for kw in ("unexpected", "serialize", "cloneable", "circular", "cyclic")):
                try:
                    handle = await page.evaluate_handle(expression)
                    descr = await handle.evaluate("""
                        obj => ({
                          type: typeof obj,
                          ctor: obj && obj.constructor ? obj.constructor.name : null,
                          keys: obj && typeof obj === 'object' ? Object.keys(obj).slice(0, 40) : null,
                          preview: (function() {
                            try { var s = JSON.stringify(obj); return s ? s.substring(0, 500) : String(obj).substring(0, 500); }
                            catch(e) { return String(obj).substring(0, 500); }
                          })()
                        })
                    """)
                    try:
                        await handle.dispose()
                    except Exception:
                        pass
                    return {"type": "handle_fallback", "value": descr, "warnings": [f"direct evaluate failed, used handle fallback: {msg[:200]}"]}
                except Exception as e2:
                    return _build_error_response(f"both paths failed: {msg[:200]} / {e2}")
            raise

        if isinstance(raw, dict) and "error" in raw:
            return _build_error_response(raw["error"])

        result_val = raw.get("result") if isinstance(raw, dict) else raw
        js_type = raw.get("type") if isinstance(raw, dict) else None
        warnings_list: list[str] = []

        ser_warn = raw.get("serialization_warning") if isinstance(raw, dict) else None
        if ser_warn:
            warnings_list.append(f"JS serialization fallback: {ser_warn}")

        if result_val is None:
            is_undef = raw.get("is_undefined") if isinstance(raw, dict) else False
            symbol_desc = raw.get("symbol_desc") if isinstance(raw, dict) else None
            if symbol_desc:
                return {"type": "primitive", "value": None, "value_raw": symbol_desc,
                        "warnings": [f"Expression returned a Symbol ({symbol_desc}). Symbols are not JSON-serializable."]}
            if is_undef or js_type == "undefined":
                return {"type": "primitive", "value": None, "value_raw": "undefined",
                        "warnings": ["Expression returned undefined. Wrap logic in IIFE with explicit return."]}
            return {"type": "primitive", "value": None, "value_raw": None, "warnings": None}

        if isinstance(result_val, str):
            cleaned, w = _clean_str(result_val)
            warnings_list.extend(w)
            parsed, parse_err = _parse_smart(cleaned, warnings_list)
            if parse_err is None and parsed is not cleaned:
                return {"type": "json", "value": parsed,
                        "value_raw": result_val if warnings_list else None,
                        "warnings": warnings_list if warnings_list else None}
            if parse_err is not None:
                warnings_list.append(parse_err)
            return {"type": "primitive", "value": cleaned,
                    "value_raw": result_val if warnings_list else None,
                    "warnings": warnings_list if warnings_list else None}

        return {"type": "primitive" if not isinstance(result_val, (dict, list)) else "json",
                "value": result_val, "warnings": warnings_list if warnings_list else None}
    except Exception as e:
        return _build_error_response(str(e))
