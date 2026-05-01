"""Histogram YAML loading and alignment helpers for evaluation.

The benchmark uses HEPData-style histogram YAML. Agent templates and outputs
are two YAML documents (metadata, then histogram); references are usually a
single histogram document. This module gives evaluation code one canonical
parser instead of each evaluator reimplementing the same traversal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml


@dataclass(frozen=True)
class Bin:
    raw: dict[str, Any]
    label: str
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class Series:
    name: str
    values: list[Any]
    errors: list[float | None]


@dataclass(frozen=True)
class Histogram:
    path: Path
    doc: dict[str, Any]
    metadata: dict[str, Any] | None
    bins: list[Bin]
    x_name: str
    x_units: str
    series: list[Series]


@dataclass(frozen=True)
class SeriesComparison:
    name: str
    reference: Series
    prediction: Series
    bins: list[Bin]
    reference_values: np.ndarray
    prediction_values: np.ndarray
    reference_errors: np.ndarray
    mask: np.ndarray
    n_bins: int
    n_filled: int


def load_yaml_docs(path: Path) -> list[Any]:
    with open(path) as f:
        return list(yaml.safe_load_all(f))


def find_histogram_doc(docs: list[Any]) -> dict[str, Any]:
    hist = next(
        (d for d in docs if isinstance(d, dict) and "dependent_variables" in d),
        None,
    )
    if hist is None:
        raise ValueError(
            "no YAML document with `dependent_variables` (expected a HEPData-style histogram)"
        )
    return hist


def find_metadata_doc(docs: list[Any]) -> dict[str, Any] | None:
    return next((d for d in docs if isinstance(d, dict) and "instructions" in d), None)


def load_histogram_yaml(path: Path) -> Histogram:
    docs = load_yaml_docs(path)
    hist = find_histogram_doc(docs)
    meta = find_metadata_doc(docs)
    bins, x_name, x_units = extract_bins(hist)
    return Histogram(
        path=path,
        doc=hist,
        metadata=meta,
        bins=bins,
        x_name=x_name,
        x_units=x_units,
        series=extract_series(hist),
    )


def _entry_error(entry: dict[str, Any]) -> float | None:
    err_sq = 0.0
    has_err = False
    for err in entry.get("errors", []) or []:
        if "symerror" in err and err["symerror"] is not None:
            err_sq += float(err["symerror"]) ** 2
            has_err = True
        elif "asymerror" in err:
            asym = err["asymerror"]
            plus = abs(float(asym.get("plus", 0) or 0))
            minus = abs(float(asym.get("minus", 0) or 0))
            if plus or minus:
                err_sq += max(plus, minus) ** 2
                has_err = True
    return math.sqrt(err_sq) if has_err else None


def extract_series(data: dict[str, Any]) -> list[Series]:
    out: list[Series] = []
    for dep in data.get("dependent_variables", []) or []:
        name = dep.get("header", {}).get("name", "unknown")
        values: list[Any] = []
        errors: list[float | None] = []
        for entry in dep.get("values", []) or []:
            values.append(entry.get("value"))
            errors.append(_entry_error(entry))
        out.append(Series(name=name, values=values, errors=errors))
    return out


def extract_bins(data: dict[str, Any]) -> tuple[list[Bin], str, str]:
    for indep in data.get("independent_variables", []) or []:
        name = indep.get("header", {}).get("name", "unknown")
        units = indep.get("header", {}).get("units", "")
        bins = []
        for entry in indep.get("values", []) or []:
            if "low" in entry and "high" in entry:
                low = float(entry["low"])
                high = float(entry["high"])
                label = f"{entry['low']}-{entry['high']}"
                bins.append(Bin(raw=entry, label=label, low=low, high=high))
            else:
                label = str(entry.get("value", "?"))
                bins.append(Bin(raw=entry, label=label))
        return bins, name, units
    return [], "unknown", ""


def bin_edges(histogram: Histogram) -> np.ndarray:
    edges: list[float] = []
    for i, b in enumerate(histogram.bins):
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
    return np.array(edges, dtype=float)


def select_series(histogram: Histogram, name: str) -> Series | None:
    return next((s for s in histogram.series if s.name == name), None)


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_series(reference: Series, prediction: Series, bins: list[Bin]) -> SeriesComparison:
    n_bins = len(reference.values)
    n = min(len(reference.values), len(prediction.values))
    n_filled = sum(
        1 for i in range(n_bins) if i < len(prediction.values) and prediction.values[i] is not None
    )
    mask = np.array(
        [
            as_float(reference.values[i]) is not None and as_float(prediction.values[i]) is not None
            for i in range(n)
        ],
        dtype=bool,
    )
    ref = np.array(
        [as_float(reference.values[i]) if i < n and mask[i] else 0.0 for i in range(n)],
        dtype=float,
    )
    pred = np.array(
        [as_float(prediction.values[i]) if i < n and mask[i] else 0.0 for i in range(n)],
        dtype=float,
    )
    errs = np.array(
        [
            reference.errors[i]
            if i < len(reference.errors) and reference.errors[i] is not None
            else 0.0
            for i in range(n)
        ],
        dtype=float,
    )
    return SeriesComparison(
        name=reference.name,
        reference=reference,
        prediction=prediction,
        bins=bins,
        reference_values=ref[mask],
        prediction_values=pred[mask],
        reference_errors=errs[mask],
        mask=mask,
        n_bins=n_bins,
        n_filled=n_filled,
    )
