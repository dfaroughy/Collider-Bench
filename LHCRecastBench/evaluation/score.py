#!/usr/bin/env python3
"""Unified scorer for recast results.

Compares the agent's filled histogram at <workspace>/results/<file>.yml
against the reference at LHCRecastBench/tasks/shared/<paper>/histograms/<file>
and emits:

  Per-bin metrics (how close is each prediction?)
    - pull = (recast - ref) / ref_err
    - rel_diff = |recast - ref| / |ref|
    - pass iff |pull| < 2 OR rel_diff < 50%
    - overall_score = n_pass / n_filled
    - overall_pass  = overall_score >= 0.5

  Baker-Cousins likelihood-ratio decomposition (is it the right shape? total?)
    λ_total  = 2·Σ [ O·ln(O/E) − (O − E) ]          ~ χ²(N)   goodness-of-fit
    λ_shape  = 2·Σ O·ln(O/Ê)     where Ê=α·E        ~ χ²(N−1) shape only (α=ΣO/ΣE)
    λ_norm   = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO−ΣE) ]         ~ χ²(1)   total only

    Each λ gives a p-value (chi2.sf), a z-score (√λ), and a bounded rubric
    score exp(−z/5). The two p-values are calibrated hypothesis tests; the
    bounded rubric score is what feeds the % weights in rubric_scorer.py.

    log₁₀(ΣO/ΣE) is kept as a human-readable normalization diagnostic —
    physicists read ratios natively, and p-values saturate near zero.

  Secondary shape metric
    Kolmogorov-Smirnov on unit-area CDFs with approximate p-value.

Output: <run_dir>/eval/score.json for single-shot layouts, or
<iter_dir>/eval/score.json when run_path resolves to an iter.

Usage:
    python -m LHCRecastBench.evaluation.score runs/simulate_<paper>_<agent>_...
    python -m LHCRecastBench.evaluation.score runs/<run_a> runs/<run_b>   # compare
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import chi2, kstwobign


# Scale factor for the bounded rubric score: rubric = exp(-z / RUBRIC_Z_SCALE).
# z = √λ is roughly an "effective number of sigmas of disagreement".
# At z=5 → 0.37, z=10 → 0.14, z=20 → 0.02. Gentle enough that distinguishing
# moderately-wrong runs from very-wrong runs is still possible in the rubric.
RUBRIC_Z_SCALE = 5.0


# ── Loading ─────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _extract_values(data: dict) -> list[dict]:
    """Extract dependent_variables as [{name, values, errors}].

    Errors sum all symerror/asymerror entries in quadrature (standard for
    uncorrelated uncertainties). `errors[i]` is None when no error was stated.
    """
    result = []
    for dep in data.get("dependent_variables", []):
        name = dep.get("header", {}).get("name", "unknown")
        values: list = []
        errors: list = []
        for entry in dep.get("values", []):
            values.append(entry.get("value"))
            err_sq = 0.0
            has_err = False
            for e in entry.get("errors", []) or []:
                if "symerror" in e and e["symerror"] is not None:
                    err_sq += float(e["symerror"]) ** 2
                    has_err = True
                elif "asymerror" in e:
                    ae = e["asymerror"]
                    plus = abs(float(ae.get("plus", 0) or 0))
                    minus = abs(float(ae.get("minus", 0) or 0))
                    if plus or minus:
                        err_sq += max(plus, minus) ** 2
                        has_err = True
            errors.append(math.sqrt(err_sq) if has_err else None)
        result.append({"name": name, "values": values, "errors": errors})
    return result


def _extract_bins(data: dict) -> list[dict]:
    """Extract independent_variables as [{name, units, bins}]."""
    result = []
    for indep in data.get("independent_variables", []):
        name = indep.get("header", {}).get("name", "unknown")
        units = indep.get("header", {}).get("units", "")
        bins = []
        for entry in indep.get("values", []):
            if "low" in entry and "high" in entry:
                bins.append(f"{entry['low']}-{entry['high']}")
            else:
                bins.append(str(entry.get("value", "?")))
        result.append({"name": name, "units": units, "bins": bins})
    return result


# ── Baker-Cousins decomposition ────────────────────────────────────────────


def _rubric(z: float) -> float:
    """Bounded [0,1] monotone score from a z-score. See RUBRIC_Z_SCALE."""
    if z <= 0:
        return 1.0
    return math.exp(-z / RUBRIC_Z_SCALE)


def bc_statistics(observed: np.ndarray, reference: np.ndarray) -> dict:
    """Baker-Cousins likelihood-ratio decomposition.

    λ_total = λ_shape + λ_norm   (exact algebraic identity)

      λ_shape = 2·Σ O_i · ln(O_i / Ê_i)       with Ê_i = (ΣO/ΣE)·E_i
      λ_norm  = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO − ΣE) ]
      λ_total = 2·Σ [ O_i·ln(O_i/E_i) − (O_i − E_i) ]

    Each is asymptotically χ²-distributed under H₀ (obs ~ Poisson(ref)):
      λ_shape ~ χ²(N−1),   λ_norm ~ χ²(1),   λ_total ~ χ²(N).

    Returns the three statistics, their p-values, z = √λ effective sigmas,
    and bounded rubric scores exp(−z/RUBRIC_Z_SCALE).

    0·ln(0) is taken as 0 (standard convention). Returns ``{"error": ...}``
    if either distribution is empty.
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return {"error": "empty or mismatched distributions"}

    tot_obs = float(np.sum(obs))
    tot_ref = float(np.sum(ref))
    n_bins = int(obs.size)
    if tot_obs <= 0 or tot_ref <= 0:
        return {"error": "total yield is zero in obs or ref"}

    ratio = tot_obs / tot_ref

    # λ_norm (1-dof test on totals)
    lam_norm = 2.0 * (tot_obs * math.log(ratio) - (tot_obs - tot_ref))
    lam_norm = max(lam_norm, 0.0)  # guard float rounding
    p_norm = float(chi2.sf(lam_norm, df=1))
    z_norm = math.sqrt(lam_norm)

    # λ_shape (n_bins−1 dof, profile over α=tot_obs/tot_ref)
    # 0·ln(0) ≡ 0; skip obs_i ≤ 0 bins (their contribution is zero).
    ref_hat = ratio * ref
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(
            (obs > 0) & (ref_hat > 0),
            obs * np.log(obs / np.where(ref_hat > 0, ref_hat, 1.0)),
            0.0,
        )
    lam_shape = max(2.0 * float(np.sum(terms)), 0.0)
    dof_shape = max(n_bins - 1, 1)
    p_shape = float(chi2.sf(lam_shape, df=dof_shape))
    z_shape = math.sqrt(lam_shape)

    # λ_total is algebraically λ_shape + λ_norm (profile + constraint).
    lam_total = lam_shape + lam_norm
    p_total = float(chi2.sf(lam_total, df=n_bins))
    z_total = math.sqrt(lam_total)

    return {
        "shape": {
            "lambda": round(lam_shape, 3),
            "dof": dof_shape,
            "lambda_per_dof": round(lam_shape / dof_shape, 3),
            "z": round(z_shape, 3),
            "p_value": p_shape,
            "score": round(_rubric(z_shape), 4),
        },
        "normalization": {
            "lambda": round(lam_norm, 3),
            "dof": 1,
            "z": round(z_norm, 3),
            "p_value": p_norm,
            "score": round(_rubric(z_norm), 4),
            "ratio": round(ratio, 3),
            "log10_ratio": round(math.log10(ratio), 3),
        },
        "total": {
            "bc_stat": round(lam_total, 3),
            "dof": n_bins,
            "z": round(z_total, 3),
            "p_value": p_total,
        },
    }


