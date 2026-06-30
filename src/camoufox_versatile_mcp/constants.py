"""Central constants for camoufox-versatile-mcp.

Organized by category to replace magic numbers and scattered module-level constants.

Usage:
    from camoufox_versatile_mcp.constants import (
        MAX_LOG_SIZE, MAX_BODY_SIZE,
        HTTP_TIMEOUT, CAPTCHA_TIMEOUT, NAV_TIMEOUT,
        TRACE_LEVELS, TLLOG_SUBSYSTEMS,
        CACHE_DIR_BASE,
    )
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Cache directories (base for all trace/property/capture data)
# ---------------------------------------------------------------------------

CACHE_DIR_BASE = Path.home() / ".cache" / "camoufox-trace"

# ---------------------------------------------------------------------------
# Browser / log limits
# ---------------------------------------------------------------------------

MAX_LOG_SIZE = 2000          # max entries in the console + network ring buffer
MAX_BODY_SIZE = 200_000     # max bytes to store of a response body before truncation

# ---------------------------------------------------------------------------
# Timeouts (milliseconds)
# ---------------------------------------------------------------------------

NAV_TIMEOUT_MS = 30_000     # page.goto timeout
CAPTCHA_TURNSTILE_TIMEOUT_MS = 15_000   # wait_for_selector for turnstile iframe
CAPTCHA_INTERSTITIAL_TIMEOUT_MS = 10_000  # wait_for_selector for interstitial
CAPTCHA_WAIT_LOAD_MS = 15_000  # wait_for_load_state after solving
SUBPROCESS_TIMEOUT_SHORT = 5     # seconds - quick probes (node --version, file ops)
SUBPROCESS_TIMEOUT_MEDIUM = 10   # seconds - wabt probe
SUBPROCESS_TIMEOUT_LONG = 30     # seconds - wasm disassembler
SUBPROCESS_TIMEOUT_NPM = 120     # seconds - npm install -g wabt
WEBSOCKET_PING_TIMEOUT = 2        # seconds
WEBSOCKET_MSG_TIMEOUT = 5         # seconds

# ---------------------------------------------------------------------------
# HTTP trace
# ---------------------------------------------------------------------------

MAX_STACK_FRAMES = 64        # SpiderMonkey stack frames to capture per request
HTTP_IGNORED_HOSTS = frozenset({"www.mozilla.org", "mozilla.org"})

# ---------------------------------------------------------------------------
# JS engine trace (SpiderMonkey tracelogger)
# ---------------------------------------------------------------------------

DEFAULT_TLLOG = "Interpreter,Baseline,Ion,Wasm"
DEFAULT_TLOPTIONS = "EnableActiveThread,EnableOffThread,EnableGraph"

TLLOG_SUBSYSTEMS = {
    "interpreter": "Interpreter",
    "baseline": "Baseline",
    "ion": "Ion",
    "wasm": "Wasm",
    "gc": "GC",
    "native": "Native",
    "args": "Arguments",
    "ops": "Operations",
}

TRACE_LEVELS = {
    "functions": "Interpreter,Baseline,Ion",
    "full": "Interpreter,Baseline,Ion,Wasm,GC,Native,Arguments,Operations",
    "wasm": "Wasm",
    "minimal": "Interpreter,Baseline",
    "bytecode": "Interpreter,Operations",
}

# ---------------------------------------------------------------------------
# Node.js binary fallback search paths
# ---------------------------------------------------------------------------

NODE_FALLBACK_PATHS = [
    r"D:\software_install\node\node.exe",
    r"C:\Program Files\nodejs\node.exe",
    "node",
]

# ---------------------------------------------------------------------------
# VMP pipeline
# ---------------------------------------------------------------------------

VMP_TAP_MAX_ENTRIES = 2000   # max entries in the transparent tap log
WAT_TRUNCATE_CHARS = 100_000  # truncate WAT text beyond this in responses
WAT_BODY_TRUNCATE_CHARS = 50_000  # truncate captured body text beyond this

# ---------------------------------------------------------------------------
# MCP defaults
# ---------------------------------------------------------------------------

MCP_DEFAULT_LIMIT = 100      # default pagination / output limit
MCP_MAX_LIMIT = 10_000       # hard cap on any single output
PRE_INJECT_REGISTER_TIMEOUT = 10.0  # seconds to wait for hook registration

# ---------------------------------------------------------------------------
# Property trace (camoufox-reverse custom builds)
# ---------------------------------------------------------------------------

PROPERTY_TRACE_KEEP_DAYS = 7  # days to keep old property trace files
MAX_EVENTS_PER_SESSION = 100_000
