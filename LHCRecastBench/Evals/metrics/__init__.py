"""Histogram comparison metrics.

Each metric is a plain function taking aligned numpy arrays
`(observed, reference)` and returning either a scalar or a small dict.
No registry, no class hierarchy — score.py composes them directly.
"""

from .baker_cousins import baker_cousins_p_value
from .bin_error import (
    Delta,
    mean_abs_frac_error_pct,
    per_bin_disagreement,
    relative_l2,
    rmsle,
    total_frac_error_pct,
)
from .jsd import jensen_shannon

__all__ = [
    "Delta",
    "baker_cousins_p_value",
    "jensen_shannon",
    "mean_abs_frac_error_pct",
    "per_bin_disagreement",
    "relative_l2",
    "rmsle",
    "total_frac_error_pct",
]
