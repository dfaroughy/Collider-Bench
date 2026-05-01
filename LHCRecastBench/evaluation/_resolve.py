"""Shared resolver for evaluation CLIs.

Every evaluation entry point takes a single positional `run_path`. This module
turns that path into the concrete paths each scorer needs and pulls the task
identity out of run_info.json + task.toml + the template histogram file.

Accepted inputs (any of):
    runs/<group>/<task_id>_<hex>/                        (top-level single-shot)
    runs/<group>/<task_id>_<hex>/workspace               (artifact dir)
    runs/<group>/<task_id>_<hex>/validation/iter_NNN     (per-iter artifact)
    runs/<group>/<task_id>_<hex>/workspace/results       (results dir directly)
    <task_id>_<hex>                                      (bare name — scanned under runs/*/)

Output (RunPaths):
    run_dir        — the dir containing run_info.json
    artifact_dir   — workspace/ or iter_NNN/
    results_dir    — <artifact_dir>/results  (agent's filled yaml)
    eval_dir       — where scorer outputs go
    task_id        — task identifier (e.g. sus-16-046_sim-TChiWg)
    paper_ref      — paper (e.g. CMS-SUS-16-046)
    reference_file — tasks/shared/<paper>/reference/<data_file>
    data_filename  — the histogram yaml filename (e.g. histogram_TChiWg.yaml)
    header_name    — series name to match inside the yaml (e.g. TChiWg_700)
    score_mode     — [metrics].mode: "shape_norm", "shape", or "yield"
    plot_mode      — "Events/bin" or "Events/GeV"
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent  # .../LHCRecastBench
TASKS_ROOT = BENCHMARK_ROOT / "tasks"

# Default per-bin log-normal systematic when a task does not declare one.
# Code default is 0.0 (statistical-only). Each task.toml in the benchmark
# overrides this via `[metrics].tolerance` (typically 0.05).
DEFAULT_SYSTEMATIC_PCT = 0.0


@dataclass
class RunPaths:
    run_dir: Path
    artifact_dir: Path
    results_dir: Path
    eval_dir: Path
    task_id: str
    paper_ref: str
    reference_file: Path
    data_filename: str
    header_name: str
    systematic_pct: float = DEFAULT_SYSTEMATIC_PCT
    score_mode: str = "shape_norm"
    plot_mode: str = "Events/bin"


def _find_results_dir(target: Path) -> Path:
    """Locate the agent's results/ dir given any accepted input path."""
    if target.name == "results" and target.is_dir():
        return target
    if (target / "results").is_dir():
        return target / "results"
    if (target / "workspace" / "results").is_dir():
        return target / "workspace" / "results"
    validation = target / "validation"
    if validation.is_dir():
        iters = sorted(p for p in validation.iterdir() if p.is_dir() and p.name.startswith("iter_"))
        for it in reversed(iters):
            if (it / "results").is_dir():
                return it / "results"
    raise FileNotFoundError(f"No results/ found under {target}")


def _find_run_info(start: Path, max_up: int = 6) -> Path:
    """Walk up from start looking for run_info.json."""
    p = start.resolve()
    for _ in range(max_up):
        if (p / "run_info.json").is_file():
            return p / "run_info.json"
        if p.parent == p:
            break
        p = p.parent
    raise FileNotFoundError(f"run_info.json not found at or above {start}")


def _resolve_bare_name(t: Path) -> Path:
    """Given a bare run name, find it under runs/<group>/<name>/."""
    runs = Path("runs")
    if not runs.is_dir():
        return t
    # Direct hit at runs/<name>
    direct = runs / t
    if direct.exists():
        return direct
    # Scan groups: runs/<group>/<name>
    for group in runs.iterdir():
        if not group.is_dir():
            continue
        candidate = group / t
        if candidate.exists():
            return candidate
    return t


