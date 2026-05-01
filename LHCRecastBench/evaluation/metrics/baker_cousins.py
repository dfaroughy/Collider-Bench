"""Baker-Cousins likelihood-ratio metric."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from LHCRecastBench.evaluation.context import EvalContext

from .base import MetricResult


DEFAULT_SYSTEMATIC = 0.0
DEFAULT_N_TOYS = 1_000_000
SCORE_TAU = 3.0


def bounded_score(z: float) -> float:
    if z <= 0 or not math.isfinite(z):
        return 1.0
    return math.exp(-z / SCORE_TAU)


def _lam_norm_vec(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    obs_tot = o.sum(axis=-1)
    ref_tot = float(r.sum())
    out = np.zeros_like(obs_tot, dtype=float)
    mask = (obs_tot > 0) & (ref_tot > 0)
    obs_m = obs_tot[mask] if obs_tot.ndim else obs_tot
    if obs_tot.ndim:
        out[mask] = 2.0 * (obs_m * np.log(obs_m / ref_tot) - (obs_m - ref_tot))
    elif mask:
        out = np.array(
            2.0 * (float(obs_tot) * math.log(float(obs_tot) / ref_tot) - (float(obs_tot) - ref_tot))
        )
    return np.maximum(out, 0.0)


def _lam_shape_vec(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    o2 = np.atleast_2d(o)
    obs_tot = o2.sum(axis=-1, keepdims=True)
    ref_tot = float(r.sum())
    ratio = np.where(obs_tot > 0, obs_tot / ref_tot, 1.0)
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
    return _lam_norm_vec(o, r) + _lam_shape_vec(o, r)


def _toy_calibrated_z(
    lam_obs: float,
    ref: np.ndarray,
    statistic_vec,
    *,
    systematic_frac: float,
    n_toys: int,
    seed: int,
) -> dict:
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
    z = float(norm.isf(float(np.clip(p, p_eps, 1.0 - p_eps))))
    return {"z": z, "p": p, "n_toys": n_toys}


def bc_statistics(
    observed: np.ndarray,
    reference: np.ndarray,
    *,
    systematic_frac: float = DEFAULT_SYSTEMATIC,
    n_toys: int = DEFAULT_N_TOYS,
    seed: int = 0,
) -> dict:
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return {"error": "empty or mismatched distributions"}
    tot_obs = float(np.sum(obs))
    tot_ref = float(np.sum(ref))
    n_bins = int(obs.size)
    if tot_obs <= 0 or tot_ref <= 0:
        return {"error": "total yield is zero in obs or ref"}

    lam_norm = float(_lam_norm_vec(obs, ref))
    lam_shape = float(_lam_shape_vec(obs, ref))
    lam_total = lam_shape + lam_norm

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

    dof_shape = max(n_bins - 1, 1)

    def _round_z(z):
        return round(z, 3) if math.isfinite(z) else z

    return {
        "shape": {
            "lambda": round(lam_shape, 3),
            "dof": dof_shape,
            "lambda_per_dof": round(lam_shape / dof_shape, 3),
            "z": _round_z(z_shape["z"]),
            "p_value": z_shape["p"],
        },
        "normalization": {
            "lambda": round(lam_norm, 3),
            "dof": 1,
            "z": _round_z(z_norm["z"]),
            "p_value": z_norm["p"],
        },
        "total": {
            "bc_stat": round(lam_total, 3),
            "dof": n_bins,
            "z": _round_z(z_total["z"]),
            "p_value": z_total["p"],
        },
        "calibration": {
            "systematic_frac": systematic_frac,
            "n_toys": n_toys,
            "seed": seed,
        },
    }


class BakerCousinsMetric:
    name = "baker_cousins"

    def __init__(self, n_toys: int = DEFAULT_N_TOYS, seed: int = 0):
        self.n_toys = n_toys
        self.seed = seed

    def compute(self, context: EvalContext) -> MetricResult:
        comp = context.comparison
        bc = bc_statistics(
            comp.prediction_values,
            comp.reference_values,
            systematic_frac=context.task.metrics.tolerance,
            n_toys=self.n_toys,
            seed=self.seed,
        )
        if "error" in bc:
            return MetricResult(name=self.name, status="error", error=bc["error"])
        shape_score = bounded_score(float(bc["shape"]["z"]))
        norm_score = bounded_score(float(bc["normalization"]["z"]))
        return MetricResult(
            name=self.name,
            components={
                "shape": bc["shape"],
                "normalization": bc["normalization"],
                "total": bc["total"],
            },
            primary_values={
                "shape_score": shape_score,
                "normalization_score": norm_score,
            },
            diagnostics={"calibration": bc["calibration"]},
        )
