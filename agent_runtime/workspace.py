"""Shared workspace setup for agent runs.

Builds a bwrap-ready workspace at <repo_root>/<run_name>/workspace/ with:
  - templates/    copied from agent_dir/runtime/templates/workspace/
  - tools/        symlink → LHCRecastBench/tools/
  - bin/          merged symlinks from LHCRecastBench/bin/ + agent_dir/runtime/bin/
  - agent_context/   all *.md files under agent_dir (outside runtime/)
  - papers/       symlink → LHCRecastBench/papers/<paper>/for_agent/papers/
  - all other for_agent/ items copied into the workspace

If the paper PDF is missing, it is downloaded once into the canonical shared
location (LHCRecastBench/papers/<paper>/for_agent/papers/) rather than the workspace.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path


def build_workspace(
    repo_root: Path,
    agent_name: str,
    paper_ref: str,
    run_name: str,
) -> Path:
    """Create a fresh workspace under <repo_root>/<run_name>/workspace.

    agent_name must match a directory under agents/ (e.g. 'simple', 'baseline').
    Raises FileNotFoundError if LHCRecastBench/papers/<paper_ref>/for_agent/ is missing.
    Returns the workspace Path.
    """
    agent_dir = repo_root / "agents" / agent_name
    benchmark_dir = repo_root / "LHCRecastBench"
    # All runs live under runs/ so they're isolated from source and
    # already gitignored as a single tree.
    workspace = repo_root / "runs" / run_name / "workspace"

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

    # for_agent/ — papers/ is symlinked (RO, shared); everything else is copied.
    for_agent = benchmark_dir / "papers" / paper_ref / "for_agent"
    if not for_agent.is_dir():
        raise FileNotFoundError(f"Missing {for_agent}")
    for item in for_agent.iterdir():
        dest = workspace / item.name
        if item.is_dir() and item.name == "papers":
            dest.symlink_to(item.resolve())
        elif item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    # Fetch the paper PDF once into the canonical location if missing.
    pdf_path = for_agent / "papers" / f"{paper_ref}.pdf"
    if not pdf_path.exists():
        pdf_path.parent.mkdir(exist_ok=True)
        url = f"https://arxiv.org/pdf/{paper_ref}"
        print(f"Downloading: {url}")
        urllib.request.urlretrieve(url, pdf_path)

    return workspace
