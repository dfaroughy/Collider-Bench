"""Per-vendor model pricing + cost computation for run_info.json["usage"].

Claude reports `total_cost_usd` in its own stream-json `result` event so we
just lift it. Codex and Gemini report token counts only — this module turns
those counts into a dollar figure.

Update the table below when vendor prices change. Keys are matched against
the model id with `startswith` (longest prefix wins), so add specific entries
ahead of generic ones.

All rates are USD per 1,000,000 tokens.
"""

from __future__ import annotations


# ── Price table ────────────────────────────────────────────────────────────
# Verified against vendor pricing pages, 2026-04. If a model is missing,
# `compute_cost` returns 0.0 and `cost_estimated=False` in the result.
_PRICING: dict[str, dict[str, float]] = {
    # ── OpenAI / Codex ────────────────────────────────────────────────────
    # Source: https://developers.openai.com/api/docs/pricing
    # Cached input is 10% of input price across the GPT-5 family.
    "gpt-5.5-pro": {"input": 30.00, "output": 180.00, "cached_input": 3.00},
    "gpt-5.5": {"input": 5.00, "output": 30.00, "cached_input": 0.50},
    "gpt-5.4-pro": {"input": 30.00, "output": 180.00, "cached_input": 3.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cached_input": 0.075},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "cached_input": 0.02},
    "gpt-5.4": {"input": 2.50, "output": 15.00, "cached_input": 0.25},
    "gpt-5.3-codex": {"input": 1.75, "output": 14.00, "cached_input": 0.175},
    # ── Google / Gemini ───────────────────────────────────────────────────
    # Gemini 3 Pro Preview pricing (under 200K context tier).
    "gemini-3.1-pro": {"input": 2.00, "output": 12.00, "cached_input": 0.50},
    "gemini-3-pro": {"input": 2.00, "output": 12.00, "cached_input": 0.50},
    "gemini-3-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.075},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached_input": 0.31},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.075},
    # ── Anthropic / Claude ───────────────────────────────────────────────
    # Source: https://platform.claude.com/docs/en/about-claude/pricing
    # Claude reports its own `total_cost_usd` in the stream so this table
    # is informational/fallback. Opus 4.5+ is on a new (cheaper) tier than
    # Opus 4/4.1; longest-prefix match means specific entries win.
    "claude-opus-4-7": {"input": 5.00, "output": 25.00, "cached_input": 0.50},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cached_input": 0.50},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00, "cached_input": 0.50},
    "claude-opus-4-1": {"input": 15.00, "output": 75.00, "cached_input": 1.50},
    "claude-opus-4": {"input": 15.00, "output": 75.00, "cached_input": 1.50},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-haiku-4": {"input": 1.00, "output": 5.00, "cached_input": 0.10},
    # ── DeepSeek (forge / aider) ──────────────────────────────────────────
    # Source: https://api-docs.deepseek.com/quick_start/pricing
    # NOTE: deepseek-v4-pro is on a "75% off" promo through 2026-05-31;
    # numbers below are the *effective* (discounted) rates the API bills
    # at today. Update or split into a date-keyed table when the promo
    # expires, otherwise post-2026-05-31 runs will be priced ~4× too low.
    "deepseek-v4-pro": {"input": 1.74, "output": 3.48, "cached_input": 0.0145},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "cached_input": 0.0028},
    # Legacy aliases (deprecated by DeepSeek; map to v4-flash equivalents).
    "deepseek-chat": {"input": 0.14, "output": 0.28, "cached_input": 0.0028},
    "deepseek-reasoner": {"input": 0.14, "output": 0.28, "cached_input": 0.0028},
}


def _lookup(model: str) -> dict[str, float] | None:
    """Longest-prefix match against the pricing table."""
    if not model:
        return None
    matches = [k for k in _PRICING if model.startswith(k)]
    if not matches:
        return None
    return _PRICING[max(matches, key=len)]


def compute_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> tuple[float, bool]:
    """Return (cost_usd, priced) given token counts.

    `input_tokens` is interpreted as the *total* prompt-token count
    (including the cached portion), matching OpenAI / Gemini convention.
    Billable non-cached input = input_tokens - cached_input_tokens.

    `priced` is False if the model isn't in the price table — caller can
    surface that in run_info.json so an unpriced figure isn't mistaken
    for a real $0.
    """
    rates = _lookup(model)
    if rates is None:
        return (0.0, False)
    non_cached_in = max(0, int(input_tokens) - int(cached_input_tokens))
    cost = (
        non_cached_in * rates["input"]
        + int(cached_input_tokens) * rates["cached_input"]
        + int(output_tokens) * rates["output"]
    ) / 1_000_000.0
    return (round(cost, 6), True)
