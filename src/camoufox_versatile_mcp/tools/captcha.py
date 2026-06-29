"""Versatile captcha auto-detect & auto-solve tools.

Wraps `camoufox_captcha` (the same library used by the project's test.py smoke test)
so the MCP can:
  1. Detect whether a captcha challenge is present (cloudflare turnstile/interstitial
     today; hcaptcha / recaptcha once `camoufox_captcha` adds them).
  2. Click through the challenge and verify protected content loaded.

The captcha provider is selected via `captcha_type`:
    - "cloudflare" (currently the only one shipped by `camoufox_captcha`).
      The library is future-proof: it accepts `captcha_type` and `challenge_type`
      parameters and is designed to grow into hcaptcha/recaptcha without breaking
      callers.
"""
from __future__ import annotations

import asyncio
import importlib
from typing import Any, Literal

from ..server import mcp, browser_manager

CaptchaType = Literal["cloudflare"]
ChallengeType = Literal["interstitial", "turnstile", "auto"]

# HTML marker that confirms a Cloudflare challenge is still being served.
# Other captcha providers can extend this map if/when they are added.
_CHALLENGE_HTML_MARKERS: dict[str, tuple[str, ...]] = {
    "cloudflare": ("/cdn-cgi/challenge-platform/",),
}


def _load_camoufox_captcha():
    try:
        return importlib.import_module("camoufox_captcha")
    except ImportError as exc:
        raise RuntimeError(
            "camoufox_captcha is not installed. Install it with "
            "`pip install camoufox-captcha` to enable captcha auto-solve."
        ) from exc


async def _detect_cloudflare(page, challenge_type: ChallengeType) -> dict[str, bool]:
    captcha_utils = importlib.import_module("camoufox_captcha.cloudflare.utils.detection")
    results: dict[str, bool] = {}
    types_to_check: list[str] = (
        ["turnstile", "interstitial"] if challenge_type == "auto" else [challenge_type]
    )
    for ctype in types_to_check:
        try:
            results[ctype] = await captcha_utils.detect_cloudflare_challenge(
                page, challenge_type=ctype
            )
        except Exception:
            results[ctype] = False
    return results


async def _detect(page, captcha_type: CaptchaType, challenge_type: ChallengeType) -> dict[str, bool]:
    """Provider-specific detection.

    Currently the only provider is cloudflare. To onboard a new provider,
    add a branch here that probes the page for its DOM markers.
    """
    if captcha_type == "cloudflare":
        return await _detect_cloudflare(page, challenge_type)
    return {}


async def _resolve_challenge_type(
    page, captcha_type: CaptchaType, challenge_type: ChallengeType
) -> tuple[str | None, dict[str, bool]]:
    """Pick the right challenge_type to solve.

    Cloudflare's 5-second interstitial + turnstile combo is tricky: the page
    first serves the interstitial (no clickable iframe yet, just a 5s JS
    challenge), and only after that timer expires does it embed a turnstile
    iframe. `auto` mode must therefore prefer the interstitial flow when both
    flags are set, otherwise the solver races the iframe mount and reports
    "Cloudflare iframes not found".
    """
    detection = await _detect(page, captcha_type, challenge_type)
    if challenge_type == "auto":
        # Interstitial first — its solver waits for the JS challenge to
        # resolve and then the turnstile iframe (if any) appears naturally.
        if detection.get("interstitial"):
            return "interstitial", detection
        if detection.get("turnstile"):
            return "turnstile", detection
        return None, detection
    return challenge_type if detection.get(challenge_type) else None, detection


async def _page_still_has_challenge(page, captcha_type: CaptchaType) -> bool:
    markers = _CHALLENGE_HTML_MARKERS.get(captcha_type, ())
    if not markers:
        return False
    try:
        html = await page.content()
    except Exception:
        return True
    return any(marker in html for marker in markers)


async def _wait_until_challenge_ready(page, captcha_type: CaptchaType, challenge_type: str,
                                       ready_delay: float) -> None:
    if captcha_type == "cloudflare" and challenge_type == "turnstile":
        try:
            await page.wait_for_selector(
                'input[name="cf-turnstile-response"], '
                'script[src*="challenges.cloudflare.com/turnstile/v0"]',
                timeout=15000,
            )
        except Exception:
            pass
    elif captcha_type == "cloudflare" and challenge_type == "interstitial":
        # 5-second JS challenge — give it room to finish before solver probes
        # for the post-challenge turnstile iframe.
        try:
            await page.wait_for_selector(
                'script[src*="/cdn-cgi/challenge-platform/"]',
                timeout=10000,
            )
        except Exception:
            pass
        if ready_delay < 6.0:
            ready_delay = 6.0
    if ready_delay > 0:
        await asyncio.sleep(ready_delay)


