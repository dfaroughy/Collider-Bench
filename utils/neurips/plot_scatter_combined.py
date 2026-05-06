#!/usr/bin/env python3
"""Two-panel composite: shape-vs-sim and delta-vs-shape scatters side by side.

Reuses the `task_pairs` extractors and the `MODEL_SPECS` lists from the two
existing single-panel modules so that any future tweak you make to either
file (axis fields, log scaling, labels, etc.) flows into both the standalone
plot and this composite without duplication. Visual styling — colors,
markers, theme, legends — mirrors the single-panel versions.

Reads utils/neurips/data/runs.json (produced by fetch_runs.py).

Usage:
    python -m utils.neurips.plot_scatter_combined
    python -m utils.neurips.plot_scatter_combined --avg-tasks
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

from utils.neurips.plot_delta_vs_shape_scatter import (
    MODEL_SPECS as DELTA_MODEL_SPECS,
    task_pairs as delta_pairs,
)
from utils.neurips.plot_shape_vs_sim_scatter import (
    MODEL_SPECS as SHAPE_MODEL_SPECS,
    task_pairs as shape_pairs,
)


def _draw_scatter(
    ax,
    models_block: dict,
    pairs_fn,
    specs,
    *,
    avg_tasks: bool,
):
    """Draw one panel exactly the way the single-panel modules do."""
    family_handles: dict[str, list] = {"Anthropic": [], "OpenAI": [], "DeepSeek": []}
    xs: list[float] = []
    ys: list[float] = []
    marker_size = 180 if avg_tasks else 42
    marker_alpha = 1.0 if avg_tasks else 0.85
    for spec in specs:
        block = models_block.get(spec.dirname) or {}
        pairs = pairs_fn(block)
        if not pairs:
            continue
        x_arr = np.array([p[1] for p in pairs])
        y_arr = np.array([p[2] for p in pairs])
        if avg_tasks:
            x_arr = np.array([float(np.mean(x_arr))])
            y_arr = np.array([float(np.mean(y_arr))])
        h = ax.scatter(
            x_arr,
            y_arr,
            s=marker_size,
            marker=spec.marker,
            color=spec.color,
            edgecolor="none",
            linewidth=0,
            alpha=marker_alpha,
            zorder=4,
            label=spec.label,
        )
        family_handles[spec.family].append(h)
        xs.extend(x_arr.tolist())
        ys.extend(y_arr.tolist())

    family_order = ["Anthropic", "OpenAI", "DeepSeek"]
    ordered = [h for f in family_order for h in family_handles.get(f, [])]
    return xs, ys, ordered


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
        default=None,
        help="Output PNG path. Defaults to figures/scatter_combined[_avg].png.",
    )
    ap.add_argument(
        "--avg-tasks",
        action="store_true",
        help="Average each model's per-task pairs into one point (6 points per panel).",
    )
    # Same figheight as plot_combined.py so the plot-frame heights match it.
    # Width = ~2x figheight so the two square panels (box_aspect=1) sit
    # side-by-side with normal margins.
    ap.add_argument("--figwidth", type=float, default=10.0)
    ap.add_argument("--figheight", type=float, default=5.0)
    args = ap.parse_args()
    if args.out is None:
        suffix = "_avg" if args.avg_tasks else ""
        args.out = repo_root / "utils" / "neurips" / "figures" / f"scatter_combined{suffix}.png"

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}\nrun: python -m utils.neurips.fetch_runs")
    data = json.loads(args.data.read_text())
    models_block = data.get("models", {})

    # Theme: same as the two single-panel modules.
    # Theme + font sizes mirror plot_combined.py exactly so all NeurIPS
    # figures share a consistent scale.
    sns.set_theme(style="whitegrid", context="paper")
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.facecolor": "white",
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

    # sharey=True + wspace=0 → both panels share the same y-axis range and
    # sit flush against each other (no gap between them).
    fig, (ax_left, ax_right) = plt.subplots(
        1,
        2,
        figsize=(args.figwidth, args.figheight),
        sharey=True,
        gridspec_kw={"wspace": 0.0},
    )

    # ── Left panel: shape_vs_sim ─────────────────────────────────────────
    # No legend here — the right panel carries the legend for both panels.
    xs, ys, _ordered_left = _draw_scatter(
        ax_left,
        models_block,
        shape_pairs,
        SHAPE_MODEL_SPECS,
        avg_tasks=args.avg_tasks,
    )
    if xs and ys:
        lo = min(xs + ys) * 0.7
        hi = max(xs + ys) * 1.3
        ax_left.plot([lo, hi], [lo, hi], color="#506A85", ls="--", lw=1.0, alpha=0.8, zorder=2)
        ax_left.set_xlim(lo, hi)
        ax_left.set_ylim(lo, hi)
    ax_left.set_xscale("log")
    ax_left.set_yscale("log")
    ax_left.set_xlabel(r"$\langle d(\hat p,p^\star) \rangle$ ($\mathtt{shape}$ tasks)")
    ax_left.set_ylabel(r"$\langle d(\hat p,p^\star) \rangle$ ($\mathtt{sim}$ tasks)")
    ax_left.set_box_aspect(1)
    ax_left.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax_left.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax_left.set_axisbelow(True)

    # ── Right panel: delta_vs_shape ──────────────────────────────────────
    _xs2, _ys2, ordered_right = _draw_scatter(
        ax_right,
        models_block,
        delta_pairs,
        DELTA_MODEL_SPECS,
        avg_tasks=args.avg_tasks,
    )
    ax_right.set_yscale("log")
    ax_right.set_xlabel(r"$\log_{10}\langle \delta_{\rm norm} \rangle$")
    ax_right.set_box_aspect(1)
    ax_right.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax_right.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax_right.set_axisbelow(True)
    leg = ax_right.legend(
        handles=ordered_right,
        loc="lower right",
        frameon=True,
        edgecolor="#444444",
        fancybox=False,
        handletextpad=0.5,
        labelspacing=0.35,
        borderpad=0.5,
    )
    leg.get_frame().set_facecolor("white")
    leg.get_frame().set_alpha(0.78)
    leg.get_frame().set_linewidth(0.9)

    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=300)
    fig.savefig(args.out.with_suffix(".pdf"))
    print(f"wrote {args.out}")
    print(f"wrote {args.out.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
