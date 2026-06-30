"""Node.js binary detection for the VMP pipeline."""
from __future__ import annotations

import subprocess
from pathlib import Path

from camoufox_versatile_mcp.constants import NODE_FALLBACK_PATHS, SUBPROCESS_TIMEOUT_SHORT


def _find_node() -> str:
    """Return the first working Node executable."""
    for candidate in NODE_FALLBACK_PATHS:
        try:
            res = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT,
            )
            if res.returncode == 0:
                return candidate
        except Exception:
            continue
    return "node"


NODE_BIN = _find_node()
NODE_VERSION = ""
try:
    NODE_VERSION = subprocess.run(
        [NODE_BIN, "--version"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT,
    ).stdout.strip()
except Exception:
    pass
