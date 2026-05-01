#!/usr/bin/env python3
"""Unified scorer for recast results.

The public CLI and score.json schema remain compatibility-preserving, but the
implementation now runs through shared histogram/context objects and a metric
registry. New metrics should be added under ``evaluation/metrics/`` rather
than extending this file.
"""

from __future__ import annotations

import argparse
import json
import math

from .context import build_eval_context
from .metrics import get_default_metrics
from .metrics.baker_cousins import (
    DEFAULT_N_TOYS as DEFAULT_N_TOYS,
    DEFAULT_SYSTEMATIC as DEFAULT_SYSTEMATIC,
    SCORE_TAU as SCORE_TAU,
    _lam_norm_vec as _lam_norm_vec,
    _lam_shape_vec as _lam_shape_vec,
    _lam_total_vec as _lam_total_vec,
    _toy_calibrated_z as _toy_calibrated_z,
    bc_statistics as bc_statistics,
    bounded_score,
)
from .metrics.mean_abs_frac_error import (
    bin_fractional_error_percent as bin_fractional_error_percent,
)


def _bounded_score(z: float) -> float:
    return bounded_score(z)


def _metric_result_to_json(result) -> dict:
    out = {
        "status": result.status,
        "components": result.components,
        "primary_values": result.primary_values,
        "diagnostics": result.diagnostics,
    }
    if result.error:
        out["error"] = result.error
    return out


def _diagnosis(shape_score: float, norm_score: float) -> str:
    if shape_score > 0.7 and norm_score > 0.7:
        return "GOOD"
    if shape_score > 0.7:
        return "SHAPE OK, NORM BAD"
    if norm_score > 0.7:
        return "SHAPE BAD, NORM OK"
    return "BOTH BAD"


def _objective_policy(score_mode: str) -> str:
    if score_mode == "shape":
        return "baker_cousins.shape_score"
    if score_mode == "yield":
        return "baker_cousins.normalization_score"
    return "geomean(baker_cousins.shape_score,baker_cousins.normalization_score)"


def _combined_score(score_mode: str, shape_score: float, norm_score: float) -> float:
    policy = _objective_policy(score_mode)
    if policy == "baker_cousins.shape_score":
        return shape_score
    if policy == "baker_cousins.normalization_score":
        return norm_score
    if policy == "geomean(baker_cousins.shape_score,baker_cousins.normalization_score)":
        return math.sqrt(shape_score * norm_score) if shape_score > 0 and norm_score > 0 else 0.0
    raise ValueError(f"Unknown objective policy: {policy}")


def _score_note(score_mode: str) -> str | None:
    if score_mode == "shape":
        return "shape-only task: normalization diagnostic is not included in overall_combined"
    if score_mode == "yield":
        return "yield-only task: shape diagnostic is not included in overall_combined"
    return None


def _score_series_from_metrics(context, metric_results: dict) -> dict:
    """Build the legacy ``series`` block from registry metric results."""
    comp = context.comparison
    series: dict = {
        "name": comp.name,
        "n_bins": comp.n_bins,
        "n_filled": comp.n_filled,
    }

    bfe = metric_results.get("mean_abs_frac_error")
    if bfe is not None and bfe.diagnostics:
        series["bin_fractional_error"] = bfe.diagnostics

    bc = metric_results.get("baker_cousins")
    if bc is None:
        return series
    if bc.status != "ok":
        series["bc_error"] = bc.error or "baker_cousins failed"
        return series

    series["shape"] = bc.components["shape"]
    series["normalization"] = bc.components["normalization"]
    series["total"] = bc.components["total"]
    series["calibration"] = bc.diagnostics["calibration"]
    series["diagnosis"] = _diagnosis(
        float(bc.primary_values["shape_score"]),
        float(bc.primary_values["normalization_score"]),
    )
    return series


