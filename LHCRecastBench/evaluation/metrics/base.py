"""Small metric interface used by the evaluation scorer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from LHCRecastBench.evaluation.context import EvalContext


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: str = "ok"
    components: dict = field(default_factory=dict)
    primary_values: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    error: str | None = None


class Metric(Protocol):
    name: str

    def compute(self, context: EvalContext) -> MetricResult: ...


class MetricRunner:
    def __init__(self, metrics: list[Metric]):
        self.metrics = {metric.name: metric for metric in metrics}

    def compute(
        self, context: EvalContext, names: tuple[str, ...] | None = None
    ) -> dict[str, MetricResult]:
        selected = names or tuple(self.metrics)
        out = {}
        for name in selected:
            metric = self.metrics.get(name)
            if metric is None:
                out[name] = MetricResult(
                    name=name, status="error", error=f"unknown metric {name!r}"
                )
                continue
            out[name] = metric.compute(context)
        return out


def get_default_metrics() -> MetricRunner:
    from .baker_cousins import BakerCousinsMetric
    from .mean_abs_frac_error import MeanAbsFracErrorMetric

    return MetricRunner([BakerCousinsMetric(), MeanAbsFracErrorMetric()])
