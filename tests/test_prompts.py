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
def test_single_shot_prompt_renders(builder):
    # paper_ref is not embedded in the prompt body anymore — it lives in
    # agent_context/TASK.md, seeded into the workspace. Tool-specific rules
    # (block on run-analysis, use root:// URLs, etc.) also live in
    # agent_context/TOOLS.md rather than the prompt. Just confirm the
    # builder renders and points the agent at the workspace docs.
    text = builder("TEST-1234")
    assert text.strip()
    assert "TASK.md" in text
    assert "TOOLS.md" in text


def test_planner_prompt_mentions_plan_md():
    text = build_planner_prompt("TEST-1234", "test-task-id")
    assert "TEST-1234" in text
    assert "plan.md" in text
    assert "PLANNER.md" in text


@pytest.mark.parametrize("iter_index", [0, 1, 5])
@pytest.mark.parametrize("has_prior", [True, False])
def test_executor_prompt_renders(iter_index, has_prior):
    text = build_executor_prompt("TEST-1234", "test-task-id", iter_index, has_prior)
    assert "TEST-1234" in text
    assert "iteration" in text.lower()
    assert "plan.md" in text
    if has_prior:
        assert "carries forward" in text.lower() or "previous iteration" in text.lower()


@pytest.mark.parametrize("iter_index", [0, 2])
def test_critic_prompt_points_at_artifacts(iter_index):
    text = build_critic_prompt("TEST-1234", "test-task-id", iter_index)
    assert "TEST-1234" in text
    assert "CRITIC.md" in text
    # Critic edits plan.md in place (no separate critique.md).
    assert "plan.md" in text
    # Critic must NOT see score numbers or reference values.
    assert "score.json" not in text
    assert "reference" in text.lower()  # the prompt explicitly says "do NOT see"
