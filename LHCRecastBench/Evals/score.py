#!/usr/bin/env python3
"""Score a recast run: write `<run_dir>/eval/score.json` and per-task plots.

Reads from:
    <run_dir>/run_info.json                    # task_id, paper_ref
    <run_dir>/workspace/results/<file>.yaml    # agent's filled histogram
    LHCRecastBench/tasks/<task_id>/task.toml   # paper, [metrics] mode/tolerance/plot
    LHCRecastBench/tasks/<task_id>/template/<file>.yaml  # picks data_filename + header_name
    LHCRecastBench/tasks/shared/<paper>/reference/<file>.yaml  # truth

Writes:
    <run_dir>/eval/score.json
    <run_dir>/eval/plots/<stem>_yield.png
    <run_dir>/eval/plots/<stem>_shape.png

Schema:
    {
      "task_id": ..., "paper": ..., "header_name": ...,
      "n_bins": N, "n_filled": M, "score_mode": "shape" | "yield" | "shape_norm",
      "shape": {
        "mean_abs_frac_error_pct":  float,    # bin-by-bin relative err, post-norm
        "p_value":                  float,    # BC λ_shape, toy-calibrated
        "jensen_shannon":           float,    # base-2 log, in [0, 1]
        "jensen_shannon_dist":      float,    # sqrt(JSD), the true metric
        "d_bar":                    float,    # ½·L1(p,q) = TV distance, in [0, 1]
        "d_max":                    float,    # max_i |p_i - q_i|, in [0, 1] (worst-bin)
        "dmax_dbar_ratio":          float     # d_max / d_bar, in [1/K, 1]; 1 = single-bin failure
      },
      "normalization": {
        "mean_abs_frac_error_pct":  float,    # |sum(obs) - sum(ref)| / sum(ref) * 100
        "Delta":                    float,    # raw fraction (= mean_abs_frac_error_pct / 100)
        "rmsle":                    float     # root-mean-squared log error on raw bin yields
      }
    }

Any metric returns `null` when undefined (e.g. zero total yield).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import histograms, plotting
from .metrics import (
    Delta,
    baker_cousins_p_value,
    jensen_shannon,
    mean_abs_frac_error_pct,
    per_bin_disagreement,
    rmsle,
    total_frac_error_pct,
)


_BENCH_ROOT = Path(__file__).resolve().parent.parent  # .../LHCRecastBench
_TASKS_ROOT = _BENCH_ROOT / "tasks"


# ── Inputs ─────────────────────────────────────────────────────────────────


def _load_task(task_id: str) -> dict:
    """Read task.toml and validate the [metrics] block."""
    path = _TASKS_ROOT / task_id / "task.toml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing task.toml: {path}")
    toml = tomllib.loads(path.read_text())
    paper = (toml.get("task") or {}).get("paper")
    if not paper:
        raise ValueError(f"{path}: [task].paper is required")
    metrics_block = toml.get("metrics") or {}
    mode = str(metrics_block.get("mode", "shape_norm")).strip()
    if mode not in {"shape", "yield", "shape_norm"}:
        raise ValueError(f"{path}: [metrics].mode must be shape/yield/shape_norm, got {mode!r}")
    tolerance = float(metrics_block.get("tolerance", 0.0))
    plot_mode = str(metrics_block.get("plot", "Events/bin")).strip()
    return {
        "paper": str(paper).strip(),
        "mode": mode,
        "tolerance": tolerance,
        "plot_mode": plot_mode,
    }


def _template_yaml(task_id: str) -> Path:
    """Return the (single) template YAML under tasks/<task_id>/template/."""
    template_dir = _TASKS_ROOT / task_id / "template"
    files = sorted(template_dir.glob("*.yaml"))
    if len(files) != 1:
        raise ValueError(
            f"Expected one template YAML in {template_dir}; got {[p.name for p in files]}"
        )
    return files[0]


def _reference_yaml(paper: str, data_filename: str) -> Path:
    """Locate the hidden reference for a paper-relative filename."""
    path = _TASKS_ROOT / "shared" / paper / "reference" / data_filename
    if not path.is_file():
        raise FileNotFoundError(f"Missing reference: {path}")
    return path


def resolve_paths(run_path: str | Path) -> dict:
    """Pull task_id from run_info.json and locate every path eval code needs.

    Returns a dict with: task_id, paper, run_dir, results_dir, eval_dir,
    data_filename, header_name, reference_file. Raises if anything's missing.
    Used by score.py and judge.py — single source of truth for run-dir layout.
    """
    run_path = Path(run_path).resolve()
    info_path = run_path / "run_info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"Missing run_info.json: {info_path}")
    info = json.loads(info_path.read_text())
    task_id = info.get("task_id")
    if not task_id:
        raise ValueError(f"{info_path}: task_id missing")

    task = _load_task(task_id)
    workspace = run_path / "workspace"
    results_dir = workspace / "results"
    if not results_dir.is_dir():
        raise FileNotFoundError(f"Missing results dir: {results_dir}")

    template = _template_yaml(task_id)
    data_filename = template.name
    template_hist = histograms.load_histogram(template)
    if not template_hist.series:
        raise ValueError(f"{template}: no dependent variables")

    return {
        "task_id": task_id,
        "paper": task["paper"],
        "run_dir": run_path,
        "workspace": workspace,
        "results_dir": results_dir,
        "eval_dir": run_path / "eval",
        "data_filename": data_filename,
        "header_name": template_hist.series[0].name,
        "reference_file": _reference_yaml(task["paper"], data_filename),
        "task": task,
    }


# ── Score ──────────────────────────────────────────────────────────────────


def score_run(
    run_path: str | Path,
    *,
    n_toys: int | None = None,
    results_dir: Path | None = None,
) -> dict:
    """Compute metrics for one run; return the score.json contents.

    `results_dir` overrides where the agent's filled histogram is read from
    — the LLM judge's corrected-results pass sets this to its own dir so we
    can re-score without mutating the run.
    """
    rp = resolve_paths(Path(run_path))
    task = rp["task"]
    src_results = Path(results_dir) if results_dir is not None else rp["results_dir"]

    pred_path = src_results / rp["data_filename"]
    if not pred_path.is_file():
        return {
            "task_id": rp["task_id"],
            "paper": task["paper"],
            "error": f"agent output missing: {pred_path}",
        }
    ref_path = rp["reference_file"]

    ref_hist = histograms.load_histogram(ref_path)
    pred_hist = histograms.load_histogram(pred_path)
    ref_series = histograms.select_series(ref_hist, rp["header_name"])
    pred_series = histograms.select_series(pred_hist, rp["header_name"])
    if ref_series is None:
        return {"task_id": rp["task_id"], "error": f"series {rp['header_name']!r} not in reference"}
    if pred_series is None:
        return {
            "task_id": rp["task_id"],
            "error": f"series {rp['header_name']!r} not in prediction",
        }

    aligned = histograms.align(ref_series, pred_series, ref_hist.bins)
    mode = task["mode"]
    want_shape = mode in ("shape", "shape_norm")
    want_norm = mode in ("yield", "shape_norm")

    out: dict = {
        "task_id": rp["task_id"],
        "paper": task["paper"],
        "header_name": rp["header_name"],
        "n_bins": aligned.n_bins,
        "n_filled": aligned.n_filled,
        "score_mode": mode,
    }

    if want_shape:
        # Bin-error on unit-area-normalized arrays so it's a pure shape metric.
        ref_sum = float(aligned.reference.sum())
        pred_sum = float(aligned.prediction.sum())
        if ref_sum > 0 and pred_sum > 0:
            shape_bin_err = mean_abs_frac_error_pct(
                aligned.prediction / pred_sum, aligned.reference / ref_sum
            )
        else:
            shape_bin_err = None
        jsd, jsd_dist = jensen_shannon(aligned.prediction, aligned.reference)
        d_bar, d_max = per_bin_disagreement(aligned.prediction, aligned.reference)
        # Concentration of the shape disagreement:
        #   ratio = 1     → disagreement concentrated in one bin
        #   ratio = 1/K   → disagreement spread uniformly across K bins
        # None when d_bar == 0 (perfect match → ratio is 0/0).
        if d_bar is None or d_max is None or d_bar == 0:
            dmax_dbar_ratio: float | None = None
        else:
            dmax_dbar_ratio = round(d_max / d_bar, 6)
        bc_kwargs: dict = {"systematic_frac": task["tolerance"]}
        if n_toys is not None:
            bc_kwargs["n_toys"] = n_toys
        p_shape = baker_cousins_p_value(
            aligned.prediction, aligned.reference, kind="shape", **bc_kwargs
        )
        out["shape"] = {
            "mean_abs_frac_error_pct": shape_bin_err,
            "p_value": p_shape,
            "jensen_shannon": jsd,
            "jensen_shannon_dist": jsd_dist,
            "d_bar": d_bar,
            "d_max": d_max,
            "dmax_dbar_ratio": dmax_dbar_ratio,
        }

    if want_norm:
        out["normalization"] = {
            "mean_abs_frac_error_pct": total_frac_error_pct(aligned.prediction, aligned.reference),
            "Delta": Delta(aligned.prediction, aligned.reference),
            "rmsle": rmsle(aligned.prediction, aligned.reference),
        }

    return out


# ── CLI ────────────────────────────────────────────────────────────────────


def _emit(run_path: Path, result: dict, *, with_plots: bool) -> Path:
    eval_dir = run_path / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    score_path = eval_dir / "score.json"
    score_path.write_text(json.dumps(result, indent=2))

    if with_plots and "error" not in result:
        try:
            rp = resolve_paths(run_path)
            pred_path = rp["results_dir"] / rp["data_filename"]
            mode = rp["task"]["mode"]
            # Match score.json: shape-mode → only shape PNG; yield-mode → only
            # yield PNG; shape_norm → both. The omitted plot would be either
            # misleading (yield plot for a shape-only task whose totals aren't
            # meant to match) or redundant (shape plot for a yield-only task).
            if mode == "shape":
                which: tuple[str, ...] = ("shape",)
            elif mode == "yield":
                which = ("yield",)
            else:
                which = ("yield", "shape")
            plotting.plot_comparison(
                rp["reference_file"],
                pred_path,
                eval_dir / "plots",
                tolerance=rp["task"]["tolerance"],
                plot_mode=rp["task"]["plot_mode"],
                which=which,
            )
        except Exception as exc:  # plotting failures shouldn't break scoring
            print(f"  plotting failed: {exc}", file=sys.stderr)

    return score_path


def main() -> int:
    parser = argparse.ArgumentParser(prog="score", description=__doc__.split("\n\n")[0])
    parser.add_argument("run_path", nargs="+", help="Run directory (must contain run_info.json)")
    parser.add_argument("--no-plots", action="store_true", help="Skip plot generation")
    parser.add_argument("--n-toys", type=int, help="Override Baker-Cousins toy count (default 1M)")
    parser.add_argument("--json", action="store_true", help="Print raw JSON to stdout")
    args = parser.parse_args()

    rc = 0
    all_results = []
    for p in args.run_path:
        run_path = Path(p)
        try:
            result = score_run(run_path, n_toys=args.n_toys)
        except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
            print(f"  ERROR: {p}: {exc}", file=sys.stderr)
            rc = 1
            continue
        out = _emit(run_path, result, with_plots=not args.no_plots)
        all_results.append((run_path, result, out))

    if args.json:
        print(json.dumps([r[1] for r in all_results], indent=2))
        return rc

    for _run_path, result, out in all_results:
        if "error" in result:
            print(f"  {result['task_id']}: ERROR — {result['error']}")
            continue
        print(f"\n  {result['task_id']}  ({result['paper']})  [{result['score_mode']}]")
        print(
            f"    series  : {result['header_name']}   filled {result['n_filled']}/{result['n_bins']}"
        )
        s = result.get("shape")
        if s is not None:
            print(
                f"    shape   : bin_err={_fmt(s['mean_abs_frac_error_pct'], '%5.2f%%')}  "
                f"p={_fmt(s['p_value'], '%.3g')}  "
                f"JSD={_fmt(s['jensen_shannon'], '%.4f')}  "
                f"d_JSD={_fmt(s['jensen_shannon_dist'], '%.4f')}"
            )
        n = result.get("normalization")
        if n is not None:
            print(f"    norm    : frac_err={_fmt(n['mean_abs_frac_error_pct'], '%5.2f%%')}")
        print(f"    saved   : {out}")
    return rc


def _fmt(x, fmt: str) -> str:
    return "n/a" if x is None else fmt % x


if __name__ == "__main__":
    sys.exit(main())
