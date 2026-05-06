#!/usr/bin/env python3
"""Two-panel composite figure for the NeurIPS draft.

Left: model Pareto plot, accuracy at threshold tau versus total resource.
Right: per-model grouped task accuracy bars using the same threshold tau.

Usage:
    python -m utils.neurips.plot_combined
    python -m utils.neurips.plot_combined --tau 0.25 --data utils/neurips/best_l2_norm.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, LogLocator, MultipleLocator, PercentFormatter


@dataclass(frozen=True)
class ModelSpec:
    dirname: str
    label: str
    color: str
    marker: str
    family: str


@dataclass(frozen=True)
class ModelSummary:
    spec: ModelSpec
    task_values: dict[str, float]
    task_accuracy: dict[str, float]
    accuracy: float
    n_pass: int
    n_tasks: int
    resource: float


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("claude_opus-4-7", "Opus 4.7", "#C44536", "o", "Anthropic"),
    ModelSpec("claude_sonnet-4-6", "Sonnet 4.6", "#E88C30", "o", "Anthropic"),
    ModelSpec("claude_haiku-4-5", "Haiku 4.5", "#D8B12D", "o", "Anthropic"),
    ModelSpec("codex_gpt-5.5", "GPT-5.5", "#2454A6", "^", "OpenAI"),
    ModelSpec("codex_gpt-5.4-mini", "GPT-5.4-mini", "#2A9FBF", "^", "OpenAI"),
    ModelSpec("forge_deepseek-v4-pro", "DeepSeek-V4", "#6D4BC3", "s", "DeepSeek"),
)

# Stronger paper colors (richer than the prior pastels, still readable).
PAPER_PALETTE: tuple[tuple[str, str], ...] = (
    ("sus-16-051", "#5C9BD5"),  # mid-strong blue
    ("sus-16-047", "#71B86A"),  # mid-strong green
    ("sus-16-046", "#F0A04A"),  # mid-strong orange
    ("sus-16-034", "#E36767"),  # mid-strong red
)
PAPER_COLOR = dict(PAPER_PALETTE)


RESOURCE_LABELS = {
    "cost": r"cost (\$)",
    "tokens": "tokens",
    "wall": "wall time (hours)",
}


def paper_of(task_id: str) -> str:
    for paper, _ in PAPER_PALETTE:
        if task_id.startswith(paper):
            return paper
    return ""


def _entry_resource(entry: dict[str, Any], xaxis: str) -> float:
    if xaxis == "cost":
        return float(entry.get("cost_usd") or 0.0)
    if xaxis == "tokens":
        return float(entry.get("tokens_total_billed") or 0.0)
    if xaxis == "wall":
        return float(entry.get("wall_s") or 0.0) / 3600.0
    raise ValueError(f"unknown x-axis {xaxis!r}")


def _task_entries(
    data: dict[str, Any],
    spec: ModelSpec,
    *,
    task_contains: str | None,
) -> dict[str, dict[str, Any]]:
    tasks = (data.get("models", {}).get(spec.dirname, {}) or {}).get("tasks", {}) or {}
    if task_contains:
        tasks = {task_id: entry for task_id, entry in tasks.items() if task_contains in task_id}
    return tasks


def collect_summaries(
    data: dict[str, Any],
    *,
    tau: float,
    task_contains: str | None,
    xaxis: str,
) -> list[ModelSummary]:
    summaries: list[ModelSummary] = []
    for spec in MODEL_SPECS:
        entries = _task_entries(data, spec, task_contains=task_contains)
        task_values: dict[str, float] = {}
        task_accuracy: dict[str, float] = {}
        resource = 0.0
        for task_id, entry in entries.items():
            value = entry.get("best")
            if value is None:
                continue
            value_f = float(value)
            task_values[task_id] = value_f
            task_accuracy[task_id] = 100.0 if value_f < tau else 0.0
            resource += _entry_resource(entry, xaxis)
        if not task_values:
            continue
        n_pass = int(sum(v < tau for v in task_values.values()))
        n_tasks = len(task_values)
        summaries.append(
            ModelSummary(
                spec=spec,
                task_values=task_values,
                task_accuracy=task_accuracy,
                accuracy=100.0 * n_pass / n_tasks,
                n_pass=n_pass,
                n_tasks=n_tasks,
                resource=resource,
            )
        )
    return summaries


def sorted_tasks(summaries: list[ModelSummary]) -> list[str]:
    all_tasks = {task_id for summary in summaries for task_id in summary.task_values}
    paper_rank = {paper: i for i, (paper, _) in enumerate(PAPER_PALETTE)}
    return sorted(all_tasks, key=lambda t: (paper_rank.get(paper_of(t), 99), t))


def pareto_front(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Lower x and higher y are better. Return non-dominated points sorted by x."""
    front: list[tuple[float, float, str]] = []
    best_y = -float("inf")
    for x, y, label in sorted(points, key=lambda p: (p[0], -p[1])):
        if y > best_y:
            front.append((x, y, label))
            best_y = y
    return front


