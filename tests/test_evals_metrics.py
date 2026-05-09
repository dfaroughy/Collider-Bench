"""Sanity tests for the ColliderBench.Evals metrics.

Covers the math of bin_error / jsd / baker_cousins independently from the
histogram I/O layer. Toy counts are tiny here so the suite stays fast;
production scoring uses 1M.
"""

from __future__ import annotations

import numpy as np
import pytest

from ColliderBench.Evals.metrics import (
    Delta,
    baker_cousins_p_value,
    jensen_shannon,
    mean_abs_frac_error_pct,
    per_bin_disagreement,
    rmsle,
    total_frac_error_pct,
)
from ColliderBench.Evals.metrics.bin_error import relative_l2


# ── bin error ────────────────────────────────────────────────────────────────


def test_bin_error_zero_when_identical():
    a = np.array([10.0, 20.0, 30.0])
    assert mean_abs_frac_error_pct(a, a) == 0.0


def test_bin_error_basic():
    obs = np.array([12.0, 18.0, 30.0])
    ref = np.array([10.0, 20.0, 30.0])
    # 20% on bin 0, 10% on bin 1, 0% on bin 2 → mean = 10%
    assert mean_abs_frac_error_pct(obs, ref) == pytest.approx(10.0)


def test_bin_error_drops_zero_truth_bins():
    # The zero-truth bin doesn't contribute (no division-by-zero).
    obs = np.array([10.0, 5.0])
    ref = np.array([10.0, 0.0])
    assert mean_abs_frac_error_pct(obs, ref) == 0.0  # only bin 0 counts → 0%


def test_bin_error_returns_none_on_no_positive_bins():
    obs = np.array([1.0, 2.0])
    ref = np.array([0.0, 0.0])
    assert mean_abs_frac_error_pct(obs, ref) is None


def test_bin_error_returns_none_on_mismatched_sizes():
    assert mean_abs_frac_error_pct(np.array([1.0]), np.array([1.0, 2.0])) is None


def test_total_frac_error_zero_for_matching_totals():
    assert total_frac_error_pct(np.array([10.0, 20.0]), np.array([15.0, 15.0])) == 0.0


def test_total_frac_error_basic():
    obs = np.array([5.0, 5.0])  # total 10
    ref = np.array([10.0, 10.0])  # total 20
    assert total_frac_error_pct(obs, ref) == pytest.approx(50.0)


def test_total_frac_error_returns_none_on_zero_ref():
    assert total_frac_error_pct(np.array([1.0]), np.array([0.0])) is None


# ── Δ (paper-text fractional yield difference, raw fraction not %) ──────────


def test_Delta_zero_for_matching_totals():
    assert Delta(np.array([10.0, 20.0]), np.array([15.0, 15.0])) == 0.0


def test_Delta_basic():
    # Σobs=10, Σref=20 → Δ = |10-20|/20 = 0.5
    assert Delta(np.array([5.0, 5.0]), np.array([10.0, 10.0])) == pytest.approx(0.5)


def test_Delta_is_total_frac_error_pct_over_100():
    """Δ must equal total_frac_error_pct / 100 by construction."""
    obs = np.array([7.0, 13.0, 11.0])
    ref = np.array([5.0, 18.0, 9.0])
    assert Delta(obs, ref) == pytest.approx(total_frac_error_pct(obs, ref) / 100.0, rel=1e-5)


def test_Delta_returns_none_on_zero_ref():
    assert Delta(np.array([1.0]), np.array([0.0])) is None


def test_Delta_returns_none_on_empty():
    assert Delta(np.array([]), np.array([])) is None


# ── d̄ / d_max (per-bin disagreement on normalized histograms) ───────────────


def test_per_bin_disagreement_zero_for_identical():
    a = np.array([10.0, 20.0, 30.0])
    d_bar, d_max = per_bin_disagreement(a, a)
    assert d_bar == 0.0
    assert d_max == 0.0


def test_per_bin_disagreement_disjoint_supports():
    """All mass in bin 0 vs all mass in bin 1: d_bar = 1 (TV), d_max = 1."""
    p = np.array([10.0, 0.0])
    q = np.array([0.0, 10.0])
    d_bar, d_max = per_bin_disagreement(p, q)
    assert d_bar == pytest.approx(1.0)
    assert d_max == pytest.approx(1.0)


def test_per_bin_disagreement_normalization_invariant():
    """Scale either histogram → no change (both are unit-area normalized first)."""
    p = np.array([1.0, 2.0, 3.0])
    q = np.array([3.0, 2.0, 1.0])
    a = per_bin_disagreement(p, q)
    b = per_bin_disagreement(p * 1000, q)
    c = per_bin_disagreement(p, q / 100)
    assert a == b == c


