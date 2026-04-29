"""Prompt builder for the simple agent must render for any paper_ref."""

from __future__ import annotations

from agents.simple.run import build_prompt as simple_prompt


def test_simple_prompt_renders():
    # paper_ref is not embedded in the prompt body — it lives in
    # agent_context/TASK.md, seeded into the workspace. Tool-specific rules
    # (block on run-analysis, use root:// URLs, etc.) also live in
    # agent_context/TOOLS.md rather than the prompt. Just confirm the
    # builder renders and points the agent at the workspace docs.
    text = simple_prompt("TEST-1234")
    assert text.strip()
    assert "TASK.md" in text
    assert "TOOLS.md" in text
