"""Unified deprecation warning mechanism for camoufox-versatile-mcp."""
from __future__ import annotations

import logging
import time
from typing import Optional

_logger = logging.getLogger(__name__)

_GLOBAL_DEPRECATION_LOG: list[dict] = []
_MAX_LOG = 100


def log_deprecated_call(
    tool_name: str,
    alternative: str,
    removed_in: str = "1.2.0",
    warning: bool = True,
) -> str:
    """Log a deprecation warning and return a formatted message.

    The message is stored in the global deprecation log (capped at _MAX_LOG entries)
    so callers or diagnostics can retrieve it later via ``get_deprecation_log()``.
    If ``warning=True``, a ``logging.warning`` is also emitted.
    """
    msg = f"'{tool_name}' is deprecated and will be removed in v{removed_in}. Use: {alternative}"
    entry = {
        "tool": tool_name,
        "alternative": alternative,
        "removed_in": removed_in,
        "called_at": time.time(),
    }
    _GLOBAL_DEPRECATION_LOG.append(entry)
    if len(_GLOBAL_DEPRECATION_LOG) > _MAX_LOG:
        _GLOBAL_DEPRECATION_LOG.pop(0)
    if warning:
        _logger.warning(msg)
    return msg


def get_deprecation_log() -> list[dict]:
    """Return a copy of all logged deprecation warnings."""
    return list(_GLOBAL_DEPRECATION_LOG)
