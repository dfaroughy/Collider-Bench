"""Shared resolver for evaluation CLIs.

Every evaluation entry point takes a single positional `run_path`. This module
turns that path into the concrete directories each scorer needs (HEPRecastData,
artifact dir, eval output dir) plus the arxiv_id / task it should score
against — both read from run_info.json (walked up to find).

Accepted inputs (any of):
    runs/simulate_<paper>_.../                        (top-level; single-shot)
    runs/simulate_<paper>_.../workspace               (artifact dir; single-shot)
    runs/simulate_<paper>_.../validation/iter_NNN     (artifact dir; per-iter)
    runs/simulate_<paper>_.../workspace/HEPRecastData (scoring dir directly)
    runs/simulate_<paper>_.../validation/iter_NNN/HEPRecastData

Output (RunPaths) tells the scorer exactly where to read and write:
    hep_dir      — HEPRecastData/ with the yamls to score
    artifact_dir — parent of hep_dir (workspace/ or iter_NNN/)
    run_dir      — the run_info.json owner
    eval_dir     — where to save outputs. For single-shot runs lives at
                   <run_dir>/eval. For per-iter runs lives at <iter_dir>/eval
                   so iterations don't clobber each other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunPaths:
    run_dir: Path
    artifact_dir: Path
    hep_dir: Path
    eval_dir: Path
    arxiv_id: str
    task: str


def _find_hep_dir(target: Path) -> Path:
    """Locate HEPRecastData/ given any of the accepted input paths."""
    if target.name == "HEPRecastData" and target.is_dir():
        return target
    if (target / "HEPRecastData").is_dir():
        return target / "HEPRecastData"
    if (target / "workspace" / "HEPRecastData").is_dir():
        return target / "workspace" / "HEPRecastData"
    # Multi-iter layout: pick the latest iter under validation/.
    validation = target / "validation"
    if validation.is_dir():
        iters = sorted(p for p in validation.iterdir() if p.is_dir() and p.name.startswith("iter_"))
        for it in reversed(iters):
            if (it / "HEPRecastData").is_dir():
                return it / "HEPRecastData"
    raise FileNotFoundError(f"No HEPRecastData/ found under {target}")


def _find_run_info(start: Path, max_up: int = 5) -> Path:
    """Walk up from start looking for run_info.json."""
    p = start.resolve()
    for _ in range(max_up):
        if (p / "run_info.json").is_file():
            return p / "run_info.json"
        if p.parent == p:
            break
        p = p.parent
    raise FileNotFoundError(f"run_info.json not found at or above {start}")


def resolve_run(target: str | Path) -> RunPaths:
    """Resolve a single `run_path` argument into concrete evaluation paths."""
    t = Path(target).resolve()
    if not t.exists():
        raise FileNotFoundError(f"{target} does not exist")
    if not t.is_dir():
        raise NotADirectoryError(f"{target} is not a directory")

    hep_dir = _find_hep_dir(t)
    artifact_dir = hep_dir.parent

    info_path = _find_run_info(artifact_dir)
    run_dir = info_path.parent
    info = json.loads(info_path.read_text())

    arxiv_id = (info.get("paper_ref") or "").strip()
    if not arxiv_id:
        raise ValueError(f"paper_ref missing from {info_path}")
    # Older runs without a task field scored against "recast".
    task = (info.get("task") or "recast").strip()

    # Per-iter runs live under <run_dir>/validation/iter_NNN/. Keep their eval
    # outputs scoped to the iter so iterations don't clobber one another.
    is_iter = artifact_dir.parent.name == "validation" and artifact_dir.name.startswith("iter_")
    eval_dir = (artifact_dir if is_iter else run_dir) / "eval"

    return RunPaths(
        run_dir=run_dir,
        artifact_dir=artifact_dir,
        hep_dir=hep_dir,
        eval_dir=eval_dir,
        arxiv_id=arxiv_id,
        task=task,
    )
