"""Startup patch for a Playwright Firefox-driver crash (issue #5).

Some Playwright builds serialize an uncaught page error by reading
``pageError.location.url`` / ``.lineNumber`` / ``.columnNumber`` without a
null check. When a page throws an uncaught error whose ``location`` is
``undefined`` (observed on sites such as arcteryx.com and rei.com), the
Node.js driver process crashes.

We fix it at startup by adding optional chaining + defaults.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

_REPLACEMENTS = (
    ("pageError.location.url", "pageError.location?.url ?? ''"),
    ("pageError.location.lineNumber", "pageError.location?.lineNumber ?? 0"),
    ("pageError.location.columnNumber", "pageError.location?.columnNumber ?? 0"),
)
_BUGGY_SENTINEL = "pageError.location.url"


def _is_site_packages(path: Path) -> bool:
    """Return True if the path is under a site-packages directory."""
    try:
        import site
        for sp in site.getsitepackages([]):
            if Path(sp) in path.parents:
                return True
        # Also check sys.prefix for venv
        if Path(sys.prefix) in path.parents:
            return True
    except Exception:
        pass
    return False


def _driver_lib_root() -> Path | None:
    try:
        import playwright
        root = Path(playwright.__file__).parent / "driver" / "package" / "lib"
        if root.is_dir():
            return root
        return None
    except Exception:
        return None


def _patch_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.debug("Could not read %s: %s", path, e)
        return False
    if _BUGGY_SENTINEL not in text:
        return False
    new_text = text
    for old, repl in _REPLACEMENTS:
        new_text = new_text.replace(old, repl)
    if new_text == text:
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
        _logger.info("[camoufox-versatile-mcp] patched: %s", path.name)
        return True
    except Exception as e:
        _logger.warning("[camoufox-versatile-mcp] could not write Playwright patch %s: %s", path, e)
        return False


def patch_playwright_pageerror() -> None:
    """Apply the Playwright pageError crash workaround.

    This is safe to call multiple times (idempotent).  It only patches
    files that:
      1. Exist under the site-packages or venv prefix (not global installs)
      2. Contain the buggy sentinel string
      3. Are writable

    Patched files are logged at INFO level so diagnostics can confirm
    whether the patch was applied.
    """
    try:
        root = _driver_lib_root()
        if root is None:
            _logger.debug("[camoufox-versatile-mcp] Playwright driver lib not found, skipping patch")
            return
        if not _is_site_packages(root):
            _logger.warning(
                "[camoufox-versatile-mcp] Playwright is not in site-packages "
                "(prefix=%s). Skipping auto-patch to avoid modifying user-level installs. "
                "Run in a virtual environment or install Playwright in site-packages.",
                sys.prefix,
            )
            return
        patched: list[str] = []
        for js in root.rglob("*.js"):
            if _patch_file(js):
                patched.append(js.name)
        if patched:
            _logger.info(
                "[camoufox-versatile-mcp] patched Playwright pageError crash "
                "(issue #5): %d file(s) - %s",
                len(patched), ", ".join(patched[:5]),
            )
    except Exception as e:
        _logger.debug("[camoufox-versatile-mcp] Playwright pageError patch skipped: %s", e)
