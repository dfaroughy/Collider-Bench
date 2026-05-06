#!/usr/bin/env python3
"""Bar plot of mean ± 1σ relative_l2 per task, across replicate runs.

x-axis: task IDs ordered by paper (051, 047, 046, 034 by default).
y-axis: mean over <vendor>/run-N/ replicates of `shape.relative_l2`.
Error bar: 1σ standard deviation across replicates.

Usage:
    python -m utils.relative_l2_bar
    python -m utils.relative_l2_bar --runs-root /global/cfs/cdirs/m4539/ColliderBench --vendor claude_opus-4-7
    python -m utils.relative_l2_bar --metric normalization
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


# Paper-number prefix in the task_id determines color.
PAPER_ORDER_DEFAULT: list[str] = ["sus-16-051", "sus-16-047", "sus-16-046", "sus-16-034"]
PAPER_COLORS: dict[str, str] = {
    "sus-16-051": "#1f77b4",
    "sus-16-047": "#2ca02c",
    "sus-16-046": "#ff7f0e",
    "sus-16-034": "#d62728",
}


def collect(vendor_root: Path, metric_block: str) -> dict[str, list[float]]:
    """task_id → [relative_l2 across replicates]."""
    out: dict[str, list[float]] = defaultdict(list)
    for sj in vendor_root.rglob("eval/score.json"):
        try:
            s = json.loads(sj.read_text())
        except Exception:
            continue
        tid = s.get("task_id")
        if not tid:
            continue
        block = s.get(metric_block) or {}
        v = block.get("relative_l2")
        if v is None:
            continue
        out[tid].append(float(v))
    return out


def paper_of(task_id: str, paper_order: list[str]) -> str:
    for p in paper_order:
        if task_id.startswith(p):
            return p
    return ""


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--runs-root",
        type=Path,
        default=Path("/global/cfs/cdirs/m4539/ColliderBench"),
        help="Directory containing <vendor>/run-N/ subfolders.",
    )
    ap.add_argument("--vendor", default="claude_opus-4-7")
    ap.add_argument("--metric", choices=("shape", "normalization"), default="shape")
    ap.add_argument(
        "--paper-order",
        nargs="+",
        default=PAPER_ORDER_DEFAULT,
        help="Task-prefix ordering for the x-axis (default: 051 047 046 034).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "relative_l2_bar.png",
    )
    args = ap.parse_args()

    vendor_root = args.runs_root / args.vendor
    if not vendor_root.is_dir():
        raise SystemExit(f"vendor root not found: {vendor_root}")

    samples = collect(vendor_root, args.metric)
    if not samples:
        raise SystemExit(f"no '{args.metric}.relative_l2' values found under {vendor_root}")

    # Order: by paper-prefix order, then alphabetical within paper.
    rows = sorted(
        samples.items(),
        key=lambda kv: (
            args.paper_order.index(paper_of(kv[0], args.paper_order))
            if paper_of(kv[0], args.paper_order) in args.paper_order
            else len(args.paper_order),
            kv[0],
        ),
    )
    labels = [tid for tid, _ in rows]
    means = np.array([np.mean(v) for _, v in rows])
    stds = np.array([np.std(v, ddof=1) if len(v) > 1 else 0.0 for _, v in rows])
    counts = [len(v) for _, v in rows]
    colors = [PAPER_COLORS.get(paper_of(tid, args.paper_order), "gray") for tid in labels]

    fig, ax = plt.subplots(figsize=(max(10, 0.45 * len(labels) + 4), 6))
    x = np.arange(len(labels))
    ax.bar(
        x,
        means,
        yerr=stds,
        color=colors,
        edgecolor="black",
        linewidth=0.5,
        capsize=3,
        error_kw={"elinewidth": 1, "alpha": 0.8},
    )

    # n=K annotation above each bar
    for xi, m, s, n in zip(x, means, stds, counts, strict=False):
        ax.text(
            xi,
            m + s + 0.02 * means.max(),
            f"n={n}",
            ha="center",
            va="bottom",
            fontsize=7,
            color="#444",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(
        rf"$\langle d_{{L_2}}^\mathrm{{rel}} \rangle$  ({args.metric}.relative_l2, mean ± 1σ)"
    )
    ax.set_title(f"{args.vendor}: {args.metric}.relative_l2 per task")
    ax.grid(axis="y", alpha=0.3)
    ax.margins(x=0.01)

    # Legend showing paper colors
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=PAPER_COLORS[p], ec="black")
        for p in args.paper_order
        if p in PAPER_COLORS
    ]
    legend_labels = [p for p in args.paper_order if p in PAPER_COLORS]
    ax.legend(handles, legend_labels, loc="upper right", fontsize=9, frameon=True)

    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print(
        f"wrote {args.out}  ({len(labels)} tasks; n_replicates ranges {min(counts)}..{max(counts)})"
    )


if __name__ == "__main__":
    main()