def ks_binned(observed: np.ndarray, reference: np.ndarray) -> dict:
    """Binned Kolmogorov-Smirnov: D = max|CDF_obs − CDF_ref| + approximate p-value.

    For an exactly-calibrated KS test you need unbinned samples; here we
    approximate the effective sample size by ΣE (the total reference yield
    treated as pseudo-counts) and use the asymptotic two-sided Kolmogorov
    distribution. This is a secondary diagnostic — the primary shape test
    is the Baker-Cousins λ_shape above.
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    tot_obs = float(np.sum(obs))
    tot_ref = float(np.sum(ref))
    if tot_obs <= 0 or tot_ref <= 0:
        return {"stat": 1.0, "p_value": 0.0, "n_eff": 0.0}
    obs_cdf = np.cumsum(obs / tot_obs)
    ref_cdf = np.cumsum(ref / tot_ref)
    stat = float(np.max(np.abs(obs_cdf - ref_cdf)))
    # Asymptotic Kolmogorov distribution: K = D·√n_eff
    p = float(kstwobign.sf(stat * math.sqrt(tot_ref)))
    return {"stat": round(stat, 4), "p_value": p, "n_eff": round(tot_ref, 3)}


# ── Scoring ─────────────────────────────────────────────────────────────────


def _as_float(x) -> float | None:
    """Coerce to float, returning None for non-numeric values (e.g. LaTeX upper-limit strings)."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _score_series(
    name: str,
    ref_vals: list,
    rec_vals: list,
    ref_errs: list,
    bins: list[dict],
) -> dict:
    """Score one dependent-variable series — summary stats only, no per-bin dump."""
    n_bins = len(ref_vals)
    series: dict = {
        "name": name,
        "n_bins": n_bins,
        "n_filled": 0,
        "n_pass": 0,
    }

    for i in range(n_bins):
        rec_val_raw = rec_vals[i] if i < len(rec_vals) else None
        if rec_val_raw is None:
            continue
        series["n_filled"] += 1

        ref_val = _as_float(ref_vals[i])
        rec_val = _as_float(rec_val_raw)
        if ref_val is None or rec_val is None:
            continue

        if ref_val == 0:
            if abs(rec_val) < 1e-6:
                series["n_pass"] += 1
            continue

        ref_err = ref_errs[i]
        total_err = float(ref_err) if ref_err is not None else math.sqrt(abs(ref_val))
        pull = (rec_val - ref_val) / total_err if total_err > 0 else 0.0
        rel_diff = abs(rec_val - ref_val) / abs(ref_val)
        if abs(pull) < 2.0 or rel_diff < 0.5:
            series["n_pass"] += 1

    series["score"] = (
        round(series["n_pass"] / series["n_filled"], 3) if series["n_filled"] > 0 else 0.0
    )

    # Baker-Cousins decomposition on the aligned (both-numeric) subset.
    aligned = [
        (rv, cv)
        for i in range(min(len(ref_vals), len(rec_vals)))
        for rv, cv in [(_as_float(ref_vals[i]), _as_float(rec_vals[i]))]
        if rv is not None and cv is not None
    ]
    if not aligned:
        return series

    ref_arr = np.array([p[0] for p in aligned], dtype=float)
    rec_arr = np.array([p[1] for p in aligned], dtype=float)

    bc = bc_statistics(rec_arr, ref_arr)
    if "error" in bc:
        series["bc_error"] = bc["error"]
        return series

    s_score = bc["shape"]["score"]
    n_score = bc["normalization"]["score"]
    combined = math.sqrt(s_score * n_score) if s_score > 0 and n_score > 0 else 0.0
    if s_score > 0.7 and n_score > 0.7:
        diagnosis = "GOOD"
    elif s_score > 0.7:
        diagnosis = "SHAPE OK, NORM BAD"
    elif n_score > 0.7:
        diagnosis = "SHAPE BAD, NORM OK"
    else:
        diagnosis = "BOTH BAD"

    series["shape"] = bc["shape"]
    series["normalization"] = bc["normalization"]
    series["total"] = bc["total"]
    series["ks"] = ks_binned(rec_arr, ref_arr)
    series["combined"] = round(combined, 3)
    series["diagnosis"] = diagnosis

    return series


