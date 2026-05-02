"""Sanity tests for the LHCRecastBench.Evals metrics.

Covers the math of bin_error / jsd / baker_cousins independently from the
histogram I/O layer. Toy counts are tiny here so the suite stays fast;
production scoring uses 1M.
"""

from __future__ import annotations

import numpy as np
import pytest

from LHCRecastBench.Evals.metrics import (
    baker_cousins_p_value,
    jensen_shannon,
    mean_abs_frac_error_pct,
    total_frac_error_pct,
)


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
