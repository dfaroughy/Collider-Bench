"""Jensen-Shannon divergence between two histograms.

JSD is a symmetrized, smoothed Kullback-Leibler divergence. With base-2
log it lies in `[0, 1]`: 0 when distributions are identical, 1 when their
supports are disjoint. Pure shape metric — both histograms get normalized
to unit area before the divergence is computed, so JSD ignores total
yield differences.

`jensen_shannon_distance = sqrt(jensen_shannon)` is the *Jensen-Shannon
distance* — a true metric on probability distributions (satisfies the
triangle inequality), in `[0, 1]` with the same base-2 convention.
"""

from __future__ import annotations

import numpy as np


def jensen_shannon(
    observed: np.ndarray, reference: np.ndarray, *, base: float = 2.0
) -> tuple[float | None, float | None]:
    """Return `(jsd, jsd_distance)`.

    `jsd_distance = sqrt(jsd)`. Both `None` if either histogram has zero
    total (shape divergence is undefined when one distribution is empty).
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
    m = 0.5 * (p + q)

    # KL(P || M) summing only over support of P (0 log 0 ≡ 0).
    log_base = np.log(base)
    with np.errstate(divide="ignore", invalid="ignore"):
        kl_pm = float(np.where(p > 0, p * (np.log(p) - np.log(m)), 0.0).sum() / log_base)
        kl_qm = float(np.where(q > 0, q * (np.log(q) - np.log(m)), 0.0).sum() / log_base)

    jsd = 0.5 * (kl_pm + kl_qm)
    # Numerical noise can land jsd slightly below 0 or above 1; clamp.
    jsd = max(0.0, min(1.0, jsd))
    return (round(jsd, 6), round(jsd**0.5, 6))
