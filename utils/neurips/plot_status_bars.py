#!/usr/bin/env python3
"""Stacked 100 %-bar of replicate-status fractions per model.

For each of the six models we read every replicate's `status` field from
data/runs.json and stack them into a single full-height bar. Categories:

  * pass  — proper run                                    (green, bottom)
  * wall  — hit the time wall before finishing            (fuchsia)
  * hung  — agent went idle after working for ≥ 5 min     (amber)
  * cheat — agent fabricated values to fill results.yaml  (red, top)

Bars run edge-to-edge (width=1.0, no border, no inter-bar gap). Y-axis is
0–100 % in 20 % ticks. Legend in upper-right inside the plot.

Usage:
    python -m utils.neurips.plot_status_bars
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator, PercentFormatter


@dataclass(frozen=True)
class ModelSpec:
    dirname: str
    label: str


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("claude_opus-4-7", "Opus 4.7"),
    ModelSpec("claude_sonnet-4-6", "Sonnet 4.6"),
    ModelSpec("claude_haiku-4-5", "Haiku 4.5"),
    ModelSpec("codex_gpt-5.5", "GPT-5.5"),
    ModelSpec("codex_gpt-5.4-mini", "GPT-5.4-mini"),
    ModelSpec("forge_deepseek-v4-pro", "DeepSeek-V4"),
)

# Stack order = bottom → top. pass at bottom, cheat on top.
STATUS_PALETTE: tuple[tuple[str, str], ...] = (
    ("pass", "#4DAF4A"),  # green
    ("wall", "#E6189F"),  # fuchsia
    ("hung", "#FF9E1F"),  # amber
    ("cheat", "#E41A1C"),  # red
)
STATUS_ORDER = [s for s, _ in STATUS_PALETTE]
STATUS_COLOR = dict(STATUS_PALETTE)

# Display labels (legend) — internal key "wall" → human-readable "time wall".
STATUS_DISPLAY: dict[str, str] = {
    "pass": "pass",
    "wall": "time wall",
    "hung": "hung",
    "cheat": "cheat",
}


def model_status_counts(model_block: dict) -> dict[str, int]:
    counts = {s: 0 for s in STATUS_ORDER}
    for task_entry in (model_block.get("tasks") or {}).values():
        for rep in task_entry.get("replicates") or []:
            s = rep.get("status", "pass")
            if s not in counts:
                counts[s] = 0
            counts[s] += 1
    return counts


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "data" / "runs.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "figures" / "status_bars.png",
    )
    # figheight matches plot_combined.py so the plot-frame height is the same
    # across all NeurIPS figures. Width kept narrow (H:W ≈ 2:1) per request.
    ap.add_argument("--figwidth", type=float, default=3.5)
    ap.add_argument("--figheight", type=float, default=5.0)
    args = ap.parse_args()

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}")
    data = json.loads(args.data.read_text())
    models_block = data.get("models", {})

    # Theme + font sizes mirror plot_combined.py for consistent NeurIPS scaling.
    sns.set_theme(style="whitegrid", context="paper")
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.facecolor": "#E8EDF3",
            "axes.edgecolor": "#333333",
            "axes.labelsize": 21.56,
            "xtick.labelsize": 13.65,
            "ytick.labelsize": 13.65,
            "legend.fontsize": 13.65,
            "axes.titlesize": 18.2,
            "font.family": "serif",
            "font.serif": ["Computer Modern Roman", "DejaVu Serif", "Times"],
            "mathtext.fontset": "cm",
            "mathtext.rm": "serif",
            "axes.unicode_minus": False,
        }
    )

    # Collect per-model fractions, normalising each model's total to 100 %.
    labels: list[str] = []
    fractions_by_status: dict[str, list[float]] = {s: [] for s in STATUS_ORDER}
    for spec in MODEL_SPECS:
        block = models_block.get(spec.dirname) or {}
        counts = model_status_counts(block)
        total = sum(counts.values())
        labels.append(spec.label)
        for s in STATUS_ORDER:
            frac = (100.0 * counts[s] / total) if total > 0 else 0.0
            fractions_by_status[s].append(frac)
        # Console summary
        breakdown = "  ".join(f"{s}={counts[s]}" for s in STATUS_ORDER)
        print(f"  {spec.label:<14} total={total:>3}   {breakdown}")

    fig, ax = plt.subplots(figsize=(args.figwidth, args.figheight))
    x = np.arange(len(labels))
    bottoms = np.zeros(len(labels))
    for status in STATUS_ORDER:
        heights = np.asarray(fractions_by_status[status], dtype=float)
        ax.bar(
            x,
            heights,
            width=1.0,
            bottom=bottoms,
            color=STATUS_COLOR[status],
            edgecolor="none",
            linewidth=0,
            zorder=2,
        )
        bottoms += heights

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_xlim(x.min() - 0.5, x.max() + 0.5)  # bars touch each other and the axes
    # Small outward x-tick mark per model. Model labels 25 % smaller than the
    # default xtick font so the rotated names don't crowd at this narrow width.
    ax.tick_params(
        axis="x",
        which="major",
        direction="out",
        length=4.0,
        width=0.9,
        color="#333333",
        bottom=True,
        top=False,
        zorder=6,
        labelsize=plt.rcParams["xtick.labelsize"] * 0.75,
    )

    # Thin white separators between adjacent bars (drawn on top of the stacks).
    for boundary in x[:-1] + 0.5:
        ax.axvline(boundary, color="white", lw=1.4, zorder=5)

    ax.set_ylim(0, 100)
    ax.yaxis.set_major_locator(MultipleLocator(20))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_ylabel("run success rate")

    ax.grid(True, axis="y", which="major", linewidth=0.55, color="#D8DDE3", zorder=1)
    ax.grid(False, axis="x")
    ax.set_axisbelow(True)

    # Legend ordered top-of-stack first (cheat) so it visually mirrors the bar.
    handles = [
        Patch(facecolor=STATUS_COLOR[s], edgecolor="none", label=STATUS_DISPLAY[s])
        for s in reversed(STATUS_ORDER)
    ]
    leg = ax.legend(
        handles=handles,
        loc="lower right",
        frameon=True,
        edgecolor="#444444",
        fancybox=False,
        handletextpad=0.5,
        labelspacing=0.35,
        borderpad=0.5,
    )
    leg.get_frame().set_facecolor("white")
    leg.get_frame().set_alpha(0.9)
    leg.get_frame().set_linewidth(0.9)

    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=300)
    fig.savefig(args.out.with_suffix(".pdf"))
    print(f"\nwrote {args.out}")
    print(f"wrote {args.out.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
