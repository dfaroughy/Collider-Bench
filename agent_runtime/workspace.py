"""Shared workspace setup for agent runs.

Builds a bwrap-ready workspace at <repo_root>/runs/<run_name>/workspace/ with:
  - templates/       copied from agent_dir/runtime/templates/workspace/
  - tools/           symlink → LHCRecastBench/tools/
  - bin/             merged symlinks from LHCRecastBench/bin/ + agent_dir/runtime/bin/
  - agent_context/   all *.md files under agent_dir (outside runtime/) + the
                     task-specific TASK.md copied from the benchmark
  - papers/          symlink → LHCRecastBench/papers/<paper>/shared/papers/
  - HEPRecastData/   copied from LHCRecastBench/papers/<paper>/tasks/<task>/templates/
  - shared/          task-invariant inputs (object_efficiencies/, …) copied in

If the paper PDF is missing, it is downloaded once into the canonical shared
location rather than the workspace.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

# Known task names; the default matches the legacy behaviour (full recast).
KNOWN_TASKS = ("validate", "simulate", "recast")
DEFAULT_TASK = "recast"


def paper_task_dir(repo_root: Path, paper_ref: str, task: str) -> Path:
    """Canonical path to a task's benchmark content."""
    return repo_root / "LHCRecastBench" / "papers" / paper_ref / "tasks" / task


def paper_shared_dir(repo_root: Path, paper_ref: str) -> Path:
    """Canonical path to a paper's task-invariant inputs."""
    return repo_root / "LHCRecastBench" / "papers" / paper_ref / "shared"


def build_workspace(
    repo_root: Path,
    agent_name: str,
    paper_ref: str,
    run_name: str,
    task: str = DEFAULT_TASK,
) -> Path:
    """Create a fresh workspace under <repo_root>/runs/<run_name>/workspace.

    agent_name must match a directory under agents/ (e.g. 'simple', 'baseline').
    task selects which tasks/<task>/ directory of the paper to seed from.
    Raises FileNotFoundError if the paper or task directory is missing.
    Returns the workspace Path.
    """
    agent_dir = repo_root / "agents" / agent_name
    benchmark_dir = repo_root / "LHCRecastBench"
    # All runs live under runs/ so they're isolated from source and
    # already gitignored as a single tree.
    workspace = repo_root / "runs" / run_name / "workspace"

    shared_dir = paper_shared_dir(repo_root, paper_ref)
    task_dir = paper_task_dir(repo_root, paper_ref, task)
    if not shared_dir.is_dir():
        raise FileNotFoundError(f"Missing {shared_dir}. Create shared/ under the paper dir first.")
    if not task_dir.is_dir():
        available = [p.name for p in (task_dir.parent).iterdir() if p.is_dir()]
        raise FileNotFoundError(f"Missing {task_dir}. Available tasks for {paper_ref}: {available}")
    task_md = task_dir / "TASK.md"
    templates_src = task_dir / "templates" / "HEPRecastData"
    if not task_md.is_file():
        raise FileNotFoundError(f"Missing {task_md}")
    if not templates_src.is_dir():
        raise FileNotFoundError(f"Missing {templates_src}")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    # Workspace templates (stubs like datasets.yaml, report.md)
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

    # Agent instructions (AGENTS.md, TOOLS.md, SOUL.md, skills/*.md, ...)
    agent_context = workspace / "agent_context"
    agent_context.mkdir()
    for src in agent_dir.rglob("*.md"):
        rel = src.relative_to(agent_dir)
        if rel.parts[0] == "runtime":
            continue
        dest = agent_context / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text()
        if paper_ref:
            text = text.replace("{arxiv_id}", paper_ref)
        dest.write_text(text)

    # Benchmark-provided task spec — copied into agent_context so it sits
    # alongside AGENTS.md and the agent's own instructions.
    task_text = task_md.read_text()
    if paper_ref:
        task_text = task_text.replace("{arxiv_id}", paper_ref)
    (agent_context / "TASK.md").write_text(task_text)

    # Task-specific HEPRecastData templates — agent fills these in place.
    shutil.copytree(templates_src, workspace / "HEPRecastData")

    # Task-invariant shared inputs: papers/ is symlinked (PDF is large and
    # immutable); any other subdirs (object_efficiencies/, etc.) are copied.
    for item in shared_dir.iterdir():
        dest = workspace / item.name
        if item.is_dir() and item.name == "papers":
            dest.symlink_to(item.resolve())
        elif item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    # Fetch the paper PDF once into the canonical shared location if missing.
    pdf_path = shared_dir / "papers" / f"{paper_ref}.pdf"
    if not pdf_path.exists():
        pdf_path.parent.mkdir(exist_ok=True)
        url = f"https://arxiv.org/pdf/{paper_ref}"
        print(f"Downloading: {url}")
        urllib.request.urlretrieve(url, pdf_path)

    return workspace