def _find_agent_output(results_dir: Path, data_filename: str) -> Path | None:
    """Find the agent-filled histogram under results/.

    Accepts the exact filename or either .yml/.yaml variant (agent may have
    kept the template's extension, which differs from the shared pool's).
    """
    direct = results_dir / data_filename
    if direct.is_file():
        return direct
    stem = Path(data_filename).stem
    for ext in (".yml", ".yaml"):
        alt = results_dir / f"{stem}{ext}"
        if alt.is_file():
            return alt
    return None


def score_run(rp) -> dict:
    """Score one task's filled histogram against its reference.

    `rp` is a RunPaths (from evaluation._resolve.resolve_run). Each task
    corresponds to exactly one histogram and one series (rp.header_name);
    anything else in the yaml is ignored.
    """
    ref_path = rp.reference_file
    if not ref_path.is_file():
        return {"error": f"Reference missing: {ref_path}"}
    agent_path = _find_agent_output(rp.results_dir, rp.data_filename)
    if agent_path is None:
        return {
            "error": (
                f"Agent output not found: {rp.results_dir}/{rp.data_filename} "
                f"(also tried .yml / .yaml)"
            )
        }

    ref_data = _load_yaml(ref_path)
    agent_data = _load_yaml(agent_path)

    ref_series = _extract_values(ref_data)
    agent_series = _extract_values(agent_data)
    bins = _extract_bins(ref_data)

    ref_s = next((s for s in ref_series if s["name"] == rp.header_name), None)
    agent_s = next((s for s in agent_series if s["name"] == rp.header_name), None)
    if ref_s is None:
        return {"error": f"Series {rp.header_name!r} not in reference {ref_path.name}"}
    if agent_s is None:
        return {"error": f"Series {rp.header_name!r} not in agent output {agent_path.name}"}

    series = _score_series(
        rp.header_name, ref_s["values"], agent_s["values"], ref_s["errors"], bins
    )

    output: dict = {
        "task_id": rp.task_id,
        "paper": rp.paper_ref,
        "header_name": rp.header_name,
        "reference": str(ref_path),
        "agent_output": str(agent_path),
        "series": series,
        "n_total": series["n_bins"],
        "n_filled": series["n_filled"],
        "n_pass": series["n_pass"],
        "overall_score": series.get("score", 0.0),
        "overall_pass": series.get("score", 0.0) >= 0.5,
    }
    if "shape" in series:
        s_score = series["shape"]["score"]
        n_score = series["normalization"]["score"]
        output["overall_shape"] = round(s_score, 3)
        output["overall_normalization"] = round(n_score, 3)
        output["overall_combined"] = round(
            math.sqrt(s_score * n_score) if s_score > 0 and n_score > 0 else 0.0, 3
        )

    return output


