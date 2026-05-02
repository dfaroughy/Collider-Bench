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
