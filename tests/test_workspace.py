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


# Public agents only.
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


def test_disabled_delphes_is_blind_and_keeps_simulate_valid(repo_root, tmp_run_name, task_id):
    """Delphes is now a *blind* ablation (load-bearing tool, accept the hole):
    no stub, scrubbed docs, and the scrubbed bin/simulate must stay valid bash."""
    ws = build_workspace(
        repo_root,
        "simple",
        task_id,
        tmp_run_name,
        tool_policy={"disabled": ["delphes"]},
    )
    try:
        # No visible stub anywhere — the old DelphesHepMC3 stub is gone.
        assert not (ws / "bin" / "DelphesHepMC3").exists()
        assert not (ws / "disabled_tools" / "delphes").exists()

        agents_md = (ws / "agent_context" / "AGENTS.md").read_text()
        tools_md = (ws / "agent_context" / "TOOLS.md").read_text()
        assert "delphes" not in agents_md.lower()
        assert "delphes" not in tools_md.lower()
        # Sibling tools survive the scrub (only Delphes is removed).
        assert "mg5" in agents_md.lower() and "pythia" in tools_md.lower()

        root = ws.parent / ".tool_policy" / "delphes"
        assert (root / "opt_sim").is_dir()
        assert not any((root / "opt_sim").iterdir())  # empty /opt/sim mask

        shadow = ws.parent / ".tool_policy" / "_docs"  # shared doc shadow
        sim_md = (shadow / "CLI" / "SIMULATE.md").read_text()
        assert "delphes" not in sim_md.lower()
        assert "## Output ROOT schema" in sim_md  # later sections intact
        assert sim_md.count("```") % 2 == 0  # code fences still balanced

        sim = shadow / "simulate"
        assert "delphes" not in sim.read_text().lower()
        proc = subprocess.run(["bash", "-n", str(sim)], check=False, text=True, capture_output=True)
        assert proc.returncode == 0, f"scrubbed bin/simulate is invalid bash: {proc.stderr}"
    finally:
        shutil.rmtree(repo_root / "runs" / tmp_run_name, ignore_errors=True)


def test_workspace_without_tool_policy_keeps_delphes(clean_workspace):
    ws = clean_workspace("simple")
    assert not (ws / "disabled_tools" / "delphes").exists()
    assert not (ws.parent / ".tool_policy").exists()
    assert "delphes" in (ws / "agent_context" / "TOOLS.md").read_text().lower()


def test_disabled_prospino_is_blind(repo_root, tmp_run_name, task_id):
    """prospino ablation must leave NO agent-visible trace anywhere."""
    ws = build_workspace(
        repo_root,
        "simple",
        task_id,
        tmp_run_name,
        tool_policy={"disabled": ["prospino"]},
    )
    try:
        # No bin shim, no stub — the wrapper is simply gone from $PATH.
        assert not (ws / "bin" / "prospino").exists()

        # Per-run agent docs are scrubbed (case-insensitive), but other
        # tools survive — only prospino is removed.
        agents_md = (ws / "agent_context" / "AGENTS.md").read_text()
        tools_md = (ws / "agent_context" / "TOOLS.md").read_text()
        assert "prospino" not in agents_md.lower()
        assert "prospino" not in tools_md.lower()
        assert "Delphes" in agents_md and "delphes" in tools_md.lower()

        # Ablation artifacts live OUTSIDE the agent-visible workspace.
        assert not (ws / "disabled_tools" / "prospino").exists()
        root = ws.parent / ".tool_policy" / "prospino"
        assert (root / "opt_sim").is_dir()
        assert not any((root / "opt_sim").iterdir())  # empty /opt/sim mask

        shadow = ws.parent / ".tool_policy" / "_docs"  # shared doc shadow
        assert not (shadow / "CLI" / "PROSPINO.md").exists()
        # The impl module + any stale .pyc must be gone too — every entry
        # point routes through ColliderBench.tools.CLI.prospino.
        assert not (shadow / "CLI" / "prospino.py").exists()
        assert not list((shadow / "CLI").rglob("*prospino*.pyc"))
        assert not (shadow / "CLI" / "__pycache__").exists()
        assert (shadow / "CLI" / "SIMULATE.md").is_file()  # other docs kept
        assert (shadow / "CLI" / "hepdata.py").is_file()  # sibling tools kept
        assert "prospino" not in (shadow / "TOOLS.md").read_text().lower()
        sim = (shadow / "simulate").read_text()
        assert "prospino" not in sim.lower()
        assert os.access(shadow / "simulate", os.X_OK)
    finally:
        shutil.rmtree(repo_root / "runs" / tmp_run_name, ignore_errors=True)


def test_disabling_both_tools_shares_one_scrubbed_doc_shadow(repo_root, tmp_run_name, task_id):
    """Both blind tools → ONE shared shadow scrubbed of BOTH (a per-tool
    shadow would collide at the same mount destination → podman exit 125)."""
    ws = build_workspace(
        repo_root,
        "simple",
        task_id,
        tmp_run_name,
        tool_policy={"disabled": ["prospino", "delphes"]},
    )
    try:
        assert not (ws / "bin" / "prospino").exists()
        for tool in ("prospino", "delphes"):
            assert (ws.parent / ".tool_policy" / tool / "opt_sim").is_dir()

        shadow = ws.parent / ".tool_policy" / "_docs"
        assert not (shadow / "CLI" / "PROSPINO.md").exists()
        # The single shared shadow is scrubbed of BOTH tools at once.
        for name in ("prospino", "delphes"):
            assert name not in (shadow / "TOOLS.md").read_text().lower()
            assert name not in (shadow / "simulate").read_text().lower()
            for md in (shadow / "CLI").rglob("*.md"):
                assert name not in md.read_text().lower()
        proc = subprocess.run(
            ["bash", "-n", str(shadow / "simulate")],
            check=False,
            text=True,
            capture_output=True,
        )
        assert proc.returncode == 0, f"scrubbed bin/simulate invalid bash: {proc.stderr}"
        # agent_context scrubbed of both.
        am = (ws / "agent_context" / "AGENTS.md").read_text().lower()
        assert "prospino" not in am and "delphes" not in am
    finally:
        shutil.rmtree(repo_root / "runs" / tmp_run_name, ignore_errors=True)


def test_invalid_task_raises_filenotfound(repo_root, tmp_run_name):
    with pytest.raises(FileNotFoundError):
        build_workspace(repo_root, "simple", "nonexistent-task-id", tmp_run_name)
