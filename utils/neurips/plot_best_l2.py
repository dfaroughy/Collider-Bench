#!/usr/bin/env python3
"""Grouped bar plot of best L2-distance per (task, model).

Reads utils/neurips/best_l2.json (produced by fetch_best_l2.py) and renders
a NeurIPS-friendly wide-and-short bar plot:
  * x-axis: task IDs in paper order (051, 047, 046, 034 by default)
  * y-axis: best (lowest) shape.relative_l2 across replicates
  * 6 bars per task, one per model, fixed left-to-right color order:
        red, orange, yellow  →  Anthropic (Opus, Sonnet, Haiku)
        dark blue, cyan       →  OpenAI    (GPT-5.5, GPT-5.4-mini)
        purple                →  DeepSeek  (DeepSeek-V4)

Usage:
    python -m utils.neurips.plot_best_l2
    python -m utils.neurips.plot_best_l2 --out /tmp/x.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# (model_dir, display_label, color)  ordered left-to-right per group of bars
MODEL_PALETTE: list[tuple[str, str, str]] = [
    ("claude_opus-4-7", "Opus 4.7", "#d62728"),  # red
    ("claude_sonnet-4-6", "Sonnet 4.6", "#ff7f0e"),  # orange
    ("claude_haiku-4-5", "Haiku 4.5", "#fbbf24"),  # yellow
    ("codex_gpt-5.5", "GPT-5.5", "#1f3a8a"),  # dark blue
    ("codex_gpt-5.4-mini", "GPT-5.4-mini", "#22d3ee"),  # cyan
    ("forge_deepseek-v4-pro", "DeepSeek-V4", "#7c3aed"),  # purple
]

PAPER_ORDER_DEFAULT: list[str] = ["sus-16-051", "sus-16-047", "sus-16-046", "sus-16-034"]


def task_paper(tid: str, paper_order: list[str]) -> int:
    for i, p in enumerate(paper_order):
        if tid.startswith(p):
            return i
    return len(paper_order)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2_bar.png",
    )
    ap.add_argument("--paper-order", nargs="+", default=PAPER_ORDER_DEFAULT)
    ap.add_argument(
        "--task-contains",
        default=None,
        help="Keep only tasks whose ID contains this substring (e.g. 'sim').",
    )
    ap.add_argument(
        "--figwidth", type=float, default=10.0, help="Figure width in inches (default 10)."
    )
    ap.add_argument(
        "--figheight", type=float, default=3.0, help="Figure height in inches (default 3)."
    )
    ap.add_argument(
        "--bar-width",
        type=float,
        default=0.12,
        help="Width of each bar (data units of one task slot).",
    )
    ap.add_argument(
        "--linear", action="store_true", help="Use linear y-axis instead of the default log."
    )
    args = ap.parse_args()

    if not args.data.is_file():
        raise SystemExit(
            f"missing data file: {args.data}\nrun: python -m utils.neurips.fetch_best_l2"
        )

    data = json.loads(args.data.read_text())
    metric = data.get("metric", "shape")
    models_block = data["models"]

    # Union of tasks seen across every model
    all_tasks: set[str] = set()
    for m, _, _ in MODEL_PALETTE:
        block = models_block.get(m) or {}
        all_tasks.update((block.get("tasks") or {}).keys())

    if args.task_contains:
        all_tasks = {t for t in all_tasks if args.task_contains in t}
    tasks = sorted(
        all_tasks,
        key=lambda t: (task_paper(t, args.paper_order), t),
    )
    if not tasks:
        raise SystemExit("no tasks left after filtering")

    n_tasks = len(tasks)
    n_models = len(MODEL_PALETTE)
    bw = args.bar_width
    group_span = bw * n_models + 0.05  # tiny gap between groups
    x = np.arange(n_tasks) * group_span * 1.4
    offsets = (np.arange(n_models) - (n_models - 1) / 2.0) * bw

    fig, ax = plt.subplots(figsize=(args.figwidth, args.figheight))

    for idx, (mdir, label, color) in enumerate(MODEL_PALETTE):
        block = models_block.get(mdir) or {}
        tdict = block.get("tasks") or {}
        ys = [tdict.get(t, {}).get("best") for t in tasks]
        # Plot only finite values; matplotlib handles NaN by leaving a gap.
        ys_arr = np.array([np.nan if v is None else float(v) for v in ys])
        ax.bar(
            x + offsets[idx],
            ys_arr,
            width=bw,
            color=color,
            edgecolor="black",
            linewidth=0.3,
            label=label,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(tasks, rotation=35, ha="right", fontsize=7)
    ax.set_ylabel(r"$d_{L^2}$" + f"  ({metric}.relative_l2)", fontsize=9)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.margins(x=0.005)

    if not args.linear:
        # Floor the y-axis just below the smallest positive value so every
        # bar sits visibly above the bottom edge on log scale.
        finite_vals: list[float] = []
        for m, _, _ in MODEL_PALETTE:
            tasks_dict = (models_block.get(m) or {}).get("tasks") or {}
            for entry in tasks_dict.values():
                v = entry.get("best")
                if v is not None and v > 0:
                    finite_vals.append(float(v))
        floor = (min(finite_vals) * 0.5) if finite_vals else 1e-3
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor)

    # Legend below x-axis or above? For wide-short layout, top-right inside.
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.005, 1.0),
        fontsize=7,
        frameon=False,
        handlelength=1.0,
        handletextpad=0.4,
    )

    fig.tight_layout()
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"wrote {args.out}  ({n_tasks} tasks × {n_models} models)")


if __name__ == "__main__":
    main()
