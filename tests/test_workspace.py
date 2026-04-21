"""build_workspace must produce a clean, bwrap-ready layout for every agent."""

from __future__ import annotations

import os
import shutil

import pytest

from agent_runtime.workspace import build_workspace


# sisyphus uses build_workspace too, but only after moving its role cards out of
# the top level; simple and baseline ship these top-level instructions directly.
AGENT_NAMES = ["simple", "baseline", "sisyphus"]
KNOWN_TASKS = ["validate", "simulate", "recast"]


@pytest.fixture
def clean_workspace(repo_root, tmp_run_name, paper_ref):
    """Build a workspace, yield its Path, then tear it down."""
    created: list = []

    def make(agent: str, task: str = "recast"):
        run_name = f"{tmp_run_name}_{agent}_{task}"
        ws = build_workspace(repo_root, agent, paper_ref, run_name, task=task)
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


@pytest.mark.parametrize("agent", AGENT_NAMES)
@pytest.mark.parametrize("task", KNOWN_TASKS)
def test_task_md_is_seeded_into_agent_context(clean_workspace, agent, task):
    """Every workspace must carry the benchmark-provided TASK.md so the agent
    reads it from the same path regardless of agent or task."""
    ws = clean_workspace(agent, task=task)
    task_path = ws / "agent_context" / "TASK.md"
    assert task_path.is_file(), f"TASK.md missing for {agent}/{task}"
    text = task_path.read_text()
    # Each TASK.md has a distinctive header so we can tell which task landed.
    expected_header = {
        "validate": "VALIDATE",
        "simulate": "SIMULATE",
        "recast": "RECAST",
    }[task]
    assert expected_header in text


@pytest.mark.parametrize("agent", AGENT_NAMES)
@pytest.mark.parametrize("task", KNOWN_TASKS)
def test_task_templates_land_at_workspace_root(clean_workspace, agent, task):
    """The agent fills HEPRecastData/ in the workspace — those files come
    from tasks/<task>/templates/HEPRecastData/."""
    ws = clean_workspace(agent, task=task)
    hep = ws / "HEPRecastData"
    assert hep.is_dir()
    assert any(hep.glob("*.yaml"))


def test_invalid_task_raises_filenotfound(repo_root, tmp_run_name, paper_ref):
    import pytest

    with pytest.raises(FileNotFoundError):
        build_workspace(repo_root, "simple", paper_ref, tmp_run_name, task="nonexistent")
