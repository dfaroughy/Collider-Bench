"""Shared workspace setup for agent runs.

Builds a bwrap-ready workspace at <repo_root>/runs/<run_dir>/workspace/ with:
  - templates/           agent runtime workspace stubs (report.md, datasets.yaml …)
  - tools/               symlink → LHCRecastBench/tools/
  - bin/                 merged symlinks from LHCRecastBench/bin/ + agent_dir/runtime/bin/
  - agent_context/       all *.md files under agent_dir (outside runtime/) + TASK.md
  - results/             copy of tasks/<task_id>/template/  (null-filled yamls;
                         agent fills in place — no separate output dir)
  - papers/              symlink → tasks/shared/<paper>/paper/
                         (materialized into a real dir by sandbox_command)
  - object_efficiencies/ copy of tasks/shared/<paper>/object_efficiencies/
                         merged with tasks/<task_id>/artifacts/ if present
                         (task-specific files override shared ones)

The per-task task.toml is intentionally NOT exposed to the agent — it is
purely metadata for the harness.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from agent_runtime import paths
from agent_runtime.config import load_task_toml


def build_workspace(
    repo_root: Path,
    agent_name: str,
    task_id: str,
    run_dir: str,
) -> Path:
    """Create a fresh workspace under <repo_root>/runs/<run_dir>/workspace.

    agent_name must match a directory under agents/ (e.g. 'simple', 'baseline').
    task_id must match a directory under LHCRecastBench/tasks/.
    Raises FileNotFoundError if prerequisites are missing. Returns the workspace Path.
    """
    agent_dir = repo_root / "agents" / agent_name
    benchmark_dir = paths.benchmark_dir(repo_root)
    workspace = repo_root / "runs" / run_dir / "workspace"

    toml = load_task_toml(repo_root, task_id)
    paper_ref = (toml.get("task") or {}).get("paper")
    if not paper_ref:
        raise ValueError(f"task.toml: [task].paper is required (task_id={task_id})")

    task_dir = paths.task_dir(repo_root, task_id)
    shared = paths.shared_paper_dir(repo_root, paper_ref)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Missing task dir: {task_dir}")
    if not shared.is_dir():
        raise FileNotFoundError(f"Missing shared paper dir: {shared}")

    task_md = task_dir / "TASK.md"
    template_src = task_dir / "template"
    if not task_md.is_file():
        raise FileNotFoundError(f"Missing {task_md}")
    if not template_src.is_dir():
        raise FileNotFoundError(f"Missing {template_src}")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    # Workspace templates (stubs like datasets.yaml, report.md) from the agent
    template_dir = agent_dir / "runtime" / "templates" / "workspace"
    if template_dir.exists():
        for f in template_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, workspace / f.name)

    # tools/ is a symlink (LHCRecastBench/tools is ro-bind-mounted by sandbox.py)
    (workspace / "tools").symlink_to(benchmark_dir / "tools")

    # bin/ merges benchmark + agent scripts
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    for script in (benchmark_dir / "bin").iterdir():
        (bin_dir / script.name).symlink_to(script)
    for script in (agent_dir / "runtime" / "bin").iterdir():
        (bin_dir / script.name).symlink_to(script)

    # Agent instructions (AGENTS.md, SOUL.md, skills/*.md, ...). TOOLS.md comes
    # from the benchmark (canonical index), not the per-agent copy.
    agent_context = workspace / "agent_context"
    agent_context.mkdir()
    for src in agent_dir.rglob("*.md"):
        rel = src.relative_to(agent_dir)
        if rel.parts[0] == "runtime":
            continue
        if rel.name == "TOOLS.md":
            continue
        dest = agent_context / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text()
        if paper_ref:
            text = text.replace("{arxiv_id}", paper_ref)
        dest.write_text(text)

    # Canonical TOOLS.md seeded from the benchmark.
    tools_index = benchmark_dir / "tools" / "TOOLS.md"
    if tools_index.is_file():
        shutil.copy2(tools_index, agent_context / "TOOLS.md")

    # Task spec — copied into agent_context alongside AGENTS.md.
    task_text = task_md.read_text()
    if paper_ref:
        task_text = task_text.replace("{arxiv_id}", paper_ref)
    (agent_context / "TASK.md").write_text(task_text)

    # results/ — null-filled yaml skeleton (metadata embedded at top of
    # the yaml itself: instructions, target, luminosity, …), copied from
    # the task's template/. Agent fills nulls in place. task.toml itself
    # is intentionally NOT copied (harness metadata only).
    results = workspace / "results"
    results.mkdir()
    for src in template_src.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(template_src)
        dest = results / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # papers/ — symlink to the shared paper dir (PDF is large and immutable).
    shared_paper = shared / "paper"
    if shared_paper.is_dir():
        (workspace / "papers").symlink_to(shared_paper.resolve())

    # object_efficiencies/ — copy the shared pool; overlay task-specific
    # artifacts/ on top (task file with same name wins).
    obj_eff = workspace / "object_efficiencies"
    shared_eff = shared / "object_efficiencies"
    if shared_eff.is_dir():
        shutil.copytree(shared_eff, obj_eff, dirs_exist_ok=True)
    task_artifacts = task_dir / "artifacts"
    if task_artifacts.is_dir():
        obj_eff.mkdir(exist_ok=True)
        for src in task_artifacts.iterdir():
            if src.is_file():
                shutil.copy2(src, obj_eff / src.name)

    # Fetch the paper PDF once into the canonical shared location if missing.
    pdf_path = shared / "paper" / f"{paper_ref}.pdf"
    if not pdf_path.exists():
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://arxiv.org/pdf/{paper_ref}"
        print(f"Downloading: {url}")
        urllib.request.urlretrieve(url, pdf_path)

    return workspace
