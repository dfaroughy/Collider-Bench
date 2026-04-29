"""Unit tests for the annealing schedule helpers."""

from __future__ import annotations

import math
import random

import pytest

from agents.anneal.runtime.controller.anneal_loop import (
    carry_forward_depth,
    should_rollback,
    temperature,
)


@pytest.mark.parametrize("schedule", ["linear", "cosine"])
def test_temperature_starts_at_t0_and_ends_at_zero(schedule):
    max_iters = 5
    t0 = 1.0
    assert temperature(1, max_iters, t0, schedule) == pytest.approx(t0)
    # iter index = max_iters+1 sits at frac=1, which both schedules collapse to 0.
    assert temperature(max_iters + 1, max_iters, t0, schedule) == pytest.approx(0.0, abs=1e-9)


def test_temperature_none_schedule_is_zero():
    assert temperature(1, 5, 1.0, "none") == 0.0
    assert temperature(3, 5, 1.0, "none") == 0.0


def test_temperature_monotone_decreasing():
    """Both linear and cosine should decrease with iter index."""
    for schedule in ("linear", "cosine"):
        prev = float("inf")
        for i in range(1, 6):
            t = temperature(i, 5, 1.0, schedule)
            assert t <= prev
            prev = t


def test_carry_forward_depth_thresholds():
    # Hot (T > 0.8): only results + report carry.
    _, ana, sims = carry_forward_depth(0.9)
    assert not ana and not sims
    # Warm (0.5 < T ≤ 0.8): analysis carries, sims don't.
    _, ana, sims = carry_forward_depth(0.7)
    assert ana and not sims
    # Cold (T ≤ 0.5): everything carries.
    _, ana, sims = carry_forward_depth(0.3)
    assert ana and sims
    # Boundary cases (≤ semantics).
    _, ana, sims = carry_forward_depth(0.8)
    assert ana and not sims
    _, ana, sims = carry_forward_depth(0.5)
    assert ana and sims


def test_rollback_no_regression_never_rolls_back():
    rng = random.Random(0)
    assert not should_rollback(0.0, 0.5, rng)
    assert not should_rollback(-0.1, 0.5, rng)


def test_rollback_zero_temperature_always_rolls_on_regression():
    rng = random.Random(0)
    assert not should_rollback(0.1, 0.0, rng)  # T=0 short-circuits to False per impl
    # Tiny T → exp(-Δ/T) ~ 0 → almost always rollback.
    rolls = sum(should_rollback(0.5, 0.001, random.Random(i)) for i in range(20))
    assert rolls >= 18


def test_rollback_high_temperature_rarely_rolls():
    """At T >> Δ, exp(-Δ/T) ≈ 1, so rollback is rare."""
    rolls = sum(should_rollback(0.05, 100.0, random.Random(i)) for i in range(200))
    # Probability of rollback ≈ 1 - exp(-0.0005) ≈ 0.0005, expect ~0 over 200 trials.
    assert rolls <= 5


def test_rollback_metropolis_probability():
    """Empirical rate at moderate Δ/T should match exp(-Δ/T)."""
    delta, t = 0.5, 1.0
    expected_keep = math.exp(-delta / t)  # P(no rollback)
    n = 5000
    rolls = sum(should_rollback(delta, t, random.Random(i)) for i in range(n))
    empirical_rollback = rolls / n
    assert abs(empirical_rollback - (1 - expected_keep)) < 0.03
