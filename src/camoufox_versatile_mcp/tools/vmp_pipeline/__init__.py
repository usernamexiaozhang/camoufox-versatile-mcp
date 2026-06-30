"""VMP pipeline tools: detect_vmp / trace_vmp_for_sign / replay_vmp_offline.

Three-stage JSVMP analysis pipeline. All three are designed to be chained:

    detect_vmp() -> pick a hint -> trace_vmp_for_sign() -> replay_vmp_offline()

Refactored into submodules:
- _node:        Node.js binary detection
- _detection:   detect_vmp
- _tracing:     trace_vmp_for_sign, get_tap_log, capture_worker_js, capture_ws_payloads
- _wasm:        disassemble_wasm
- _replay:      replay_vmp_offline, auto_suggest_missing_props
"""
from __future__ import annotations

# Re-export all public tools so `from .tools import vmp_pipeline` still works
from ._detection import detect_vmp
from ._tracing import trace_vmp_for_sign, get_tap_log, capture_worker_js, capture_ws_payloads
from ._wasm import disassemble_wasm
from ._replay import replay_vmp_offline, auto_suggest_missing_props
from ._node import NODE_BIN, NODE_VERSION

# Expose common FP dictionary for use in suggestions
from ._replay import _COMMON_FP