def plot_pareto(ax, summaries: list[ModelSummary], *, tau: float, xaxis: str) -> None:
    points: list[tuple[float, float, str]] = []
    for summary in summaries:
        ax.scatter(
            summary.resource,
            summary.accuracy,
            s=256,  # 50 % larger than previous (was 84)
            marker=summary.spec.marker,
            color=summary.spec.color,
            edgecolor="none",
            linewidth=0,
            zorder=4,
        )
        points.append((summary.resource, summary.accuracy, summary.spec.label))

    front = pareto_front(points)
    if len(front) >= 2:
        ax.plot(
            [p[0] for p in front],
            [p[1] for p in front],
            color="#506A85",  # cobalt-gray
            lw=1.6,
            ls="--",
            alpha=0.9,
            zorder=3,
        )

    x_values = [s.resource for s in summaries if s.resource > 0]
    if x_values:
        ax.set_xscale("log")
        ax.set_xlim(min(x_values) * 0.75, max(x_values) * 1.35)
        # Finer x ticks: $50 majors, $10 minors. Locators take linear values
        # even on a log axis, so they show up unevenly spaced — that's fine.
        from matplotlib.ticker import FixedLocator, NullLocator

        if xaxis == "cost":
            ax.xaxis.set_major_locator(FixedLocator([1, 5, 10, 25, 50, 100, 200]))
            ax.xaxis.set_minor_locator(NullLocator())
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${int(v)}"))
        elif xaxis == "tokens":
            ax.xaxis.set_major_locator(LogLocator(base=10.0, numticks=8))
            ax.xaxis.set_minor_locator(NullLocator())
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        else:
            ax.xaxis.set_major_locator(MultipleLocator(10))
            ax.xaxis.set_minor_locator(NullLocator())
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))

    from matplotlib.ticker import NullLocator

    ax.set_ylim(-2, 102)
    # Y ticks: 10 % majors only (no minors per request).
    ax.yaxis.set_major_locator(MultipleLocator(10))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel(RESOURCE_LABELS[xaxis])
    ax.set_ylabel(r"${\rm Acc}_{\tau}$")
    # Grid follows the major ticks only.
    ax.grid(True, which="major", axis="both", linewidth=0.55, color="#D8DDE3")
    ax.grid(False, which="minor")
    ax.tick_params(axis="both", which="major", length=4.5)
    ax.set_axisbelow(True)
    ax.set_box_aspect(1)  # Panel 1 forced to 1:1 (square drawing area)

    # Per-model legend, ordered Anthropic → OpenAI → DeepSeek, each
    # marker drawn in the model's actual color.
    family_order = ["Anthropic", "OpenAI", "DeepSeek"]
    handles_by_family: dict[str, list] = {f: [] for f in family_order}
    for spec in MODEL_SPECS:
        handles_by_family.setdefault(spec.family, []).append(
            Line2D(
                [0],
                [0],
                marker=spec.marker,
                color="none",
                markerfacecolor=spec.color,
                markeredgecolor="none",
                markersize=8,
                label=spec.label,
            )
        )
    ordered_handles = [h for f in family_order for h in handles_by_family.get(f, [])]
    ax.legend(
        handles=ordered_handles,
        loc="upper left",
        frameon=False,
        handletextpad=0.5,
        labelspacing=0.3,
    )


