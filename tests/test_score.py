"""Baker-Cousins shape/norm decomposition + bin-error diagnostic with toy-MC z.

The statistical content of the scorer lives in ``bc_statistics()`` and
``bin_fractional_error_percent()`` in ``LHCRecastBench.evaluation.score``.

We deliberately use a small toy budget here (``n_toys=1000``) so the suite
stays fast; production scoring uses ``DEFAULT_N_TOYS`` (1M). All assertions
are robust to the toy noise that small-N introduces.

Tests cover:
  - λ_total = λ_shape + λ_norm  (algebraic identity, unchanged from before)
  - identical distributions → λ = 0, score = 1
  - factor-k normalization errors hit λ_norm only, λ_shape ≈ 0
  - shape distortions at fixed total hit λ_shape only, λ_norm = 0
  - z is monotone in λ for each axis (replaces old z = √λ assertion)
  - bounded score is monotone-decreasing in z
  - the systematic broadens the toy null (z with sys ≤ z without sys)
  - z is finite even for perfect agreement (no -inf from log of 1)
  - bin fractional-error diagnostic sanity properties
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from LHCRecastBench.evaluation.score import (
    DEFAULT_SYSTEMATIC,
    _bounded_score,
    bin_fractional_error_percent,
    bc_statistics,
)


# Small toy budget to keep the suite fast. Scaled-up checks (e.g. p ≈ 1
# for identity) use abs tolerances of order 1/n_toys.
N_TOYS = 1000


def _bc(obs, ref, sys=DEFAULT_SYSTEMATIC, seed=0):
    return bc_statistics(obs, ref, systematic_frac=sys, n_toys=N_TOYS, seed=seed)


# ── Basic algebra ──────────────────────────────────────────────────────────


def test_identity_identical_distributions():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    bc = _bc(ref, ref)
    assert bc["shape"]["lambda"] == 0.0
    assert bc["normalization"]["lambda"] == 0.0
    assert bc["total"]["bc_stat"] == 0.0
    # Toy p saturates near 1 (all toys with non-zero λ exceed 0). z ≤ 0.
    assert bc["shape"]["z"] <= 0
    assert bc["normalization"]["z"] <= 0
    assert bc["total"]["z"] <= 0
    # Toy p_value should be near 1; allow toy noise.
    assert bc["shape"]["p_value"] == pytest.approx(1.0, abs=2.0 / N_TOYS)


@pytest.mark.parametrize("k", [0.5, 2.0, 3.0, 5.0])
def test_pure_normalization_error_leaves_shape_untouched(k):
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = k * ref
    bc = _bc(obs, ref)
    assert bc["shape"]["lambda"] == pytest.approx(0.0, abs=1e-9)
    assert bc["normalization"]["lambda"] > 0
    # z grows monotonically with the size of the normalization error.
    assert bc["normalization"]["z"] > 0


def test_pure_shape_distortion_leaves_norm_untouched():
    # Same total (90), different shape.
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([5.0, 5.0, 50.0, 20.0, 10.0])
    assert np.sum(obs) == np.sum(ref)
    bc = _bc(obs, ref)
    assert bc["normalization"]["lambda"] == pytest.approx(0.0, abs=1e-9)
    assert bc["shape"]["lambda"] > 0


def test_additive_identity_lambda_total_equals_shape_plus_norm():
    """λ_total = λ_shape + λ_norm must hold exactly for any obs/ref."""
    rng = np.random.default_rng(42)
    for _ in range(10):
        ref = rng.uniform(1.0, 100.0, size=10)
        obs = rng.uniform(1.0, 100.0, size=10)
        bc = _bc(obs, ref)
        expected = bc["shape"]["lambda"] + bc["normalization"]["lambda"]
        # Stored fields are rounded to 3 decimals — identity exact in unrounded math.
        assert bc["total"]["bc_stat"] == pytest.approx(expected, abs=0.005)


# ── Dof + z + score consistency ────────────────────────────────────────────


def test_dof_is_n_minus_one_for_shape_and_one_for_norm():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = 1.5 * ref
    bc = _bc(obs, ref)
    assert bc["shape"]["dof"] == len(ref) - 1
    assert bc["normalization"]["dof"] == 1
    assert bc["total"]["dof"] == len(ref)


def test_z_monotone_in_lambda_for_normalization():
    """Larger λ_norm → larger z_norm (toy MC must preserve monotonicity)."""
    ref = np.array([5.0, 10.0, 20.0, 10.0, 5.0])
    prev_z = -math.inf
    for k in (1.0, 1.2, 1.5, 2.0, 3.0, 5.0):
        bc = _bc(k * ref, ref, seed=int(k * 100))  # vary seed so toy ensembles differ
        z = bc["normalization"]["z"]
        # Skip k=1 where z is below 0 (perfect agreement); start checking at k>1.
        if k > 1.0:
            assert z >= prev_z - 0.2  # small slack for toy noise
        prev_z = z


def test_bounded_score_monotone_in_z():
    """exp(-z/τ) must be monotonically decreasing in z, bounded in [0,1]."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    prev_score = 1.0
    for k in (1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
        bc = _bc(k * ref, ref, seed=int(k * 100))
        score = _bounded_score(bc["normalization"]["z"])
        assert 0.0 <= score <= 1.0
        if k > 1.0:
            assert score <= prev_score + 1e-9
        prev_score = score


def test_z_is_finite_for_perfect_agreement():
    """z must never be ±inf; the empirical p-value is clamped away from {0, 1}."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    bc = _bc(ref, ref)
    for axis in ("shape", "normalization", "total"):
        assert math.isfinite(bc[axis]["z"]), f"{axis} z={bc[axis]['z']} not finite"


# ── Systematic broadens the null ───────────────────────────────────────────


def test_systematic_loosens_normalization_score():
    """A 50% normalization error should map to a smaller |z| with sys=20%
    than with sys=0 — the systematic absorbs part of the disagreement."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = 1.5 * ref
    bc_with = _bc(obs, ref, sys=0.20, seed=7)
    bc_without = _bc(obs, ref, sys=0.0, seed=7)
    # With sys, observed disagreement is more likely under the null → smaller z.
    assert bc_with["normalization"]["z"] < bc_without["normalization"]["z"]


def test_calibration_metadata_recorded():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    bc = _bc(ref, ref, sys=0.15, seed=42)
    cal = bc["calibration"]
    assert cal["systematic_frac"] == 0.15
    assert cal["n_toys"] == N_TOYS
    assert cal["seed"] == 42


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_zero_bins_dont_break_shape_statistic():
    """O_i = 0 with E_i > 0 is legal: 0·ln(0) ≡ 0, should not produce NaN/inf."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([10.0, 0.0, 30.0, 0.0, 10.0])
    bc = _bc(obs, ref)
    assert math.isfinite(bc["shape"]["lambda"])
    assert math.isfinite(bc["normalization"]["lambda"])
    assert math.isfinite(bc["total"]["bc_stat"])


def test_empty_and_degenerate_distributions_return_error():
    assert "error" in _bc(np.array([]), np.array([]))
    assert "error" in _bc(np.zeros(5), np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert "error" in _bc(np.array([1.0, 2.0, 3.0]), np.zeros(3))


def test_mismatched_lengths_return_error():
    assert "error" in _bc(np.array([1.0, 2.0]), np.array([1.0, 2.0, 3.0]))


def test_single_bin_series_has_shape_dof_one():
    """With N=1, shape has zero effective dof — we clamp to 1 for sanity."""
    bc = _bc(np.array([10.0]), np.array([10.0]))
    assert bc["shape"]["dof"] == 1
    assert bc["shape"]["lambda"] == 0.0


# ── Bin fractional-error secondary metric ──────────────────────────────────


def test_bin_fractional_error_identical_is_zero():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    r = bin_fractional_error_percent(ref, ref)
    assert r["mean_abs_frac_error_percent"] == 0.0
    assert r["n_valid_bins"] == 5


def test_bin_fractional_error_reports_mean_percent():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([5.0, 20.0, 60.0, 10.0, 10.0])
    r = bin_fractional_error_percent(obs, ref)
    # Terms are 0.5, 0, 1, 0.5, 0 => mean 40%.
    assert r["mean_abs_frac_error_percent"] == pytest.approx(40.0)


def test_bin_fractional_error_handles_zero_truth_bins():
    ref = np.array([0.0, 10.0, 0.0, 20.0])
    obs = np.array([0.0, 20.0, 3.0, 10.0])
    r = bin_fractional_error_percent(obs, ref)
    # Positive-truth terms are 1.0 and 0.5 => mean 75%.
    assert r["mean_abs_frac_error_percent"] == pytest.approx(75.0)
    assert r["n_valid_bins"] == 2
    assert r["n_zero_truth_bins"] == 2
    assert r["n_zero_truth_nonzero_pred"] == 1
