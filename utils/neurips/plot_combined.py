#!/usr/bin/env python3
"""Two-panel composite figure for the NeurIPS draft.

Left: model Pareto plot, accuracy at threshold tau versus total resource.
Right: per-model grouped task accuracy bars using the same threshold tau.

Usage:
    python -m utils.neurips.plot_combined
    python -m utils.neurips.plot_combined --tau 0.25 --data utils/neurips/data/runs.json
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
from matplotlib.legend_handler import HandlerPatch
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import (
    FixedLocator,
    FuncFormatter,
    LogLocator,
    MultipleLocator,
    NullLocator,
    PercentFormatter,
)


class _BigPatchHandler(HandlerPatch):
    """Custom legend handler that draws a Patch swatch at an explicit size in points."""

    def __init__(self, *, width_pt: float, height_pt: float):
        super().__init__()
        self.width_pt = width_pt
        self.height_pt = height_pt

    def create_artists(
        self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans
    ):
        # Override matplotlib's computed swatch box with our explicit pt sizes,
        # converted to pixels via the legend's renderer dpi.
        dpi = legend.figure.dpi
        w = self.width_pt * dpi / 72.0
        h = self.height_pt * dpi / 72.0
        # center the swatch within the slot matplotlib reserved for it
        x0 = -xdescent + (width - w) / 2.0
        y0 = -ydescent + (height - h) / 2.0
        rect = Rectangle(
            (x0, y0),
            w,
            h,
            facecolor=orig_handle.get_facecolor(),
            edgecolor=orig_handle.get_edgecolor(),
            linewidth=orig_handle.get_linewidth(),
            transform=trans,
        )
        return [rect]


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
    ModelSpec("forge_deepseek-v4-pro", " DeepSeek-V4", "#6D4BC3", "s", "DeepSeek"),
    ModelSpec("forge_deepseek-r1", " DeepSeek-R1", "#A88EE0", "s", "DeepSeek"),
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


def _replicate_resource(rep: dict[str, Any], xaxis: str) -> float:
    if xaxis == "cost":
        return float(rep.get("cost_usd") or 0.0)
    if xaxis == "tokens":
        return float(rep.get("tokens_total_billed") or 0.0)
    if xaxis == "wall":
        return float(rep.get("wall_s") or 0.0) / 3600.0
    raise ValueError(f"unknown x-axis {xaxis!r}")


def _replicate_l2(rep: dict[str, Any], metric: str, *, use_delta: bool = False) -> float | None:
    """Pull the right d_L^2 field.

    metric == 'norm' (default): rep['relative_l2_norm']  (sim-task normalization)
    metric == 'shape':           rep['relative_l2_shape'] (shape distribution L^2)
    use_delta=True overrides the 'norm' branch and returns rep['delta'] instead
    of relative_l2_norm — useful for the --delta_norm CLI flag.
    """
    if metric == "norm":
        v = rep.get("delta") if use_delta else rep.get("relative_l2_norm")
    elif metric == "shape":
        v = rep.get("relative_l2_shape")
    else:
        raise ValueError(f"unknown metric {metric!r}")
    return None if v is None else float(v)


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
    metric: str,
    use_delta: bool = False,
) -> list[ModelSummary]:
    """Aggregate replicates → per-(model, task) means → per-model accuracy + cost.

    `metric` selects which `relative_l2_*` field is the d_L^2 in the bars
    panel and the threshold check (`norm` for sim tasks, `shape` for shape
    tasks). Cost is summed over every replicate of every task that
    contributed (i.e. total spend, not per-replicate average).
    """
    # The DeepSeek experiments ran at a promotional discount; per-model gross-up
    # factors below restore an approximate list-price USD for the Pareto x-axis.
    # V4: ~75 % off across input/output → factor 4.
    # R1: same percentage discount, but R1's list price is ~2× V4's (input
    #     $0.55 vs $0.27, output $2.19 vs $1.10), so factor ≈ 2 × 4 = 8.
    DEEPSEEK_COST_FACTORS = {
        "forge_deepseek-v4-pro": 4.0,
        "forge_deepseek-r1": 8.0,
    }

    summaries: list[ModelSummary] = []
    for spec in MODEL_SPECS:
        entries = _task_entries(data, spec, task_contains=task_contains)
        task_values: dict[str, float] = {}
        task_accuracy: dict[str, float] = {}
        resource = 0.0
        for task_id, entry in entries.items():
            replicates = entry.get("replicates") or []
            l2_vals: list[float] = []
            for rep in replicates:
                # Resource (cost / wall / tokens) is summed over EVERY replicate
                # regardless of status — the agent still spent that money/time
                # even on cheat/hung runs, and the Pareto x-axis is meant to
                # reflect total spend.
                resource += _replicate_resource(rep, xaxis)
                # The d_L^2 (accuracy) metric drops only "cheat" replicates —
                # those fabricated outputs and would bias the mean. "hung" and
                # "wall" runs often produced legitimate (if incomplete) numbers
                # before halting, so they're kept.
                if rep.get("status", "pass") == "cheat":
                    continue
                v = _replicate_l2(rep, metric, use_delta=use_delta)
                if v is not None:
                    l2_vals.append(v)
            if not l2_vals:
                continue
            mean_l2 = sum(l2_vals) / len(l2_vals)
            task_values[task_id] = mean_l2
            task_accuracy[task_id] = 100.0 if mean_l2 < tau else 0.0
        if xaxis == "cost" and spec.dirname in DEEPSEEK_COST_FACTORS:
            resource *= DEEPSEEK_COST_FACTORS[spec.dirname]
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
        if xaxis == "cost":
            ax.xaxis.set_major_locator(FixedLocator([1, 5, 10, 25, 50, 100, 200, 400]))
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

    ax.set_ylim(-5, 50)
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
    ax.set_anchor("W")  # Pareto now on the right; hug the left of its slot (next to bars)

    # Panel caption "(b)" below the resource axis label.
    ax.text(
        0.5,
        -0.18,
        "(b)",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=plt.rcParams["axes.labelsize"],
    )

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
    leg = ax.legend(
        handles=ordered_handles,
        loc="upper left",
        fontsize=plt.rcParams["legend.fontsize"] * 1.2,  # a bit larger
        frameon=True,
        edgecolor="#444444",
        fancybox=False,  # square corners
        handletextpad=0.5,
        labelspacing=0.35,
        borderpad=0.5,
    )
    # Whiter, slightly translucent frame so the panel backdrop bleeds through.
    leg.get_frame().set_facecolor("white")
    leg.get_frame().set_alpha(0.78)
    leg.get_frame().set_linewidth(0.9)


def plot_accuracy_bars(ax, summaries: list[ModelSummary], tasks: list[str], *, tau: float) -> None:
    """Per-model bar groups: 10 narrow bars per model, one per task.

    Bar color = viridis(task_index / (n_tasks-1)). Legend lists every task.
    """
    if not tasks:
        raise ValueError("no tasks to plot")

    paper_rank = {paper: i for i, (paper, _) in enumerate(PAPER_PALETTE)}
    tasks = sorted(tasks, key=lambda t: (paper_rank.get(paper_of(t), 99), t))

    n_models = len(summaries)
    n_tasks = len(tasks)
    bw = 0.1365  # +30 % over previous 0.105
    group_span = bw * n_tasks + 0.075
    x_centers = np.arange(n_models) * (group_span * 1.35)
    offsets = (np.arange(n_tasks) - (n_tasks - 1) / 2.0) * bw

    cmap = plt.get_cmap("viridis")
    task_colors = [cmap(0.0 if n_tasks == 1 else ti / (n_tasks - 1)) for ti in range(n_tasks)]

    for ti, task_id in enumerate(tasks):
        ys = []
        for summary in summaries:
            v = summary.task_values.get(task_id)
            ys.append(np.nan if v is None else float(v))
        ax.bar(
            x_centers + offsets[ti],
            ys,
            width=bw,
            color=task_colors[ti],
            edgecolor="none",
            linewidth=0,
        )

    ax.set_xticks(x_centers)

    # Two-line tick labels: model name on top, version below.
    # Splits on the first space, falling back to the first hyphen for
    # hyphenated model names like "GPT-5.5" or "DeepSeek-V4". Already-multi-line
    # labels (e.g. the Human-in-loop column) are left alone.
    def _two_line(label: str) -> str:
        if "\n" in label:
            return label
        s = label.strip()
        if " " in s:
            head, _, tail = s.partition(" ")
            return f"{head}\n{tail}"
        if "-" in s:
            head, _, tail = s.partition("-")
            return f"{head}\n{tail}"
        return label

    ax.set_xticklabels([_two_line(summary.spec.label) for summary in summaries])
    # Model tick labels (panel 2 x-axis): +25 % over rcParams xtick.labelsize.
    ax.tick_params(axis="x", labelsize=plt.rcParams["xtick.labelsize"] * 1.35)

    # Linear y-axis: 0 floor, fixed top to keep the d=tau line and the Human
    # baseline in a comparable visual range across runs.
    ax.set_ylim(bottom=0.0, top=2.0)
    ax.set_ylabel(r"$\langle\, d(\hat y, y^\star)\, \rangle$")

    # Panel-1 title in small caps. matplotlib mathtext doesn't honor \textsc,
    # so we use a FontProperties with variant="small-caps" — Computer Modern
    # serif has a true small-caps face that this picks up.
    from matplotlib.font_manager import FontProperties

    title_fp = FontProperties(
        family="serif",
        variant="small-caps",
        size=plt.rcParams["axes.titlesize"],
    )
    ax.set_title("Collider-Bench", fontproperties=title_fp, pad=8)
    ax.grid(True, which="major", axis="y", linewidth=0.55, color="#D8DDE3")
    ax.set_axisbelow(True)
    ax.margins(x=0.01)

    # In-panel annotation: "{Shape} Task" with Shape in typewriter via mathtext.
    # ax.text(
    #     0.6, 0.95, r"$\mathtt{Shape}$ Task",
    #     transform=ax.transAxes,
    #     ha="left", va="top",
    #     fontsize=plt.rcParams["axes.labelsize"] * 0.85,
    #     zorder=6,
    # )

    # Reference threshold line at d_L2 = 0.3 (matches the Pareto tau on the
    # right panel). Drawn on top of the grid but below the bars.
    # Reference threshold line at d = tau (tracks the Pareto threshold on the
    # right panel automatically, no risk of drift).
    ax.axhline(y=tau, color="#C44536", ls="--", lw=1.0, alpha=0.9, zorder=2.5)
    # Sit the label between the first two model groups, then nudge slightly
    # left so it's visually closer to the Human-in-loop column.
    label_x = (
        0.5 * (x_centers[0] + x_centers[1]) - 0.10 * (x_centers[1] - x_centers[0])
        if len(x_centers) >= 2
        else x_centers[0]
    )
    ax.text(
        label_x,
        tau * 1.10,
        rf"$d={tau:g}$",
        color="#C44536",
        fontsize=11,
        va="bottom",
        ha="center",
    )
    # Panel caption "(a)" below the model x-tick labels.
    ax.text(
        0.5,
        -0.18,
        "(a)",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=plt.rcParams["axes.labelsize"],
    )
    # No set_box_aspect: panel 2 fills its full grid slot. Both axes share the
    # same gridspec row, so their slot height is identical → panel 2's drawing
    # area height equals panel 1's square edge automatically.
    ax.set_anchor("E")  # bars now on the left; hug the right of their slot (against Pareto)

    handles = [
        Patch(facecolor=task_colors[ti], edgecolor="none", label=tasks[ti]) for ti in range(n_tasks)
    ]
    # 5-row × 2-column legend; monospace ('\tt') task labels.
    # Custom Patch handler forces the swatch to a guaranteed size in points,
    # bypassing the handlelength/handleheight font-size dance that didn't take
    # in the previous attempt.
    big_handler = _BigPatchHandler(width_pt=22.0, height_pt=14.0)
    leg2 = ax.legend(
        handles=handles,
        loc="upper left",
        ncol=2,
        frameon=True,
        edgecolor="#888888",
        fontsize=13,
        handlelength=2.0,
        handleheight=1.4,
        handletextpad=0.5,
        labelspacing=0.35,
        columnspacing=1.0,
        prop={"family": "monospace", "size": 13},
        handler_map={Patch: big_handler},
    )
    # Whiter, slightly translucent frame so bars behind the legend show through.
    leg2.get_frame().set_facecolor("white")
    leg2.get_frame().set_alpha(0.78)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "data" / "runs.json",
        help="Unified replicate-level data file from fetch_runs.py.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "figures" / "combined_pareto_bymodel.png",
    )
    parser.add_argument("--tau", type=float, default=0.33)
    parser.add_argument("--task-contains", default="sim")
    parser.add_argument(
        "--metric",
        choices=("norm", "shape"),
        default="norm",
        help="Which relative_l2 to use as d_L^2 (norm = normalization for sim tasks, "
        "shape = shape distribution L2). Default norm matches task-contains=sim.",
    )
    parser.add_argument(
        "--xaxis",
        choices=("cost", "wall", "tokens"),
        default="cost",
        help="Pareto resource axis. Values are summed over every replicate of every selected task.",
    )
    parser.add_argument(
        "--shape",
        action="store_true",
        help="Plot the shape tasks (overrides --task-contains and --metric to "
        "'shape'). Default is sim tasks with norm metric.",
    )
    parser.add_argument(
        "--delta_norm",
        action="store_true",
        help="Use rep['delta'] instead of rep['relative_l2_norm'] as d_L^2. "
        "Only meaningful for the norm metric (sim tasks).",
    )
    parser.add_argument("--figwidth", type=float, default=19.2)
    parser.add_argument("--figheight", type=float, default=5.0)
    args = parser.parse_args()

    # --shape forces sim → shape filter and norm → shape metric so users can
    # flip the whole figure with one flag.
    if args.shape:
        # Keep sim tasks; only swap the d_L^2 metric to the shape distribution L^2.
        # Sim runs carry both `relative_l2_norm` and `relative_l2_shape`, so we
        # stay on the same task corpus and just look at the other field.
        args.metric = "shape"
        if args.delta_norm:
            raise SystemExit(
                "--delta_norm has no meaning with --shape (delta is paired with relative_l2_norm)."
            )

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}\nrun: python -m utils.neurips.fetch_runs")

    data = json.loads(args.data.read_text())
    summaries = collect_summaries(
        data,
        tau=args.tau,
        task_contains=args.task_contains or None,
        xaxis=args.xaxis,
        metric=args.metric,
        use_delta=args.delta_norm,
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
            "axes.facecolor": "#E8EDF3",  # lighter cool gray-blue panel face
            "axes.edgecolor": "#333333",
            "axes.labelsize": 21.56,
            "xtick.labelsize": 13.65,
            "ytick.labelsize": 13.65,
            "legend.fontsize": 13.65,
            "axes.titlesize": 18.2,
            # LaTeX-style typography: Computer Modern mathtext + serif body.
            "font.family": "serif",
            "font.serif": ["Computer Modern Roman", "DejaVu Serif", "Times"],
            "mathtext.fontset": "cm",  # Computer Modern math
            "mathtext.rm": "serif",
            "axes.unicode_minus": False,
        }
    )

    # Use a single gridspec (not subfigures): with constrained_layout the slot
    # widths track `width_ratios` exactly, which is what we need for the
    # set_box_aspect() math below to land both axes at identical height.
    fig = plt.figure(figsize=(args.figwidth, args.figheight), constrained_layout=True)
    # Panels swapped: bars on the left (wide slot), Pareto on the right (square slot).
    gs = fig.add_gridspec(1, 2, width_ratios=[3.0, 1.0], wspace=0.04)
    ax_bars = fig.add_subplot(gs[0, 0])
    ax_pareto = fig.add_subplot(gs[0, 1])

    plot_pareto(ax_pareto, summaries, tau=args.tau, xaxis=args.xaxis)

    # Bars panel: reorder models per the figure spec and prepend a synthetic
    # "Human+Opus 4.7" reference column whose every task value sits exactly on
    # the d=tau dashed line. The Pareto panel keeps the canonical model order.
    BAR_ORDER = (
        "codex_gpt-5.5",
        "claude_opus-4-7",
        "claude_sonnet-4-6",
        "codex_gpt-5.4-mini",
        "forge_deepseek-v4-pro",
        "forge_deepseek-r1",
        "claude_haiku-4-5",
    )
    by_dirname = {s.spec.dirname: s for s in summaries}
    ordered_summaries = [by_dirname[d] for d in BAR_ORDER if d in by_dirname]
    human_spec = ModelSpec(
        dirname="human_opus-4-7",
        # Two-line tick label, centered (matplotlib centers each line by default).
        label="Human-in-loop\n(opus 4.7)",
        color="#5A5A5A",
        marker="X",
        family="Human",
    )
    # Real measured Human-in-loop d_rel_l2 per task; tasks not listed default
    # to the dashed-line tau value (placeholder until the rest are measured).
    HUMAN_SCORES: dict[str, float] = {
        "sus-16-046_sim-TChiWg": 0.036,
        "sus-16-046_sim-T5Wg": 0.048,
        "sus-16-047_sim-T5Wg_highHT": 0.236,
        "sus-16-047_sim-T5Wg_lowHT": 0.331,
        "sus-16-047_sim-T6gg_highHT": 0.12997,
        "sus-16-047_sim-T6gg_lowHT": 0.055060,
        "sus-16-034_sim-TChiWZ": 0.08526,
        "sus-16-051_sim-T2bW_SRG": 0.244654,
        "sus-16-051_sim-T2tt_comp": 0.290484,
        "sus-16-051_sim-T2tt_SRG": 0.089739,
    }
    # Shape-d human-in-loop values (used when --shape is set). Keys match the
    # sim task IDs because --shape keeps task_contains="sim"; only the metric
    # flips from relative_l2_norm → relative_l2_shape.
    HUMAN_SCORES_SHAPE: dict[str, float] = {
        "sus-16-046_sim-T5Wg": 0.0193,
        "sus-16-046_sim-TChiWg": 0.0331,
        "sus-16-047_sim-T5Wg_highHT": 0.0492,
        "sus-16-047_sim-T5Wg_lowHT": 0.2449,
        "sus-16-047_sim-T6gg_highHT": 0.02594,
        "sus-16-047_sim-T6gg_lowHT": 0.01692,
        "sus-16-034_sim-TChiWZ": 0.073748,
        "sus-16-051_sim-T2tt_comp": 0.150134,
        "sus-16-051_sim-T2tt_SRG": 0.091991,
        "sus-16-051_sim-T2bW_SRG": 0.186758,
    }
    scores_table = HUMAN_SCORES_SHAPE if args.shape else HUMAN_SCORES
    human_task_values = {t: scores_table.get(t, args.tau) for t in tasks}
    human_summary = ModelSummary(
        spec=human_spec,
        task_values=human_task_values,
        task_accuracy={t: 100.0 if v < args.tau else 0.0 for t, v in human_task_values.items()},
        accuracy=100.0
        * sum(v < args.tau for v in human_task_values.values())
        / max(len(human_task_values), 1),
        n_pass=sum(v < args.tau for v in human_task_values.values()),
        n_tasks=len(human_task_values),
        resource=0.0,
    )
    bar_summaries = [human_summary] + ordered_summaries
    plot_accuracy_bars(ax_bars, bar_summaries, tasks, tau=args.tau)

    # Save the requested output (PNG by default) plus a vector PDF alongside it
    # for NeurIPS — PDF is resolution-independent so the paper compile picks the
    # cleanest version regardless of zoom level.
    out_png = args.out
    fig.savefig(out_png, dpi=300)
    out_pdf = out_png.with_suffix(".pdf")
    fig.savefig(out_pdf)  # vector — DPI is irrelevant for PDF axes/text
    print(
        f"wrote {out_png} (PNG, dpi=300)\n"
        f"wrote {out_pdf} (PDF, vector)\n"
        f"  ({len(summaries)} models, {len(tasks)} tasks)"
    )


if __name__ == "__main__":
    main()
