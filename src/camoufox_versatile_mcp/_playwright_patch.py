"""
Startup patch for a Playwright Firefox-driver crash (issue #5).
Some Playwright builds serialize an uncaught page error by reading
``pageError.location.url`` / ``.lineNumber`` / ``.columnNumber`` without a
null check. When a page throws an uncaught error whose ``location`` is
``undefined`` (observed on sites such as arcteryx.com and rei.com), the
Node.js driver process crashes.
We fix it at startup by adding optional chaining + defaults.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPLACEMENTS = (
    ("pageError.location.url", "pageError.location?.url ?? ''"),
    ("pageError.location.lineNumber", "pageError.location?.lineNumber ?? 0"),
    ("pageError.location.columnNumber", "pageError.location?.columnNumber ?? 0"),
)
_BUGGY_SENTINEL = "pageError.location.url"


def _driver_lib_root() -> Path | None:
    try:
        import playwright
        root = Path(playwright.__file__).parent / "driver" / "package" / "lib"
        return root if root.is_dir() else None
    except Exception:
        return None


def _patch_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
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
        return True
    except Exception as e:
        print(f"[camoufox-versatile-mcp] could not write Playwright patch: {e}", file=sys.stderr)
        return False


def patch_playwright_pageerror() -> None:
    try:
        root = _driver_lib_root()
        if root is None:
            return
        patched: list[str] = []
        for js in root.rglob("*.js"):
            if _patch_file(js):
                patched.append(js.name)
        if patched:
            print("[camoufox-versatile-mcp] patched Playwright pageError crash (issue #5)", file=sys.stderr)
    except Exception as e:
        print(f"[camoufox-versatile-mcp] Playwright pageError patch skipped: {e}", file=sys.stderr)
