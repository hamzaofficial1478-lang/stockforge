"""Token accounting. Kept honest and in one place so the spend cap means
something. Rates are USD per million tokens, Anthropic first-party API."""

from __future__ import annotations

RATES = {
    "claude-opus-5":   {"in": 5.00, "cached": 0.50, "out": 25.00},
    "claude-sonnet-5": {"in": 2.00, "cached": 0.20, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "cached": 0.10, "out": 5.00},
}


def cost_usd(model: str, usage) -> float:
    r = RATES.get(model)
    if r is None:
        return 0.0
    get = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k, 0) or 0)
    fresh = get("input_tokens")
    cached = get("cache_read_input_tokens")
    written = get("cache_creation_input_tokens")
    out = get("output_tokens")
    return (
        fresh * r["in"]
        + cached * r["cached"]
        + written * r["in"] * 1.25     # writing to cache carries a premium
        + out * r["out"]
    ) / 1_000_000


def usage_dict(usage) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    if isinstance(usage, dict):
        return {k: usage.get(k, 0) or 0 for k in keys}
    return {k: getattr(usage, k, 0) or 0 for k in keys}
