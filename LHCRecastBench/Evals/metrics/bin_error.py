"""Bin-level fractional error: shape (per-bin mean) + normalization (totals)."""

from __future__ import annotations

import numpy as np


def mean_abs_frac_error_pct(observed: np.ndarray, reference: np.ndarray) -> float | None:
    """Mean of |ref - obs| / ref over reference-positive bins, in percent.

    Returns None if there are no reference-positive bins (ill-defined).
    Bins where reference is zero are silently dropped — they're mostly tail
    bins where the agent's job is "match the absence".
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return None
    mask = ref > 0
    if not mask.any():
        return None
    terms = np.abs(ref[mask] - obs[mask]) / ref[mask]
    return float(round(100.0 * terms.mean(), 3))


def total_frac_error_pct(observed: np.ndarray, reference: np.ndarray) -> float | None:
    """`100 * |sum(obs) - sum(ref)| / sum(ref)`. Returns None if sum(ref) == 0."""
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0:
        return None
    obs_total = float(obs.sum())
    ref_total = float(ref.sum())
    if ref_total == 0:
        return None
    return float(round(100.0 * abs(obs_total - ref_total) / ref_total, 3))


def per_bin_disagreement(
    observed: np.ndarray, reference: np.ndarray
) -> tuple[float | None, float | None]:
    """Mean and worst per-bin disagreement on unit-area-normalized histograms.

    Define `p = obs / Σobs` and `q = ref / Σref`, so each is a probability
    distribution over the K bins. Then with `d_i = |p_i - q_i| / (1/K)`,
    the raw paper-text quantities are `d̄ = Σ_i |p_i - q_i| ∈ [0, 2]` and
    `d_max = K · max_i |p_i - q_i|`. We report both rescaled to [0, 1]:

        d_bar = (1/2) · Σ_i |p_i - q_i|         ∈ [0, 1]
              = total-variation distance between p and q.

        d_max = max_i |p_i - q_i|               ∈ [0, 1]
              = worst single-bin probability difference (K-independent,
                so values are comparable across tasks with different K).

    Both are zero iff `p == q`, one iff supports are disjoint. d_bar
    averages absolute disagreement; d_max isolates worst-bin failures
    that integrated metrics average away.

    Returns `(d_bar, d_max)`, or `(None, None)` if either histogram has
    zero total or sizes mismatch.
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return (None, None)
    obs_sum = float(obs.sum())
    ref_sum = float(ref.sum())
    if obs_sum <= 0 or ref_sum <= 0:
        return (None, None)
    p = obs / obs_sum
    q = ref / ref_sum
    diff = np.abs(p - q)
    d_bar = 0.5 * float(diff.sum())  # TV distance ∈ [0, 1]
    d_max = float(diff.max())  # K-independent, ∈ [0, 1]
    return (round(d_bar, 6), round(d_max, 6))


def rmsle(observed: np.ndarray, reference: np.ndarray) -> float | None:
    """Root-mean-squared logarithmic error on raw bin yields.

        RMSLE = sqrt( (1/K) Σ_i [ ln(N_obs_i + 1) - ln(N_ref_i + 1) ]² )

    Designed for non-negative yields that span orders of magnitude
    (typical of HEP histograms: signal-region peaks at ~10^4 events,
    tail bins at ~10). The `+1` offset cleanly handles zero counts
    without an epsilon hack. Lower = better; 0 = exact agreement on
    every bin's `log(N+1)`.

    Returns None if sizes mismatch or any value is negative (RMSLE is
    only defined for non-negative inputs). Unbounded above — RMSLE
    of 1 means an average bin disagreement of `e ≈ 2.7×` in (N+1).
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return None
    if (obs < 0).any() or (ref < 0).any():
        return None
    diff = np.log(obs + 1.0) - np.log(ref + 1.0)
    return float(round(float(np.sqrt(np.mean(diff**2))), 6))


def Delta(observed: np.ndarray, reference: np.ndarray) -> float | None:
    """Fractional yield difference Δ ≡ |Σ_obs − Σ_ref| / Σ_ref.

    The natural scoring primitive for `sim` tasks (`score_mode = "shape_norm"`):
    the absolute event rate is what the agent is being asked to predict, so
    the cross-section × luminosity × efficiency axis dominates here. Same
    calculation as `total_frac_error_pct` but in raw-fraction units (not %),
    matching the paper-text Δ notation. Returns None if Σ_ref == 0.
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0:
        return None
    ref_total = float(ref.sum())
    if ref_total == 0:
        return None
    return float(round(abs(float(obs.sum()) - ref_total) / ref_total, 6))
