#!/usr/bin/env python3
"""Inverse layout of plot_best_l2.py: x-axis = models, bars-per-group = tasks.

For each of the 6 models we draw one bar per task (10 sim tasks by default,
or whatever the data file contains). Bars are color-coded by paper number,
not by task — task labels are intentionally omitted because the same task
appears as a bar in every model group anyway.

Usage:
    python -m utils.neurips.plot_best_l2_by_model
    python -m utils.neurips.plot_best_l2_by_model --task-contains shape
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# Same model order / labels as plot_best_l2.py.
MODEL_ORDER: list[tuple[str, str]] = [
    ("claude_opus-4-7", "Opus 4.7"),
    ("claude_sonnet-4-6", "Sonnet 4.6"),
    ("claude_haiku-4-5", "Haiku 4.5"),
    ("codex_gpt-5.5", "GPT-5.5"),
    ("codex_gpt-5.4-mini", "GPT-5.4-mini"),
    ("forge_deepseek-v4-pro", "DeepSeek-V4"),
]

# One color per paper. Order also defines paper-rank for sorting tasks in
# the within-group bar order.
PAPER_PALETTE: list[tuple[str, str]] = [
    ("sus-16-051", "#1f77b4"),  # blue
    ("sus-16-047", "#2ca02c"),  # green
    ("sus-16-046", "#ff7f0e"),  # orange
    ("sus-16-034", "#d62728"),  # red
]
PAPER_COLOR = dict(PAPER_PALETTE)


def paper_of(tid: str) -> str:
    for p, _ in PAPER_PALETTE:
        if tid.startswith(p):
            return p
    return ""


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2_norm.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2_norm_sim_by_model.png",
    )
    ap.add_argument(
        "--task-contains", default=None, help="Keep only tasks whose ID contains this substring."
    )
    ap.add_argument("--figwidth", type=float, default=10.0)
    ap.add_argument("--figheight", type=float, default=3.0)
    ap.add_argument("--bar-width", type=float, default=0.07)
    ap.add_argument(
        "--linear", action="store_true", help="Linear y-axis instead of the default log."
    )
    args = ap.parse_args()

    if not args.data.is_file():
        raise SystemExit(
            f"missing data file: {args.data}\n"
            "Run, e.g.:  python -m utils.neurips.fetch_best_l2 --metric normalization "
            "--out utils/neurips/best_l2_norm.json"
        )

    data = json.loads(args.data.read_text())
    metric = data.get("metric", "shape")
    models_block = data["models"]

    # Union of tasks across all models, optionally filtered.
    all_tasks: set[str] = set()
    for mdir, _ in MODEL_ORDER:
        all_tasks.update((models_block.get(mdir, {}).get("tasks") or {}).keys())
    if args.task_contains:
        all_tasks = {t for t in all_tasks if args.task_contains in t}

    paper_rank = {p: i for i, (p, _) in enumerate(PAPER_PALETTE)}
    tasks = sorted(all_tasks, key=lambda t: (paper_rank.get(paper_of(t), 99), t))
    if not tasks:
        raise SystemExit("no tasks left after filtering")

    n_models = len(MODEL_ORDER)
    n_tasks = len(tasks)
    bw = args.bar_width
    group_span = bw * n_tasks + 0.05
    x_centers = np.arange(n_models) * (group_span * 1.4)
    offsets = (np.arange(n_tasks) - (n_tasks - 1) / 2.0) * bw

    fig, ax = plt.subplots(figsize=(args.figwidth, args.figheight))

    for ti, tid in enumerate(tasks):
        ys = []
        for mdir, _ in MODEL_ORDER:
            entry = (models_block.get(mdir, {}).get("tasks") or {}).get(tid, {})
            v = entry.get("best")
            ys.append(np.nan if v is None else float(v))
        ax.bar(
            x_centers + offsets[ti],
            ys,
            width=bw,
            color=PAPER_COLOR.get(paper_of(tid), "gray"),
            edgecolor="black",
            linewidth=0.3,
        )

    ax.set_xticks(x_centers)
    ax.set_xticklabels([label for _, label in MODEL_ORDER], fontsize=8)
    ax.set_ylabel(r"$d_{L^2}$" + f"  ({metric}.relative_l2)", fontsize=9)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.margins(x=0.01)

    if not args.linear:
        finite_vals = [
            float(v)
            for mdir, _ in MODEL_ORDER
            for entry in (models_block.get(mdir, {}).get("tasks") or {}).values()
            for v in [entry.get("best")]
            if v is not None and v > 0
        ]
        floor = (min(finite_vals) * 0.5) if finite_vals else 1e-3
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor)

    # Legend by paper, not by task.
    handles = [plt.Rectangle((0, 0), 1, 1, color=c, ec="black") for _, c in PAPER_PALETTE]
    legend_labels = [p for p, _ in PAPER_PALETTE]
    ax.legend(
        handles,
        legend_labels,
        loc="upper left",
        bbox_to_anchor=(1.005, 1.0),
        fontsize=7,
        frameon=False,
        handlelength=1.0,
        handletextpad=0.4,
    )

    fig.tight_layout()
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"wrote {args.out}  ({n_models} models × {n_tasks} tasks)")


if __name__ == "__main__":
    main()