def _load_task_identity(task_id: str) -> tuple[str, str, str, float, str, str]:
    """Return (paper_ref, data_filename, header_name, systematic_pct, score_mode, plot_mode).

    paper_ref comes from task.toml's [task].paper. data_filename is the
    single .yaml histogram file under tasks/<task_id>/template/.
    header_name is taken from dependent_variables[0].header.name inside
    that file. systematic_pct is read from task.toml's [metrics].tolerance
    (a per-task scoring knob, not a data attribute); falls back to
    DEFAULT_SYSTEMATIC_PCT (0.0 = stats-only) when absent.
    plot_mode controls the yield-panel display only; scoring always uses
    raw bin-integrated values from the YAML.
    """
    from .context import ALLOWED_METRIC_MODES, ALLOWED_PLOT_MODES, load_task_spec

    task = load_task_spec(task_id, TASKS_ROOT)
    if not task.paper_ref:
        raise ValueError(f"{TASKS_ROOT / task_id / 'task.toml'}: [task].paper missing")
    if task.metrics.tolerance < 0:
        raise ValueError(
            f"{TASKS_ROOT / task_id / 'task.toml'}: [metrics].tolerance must be non-negative, "
            f"got {task.metrics.tolerance}"
        )
    if task.metrics.score_mode not in ALLOWED_METRIC_MODES:
        raise ValueError(
            f"{TASKS_ROOT / task_id / 'task.toml'}: [metrics].mode must be one of "
            f"{sorted(ALLOWED_METRIC_MODES)}, got {task.metrics.score_mode!r}"
        )
    if task.metrics.plot_mode not in ALLOWED_PLOT_MODES:
        raise ValueError(
            f"{TASKS_ROOT / task_id / 'task.toml'}: [metrics].plot must be one of "
            f"{sorted(ALLOWED_PLOT_MODES)}, got {task.metrics.plot_mode!r}"
        )
    return (
        task.paper_ref,
        task.data_filename,
        task.header_name,
        task.metrics.tolerance,
        task.metrics.score_mode,
        task.metrics.plot_mode,
    )


def _reference_file(paper_ref: str, data_filename: str) -> Path:
    """Locate the ground-truth file under tasks/shared/<paper>/reference/."""
    base = TASKS_ROOT / "shared" / paper_ref / "reference"
    candidate = base / data_filename
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"No reference histogram for {paper_ref}: tried {candidate}")


def resolve_run(target: str | Path) -> RunPaths:
    """Resolve a single `run_path` argument into concrete evaluation paths."""
    t = Path(target)
    if not t.exists() and not t.is_absolute():
        t = _resolve_bare_name(t)
    t = t.resolve()
    if not t.exists():
        raise FileNotFoundError(f"{target} does not exist")
    if not t.is_dir():
        raise NotADirectoryError(f"{target} is not a directory")

    results_dir = _find_results_dir(t)
    artifact_dir = results_dir.parent

    info_path = _find_run_info(artifact_dir)
    run_dir = info_path.parent
    info = json.loads(info_path.read_text())

    task_id = str(info.get("task_id") or "").strip()
    if not task_id:
        raise ValueError(f"task_id missing from {info_path}")

    (
        paper_ref,
        data_filename,
        header_name,
        sys_pct,
        score_mode,
        plot_mode,
    ) = _load_task_identity(task_id)
    reference_file = _reference_file(paper_ref, data_filename)

    # Per-iter runs live under <run_dir>/validation/iter_NNN/. Scope eval output
    # to the iter so iterations don't clobber one another. The artifact_dir
    # may be the iter dir itself (legacy iterative layout) or its workspace/
    # subdir (anneal layout) — walk up to find the iter_NNN ancestor.
    iter_dir: Path | None = None
    p = artifact_dir
    while p != p.parent:
        if p.name.startswith("iter_") and p.parent.name == "validation":
            iter_dir = p
            break
        p = p.parent
    eval_dir = (iter_dir if iter_dir is not None else run_dir) / "eval"

    return RunPaths(
        run_dir=run_dir,
        artifact_dir=artifact_dir,
        results_dir=results_dir,
        eval_dir=eval_dir,
        task_id=task_id,
        paper_ref=paper_ref,
        reference_file=reference_file,
        data_filename=data_filename,
        header_name=header_name,
        systematic_pct=sys_pct,
        score_mode=score_mode,
        plot_mode=plot_mode,
    )