def test_per_bin_disagreement_d_bar_in_unit_interval():
    """TV distance between probability distributions is in [0, 1]."""
    rng = np.random.default_rng(0)
    for _ in range(20):
        p = rng.exponential(size=8) + 0.1
        q = rng.exponential(size=8) + 0.1
        d_bar, _ = per_bin_disagreement(p, q)
        assert 0.0 <= d_bar <= 1.0 + 1e-9


def test_per_bin_disagreement_d_max_in_unit_interval():
    """Worst-bin probability difference is in [0, 1]."""
    rng = np.random.default_rng(1)
    for _ in range(20):
        p = rng.exponential(size=8) + 0.1
        q = rng.exponential(size=8) + 0.1
        _, d_max = per_bin_disagreement(p, q)
        assert 0.0 <= d_max <= 1.0 + 1e-9


def test_per_bin_disagreement_d_max_ge_d_bar_over_K():
    """d_max ≥ d_bar / K (since Σ|p-q|/2 ≤ K·max|p-q|/2 → max ≥ Σ/K, then ÷2)."""
    rng = np.random.default_rng(2)
    for _ in range(10):
        K = 12
        p = rng.exponential(size=K) + 0.1
        q = rng.exponential(size=K) + 0.1
        d_bar, d_max = per_bin_disagreement(p, q)
        # 2·d_bar = Σ|p-q| ≤ K·max|p-q| = K·d_max → d_max ≥ 2·d_bar/K
        assert d_max + 1e-9 >= 2 * d_bar / K


def test_per_bin_disagreement_returns_none_on_zero_total():
    out = per_bin_disagreement(np.array([0.0, 0.0]), np.array([1.0, 1.0]))
    assert out == (None, None)


def test_per_bin_disagreement_returns_none_on_size_mismatch():
    out = per_bin_disagreement(np.array([1.0]), np.array([1.0, 2.0]))
    assert out == (None, None)


# ── RMSLE (raw-yield, multi-OoM log error) ───────────────────────────────────


def test_rmsle_zero_for_identical():
    a = np.array([10.0, 100.0, 1000.0])
    assert rmsle(a, a) == 0.0


def test_rmsle_basic():
    """Predicting 10× too low on every bin: ln(10+1)-ln(100+1) ≈ -2.214 ⇒ RMSLE ≈ 2.214."""
    obs = np.array([10.0, 10.0])
    ref = np.array([100.0, 100.0])
    expected = abs(np.log(11.0) - np.log(101.0))
    assert rmsle(obs, ref) == pytest.approx(expected, abs=1e-5)


def test_rmsle_scale_aware_across_orders_of_magnitude():
    """Catches tail-bin disagreement that linear MSE would miss."""
    # Same absolute error (Δ=10) on a peak (1000→1010) vs a tail (5→15)
    # has very different log-ratio impact: peak log-ratio is tiny, tail is large.
    peak_err = rmsle(np.array([1010.0]), np.array([1000.0]))
    tail_err = rmsle(np.array([15.0]), np.array([5.0]))
    assert tail_err > 5.0 * peak_err


def test_rmsle_handles_zero_bins():
    """+1 offset means zero counts don't blow up the log."""
    obs = np.array([0.0, 5.0, 10.0])
    ref = np.array([0.0, 5.0, 10.0])
    assert rmsle(obs, ref) == 0.0
    # And a one-sided zero (truth=0, pred=10) is finite:
    out = rmsle(np.array([10.0]), np.array([0.0]))
    assert out is not None
    assert out > 0


def test_rmsle_returns_none_on_negative():
    """Negative inputs are undefined for RMSLE."""
    assert rmsle(np.array([-1.0, 5.0]), np.array([2.0, 5.0])) is None
    assert rmsle(np.array([1.0, 5.0]), np.array([-2.0, 5.0])) is None


def test_rmsle_returns_none_on_size_mismatch():
    assert rmsle(np.array([1.0]), np.array([1.0, 2.0])) is None


def test_rmsle_returns_none_on_empty():
    assert rmsle(np.array([]), np.array([])) is None


# ── Jensen-Shannon ───────────────────────────────────────────────────────────


def test_jsd_zero_for_identical_distributions():
    a = np.array([10.0, 20.0, 30.0])
    jsd, dist = jensen_shannon(a, a)
    assert jsd == pytest.approx(0.0, abs=1e-9)
    assert dist == pytest.approx(0.0, abs=1e-9)


def test_jsd_max_for_disjoint_supports():
    # All mass in bin 0 vs all mass in bin 1 (base-2 log → JSD = 1).
    p = np.array([10.0, 0.0])
    q = np.array([0.0, 10.0])
    jsd, dist = jensen_shannon(p, q)
    assert jsd == pytest.approx(1.0, abs=1e-6)
    assert dist == pytest.approx(1.0, abs=1e-6)


def test_jsd_dist_is_sqrt_of_jsd():
    p = np.array([3.0, 7.0])
    q = np.array([6.0, 4.0])
    jsd, dist = jensen_shannon(p, q)
    assert dist == pytest.approx(jsd**0.5, rel=1e-5)


