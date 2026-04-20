"""build_workspace must produce a clean, bwrap-ready layout for every agent."""

from __future__ import annotations

import os
import shutil

import pytest

from agent_runtime.workspace import build_workspace


# sisyphus uses build_workspace too, but only after moving its role cards out of
# the top level; simple and baseline ship these top-level instructions directly.
AGENT_NAMES = ["simple", "baseline", "sisyphus"]


@pytest.fixture
def clean_workspace(repo_root, tmp_run_name, paper_ref):
    """Build a workspace, yield its Path, then tear it down."""
    created: list = []

    def make(agent: str):
        run_name = f"{tmp_run_name}_{agent}"
        ws = build_workspace(repo_root, agent, paper_ref, run_name)
        # build_workspace puts runs under repo_root/runs/<run_name>/
        created.append(repo_root / "runs" / run_name)
        return ws

    yield make

    for p in created:
        if p.exists():
            shutil.rmtree(p)


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_workspace_layout(clean_workspace, agent):
    ws = clean_workspace(agent)
    assert (ws / "agent_context" / "AGENTS.md").is_file()
    assert (ws / "bin").is_dir()
    assert (ws / "tools").is_symlink()
    assert (ws / "papers").is_symlink()
    # papers/ must resolve to the shared for_agent location
    assert "LHCRecastBench" in os.readlink(ws / "papers")


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_workspace_has_run_analysis(clean_workspace, agent):
    ws = clean_workspace(agent)
    assert (ws / "bin" / "run-analysis").exists()


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_planner_critic_role_cards_not_leaked(clean_workspace, agent):
    """PLANNER.md / CRITIC.md live under runtime/roles/ — they must not
    appear in the executor's agent_context/ even when the agent is sisyphus."""
    ws = clean_workspace(agent)
    ctx_files = {p.name for p in (ws / "agent_context").rglob("*.md")}
    assert "PLANNER.md" not in ctx_files
    assert "CRITIC.md" not in ctx_files