def plot_accuracy_bars(ax, summaries: list[ModelSummary], tasks: list[str]) -> None:
    """Per-model bar groups: 10 narrow bars per model, one per task, colored by paper."""
    if not tasks:
        raise ValueError("no tasks to plot")

    paper_rank = {paper: i for i, (paper, _) in enumerate(PAPER_PALETTE)}
    tasks = sorted(tasks, key=lambda t: (paper_rank.get(paper_of(t), 99), t))

    n_models = len(summaries)
    n_tasks = len(tasks)
    bw = 0.105  # 50 % wider bars (was 0.07)
    group_span = bw * n_tasks + 0.075
    x_centers = np.arange(n_models) * (group_span * 1.35)
    offsets = (np.arange(n_tasks) - (n_tasks - 1) / 2.0) * bw

    for ti, task_id in enumerate(tasks):
        ys = []
        for summary in summaries:
            v = summary.task_values.get(task_id)
            ys.append(np.nan if v is None else float(v))
        ax.bar(
            x_centers + offsets[ti],
            ys,
            width=bw,
            color=PAPER_COLOR.get(paper_of(task_id), "gray"),
            edgecolor="none",
            linewidth=0,
        )

    ax.set_xticks(x_centers)
    ax.set_xticklabels([summary.spec.label for summary in summaries])

    finite_vals = [
        v for summary in summaries for v in summary.task_values.values() if v is not None and v > 0
    ]
    floor = (min(finite_vals) * 0.5) if finite_vals else 1e-3
    ax.set_yscale("log")
    ax.set_ylim(bottom=floor)
    ax.set_ylabel(r"$d_{L^2}$")
    ax.grid(True, which="major", axis="y", linewidth=0.55, color="#D8DDE3")
    ax.set_axisbelow(True)
    ax.margins(x=0.01)
    # Match panel 1's height: panel 1 has box_aspect=1 in a 1-wide slot;
    # panel 2 is in a 1.85-wide slot, so its h/w must be 1/1.85 to match.
    ax.set_box_aspect(1.0 / 1.85)

    handles = [
        Patch(facecolor=color, edgecolor="none", label=paper) for paper, color in PAPER_PALETTE
    ]
    ax.legend(
        handles=handles,
        loc="upper right",
        frameon=True,
        framealpha=0.92,
        edgecolor="#888888",
        handlelength=1.1,
        handletextpad=0.5,
        labelspacing=0.3,
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2_norm.json",
        help="Best-run d_L2 data file.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "combined_pareto_bymodel.png",
    )
    parser.add_argument("--tau", type=float, default=0.3)
    parser.add_argument("--task-contains", default="sim")
    parser.add_argument(
        "--xaxis",
        choices=("cost", "wall", "tokens"),
        default="cost",
        help="Pareto resource axis. Values are summed over the selected best runs in the data file.",
    )
    parser.add_argument("--figwidth", type=float, default=16.0)
    parser.add_argument("--figheight", type=float, default=6.4)
    args = parser.parse_args()

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}")

    data = json.loads(args.data.read_text())
    summaries = collect_summaries(
        data,
        tau=args.tau,
        task_contains=args.task_contains or None,
        xaxis=args.xaxis,
    )
    if not summaries:
        raise SystemExit("no model data after filtering")
    tasks = sorted_tasks(summaries)

    sns.set_theme(style="whitegrid", context="paper")
    # Fonts +40 % over the previous spec.
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.facecolor": "#F7F8FA",
            "axes.edgecolor": "#333333",
            "axes.labelsize": 15.4,
            "xtick.labelsize": 13.65,
            "ytick.labelsize": 13.65,
            "legend.fontsize": 13.65,
            "axes.titlesize": 18.2,
            "font.family": "DejaVu Sans",
        }
    )

    # Use a single gridspec (not subfigures): with constrained_layout the slot
    # widths track `width_ratios` exactly, which is what we need for the
    # set_box_aspect() math below to land both axes at identical height.
    fig = plt.figure(figsize=(args.figwidth, args.figheight), constrained_layout=True)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.85], wspace=0.18)
    ax_pareto = fig.add_subplot(gs[0, 0])
    ax_bars = fig.add_subplot(gs[0, 1])

    plot_pareto(ax_pareto, summaries, tau=args.tau, xaxis=args.xaxis)
    plot_accuracy_bars(ax_bars, summaries, tasks)

    fig.savefig(args.out, dpi=220)
    print(f"wrote {args.out}  ({len(summaries)} models, {len(tasks)} tasks)")


if __name__ == "__main__":
    main()