def score_run(rp) -> dict:
    """Score one task's filled histogram against its reference."""
    try:
        context = build_eval_context(rp)
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}

    report_metrics = context.task.metrics.report
    runner = get_default_metrics()
    metric_results = runner.compute(context, report_metrics)
    series = _score_series_from_metrics(context, metric_results)

    sys_frac = context.task.metrics.tolerance
    score_mode = context.task.metrics.score_mode
    objective = _objective_policy(score_mode)
    agent_path = context.prediction_histogram.path

    output: dict = {
        "task_id": rp.task_id,
        "paper": rp.paper_ref,
        "header_name": rp.header_name,
        "reference": str(rp.reference_file),
        "agent_output": str(agent_path),
        "systematic_pct": sys_frac,
        "score_mode": score_mode,
        "objective": objective,
        "metrics": {
            name: _metric_result_to_json(result) for name, result in metric_results.items()
        },
        "series": series,
        "n_bins": series["n_bins"],
        "n_filled": series["n_filled"],
    }

    bc = metric_results.get("baker_cousins")
    if bc is not None and bc.status == "ok":
        shape_score = float(bc.primary_values["shape_score"])
        norm_score = float(bc.primary_values["normalization_score"])
        output["overall_shape"] = round(shape_score, 3)
        output["overall_normalization"] = round(norm_score, 3)
        output["overall_combined"] = round(
            _combined_score(score_mode, shape_score, norm_score),
            3,
        )
        note = _score_note(score_mode)
        if note:
            output["score_note"] = note

    return output


# ── Display ─────────────────────────────────────────────────────────────────


def print_scores(result: dict) -> None:
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    print(f"\n  Task: {result['task_id']}")
    print(f"  Paper: {result['paper']}   Series: {result['header_name']}")
    if result.get("score_mode") == "shape":
        print("  Score mode: shape-only (normalization is diagnostic)")
    elif result.get("score_mode") == "yield":
        print("  Score mode: yield-only (shape is diagnostic)")
    print(f"  Objective: {result.get('objective', 'unknown')}")
    print(f"  {'=' * 68}")

    s = result["series"]
    if "shape" in s:
        sh = s["shape"]
        no = s["normalization"]
        bfe = s.get("bin_fractional_error", {})
        print(
            f"    {s['name']}: filled {s['n_filled']}/{s['n_bins']}  "
            f"shape p={sh['p_value']:.2g}  "
            f"norm p={no['p_value']:.2g}  "
            f"mean bin frac err={bfe.get('mean_abs_frac_error_percent', float('nan')):.1f}%  "
            f"[{s['diagnosis']}]"
        )
    else:
        print(f"    {s['name']}: filled {s['n_filled']}/{s['n_bins']}  (no Baker-Cousins)")

    print(f"\n  {'=' * 68}")
    if "overall_shape" in result:
        print(
            f"  Shape: {result['overall_shape']:.2f}   "
            f"Norm: {result['overall_normalization']:.2f}   "
            f"Combined: {result['overall_combined']:.2f}"
        )
    print()


def print_comparison(results: list[dict]) -> None:
    print(f"\n  {'Task':<52s} {'Shape':>7s} {'Norm':>7s} {'Comb':>7s}")
    print(f"  {'─' * 75}")
    for r in results:
        if "error" in r:
            label = r.get("task_id") or r.get("agent_output") or "?"
            print(f"  {str(label):<52s}  {r['error']}")
            continue
        label = str(r.get("task_id", ""))[:52]
        print(
            f"  {label:<52s} "
            f"{r.get('overall_shape', 0):>7.2f} "
            f"{r.get('overall_normalization', 0):>7.2f} "
            f"{r.get('overall_combined', 0):>7.2f}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="score",
        description="Score the filled histogram against its task's reference. "
        "Task id, paper, and data file are read from run_info.json + task.toml.",
    )
    parser.add_argument(
        "run_path",
        nargs="+",
        help="Run directory, workspace, iter dir, or results dir. Multiple paths compare.",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON to stdout")
    args = parser.parse_args()

    from ._resolve import resolve_run

    results: list[tuple] = []
    for p in args.run_path:
        try:
            rp = resolve_run(p)
        except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
            print(f"  ERROR: {p}: {exc}")
            continue
        result = score_run(rp)
        rp.eval_dir.mkdir(parents=True, exist_ok=True)
        out = rp.eval_dir / "score.json"
        out.write_text(json.dumps(result, indent=2))
        results.append((rp, result, out))

    if args.json:
        print(json.dumps([r[1] for r in results], indent=2))
        return

    if len(results) > 1:
        print_comparison([r[1] for r in results])
    for _rp, result, out in results:
        print_scores(result)
        print(f"\n  Saved to {out}")


if __name__ == "__main__":
    main()