@mcp.tool()
async def auto_solve_captcha(
    captcha_type: CaptchaType = "cloudflare",
    challenge_type: ChallengeType = "auto",
    ready_delay: float = 5.0,
    expected_content_selector: str | None = None,
    solve_attempts: int = 3,
    solve_click_delay: int = 8,
    wait_checkbox_attempts: int = 10,
    wait_checkbox_delay: int = 3,
    checkbox_click_attempts: int = 2,
    attempt_delay: int = 3,
    verify: bool = True,
) -> dict:
    """Auto-detect and solve a captcha challenge on the current page.

    Wraps `camoufox_captcha.solve_captcha` (same library as project test.py).
    Probes the page for captcha indicators, clicks through the challenge, and
    verifies the protected content actually loaded.

    Args:
        captcha_type: Captcha provider, e.g. "cloudflare". The library is
            future-proof; new providers (hcaptcha, recaptcha, ...) will be
            supported as `camoufox_captcha` adds them — pass the new name here.
        challenge_type: "auto" (recommended), "interstitial", or "turnstile".
            "auto" probes both variants and solves whichever is detected.
        ready_delay: Seconds to wait for the captcha iframe to mount before
            attempting to click.
        expected_content_selector: Optional CSS selector to wait for after
            solving.
        solve_attempts: Maximum solve attempts (passed to solver).
        solve_click_delay: Seconds to wait after clicking so the provider
            can validate the click.
        wait_checkbox_attempts: Maximum polls to wait for the checkbox to appear.
        wait_checkbox_delay: Seconds between checkbox-wait polls.
        checkbox_click_attempts: Maximum click attempts on the checkbox itself.
        attempt_delay: Seconds between top-level solve attempts.
        verify: Re-read page.content() after solving and confirm the provider's
            challenge marker is gone. Strongly recommended.

    Returns:
        dict with detected, challenge_type_used, solved, attempts, verified,
        final_url, error (if any).
    """
    result: dict[str, Any] = {
        "captcha_type": captcha_type,
        "detected": {},
        "challenge_type_used": None,
        "solved": False,
        "attempts": 0,
        "verified": False,
        "final_url": None,
        "error": None,
    }
    try:
        page = await browser_manager.get_active_page()
        result["final_url"] = page.url
    except Exception as exc:
        result["error"] = f"browser not available: {exc}"
        return result

    try:
        captcha_mod = _load_camoufox_captcha()
    except RuntimeError as exc:
        result["error"] = str(exc)
        return result

    try:
        chosen, detection = await _resolve_challenge_type(page, captcha_type, challenge_type)
        result["detected"] = detection
        if chosen is None:
            result["error"] = f"no {captcha_type} challenge detected on the current page"
            return result

        result["challenge_type_used"] = chosen
        await _wait_until_challenge_ready(page, captcha_type, chosen, ready_delay)

        kwargs: dict[str, Any] = {
            "solve_attempts": solve_attempts,
            "solve_click_delay": solve_click_delay,
            "wait_checkbox_attempts": wait_checkbox_attempts,
            "wait_checkbox_delay": wait_checkbox_delay,
            "checkbox_click_attempts": checkbox_click_attempts,
            "attempt_delay": attempt_delay,
        }
        if expected_content_selector:
            kwargs["expected_content_selector"] = expected_content_selector

        solved = await captcha_mod.solve_captcha(
            page,
            captcha_type=captcha_type,
            challenge_type=chosen,
            **kwargs,
        )
        result["solved"] = bool(solved)
        result["attempts"] = solve_attempts

        if not solved:
            result["error"] = "solve_captcha returned False"
            return result

        if verify:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            still_blocked = await _page_still_has_challenge(page, captcha_type)
            result["verified"] = not still_blocked
            if still_blocked:
                result["error"] = (
                    "solver reported success but the captcha challenge "
                    "marker is still present in the page HTML"
                )
        else:
            result["verified"] = True

        result["final_url"] = page.url
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


@mcp.tool()
async def detect_captcha(captcha_type: CaptchaType = "cloudflare") -> dict:
    """Detect whether a captcha challenge is currently shown.

    Lightweight probe: returns detection flags without attempting to solve
    anything. Useful when you want to decide whether to call
    auto_solve_captcha.

    Args:
        captcha_type: Captcha provider to probe, e.g. "cloudflare".

    Returns:
        dict with provider-specific detection flags and the current URL.
        For "cloudflare" today: {turnstile: bool, interstitial: bool}.
        Other providers will report under their own keys.
    """
    try:
        page = await browser_manager.get_active_page()
    except Exception as exc:
        return {"error": f"browser not available: {exc}"}

    try:
        detection = await _detect(page, captcha_type, "auto")
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "url": page.url,
        "captcha_type": captcha_type,
        "detection": detection,
        "challenge_present": any(detection.values()),
    }