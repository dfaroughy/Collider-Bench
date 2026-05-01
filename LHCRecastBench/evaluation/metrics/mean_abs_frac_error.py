"""Mean absolute fractional bin-error metric."""

from __future__ import annotations

import numpy as np

from LHCRecastBench.evaluation.context import EvalContext

from .base import MetricResult


def bin_fractional_error_percent(observed: np.ndarray, reference: np.ndarray) -> dict:
    obs = np.asarray(observed, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if obs.size == 0 or ref.size == 0 or obs.size != ref.size:
        return {"error": "empty or mismatched distributions"}

    mask = ref > 0
    n_valid = int(mask.sum())
    n_zero_truth = int((~mask).sum())
    n_zero_truth_nonzero_pred = int(((~mask) & (obs != 0)).sum())
    if n_valid == 0:
        return {
            "error": "no positive truth bins",
            "n_valid_bins": 0,
            "n_zero_truth_bins": n_zero_truth,
            "n_zero_truth_nonzero_pred": n_zero_truth_nonzero_pred,
        }

    terms = np.abs(ref[mask] - obs[mask]) / ref[mask]
    return {
        "mean_abs_frac_error_percent": round(float(100.0 * terms.mean()), 3),
        "n_valid_bins": n_valid,
        "n_zero_truth_bins": n_zero_truth,
        "n_zero_truth_nonzero_pred": n_zero_truth_nonzero_pred,
    }


class MeanAbsFracErrorMetric:
    name = "mean_abs_frac_error"

    def compute(self, context: EvalContext) -> MetricResult:
        result = bin_fractional_error_percent(
            context.comparison.prediction_values,
            context.comparison.reference_values,
        )
        if "error" in result:
            return MetricResult(
                name=self.name, status="error", error=result["error"], diagnostics=result
            )
        return MetricResult(name=self.name, diagnostics=result)
