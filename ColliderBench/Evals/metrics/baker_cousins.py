"""Baker-Cousins likelihood-ratio + toy-calibrated p-value.

We expose two p-values via `baker_cousins_p_value`:

  - `shape`: λ_shape = 2 Σ O · ln(O / Ê), where Ê = α·E with α = ΣO/ΣE.
    Sensitive to bin-by-bin disagreement after the totals are matched.
  - `normalization`: λ_norm = 2 [ΣO·ln(ΣO/ΣE) - (ΣO - ΣE)].
    Sensitive only to the total-yield discrepancy.

Each λ is calibrated by toy pseudo-experiments under a Poisson + log-normal
tolerance null. The reported p-value is `Pr(λ_toy ≥ λ_obs)`. We don't
report a calibrated z or a "bounded score" — the raw p-value is the
statistical reading and downstream consumers can convert if they want.

`λ_total = λ_shape + λ_norm` is an algebraic identity (not asymptotic),
falling out of profiling α over the Poisson likelihood.
"""

from __future__ import annotations

import numpy as np


DEFAULT_N_TOYS = 1_000_000


def _lam_norm(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Per-toy normalization λ. Vectorized in the leading axis."""
    obs_tot = o.sum(axis=-1)
    ref_tot = float(r.sum())
    out = np.zeros_like(obs_tot, dtype=float)
    mask = (obs_tot > 0) & (ref_tot > 0)
    if obs_tot.ndim:
        out[mask] = 2.0 * (
            obs_tot[mask] * np.log(obs_tot[mask] / ref_tot) - (obs_tot[mask] - ref_tot)
        )
    elif mask:
        out = np.array(
            2.0 * (float(obs_tot) * np.log(float(obs_tot) / ref_tot) - (float(obs_tot) - ref_tot))
        )
    return np.maximum(out, 0.0)


def _lam_shape(o: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Per-toy shape λ. Vectorized in the leading axis."""
    o2 = np.atleast_2d(o)
    obs_tot = o2.sum(axis=-1, keepdims=True)
    ref_tot = float(r.sum())
    ratio = np.where(obs_tot > 0, obs_tot / ref_tot, 1.0)
    r_hat = ratio * r[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(
            (o2 > 0) & (r_hat > 0),
            o2 * np.log(o2 / np.where(r_hat > 0, r_hat, 1.0)),
            0.0,
        )
    out = np.maximum(2.0 * terms.sum(axis=-1), 0.0)
    return out if o.ndim > 1 else out[0]


def _toy_p_value(
    lam_obs: float,
    ref: np.ndarray,
    statistic_vec,
    *,
    systematic_frac: float,
    n_toys: int,
    seed: int,
) -> float:
    """Empirical upper-tail probability `Pr(λ_toy ≥ λ_obs)` under the null."""
    rng = np.random.default_rng(seed)
    n = len(ref)
    if systematic_frac > 0:
        theta = rng.standard_normal((n_toys, n))
        nu = ref[None, :] * np.exp(systematic_frac * theta)
    else:
        nu = np.broadcast_to(ref, (n_toys, n)).astype(float, copy=False)
    nu = np.clip(nu, 0.0, None)

    o_toy = rng.poisson(nu).astype(float, copy=False)
    lam_toys = statistic_vec(o_toy, ref)
    n_above = int((lam_toys >= lam_obs).sum())
    return (n_above + 1) / (n_toys + 1)


def baker_cousins_p_value(
    observed: np.ndarray,
    reference: np.ndarray,
    *,
    kind: str = "shape",
    systematic_frac: float = 0.0,
    n_toys: int = DEFAULT_N_TOYS,
    seed: int = 0,
) -> float | None:
    """Toy-calibrated p-value for one of {"shape", "normalization"}.

    Returns None if observed/reference are mismatched or one total is zero.
    """
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return None
    if float(obs.sum()) <= 0 or float(ref.sum()) <= 0:
        return None

    if kind == "shape":
        statistic = _lam_shape
        lam_obs = float(_lam_shape(obs, ref))
    elif kind == "normalization":
        statistic = _lam_norm
        lam_obs = float(_lam_norm(obs, ref))
    else:
        raise ValueError(f"kind must be 'shape' or 'normalization', got {kind!r}")

    p = _toy_p_value(
        lam_obs, ref, statistic, systematic_frac=systematic_frac, n_toys=n_toys, seed=seed
    )
    return float(round(p, 6))
