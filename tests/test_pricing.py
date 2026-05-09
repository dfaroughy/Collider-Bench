"""Unit tests for agent_runtime.pricing.compute_cost.

Covers: longest-prefix model match, unknown-model handling, cached input
discount math, edge cases where cached_input_tokens > input_tokens.
"""

from __future__ import annotations

import pytest

from agent_runtime.pricing import compute_cost


def test_known_claude_model_is_priced():
    cost, priced = compute_cost(
        "claude-opus-4-7",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert priced is True
    # claude-opus-4-7 input = $5.00 / 1M tokens
    assert cost == pytest.approx(5.0)


def test_unknown_model_returns_zero_unpriced():
    cost, priced = compute_cost("totally-fake-model", input_tokens=1_000_000)
    assert cost == 0.0
    assert priced is False


def test_longest_prefix_match_wins():
    # `claude-opus-4-7` should win over the more general `claude-opus-4`
    # entry, which is priced 3× higher (legacy tier).
    specific, _ = compute_cost("claude-opus-4-7", input_tokens=1_000_000)
    legacy, _ = compute_cost("claude-opus-4", input_tokens=1_000_000)
    assert specific == pytest.approx(5.0)
    assert legacy == pytest.approx(15.0)


def test_cached_input_billed_at_cached_rate():
    # 1M input, all cached → billed at cached_input rate ($0.50/M for opus 4.7).
    cost, priced = compute_cost(
        "claude-opus-4-7",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
    )
    assert priced is True
    assert cost == pytest.approx(0.5)


def test_cached_exceeds_input_clamps_to_zero():
    # Pathological inputs (cached > total). The non-cached portion should
    # clamp to zero rather than going negative.
    cost, priced = compute_cost(
        "claude-opus-4-7",
        input_tokens=500_000,
        cached_input_tokens=1_000_000,
    )
    assert priced is True
    # Billed only at cached rate × min(input, cached) = 1M × $0.50/M = 0.50.
    # The non-cached side must NOT subtract negatively.
    assert cost >= 0.0


def test_output_tokens_priced_separately():
    # 0 input, 1M output. Output rate for opus 4.7 = $25.00/M.
    cost, priced = compute_cost("claude-opus-4-7", output_tokens=1_000_000)
    assert priced is True
    assert cost == pytest.approx(25.0)


def test_zero_tokens_zero_cost():
    cost, priced = compute_cost("claude-opus-4-7")
    assert priced is True
    assert cost == 0.0


def test_empty_model_unpriced():
    cost, priced = compute_cost("")
    assert cost == 0.0
    assert priced is False


def test_deepseek_v4_pro_uses_promo_rate():
    # Sanity: the table currently encodes the discounted promo rate, not list.
    # If this test starts failing, the promo expired and the table was updated
    # (good!); update the expected number then.
    cost, priced = compute_cost("deepseek-v4-pro", input_tokens=1_000_000)
    assert priced is True
    assert cost == pytest.approx(1.74)
