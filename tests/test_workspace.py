"""build_workspace must produce a clean, container-ready layout for every agent.

Post-tasks/ refactor: workspace is driven by a task_id (not paper+task enum),
and the agent's fillable yaml lives at workspace/results/ (not HEPRecastData/).
"""

from __future__ import annotations

import os
import shutil

import pytest

from agent_runtime.workspace import build_workspace


# Only single-shot agents are exercised here. Iterative/anneal controllers
# haven't been migrated to the tasks/ layout yet (see their main() guards).
AGENT_NAMES = ["simple", "baseline"]


@pytest.fixture
def clean_workspace(repo_root, tmp_run_name, task_id):
    """Build a workspace, yield a builder, then tear it down."""
    created: list = []

    def make(agent: str):
        run_name = f"{tmp_run_name}_{agent}"
        ws = build_workspace(repo_root, agent, task_id, run_name)
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
    # papers/ must resolve to the shared paper dir under tasks/shared/
    link = os.readlink(ws / "papers")
    assert "tasks/shared" in link or "LHCRecastBench" in link


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_workspace_has_run_analysis(clean_workspace, agent):
    ws = clean_workspace(agent)
    assert (ws / "bin" / "run-analysis").exists()


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_planner_examiner_role_cards_not_leaked(clean_workspace, agent):
    """PLANNER.md / EXAMINER.md live under runtime/roles/ — they must not
    appear in the executor's agent_context/."""
    ws = clean_workspace(agent)
    ctx_files = {p.name for p in (ws / "agent_context").rglob("*.md")}
    assert "PLANNER.md" not in ctx_files
    assert "EXAMINER.md" not in ctx_files


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_task_md_is_seeded_into_agent_context(clean_workspace, agent):
    """Every workspace carries the task's TASK.md at agent_context/TASK.md."""
    ws = clean_workspace(agent)
    task_path = ws / "agent_context" / "TASK.md"
    assert task_path.is_file(), f"TASK.md missing for {agent}"
    assert task_path.read_text().strip()


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_results_seeded_from_task_template(clean_workspace, agent):
    """The agent fills results/ in place — seeded from tasks/<task_id>/template/.

    The template histogram file embeds its metadata at the top (instructions,
    target, luminosity, …) before a `---` separator, so there is no longer a
    standalone description.toml — both blocks live in the .yml/.yaml file.
    """
    import yaml

    ws = clean_workspace(agent)
    results = ws / "results"
    assert results.is_dir()
    yamls = list(results.glob("*.yml")) + list(results.glob("*.yaml"))
    assert yamls, "results/ should contain the null-filled histogram"
    assert not (results / "description.toml").exists(), (
        "description.toml should no longer be seeded — metadata lives "
        "inside the histogram .yml/.yaml file itself"
    )
    # The seeded file must have both the metadata block and the HEPData
    # histogram doc in it (two YAML documents).
    docs = list(yaml.safe_load_all(yamls[0].read_text()))
    has_meta = any(isinstance(d, dict) and "target" in d for d in docs)
    has_hist = any(isinstance(d, dict) and "dependent_variables" in d for d in docs)
    assert (
        has_meta and has_hist
    ), f"expected metadata + histogram docs in {yamls[0]}, got {len(docs)} doc(s)"


def test_task_toml_not_leaked_into_workspace(clean_workspace):
    """task.toml is harness metadata — it must NOT end up in the workspace."""
    ws = clean_workspace("simple")
    assert not (ws / "task.toml").exists()
    assert not (ws / "results" / "task.toml").exists()


def test_invalid_task_raises_filenotfound(repo_root, tmp_run_name):
    with pytest.raises(FileNotFoundError):
        build_workspace(repo_root, "simple", "nonexistent-task-id", tmp_run_name)