def test_jsd_normalization_invariant():
    # JSD only sees shape: scaling either side leaves JSD unchanged.
    p = np.array([1.0, 2.0, 3.0])
    q = np.array([1.0, 2.0, 3.0])  # same shape
    jsd_a, _ = jensen_shannon(p, q)
    jsd_b, _ = jensen_shannon(p, 1000.0 * q)
    assert jsd_a == pytest.approx(jsd_b, abs=1e-9)


def test_jsd_in_unit_interval():
    rng = np.random.default_rng(0)
    for _ in range(20):
        p = rng.exponential(size=10) + 0.1
        q = rng.exponential(size=10) + 0.1
        jsd, dist = jensen_shannon(p, q)
        assert 0.0 <= jsd <= 1.0
        assert 0.0 <= dist <= 1.0


def test_jsd_returns_none_on_zero_total():
    assert jensen_shannon(np.array([0.0, 0.0]), np.array([1.0, 1.0])) == (None, None)


def test_jsd_returns_none_on_mismatched_sizes():
    assert jensen_shannon(np.array([1.0]), np.array([1.0, 2.0])) == (None, None)


# ── Baker-Cousins ────────────────────────────────────────────────────────────

# Use a tiny toy count to keep the suite fast. Production: 1M.
_FAST_TOYS = 5_000


def test_bc_shape_p_value_high_when_obs_matches_ref():
    """Drawing observed = expected mean → λ_obs is small → p large."""
    rng = np.random.default_rng(0)
    ref = np.array([100.0, 200.0, 300.0, 200.0, 100.0])
    obs = rng.poisson(ref).astype(float)
    p = baker_cousins_p_value(obs, ref, kind="shape", n_toys=_FAST_TOYS, seed=0)
    assert p > 0.05  # not at toy floor


def test_bc_shape_p_value_low_when_shape_disagrees():
    """A reversed-shape observation should yield a tiny p (asymmetric ref)."""
    ref = np.array([400.0, 300.0, 200.0, 100.0, 50.0])  # decreasing
    obs = ref[::-1].astype(float)  # increasing — same total
    assert obs.sum() == pytest.approx(ref.sum())
    p = baker_cousins_p_value(obs, ref, kind="shape", n_toys=_FAST_TOYS, seed=0)
    assert p < 0.01


def test_bc_normalization_p_value_low_when_totals_disagree():
    ref = np.array([100.0, 100.0, 100.0])
    obs = ref * 5.0  # 5x the total → very unlikely under Poisson(ref)
    p = baker_cousins_p_value(obs, ref, kind="normalization", n_toys=_FAST_TOYS, seed=0)
    assert p < 0.01


def test_bc_returns_none_on_zero_total():
    assert (
        baker_cousins_p_value(
            np.zeros(3), np.array([1.0, 2.0, 3.0]), kind="shape", n_toys=_FAST_TOYS
        )
        is None
    )


def test_bc_invalid_kind_raises():
    with pytest.raises(ValueError, match="kind"):
        baker_cousins_p_value(np.array([1.0]), np.array([1.0]), kind="weird", n_toys=_FAST_TOYS)


# ── relative_l2 ─────────────────────────────────────────────────────────────


def test_relative_l2_zero_when_identical():
    a = np.array([10.0, 20.0, 30.0])
    assert relative_l2(a, a) == pytest.approx(0.0)


def test_relative_l2_scales_with_deviation():
    ref = np.array([10.0, 20.0, 30.0])
    obs = np.array([11.0, 22.0, 33.0])  # +10% everywhere
    # sqrt(Σ Δ² / Σ ref²) = sqrt(14 / 1400) = 0.1
    assert relative_l2(obs, ref) == pytest.approx(0.1)


def test_relative_l2_returns_none_on_size_mismatch():
    assert relative_l2(np.array([1.0, 2.0]), np.array([1.0])) is None


def test_relative_l2_returns_none_on_zero_reference():
    assert relative_l2(np.array([1.0, 2.0]), np.array([0.0, 0.0])) is None


def test_relative_l2_normalize_makes_it_a_shape_metric():
    # Same shape, different normalization → normalize=True yields ~0.
    ref = np.array([1.0, 2.0, 3.0])
    obs = np.array([10.0, 20.0, 30.0])  # 10× scale
    assert relative_l2(obs, ref, normalize=True) == pytest.approx(0.0, abs=1e-12)
    # Without normalize, the scale shows up as a large value.
    assert relative_l2(obs, ref, normalize=False) > 1.0


def test_relative_l2_normalize_returns_none_when_either_sum_nonpositive():
    assert relative_l2(np.zeros(3), np.array([1.0, 2.0, 3.0]), normalize=True) is None
    assert relative_l2(np.array([1.0, 2.0, 3.0]), np.zeros(3), normalize=True) is None


def test_relative_l2_empty_inputs_return_none():
    assert relative_l2(np.array([]), np.array([])) is None
