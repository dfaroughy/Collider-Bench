"""Baker-Cousins shape/norm decomposition + KS p-value.

The statistical content of the scorer lives in bc_statistics() and
ks_binned() in LHCRecastBench.evaluation.score. These tests check:

  - λ_total = λ_shape + λ_norm  (algebraic identity)
  - Identical distributions → all zero, p=1, score=1
  - Factor-k normalization errors hit λ_norm only, leave λ_shape = 0
  - Shape distortions at fixed total hit λ_shape only, leave λ_norm = 0
  - Edge cases (zero bins, zero totals, single-bin series) don't explode
  - KS p-value tracks KS stat monotonically on smooth distortions
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from LHCRecastBench.evaluation.score import bc_statistics, ks_binned


# ── Basic algebra ──────────────────────────────────────────────────────────


def test_identity_identical_distributions():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    bc = bc_statistics(ref, ref)
    assert bc["shape"]["lambda"] == 0.0
    assert bc["normalization"]["lambda"] == 0.0
    assert bc["total"]["bc_stat"] == 0.0
    assert bc["shape"]["p_value"] == pytest.approx(1.0)
    assert bc["normalization"]["p_value"] == pytest.approx(1.0)
    assert bc["total"]["p_value"] == pytest.approx(1.0)
    assert bc["shape"]["score"] == 1.0
    assert bc["normalization"]["score"] == 1.0
    assert bc["normalization"]["ratio"] == 1.0


@pytest.mark.parametrize("k", [0.5, 2.0, 3.0, 5.0])
def test_pure_normalization_error_leaves_shape_untouched(k):
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = k * ref
    bc = bc_statistics(obs, ref)
    assert bc["shape"]["lambda"] == pytest.approx(0.0, abs=1e-9)
    assert bc["normalization"]["lambda"] > 0
    assert bc["normalization"]["ratio"] == pytest.approx(k)
    # z grows with the log of the ratio — sanity check monotonicity
    assert bc["normalization"]["z"] > 0


def test_pure_shape_distortion_leaves_norm_untouched():
    # Same total (90), different shape.
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([5.0, 5.0, 50.0, 20.0, 10.0])
    assert np.sum(obs) == np.sum(ref)
    bc = bc_statistics(obs, ref)
    assert bc["normalization"]["lambda"] == pytest.approx(0.0, abs=1e-9)
    assert bc["shape"]["lambda"] > 0
    assert bc["normalization"]["ratio"] == 1.0


def test_additive_identity_lambda_total_equals_shape_plus_norm():
    """λ_total = λ_shape + λ_norm must hold exactly for any obs/ref."""
    rng = np.random.default_rng(42)
    for _ in range(20):
        ref = rng.uniform(1.0, 100.0, size=10)
        obs = rng.uniform(1.0, 100.0, size=10)
        bc = bc_statistics(obs, ref)
        expected = bc["shape"]["lambda"] + bc["normalization"]["lambda"]
        # Stored fields are rounded to 3 decimals; the identity is exact in
        # the unrounded math but picks up ≤ 0.001 of drift after rounding.
        assert bc["total"]["bc_stat"] == pytest.approx(expected, abs=0.005)


# ── Dof + z + p-value consistency ──────────────────────────────────────────


def test_dof_is_n_minus_one_for_shape_and_one_for_norm():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = 1.5 * ref
    bc = bc_statistics(obs, ref)
    assert bc["shape"]["dof"] == len(ref) - 1
    assert bc["normalization"]["dof"] == 1
    assert bc["total"]["dof"] == len(ref)


def test_z_equals_sqrt_lambda():
    ref = np.array([5.0, 10.0, 20.0, 10.0, 5.0])
    obs = np.array([2.0, 10.0, 25.0, 7.0, 6.0])
    bc = bc_statistics(obs, ref)
    assert bc["shape"]["z"] == pytest.approx(math.sqrt(bc["shape"]["lambda"]), rel=1e-3)
    assert bc["normalization"]["z"] == pytest.approx(
        math.sqrt(bc["normalization"]["lambda"]), rel=1e-3
    )


def test_rubric_score_monotone_in_z():
    """exp(-z/5) must be monotonically decreasing in z, bounded in [0,1]."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    prev_score = 1.0
    for k in (1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
        bc = bc_statistics(k * ref, ref)
        score = bc["normalization"]["score"]
        assert 0.0 <= score <= 1.0
        if k > 1.0:
            assert score <= prev_score
        prev_score = score


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_zero_bins_dont_break_shape_statistic():
    """O_i = 0 with E_i > 0 is legal: 0·ln(0) ≡ 0, should not produce NaN/inf."""
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([10.0, 0.0, 30.0, 0.0, 10.0])
    bc = bc_statistics(obs, ref)
    assert math.isfinite(bc["shape"]["lambda"])
    assert math.isfinite(bc["normalization"]["lambda"])
    assert math.isfinite(bc["total"]["bc_stat"])


def test_empty_and_degenerate_distributions_return_error():
    assert "error" in bc_statistics(np.array([]), np.array([]))
    assert "error" in bc_statistics(np.zeros(5), np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert "error" in bc_statistics(np.array([1.0, 2.0, 3.0]), np.zeros(3))


def test_mismatched_lengths_return_error():
    assert "error" in bc_statistics(np.array([1.0, 2.0]), np.array([1.0, 2.0, 3.0]))


def test_single_bin_series_has_shape_dof_one():
    """With N=1, shape has zero effective dof — we clamp to 1 for p-value sanity."""
    bc = bc_statistics(np.array([10.0]), np.array([10.0]))
    assert bc["shape"]["dof"] == 1
    assert bc["shape"]["lambda"] == 0.0


# ── KS secondary metric ────────────────────────────────────────────────────


def test_ks_identical_is_zero():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    r = ks_binned(ref, ref)
    assert r["stat"] == 0.0
    assert r["p_value"] == pytest.approx(1.0)


def test_ks_distorted_has_small_p():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    obs = np.array([30.0, 20.0, 10.0, 20.0, 10.0])  # weight shifted leftward
    r = ks_binned(obs, ref)
    assert r["stat"] > 0
    assert r["p_value"] < 0.5


def test_ks_monotonic_in_distortion_strength():
    ref = np.array([10.0, 20.0, 30.0, 20.0, 10.0])
    # Progressively push mass from middle to first bin.
    prev_stat = 0.0
    for push in (0.0, 10.0, 20.0, 29.0):
        obs = ref.copy()
        obs[0] += push
        obs[2] -= push
        r = ks_binned(obs, ref)
        assert r["stat"] >= prev_stat
        prev_stat = r["stat"]
