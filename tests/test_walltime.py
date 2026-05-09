"""Tests for agent_runtime.launch._parse_walltime_s.

The walltime regex is the single thing standing between a typo in
task.toml and an SLURM allocation that runs for the wrong duration.
"""

from __future__ import annotations

import pytest

from agent_runtime.launch import _parse_walltime_s


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4h", 4 * 3600),
        ("30m", 30 * 60),
        ("120s", 120),
        ("1h30m", 3600 + 30 * 60),
        ("2h45m30s", 2 * 3600 + 45 * 60 + 30),
        ("0s", 0),
        ("1h0m0s", 3600),
    ],
)
def test_valid_forms_parse(raw, expected):
    assert _parse_walltime_s(raw) == float(expected)


@pytest.mark.parametrize("raw", [None, ""])
def test_none_or_empty_returns_none(raw):
    assert _parse_walltime_s(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "1.5h",  # decimals not supported
        "1d",  # unit not supported
        "abc",
        "h",  # missing number
        "1h2x",  # garbage after valid prefix
    ],
)
def test_malformed_raises(raw):
    with pytest.raises(ValueError):
        _parse_walltime_s(raw)


def test_surrounding_whitespace_is_tolerated():
    # The regex anchors to ^\s*…\s*$ so leading/trailing spaces are OK.
    assert _parse_walltime_s(" 1h ") == 3600.0


def test_minutes_above_60_still_parse():
    # The regex doesn't normalize minutes/seconds — "60m" is valid and equals 1h.
    assert _parse_walltime_s("60m") == _parse_walltime_s("1h")
