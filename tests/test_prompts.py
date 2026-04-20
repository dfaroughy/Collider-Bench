"""Prompt builders must render for any paper_ref without error."""

from __future__ import annotations

import pytest

from agents.simple.run import build_prompt as simple_prompt
from agents.baseline.run import build_prompt as baseline_prompt
from agents.sisyphus.runtime.controller.roles import (
    build_planner_prompt,
    build_executor_prompt,
    build_critic_prompt,
)


@pytest.mark.parametrize("builder", [simple_prompt, baseline_prompt])
def test_single_shot_prompt_contains_paper_ref(builder):
    text = builder("TEST-1234")
    assert "TEST-1234" in text
    # Stock safety lines — the agent must be told to block on run-analysis.
    assert "run-analysis" in text
    assert "never run_in_background" in text or "synchronously" in text


def test_planner_prompt_mentions_plan_md():
    text = build_planner_prompt("TEST-1234")
    assert "TEST-1234" in text
    assert "plan.md" in text
    assert "PLANNER.md" in text


@pytest.mark.parametrize("iter_index", [0, 1, 5])
@pytest.mark.parametrize("has_prior", [True, False])
def test_executor_prompt_renders(iter_index, has_prior):
    text = build_executor_prompt("TEST-1234", iter_index, has_prior)
    assert "TEST-1234" in text
    assert "iteration" in text.lower()
    if has_prior:
        assert "critique.md" in text
    else:
        # Iter 0: no critique to read yet.
        assert "critique.md" not in text


@pytest.mark.parametrize("iter_index", [0, 2])
def test_critic_prompt_points_at_artifacts(iter_index):
    text = build_critic_prompt("TEST-1234", iter_index)
    assert "TEST-1234" in text
    assert "CRITIC.md" in text
    assert "score.json" in text
    assert "critique.md" in text
