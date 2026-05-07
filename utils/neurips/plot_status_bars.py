#!/usr/bin/env python3
"""Six pie charts (one per model) of replicate-status fractions.

For each of the six models we read every replicate's `status` field from
data/runs.json and render the per-status fractions as a pie. Categories:

  * pass  — proper run                                    (green)
  * wall  — hit the time wall before finishing            (fuchsia)
  * hung  — agent went idle after working for ≥ 5 min     (amber)
  * cheat — agent fabricated values to fill results.yaml  (red)

The six pies are laid out in a single horizontal row; each model's name
appears as the panel title. A shared legend sits below the row.

Usage:
    python -m utils.neurips.plot_status_bars
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.patches import Patch


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
        default=repo_root / "utils" / "neurips" / "figures" / "status_pies.png",
    )
    # figheight matches plot_combined.py so the plot-frame height is the same
    # across all NeurIPS figures. Width kept narrow (H:W ≈ 2:1) per request.
    ap.add_argument("--figwidth", type=float, default=14.0)
    ap.add_argument("--figheight", type=float, default=3.5)
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

    # Collect per-model raw counts in the same status order as the legend.
    per_model_counts: list[tuple[str, dict[str, int], int]] = []
    for spec in MODEL_SPECS:
        block = models_block.get(spec.dirname) or {}
        counts = model_status_counts(block)
        total = sum(counts.values())
        per_model_counts.append((spec.label, counts, total))
        breakdown = "  ".join(f"{s}={counts[s]}" for s in STATUS_ORDER)
        print(f"  {spec.label:<14} total={total:>3}   {breakdown}")

    # Six pies in a single horizontal row, all the same size. constrained_layout
    # keeps the row tight; we'll add the shared legend + caption via fig.text.
    fig, axes = plt.subplots(
        1,
        len(MODEL_SPECS),
        figsize=(args.figwidth, args.figheight),
        constrained_layout=True,
    )
    wedge_kwargs = {"edgecolor": "white", "linewidth": 1.5}
    for ax, (label, counts, _total) in zip(axes, per_model_counts, strict=False):
        # Drop zero-count slices to avoid degenerate wedges; preserve order.
        sizes = [counts[s] for s in STATUS_ORDER if counts[s] > 0]
        colors = [STATUS_COLOR[s] for s in STATUS_ORDER if counts[s] > 0]
        wedges, _texts, autotexts = ax.pie(
            sizes,
            colors=colors,
            startangle=90,
            counterclock=False,
            wedgeprops=wedge_kwargs,
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
            pctdistance=0.72,
        )
        for t in autotexts:
            t.set_color("white")
            t.set_fontsize(plt.rcParams["axes.labelsize"] * 0.55)
            t.set_fontweight("bold")
        # Title above the pie: model label only.
        ax.set_title(
            label,
            fontsize=plt.rcParams["axes.labelsize"] * 0.65,
            pad=6,
        )
        ax.set_aspect("equal")

    # Shared legend (one row, four entries) horizontally centered below pies.
    handles = [
        Patch(facecolor=STATUS_COLOR[s], edgecolor="none", label=STATUS_DISPLAY[s])
        for s in STATUS_ORDER
    ]
    leg = fig.legend(
        handles=handles,
        loc="lower center",
        ncol=len(STATUS_ORDER),
        frameon=True,
        edgecolor="#444444",
        fancybox=False,
        handletextpad=0.5,
        columnspacing=2.0,
        borderpad=0.5,
        bbox_to_anchor=(0.5, -0.02),
    )
    leg.get_frame().set_facecolor("white")
    leg.get_frame().set_alpha(0.9)
    leg.get_frame().set_linewidth(0.9)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=300, bbox_inches="tight")
    fig.savefig(args.out.with_suffix(".pdf"), bbox_inches="tight")
    print(f"\nwrote {args.out}")
    print(f"wrote {args.out.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
