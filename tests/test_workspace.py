"""build_workspace must produce a clean, container-ready layout for every agent.

Post-tasks/ refactor: workspace is driven by a task_id (not paper+task enum),
and the agent's fillable yaml lives at workspace/results/ (not HEPRecastData/).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from agent_runtime.workspace import build_workspace


# Public agents only — the private anneal agent is tested under
# tests/test_prompts_private.py when present locally.
AGENT_NAMES = ["simple"]


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
    assert "tasks/shared" in link or "ColliderBench" in link


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
    standalone description.toml — both blocks live in the .yaml file.
    """
    import yaml

    ws = clean_workspace(agent)
    results = ws / "results"
    assert results.is_dir()
    assert not list(results.glob("*.yml")), ".yml result templates are obsolete"
    yamls = list(results.glob("*.yaml"))
    assert yamls, "results/ should contain the null-filled histogram"
    assert not (results / "description.toml").exists(), (
        "description.toml should no longer be seeded — metadata lives "
        "inside the histogram .yaml file itself"
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


def test_disabled_delphes_policy_installs_stubs(repo_root, tmp_run_name, task_id):
    ws = build_workspace(
        repo_root,
        "simple",
        task_id,
        tmp_run_name,
        tool_policy={"disabled": ["delphes"]},
    )
    try:
        for rel in (
            "bin/DelphesHepMC3",
            "bin/DelphesROOT",
            "disabled_tools/delphes/DelphesHepMC3",
        ):
            assert (ws / rel).is_file()
            assert os.access(ws / rel, os.X_OK)

        proc = subprocess.run(
            [str(ws / "bin" / "DelphesHepMC3")],
            check=False,
            text=True,
            capture_output=True,
        )
        assert proc.returncode != 0
        assert "Delphes tool is disabled for this benchmark task." in proc.stderr
    finally:
        shutil.rmtree(repo_root / "runs" / tmp_run_name, ignore_errors=True)


def test_workspace_without_tool_policy_has_no_delphes_stub(clean_workspace):
    ws = clean_workspace("simple")
    assert not (ws / "disabled_tools" / "delphes" / "DelphesHepMC3").exists()


def test_invalid_task_raises_filenotfound(repo_root, tmp_run_name):
    with pytest.raises(FileNotFoundError):
        build_workspace(repo_root, "simple", "nonexistent-task-id", tmp_run_name)
