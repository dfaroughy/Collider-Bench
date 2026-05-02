"""HEPData-style histogram YAML loading + alignment.

Histogram YAMLs come in two shapes in this benchmark:
  - Reference files: a single histogram document.
  - Agent templates / outputs: two YAML documents, an `instructions` metadata
    doc followed by the histogram doc.

`load_histogram` accepts either; `align` produces the index-aligned numpy
arrays the metrics consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml


@dataclass(frozen=True)
class Bin:
    label: str
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class Series:
    name: str
    values: list[Any]


@dataclass(frozen=True)
class Histogram:
    path: Path
    bins: list[Bin]
    x_name: str
    x_units: str
    series: list[Series]


@dataclass(frozen=True)
class Aligned:
    """Index-aligned (both-non-null) numpy arrays for one series comparison."""

    name: str
    bins: list[Bin]
    reference: np.ndarray
    prediction: np.ndarray
    n_bins: int  # total bins in the reference
    n_filled: int  # bins the agent actually filled (non-null in prediction)


def load_histogram(path: Path) -> Histogram:
    """Load a HEPData-style histogram YAML (one or two docs)."""
    with open(path) as f:
        docs = list(yaml.safe_load_all(f))
    hist = next((d for d in docs if isinstance(d, dict) and "dependent_variables" in d), None)
    if hist is None:
        raise ValueError(f"{path}: no YAML doc with `dependent_variables`")

    # Bins from the (single) independent variable.
    bins: list[Bin] = []
    x_name = "unknown"
    x_units = ""
    for indep in hist.get("independent_variables", []) or []:
        x_name = indep.get("header", {}).get("name", "unknown")
        x_units = indep.get("header", {}).get("units", "")
        for entry in indep.get("values", []) or []:
            if "low" in entry and "high" in entry:
                bins.append(
                    Bin(
                        label=f"{entry['low']}-{entry['high']}",
                        low=float(entry["low"]),
                        high=float(entry["high"]),
                    )
                )
            else:
                bins.append(Bin(label=str(entry.get("value", "?"))))
        break  # only one independent var supported

    # Series from dependent variables.
    series: list[Series] = []
    for dep in hist.get("dependent_variables", []) or []:
        name = dep.get("header", {}).get("name", "unknown")
        values = [entry.get("value") for entry in (dep.get("values", []) or [])]
        series.append(Series(name=name, values=values))

    return Histogram(path=path, bins=bins, x_name=x_name, x_units=x_units, series=series)


def select_series(histogram: Histogram, name: str) -> Series | None:
    return next((s for s in histogram.series if s.name == name), None)


def bin_edges(bins: list[Bin]) -> np.ndarray:
    """Return monotonically-increasing edges for a list of bins. For
    discrete-label bins falls back to integer indices."""
    edges: list[float] = []
    last = 0.0
    for i, b in enumerate(bins):
        if b.low is not None and b.high is not None:
            edges.append(float(b.low))
            last = float(b.high)
        else:
            try:
                edge = float(b.label)
            except ValueError:
                edge = float(i)
            edges.append(edge)
            last = edge + 1.0
    if edges:
        edges.append(last)
    return np.asarray(edges, dtype=float)


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def align(reference: Series, prediction: Series, bins: list[Bin]) -> Aligned:
    """Align two series by index; keep only bins where both are non-null."""
    n_bins = len(reference.values)
    n = min(len(reference.values), len(prediction.values))
    n_filled = sum(
        1 for i in range(n_bins) if i < len(prediction.values) and prediction.values[i] is not None
    )
    ref_vals = np.asarray([_as_float(reference.values[i]) or 0.0 for i in range(n)], dtype=float)
    pred_vals = np.asarray([_as_float(prediction.values[i]) or 0.0 for i in range(n)], dtype=float)
    mask = np.asarray(
        [
            _as_float(reference.values[i]) is not None
            and _as_float(prediction.values[i]) is not None
            for i in range(n)
        ],
        dtype=bool,
    )
    return Aligned(
        name=reference.name,
        bins=bins[:n],
        reference=ref_vals[mask],
        prediction=pred_vals[mask],
        n_bins=n_bins,
        n_filled=n_filled,
    )
