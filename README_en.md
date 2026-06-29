# camoufox-versatile-mcp

[中文](README.md) | [English](README_en.md)

> MCP Server built on Camoufox anti-detection browser, featuring **versatile captcha auto-solving** and **JSVMP signature-recovery pipeline**.

Forked from [camoufox-reverse-mcp](https://github.com/WhiteNightShadow/camoufox-reverse-mcp) with new capabilities:
- Built-in **captcha auto-detection and solving** (backed by `camoufox_captcha`)
- Brand-new **JSVMP offline reproduction pipeline**: WASM disassembly / Worker source capture / WS traffic capture / offline replay + auto-diff

---

## Installation

```bash
pip install -e .
```

## Cursor Configuration

```json
{
  "mcpServers": {
    "camoufox-versatile": {
      "command": "python",
      "args": ["-m", "camoufox_versatile_mcp"]
    }
  }
}
```

## Startup Flags

| Flag | Description |
|------|-------------|
| `--headless` | Run in headless mode |
| `--proxy` | Proxy server, e.g. `--proxy http://127.0.0.1:7890` |
| `--geoip` | Enable GeoIP inference (sets location from proxy IP) |
| `--humanize` | Enable human-like mouse movement |
| `--os` | OS fingerprint: `auto` / `windows` / `macos` / `linux` |
| `--locale` | Browser language, e.g. `zh-CN` |

---

## Tools Overview (45 total)

### Browser Control (11)
| Tool | Description |
|------|-------------|
| `launch_browser` | Launch Camoufox anti-detection browser (defaults: i_know_what_im_doing + disable_coop + forceScopeAccess) |
| `close_browser` | Close the browser |
| `navigate` | Navigate to URL (**supports `auto_solve_challenge` parameter**) |
| `reload` | Reload the page |
| `take_screenshot` | Take a screenshot |
| `take_snapshot` | Get page accessibility tree |
| `click` / `type_text` | Click element / type text |
| `wait_for` | Wait for element or URL match |
| `get_page_info` | Get current page info |
| `scroll` | Scroll the page |
| `go_back` / `go_forward` | Back / forward |

### Versatile Captcha Auto-Solve (2) ⭐
| Tool | Description |
|------|-------------|
| `detect_captcha` | Detect captcha challenge on current page (no clicking) |
| `auto_solve_captcha` | Auto-detect and solve captcha (supports `captcha_type` / `challenge_type`) |

> Supports Cloudflare Turnstile and Interstitial. `captcha_type` is future-proof for hcaptcha, recaptcha, etc.
>
> **Note**: Non-standard challenges (e.g. custom WAF checkboxes) require additional customization.

### JS Execution & Debugging (1)
| Tool | Description |
|------|-------------|
| `evaluate_js` | Execute arbitrary JS expression in page context |

### Script Analysis (2)
| Tool | Description |
|------|-------------|
| `scripts(action)` | Script management: `list` / `get` / `save` |
| `search_code` | Search keywords |

### Hook & Tracing (4)
| Tool | Description |
|------|-------------|
| `hook_function` | Hook or trace a function |
| `inject_hook_preset` | Inject preset hooks (xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe) |
| `remove_hooks` | Remove all hooks |
| `inject_hook` | Inject custom JS hook |

### Network Analysis (5)
| Tool | Description |
|------|-------------|
| `network_capture(action)` | Network capture control |
| `list_network_requests` | List captured requests |
| `get_network_request` | Get full request details |
| `get_request_initiator` | Get JS call stack for request origin |
| `intercept_request` | Intercept requests |

### JSVMP Reverse Analysis (5)
| Tool | Description |
|------|-------------|
| `hook_jsvmp_interpreter` | JSVMP runtime probe |
| `instrumentation(action)` | Source-level instrumentation |
| `compare_env` | Browser environment fingerprint collection |
| `detect_vmp` | Detect which VMP protection the page uses (obfuscated / string_eval / vm_dispatch / wasm) |
| `trace_vmp_for_sign` | Signature-safe tap: trace what fingerprint props the VMP reads |

### Cookie & Storage (4)
| Tool | Description |
|------|-------------|
| `cookies(action)` | Cookie management |
| `get_storage` | Get localStorage / sessionStorage |
| `export_state` / `import_state` | Export / import browser state |

### Verification (1)
| Tool | Description |
|------|-------------|
| `verify_fingerprint` | Verify if fingerprint meets target site requirements |

### Environment (1)
| Tool | Description |
|------|-------------|
| `get_env_info` | Get current browser environment info (OS, UA, screen, locale, etc.) |

### JSVMP Offline Replay Pipeline (8) 🆕
| Tool | Description |
|------|-------------|
| `get_tap_log` | Read the log recorded by trace_vmp_for_sign |
| `disassemble_wasm` | Disassemble WASM bytecode to WAT + import/export list + entry function candidates |
| `capture_worker_js` | Hijack `new Worker()`, capture blob: URL source (direct source of JSVMP bytecode) |
| `capture_ws_payloads` | Hijack `new WebSocket()`, record all send/recv payloads |
| `auto_suggest_missing_props` | Recommend missing fingerprint props based on failed replay + trace_log |
| `replay_vmp_offline` | Run VMP code in Node sandbox (**enhanced**: `supplied_env` / `expected_sign` / `auto_diff`) |
| `trace_vmp_for_sign` | Signature-safe tap: trace what fingerprint props the VMP reads |
| `detect_vmp` | Detect VMP type on current page |

---

## Captcha Auto-Solve Usage

### Method 1: Navigate + auto-solve (recommended)

```python
navigate(
    url="https://target-site-with-captcha.com/",
    auto_solve_challenge=True,
    challenge_type="auto",             # auto-detect turnstile / interstitial
    challenge_ready_delay=5.0,
    expected_content_selector="#main"
)
```

### Method 2: Manual tool calls

```python
# 1. Detect
result = detect_captcha()
# Returns: {"url": "...", "detected": {"turnstile": True, "interstitial": False}, "challenge_present": True}

# 2. Solve
result = auto_solve_captcha(
    captcha_type="cloudflare",
    challenge_type="auto",
    ready_delay=5.0,
    solve_attempts=3,
    verify=True
)
# Returns: {"solved": True, "verified": True, "challenge_type_used": "turnstile", ...}
```

---

## JSVMP Offline Replay Pipeline (Typical Flow)

```
┌─────────────────────────────────────────────────────────┐
│  ① Browser: trace_vmp_for_sign()                       │
│     → Trace which fingerprint props the VMP reads       │
│     → Output prop → value map                           │
│                                                         │
│  ② WASM Disassembly: disassemble_wasm()                 │
│     → Input b64/hex/wasm-url                           │
│     → Output WAT + imports/exports + entry candidates    │
│                                                         │
│  ③ Worker Source Capture: capture_worker_js()            │
│     → Hijack new Worker(blob:...)                      │
│     → Extract VMP bytecode source                       │
│                                                         │
│  ④ WS Traffic Capture: capture_ws_payloads()             │
│     → Hijack new WebSocket()                            │
│     → Record VMP bytecode fetch request/response        │
│                                                         │
│  ⑤ Offline Replay: replay_vmp_offline()                │
│     → Execute VMP in Node sandbox                       │
│     → supplied_env injects missing fingerprint          │
│     → auto_diff compares signatures                     │
│                                                         │
│  ⑥ Missing Props: auto_suggest_missing_props()         │
│     → Combine trace_log + failure log → recommend props  │
│     → Output can be copied directly into supplied_env   │
└─────────────────────────────────────────────────────────┘
```

### Full Example

```python
# Step 1: Trace VMP reads in browser
props = trace_vmp_for_sign(
    trigger_js="sign({order_id: 12345})",
    wait_ms=500,
)

# Step 2: Disassemble WASM (if VMP uses WebAssembly)
wasm_info = disassemble_wasm(wasm_b64, source_kind="base64", generate_wat=True)
print(wasm_info["exports"])       # e.g. ["enc", "dec"]
print(wasm_info["entry_candidates"])

# Step 3: Offline replay (first attempt may mismatch)
result = replay_vmp_offline(
    vmp_code=vmp_code,
    entry="sign",
    input={"order_id": 12345},
    expected_sign="target_sign_value",
    auto_diff=True,
)
if not result["diff"]["match"]:
    # Step 4: Get missing prop suggestions
    suggestions = auto_suggest_missing_props(
        failed_replay_error=result["diff"]["replayed_preview"],
        supplied_env={},
    )
    print(suggestions["suggestions"])

    # Step 5: Replay with suggested env
    result = replay_vmp_offline(
        vmp_code=vmp_code,
        entry="sign",
        input={"order_id": 12345},
        expected_sign="target_sign_value",
        auto_diff=True,
        supplied_env={"navigator": {"userAgent": "..."}},
    )
    assert result["diff"]["match"]
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│           AI Coding Assistant (Cursor / Claude)  │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│           camoufox-versatile-mcp                  │
│  ┌──────────┬──────────┬──────────┬──────────┐   │
│  │Navigation│ Script   │Debugging │ Hooking  │   │
│  ├──────────┼──────────┼──────────┼──────────┤   │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │   │
│  ├──────────┼──────────┼──────────┼──────────┤   │
│  │ ★ Captcha Auto-Solve (cloudflare)            │   │
│  │ ★ JSVMP Pipeline (WASM/Worker/WS/Replay)   │   │
│  └──────────┴──────────┴──────────┴──────────┘   │
│                    ↕ Playwright API              │
├─────────────────────────────────────────────────┤
│     Camoufox (Anti-Detect Firefox, Juggler)      │
└─────────────────────────────────────────────────┘
```

---

## Comparison with camoufox-reverse-mcp

| Feature | camoufox-reverse-mcp | camoufox-versatile-mcp |
|---------|---------------------|------------------------|
| Captcha auto-solve | Requires separate `camoufox-captcha` | **Built-in, no extra setup** |
| navigate auto-solve | N/A | `auto_solve_challenge=True` |
| detect_captcha tool | N/A | Yes |
| auto_solve_captcha tool | External dependency | Yes |
| JSVMP offline replay pipeline | N/A | **New (8 tools)** |
| VMP detection + tap tracing | N/A | Yes |
| WASM disassembly | N/A | Yes |
| Worker / WS traffic capture | N/A | Yes |
| Auto-diff + missing prop suggestion | N/A | Yes |

---

## License

MIT
