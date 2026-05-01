"""Evaluation context construction.

This layer combines resolved run paths, task configuration, and parsed
histograms into a single object that scorers, plotters, and judges can share.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import histio
from ._resolve import RunPaths

ALLOWED_METRIC_MODES = {"shape_norm", "shape", "yield"}
ALLOWED_PLOT_MODES = {"Events/bin", "Events/GeV"}
ALLOWED_REPORT_METRICS = {"baker_cousins", "mean_abs_frac_error"}


@dataclass(frozen=True)
class MetricConfig:
    tolerance: float = 0.0
    mode: str = "shape_norm"
    plot_mode: str = "Events/bin"
    report: tuple[str, ...] = ()

    @property
    def score_mode(self) -> str:
        """Output-facing name for the task scoring mode."""
        return self.mode


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    paper_ref: str
    data_filename: str
    header_name: str
    metrics: MetricConfig
    task_toml: dict[str, Any]


@dataclass(frozen=True)
class EvalContext:
    paths: RunPaths
    task: TaskSpec
    reference_histogram: histio.Histogram
    prediction_histogram: histio.Histogram
    reference_series: histio.Series
    prediction_series: histio.Series
    comparison: histio.SeriesComparison


def _metric_config(metrics_block: dict[str, Any]) -> MetricConfig:
    required = ("tolerance", "mode", "plot", "report")
    missing = [key for key in required if key not in metrics_block]
    if missing:
        raise ValueError(f"[metrics] missing required field(s): {', '.join(missing)}")

    tolerance = float(metrics_block.get("tolerance", 0.0))
    mode = str(metrics_block["mode"]).strip()
    plot_mode = str(metrics_block["plot"]).strip()
    report_raw = metrics_block["report"]
    if isinstance(report_raw, str):
        report = (report_raw,)
    else:
        report = tuple(str(x) for x in report_raw)

    if tolerance < 0:
        raise ValueError(f"[metrics].tolerance must be non-negative, got {tolerance}")
    if mode not in ALLOWED_METRIC_MODES:
        raise ValueError(
            f"[metrics].mode must be one of {sorted(ALLOWED_METRIC_MODES)}, got {mode!r}"
        )
    if plot_mode not in ALLOWED_PLOT_MODES:
        raise ValueError(
            f"[metrics].plot must be one of {sorted(ALLOWED_PLOT_MODES)}, got {plot_mode!r}"
        )
    if not report:
        raise ValueError("[metrics].report must be a non-empty list")
    unknown_report = sorted(set(report) - ALLOWED_REPORT_METRICS)
    if unknown_report:
        raise ValueError(f"[metrics].report contains unknown metric(s): {unknown_report}")

    return MetricConfig(
        tolerance=tolerance,
        mode=mode,
        plot_mode=plot_mode,
        report=report,
    )


def load_task_spec(task_id: str, tasks_root: Path) -> TaskSpec:
    task_dir = tasks_root / task_id
    task_toml_path = task_dir / "task.toml"
    task_toml = tomllib.loads(task_toml_path.read_text())
    task_block = task_toml.get("task") or {}
    metrics = _metric_config(task_toml.get("metrics") or {})

    template_files = sorted((task_dir / "template").glob("*.yaml"))
    if len(template_files) != 1:
        raise ValueError(
            f"Expected a single histogram file in {task_dir / 'template'}; "
            f"got {[p.name for p in template_files]}"
        )
    template = histio.load_histogram_yaml(template_files[0])
    if not template.series:
        raise ValueError(f"{template_files[0]}: no dependent variables")

    return TaskSpec(
        task_id=task_id,
        paper_ref=str(task_block.get("paper", "")).strip(),
        data_filename=template_files[0].name,
        header_name=template.series[0].name,
        metrics=metrics,
        task_toml=task_toml,
    )


def _agent_output_path(rp: RunPaths) -> Path:
    path = rp.results_dir / rp.data_filename
    if not path.is_file():
        raise FileNotFoundError(f"Agent output not found: {path}")
    return path


def build_eval_context(rp: RunPaths) -> EvalContext:
    from ._resolve import TASKS_ROOT

    task = load_task_spec(rp.task_id, TASKS_ROOT)
    ref_hist = histio.load_histogram_yaml(rp.reference_file)
    pred_hist = histio.load_histogram_yaml(_agent_output_path(rp))

    ref_series = histio.select_series(ref_hist, rp.header_name)
    pred_series = histio.select_series(pred_hist, rp.header_name)
    if ref_series is None:
        raise ValueError(f"Series {rp.header_name!r} not in reference {rp.reference_file.name}")
    if pred_series is None:
        raise ValueError(f"Series {rp.header_name!r} not in agent output {rp.data_filename}")

    comparison = histio.compare_series(ref_series, pred_series, ref_hist.bins)
    return EvalContext(
        paths=rp,
        task=task,
        reference_histogram=ref_hist,
        prediction_histogram=pred_hist,
        reference_series=ref_series,
        prediction_series=pred_series,
        comparison=comparison,
    )