# ── Display ─────────────────────────────────────────────────────────────────


def print_scores(result: dict) -> None:
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    print(f"\n  Task: {result['task_id']}")
    print(f"  Paper: {result['paper']}   Series: {result['header_name']}")
    print(f"  {'=' * 68}")

    s = result["series"]
    n_pass = s["n_pass"]
    n_filled = s["n_filled"]
    score = s.get("score", 0)
    if "shape" in s:
        sh = s["shape"]
        no = s["normalization"]
        ks = s.get("ks", {})
        print(
            f"    {s['name']}: {n_pass}/{n_filled} pass ({score:.0%})  "
            f"shape p={sh['p_value']:.2g} (score={sh['score']:.2f})  "
            f"norm p={no['p_value']:.2g} (score={no['score']:.2f})  "
            f"KS p={ks.get('p_value', float('nan')):.2g}  "
            f"[{s['diagnosis']}]"
        )
    else:
        print(f"    {s['name']}: {n_pass}/{n_filled} pass ({score:.0%})")

    print(f"\n  {'=' * 68}")
    print(
        f"  Bins: {result['n_pass']}/{result['n_filled']} pass "
        f"({result['overall_score']:.0%})   status: "
        f"{'PASS' if result['overall_pass'] else 'FAIL'}"
    )
    if "overall_shape" in result:
        print(
            f"  Shape: {result['overall_shape']:.2f}   "
            f"Norm: {result['overall_normalization']:.2f}   "
            f"Combined: {result['overall_combined']:.2f}"
        )
    print()


def print_comparison(results: list[dict]) -> None:
    print(f"\n  {'Task':<52s} {'Pass%':>7s} {'Shape':>7s} {'Norm':>7s} {'Comb':>7s}")
    print(f"  {'─' * 83}")
    for r in results:
        if "error" in r:
            label = r.get("task_id") or r.get("agent_output") or "?"
            print(f"  {str(label):<52s}  {r['error']}")
            continue
        label = str(r.get("task_id", ""))[:52]
        print(
            f"  {label:<52s} "
            f"{r.get('overall_score', 0):>7.2f} "
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
