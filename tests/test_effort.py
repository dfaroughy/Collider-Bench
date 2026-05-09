"""Tests for agent_runtime.effort.resolve_effort.

Validates the (label, max_thinking_tokens) mapping plus the new strict
behavior: unknown labels raise instead of silently mapping to medium.
"""

from __future__ import annotations

import pytest

from agent_runtime.effort import EFFORT_THINKING_TOKENS, resolve_effort


@pytest.mark.parametrize("label", sorted(EFFORT_THINKING_TOKENS))
def test_known_labels_round_trip(label):
    out_label, tokens = resolve_effort(label)
    assert out_label == label
    assert tokens == EFFORT_THINKING_TOKENS[label]


def test_none_defaults_to_medium():
    label, tokens = resolve_effort(None)
    assert label == "medium"
    assert tokens == EFFORT_THINKING_TOKENS["medium"]


def test_empty_string_defaults_to_medium():
    label, tokens = resolve_effort("")
    assert label == "medium"


def test_int_input_returns_custom_tuple():
    label, tokens = resolve_effort(12000)
    assert label == "custom(12000)"
    assert tokens == 12000


def test_digit_string_returns_custom_tuple():
    label, tokens = resolve_effort("8000")
    assert label == "custom(8000)"
    assert tokens == 8000


def test_unknown_label_raises():
    # Typos must NOT silently coast on the medium default.
    with pytest.raises(ValueError, match="unknown effort"):
        resolve_effort("higgh")


def test_case_insensitive():
    label, tokens = resolve_effort("MEDIUM")
    assert label == "medium"
    assert tokens == EFFORT_THINKING_TOKENS["medium"]
