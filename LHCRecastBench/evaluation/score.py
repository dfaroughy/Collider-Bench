#!/usr/bin/env python3
"""Unified scorer for recast results.

Compares the agent's filled histogram at <workspace>/results/<file>.yaml
against the reference at LHCRecastBench/tasks/shared/<paper>/reference/<file>
and emits:

  Baker-Cousins likelihood-ratio decomposition (shape vs norm vs total)
    λ_total  = 2·Σ [ O·ln(O/E) − (O − E) ]          (n bins)
    λ_shape  = 2·Σ O·ln(O/Ê)   with Ê = α·E         (n−1 bins, α=ΣO/ΣE)
    λ_norm   = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO−ΣE) ]         (1 bin)

  Toy-MC calibrated z-score for each axis. Each λ is calibrated against a
  null built from N pseudo-experiments under
      ν_i = r_i · exp(σ_sys · θ_i),  θ_i ~ N(0,1)   (per-bin log-normal)
      o_i ~ Poisson(ν_i)
  with σ_sys ≈ 0.20 by default — accounting for tooling differences from
  the published recast (MC generator, PDFs, detector sim, calibration).
  z = Φ⁻¹(1 − p_empirical), so z is consistent across axes and properly
  calibrated even when bin counts are low (where the asymptotic χ²(dof)
  approximation breaks down). See PDG Statistics review and Cowan, ch.10.

  Bounded score: S = exp(−z / SCORE_TAU) ∈ (0,1], suitable for monotone
  aggregation across runs.

  log₁₀(ΣO/ΣE) is kept as a human-readable normalization diagnostic.

  Secondary shape metric
    Kolmogorov-Smirnov on unit-area CDFs with approximate p-value.

Output: <run_dir>/eval/score.json for single-shot layouts, or
<iter_dir>/eval/score.json when run_path resolves to an iter.

Usage:
    python -m LHCRecastBench.evaluation.score runs/<run_dir>
    python -m LHCRecastBench.evaluation.score runs/<run_a> runs/<run_b>   # compare
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import chi2, kstwobign, norm


# Default per-bin log-normal systematic on the reference (multiplicative).
# Code default is 0.0 (statistical-only) so callers must opt in to a
# non-zero tolerance. Production runs read the task-specific value from
# task.toml's `[metrics].tolerance` field via _resolve.py, threaded into
# RunPaths.systematic_pct (default 0.05 across the benchmark suite).
DEFAULT_SYSTEMATIC = 0.0

# Number of toy pseudo-experiments per axis. With N=1M toys, p saturates at
# ~1e-6 → z capped near 4.75.
DEFAULT_N_TOYS = 1_000_000

# Bounded score scale: S = exp(-z / SCORE_TAU). With toy-calibrated z:
#   z=0 → S=1.00 (within the systematic+stat envelope)
#   z=1 → S=0.72 (1σ off)
#   z=2 → S=0.51 (2σ off — borderline)
#   z=3 → S=0.37 (3σ off — clearly wrong)
#   z=5 → S=0.19 (well off)
SCORE_TAU = 3.0


# ── Loading ─────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    """Load a histogram yaml.

    Templates (and therefore the agent's filled output) are now two YAML
    documents — a metadata block followed by the HEPData-style histogram —
    while reference files in tasks/shared/<paper>/reference/ are still a
    single histogram document. Return the doc carrying `dependent_variables`
    in either case.
    """
    with open(path) as f:
        docs = list(yaml.safe_load_all(f))
    hist = next(
        (d for d in docs if isinstance(d, dict) and "dependent_variables" in d),
        None,
    )
    if hist is None:
        raise ValueError(
            f"{path}: no YAML document with `dependent_variables` "
            "(expected a HEPData-style histogram)"
        )
    return hist


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


def _bounded_score(z: float) -> float:
    """Bounded [0,1] monotone score from a toy-calibrated z. See SCORE_TAU.

    z ≤ 0 means observed agreement is ≥ median of the systematic+stat null
    (i.e. consistent with reference). Score saturates at 1.0 there.
    """
    if z <= 0 or not math.isfinite(z):
        return 1.0
    return math.exp(-z / SCORE_TAU)


# ── Vectorized BC statistics (used inside the toy MC inner loop) ──────────


def _lam_norm_vec(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    """λ_norm vectorized over leading axis: o is (..., n), r is (n,).

    Returns a 0-d or 1-d array of λ_norm values, one per row of `o`.
    """
    obs_tot = o.sum(axis=-1)
    R = float(r.sum())
    out = np.zeros_like(obs_tot, dtype=float)
    mask = (obs_tot > 0) & (R > 0)
    obs_m = obs_tot[mask] if obs_tot.ndim else obs_tot
    if obs_tot.ndim:
        out[mask] = 2.0 * (obs_m * np.log(obs_m / R) - (obs_m - R))
    elif mask:
        out = np.array(2.0 * (float(obs_tot) * math.log(float(obs_tot) / R) - (float(obs_tot) - R)))
    return np.maximum(out, 0.0)


def _lam_shape_vec(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    """λ_shape vectorized over leading axis. 0·ln(0) ≡ 0."""
    o2 = np.atleast_2d(o)
    obs_tot = o2.sum(axis=-1, keepdims=True)
    R = float(r.sum())
    ratio = np.where(obs_tot > 0, obs_tot / R, 1.0)
    r_hat = ratio * r[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(
            (o2 > 0) & (r_hat > 0),
            o2 * np.log(o2 / np.where(r_hat > 0, r_hat, 1.0)),
            0.0,
        )
    out = np.maximum(2.0 * terms.sum(axis=-1), 0.0)
    return out if o.ndim > 1 else out[0]


def _lam_total_vec(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    """λ_total = λ_norm + λ_shape (algebraic identity)."""
    return _lam_norm_vec(o, r) + _lam_shape_vec(o, r)


# ── Toy-MC calibration ────────────────────────────────────────────────────


def _toy_calibrated_z(
    lam_obs: float,
    ref: np.ndarray,
    statistic_vec,
    *,
    systematic_frac: float,
    n_toys: int,
    seed: int,
) -> dict:
    """Calibrate z via toy MC under (Poisson + log-normal systematic) null.

    Per toy:
      θ_i ~ N(0, 1)
      ν_i = r_i · exp(σ_sys · θ_i)              (per-bin log-normal)
      o_i ~ Poisson(ν_i)
      λ_toy = statistic_vec(o_i, r_i)

    Empirical p with +1 continuity correction, clamped to [1/(N+1),
    1−1/(N+1)] so z is always finite. z = Φ⁻¹(1 − p).
    """
    rng = np.random.default_rng(seed)
    n = len(ref)

    if systematic_frac > 0:
        theta = rng.standard_normal((n_toys, n))
        nu = ref[None, :] * np.exp(systematic_frac * theta)
    else:
        nu = np.broadcast_to(ref, (n_toys, n)).astype(float, copy=False)
    nu = np.clip(nu, 0.0, None)

    o_toy = rng.poisson(nu).astype(float, copy=False)
    lam_toys = statistic_vec(o_toy, ref)

    n_above = int((lam_toys >= lam_obs).sum())
    p_eps = 1.0 / (n_toys + 1)
    p = (n_above + 1) / (n_toys + 1)
    p_clipped = float(np.clip(p, p_eps, 1.0 - p_eps))
    z = float(norm.isf(p_clipped))
    return {
        "z": z,
        "p": p,
        "z_capped_high": n_above == 0,
        "n_toys": n_toys,
    }


def bc_statistics(
    observed: np.ndarray,
    reference: np.ndarray,
    *,
    systematic_frac: float = DEFAULT_SYSTEMATIC,
    n_toys: int = DEFAULT_N_TOYS,
    seed: int = 0,
) -> dict:
    """Baker-Cousins likelihood-ratio decomposition with toy-calibrated z.

    λ_total = λ_shape + λ_norm  (exact algebraic identity)

      λ_shape = 2·Σ O_i · ln(O_i / Ê_i)   with Ê_i = (ΣO/ΣE)·E_i
      λ_norm  = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO − ΣE) ]
      λ_total = 2·Σ [ O_i·ln(O_i/E_i) − (O_i − E_i) ]

    Each λ is calibrated to a z-score via toy MC under a null that includes
    Poisson statistical fluctuation and a per-bin log-normal multiplicative
    systematic of size `systematic_frac`. z is the Gaussian-equivalent
    one-sided tail value Φ⁻¹(1-p_toy), not sqrt(lambda). This keeps the
    reported z consistent across the total, normalization, and profiled-shape
    axes and well-defined at low counts where the asymptotic χ²(dof)
    approximation can fail.

    Per-axis output: `lambda`, `dof`, `lambda_per_dof`, `z`, `z_stat_only`
    (no-sys baseline for ablation), `p_value`, `score = exp(-z/τ)`,
    plus the asymptotic χ² p-value `p_asymptotic` for reference.

    0·ln(0) ≡ 0. Returns {"error": ...} for empty/degenerate inputs.
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
    lam_norm = float(_lam_norm_vec(obs, ref))
    lam_shape = float(_lam_shape_vec(obs, ref))
    lam_total = lam_shape + lam_norm

    # Toy-calibrated z (with systematic) — different seeds per axis so the
    # three calibrations use independent toy ensembles.
    z_norm = _toy_calibrated_z(
        lam_norm,
        ref,
        _lam_norm_vec,
        systematic_frac=systematic_frac,
        n_toys=n_toys,
        seed=seed,
    )
    z_shape = _toy_calibrated_z(
        lam_shape,
        ref,
        _lam_shape_vec,
        systematic_frac=systematic_frac,
        n_toys=n_toys,
        seed=seed + 1,
    )
    z_total = _toy_calibrated_z(
        lam_total,
        ref,
        _lam_total_vec,
        systematic_frac=systematic_frac,
        n_toys=n_toys,
        seed=seed + 2,
    )

    # Stat-only ablation (sys = 0): how much of the loosening comes from
    # the systematic. Use the SAME n_toys as the with-sys calibration so
    # both axes saturate at the same cap; otherwise z and z_stat_only are
    # not directly comparable.
    z_norm_stat = _toy_calibrated_z(
        lam_norm,
        ref,
        _lam_norm_vec,
        systematic_frac=0.0,
        n_toys=n_toys,
        seed=seed + 100,
    )
    z_shape_stat = _toy_calibrated_z(
        lam_shape,
        ref,
        _lam_shape_vec,
        systematic_frac=0.0,
        n_toys=n_toys,
        seed=seed + 101,
    )
    z_total_stat = _toy_calibrated_z(
        lam_total,
        ref,
        _lam_total_vec,
        systematic_frac=0.0,
        n_toys=n_toys,
        seed=seed + 102,
    )

    # Asymptotic χ² p-values — kept for transparency / sanity checks but
    # not used for any decision.
    p_norm_asym = float(chi2.sf(lam_norm, df=1))
    dof_shape = max(n_bins - 1, 1)
    p_shape_asym = float(chi2.sf(lam_shape, df=dof_shape))
    p_total_asym = float(chi2.sf(lam_total, df=n_bins))

    def _round_z(z):
        return round(z, 3) if math.isfinite(z) else z

    return {
        "shape": {
            "lambda": round(lam_shape, 3),
            "dof": dof_shape,
            "lambda_per_dof": round(lam_shape / dof_shape, 3),
            "z": _round_z(z_shape["z"]),
            "z_stat_only": _round_z(z_shape_stat["z"]),
            "z_capped": z_shape["z_capped_high"],
            "p_value": z_shape["p"],
            "p_asymptotic": p_shape_asym,
            "score": round(_bounded_score(z_shape["z"]), 4),
        },
        "normalization": {
            "lambda": round(lam_norm, 3),
            "dof": 1,
            "z": _round_z(z_norm["z"]),
            "z_stat_only": _round_z(z_norm_stat["z"]),
            "z_capped": z_norm["z_capped_high"],
            "p_value": z_norm["p"],
            "p_asymptotic": p_norm_asym,
            "score": round(_bounded_score(z_norm["z"]), 4),
            "ratio": round(ratio, 3),
            "log10_ratio": round(math.log10(ratio), 3),
        },
        "total": {
            "bc_stat": round(lam_total, 3),
            "dof": n_bins,
            "z": _round_z(z_total["z"]),
            "z_stat_only": _round_z(z_total_stat["z"]),
            "z_capped": z_total["z_capped_high"],
            "p_value": z_total["p"],
            "p_asymptotic": p_total_asym,
        },
        "calibration": {
            "systematic_frac": systematic_frac,
            "n_toys": n_toys,
            "seed": seed,
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
    *,
    systematic_frac: float = DEFAULT_SYSTEMATIC,
    n_toys: int = DEFAULT_N_TOYS,
) -> dict:
    """Score one dependent-variable series.

    Output: Baker-Cousins shape/norm/total p-values + bounded scores, KS,
    and an at-a-glance diagnosis. n_filled is kept as a sanity flag for
    "did the agent fill in the histogram at all?"; per-bin pulls and the
    n_pass / pass-rate are deliberately NOT computed — the BC/KS triple
    is what we use to judge runs.
    """
    n_bins = len(ref_vals)
    n_filled = sum(1 for i in range(n_bins) if i < len(rec_vals) and rec_vals[i] is not None)
    series: dict = {
        "name": name,
        "n_bins": n_bins,
        "n_filled": n_filled,
    }

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

    bc = bc_statistics(
        rec_arr,
        ref_arr,
        systematic_frac=systematic_frac,
        n_toys=n_toys,
    )
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
    series["calibration"] = bc["calibration"]
    series["ks"] = ks_binned(rec_arr, ref_arr)
    series["combined"] = round(combined, 3)
    series["diagnosis"] = diagnosis

    return series


def _find_agent_output(results_dir: Path, data_filename: str) -> Path | None:
    """Find the agent-filled histogram under results/.

    Accepts the exact filename or either .yaml/.yml variant (agent may have
    kept the template's extension, which differs from the shared pool's).
    """
    direct = results_dir / data_filename
    if direct.is_file():
        return direct
    stem = Path(data_filename).stem
    for ext in (".yaml", ".yml"):
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
                f"(also tried .yaml / .yml)"
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

    sys_frac = getattr(rp, "systematic_pct", DEFAULT_SYSTEMATIC)
    score_mode = getattr(rp, "score_mode", "shape_norm")
    series = _score_series(
        rp.header_name,
        ref_s["values"],
        agent_s["values"],
        ref_s["errors"],
        bins,
        systematic_frac=sys_frac,
    )

    output: dict = {
        "task_id": rp.task_id,
        "paper": rp.paper_ref,
        "header_name": rp.header_name,
        "reference": str(ref_path),
        "agent_output": str(agent_path),
        "systematic_pct": sys_frac,
        "score_mode": score_mode,
        "series": series,
        "n_bins": series["n_bins"],
        "n_filled": series["n_filled"],
    }
    if "shape" in series:
        s_score = series["shape"]["score"]
        n_score = series["normalization"]["score"]
        output["overall_shape"] = round(s_score, 3)
        output["overall_normalization"] = round(n_score, 3)
        if score_mode == "shape":
            output["overall_combined"] = round(s_score, 3)
            output["score_note"] = (
                "shape-only task: normalization diagnostic is not included in overall_combined"
            )
        elif score_mode == "yield":
            output["overall_combined"] = round(n_score, 3)
            output["score_note"] = (
                "yield-only task: shape diagnostic is not included in overall_combined"
            )
        else:
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
    if result.get("score_mode") == "shape":
        print("  Score mode: shape-only (normalization is diagnostic)")
    elif result.get("score_mode") == "yield":
        print("  Score mode: yield-only (shape is diagnostic)")
    print(f"  {'=' * 68}")

    s = result["series"]
    if "shape" in s:
        sh = s["shape"]
        no = s["normalization"]
        ks = s.get("ks", {})
        print(
            f"    {s['name']}: filled {s['n_filled']}/{s['n_bins']}  "
            f"shape p={sh['p_value']:.2g} (score={sh['score']:.2f})  "
            f"norm p={no['p_value']:.2g} (score={no['score']:.2f})  "
            f"KS p={ks.get('p_value', float('nan')):.2g}  "
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
