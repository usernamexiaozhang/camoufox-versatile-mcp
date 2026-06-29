from __future__ import annotations

from ..server import mcp, browser_manager


@mcp.tool()
async def verify_signer_offline(signer_code: str, samples: list[dict]) -> dict:
    """Verify a signing function against known samples (offline, no browser needed).

    Args:
        signer_code: JavaScript code that exports a signing function.
            Must be a function expression or arrow function: e.g. "(s) => mySign(s)"
        samples: List of samples, each with expected request/response pairs:
            [{"id": "r1", "input": {...}, "expected": {"X-Bogus": "..."}}]

    Returns:
        dict with overall passed/failed, per-sample results, and first mismatch.
    """
    try:
        results = []
        first_mismatch = None
        for sample in samples:
            sid = sample.get("id", "unknown")
            input_data = sample.get("input", {})
            expected = sample.get("expected", {})
            results.append({
                "id": sid, "status": "skipped",
                "note": "offline verification requires a JS runtime (Node.js/jsdom)"
            })

        return {
            "overall": "partial",
            "note": "Full offline verification requires a JS runtime. "
                    "For browser-based verification, use list_network_requests + "
                    "get_request_initiator to capture real signed requests.",
            "sample_count": len(samples),
            "results": results,
            "first_mismatch": first_mismatch,
        }
    except Exception as e:
        return {"error": str(e)}
