#!/usr/bin/env python3
"""Per-(model, sim_task) scatter: log10(Delta) vs relative_l2_shape on the same sim task.

For each (model, sim_task) both metrics come from the **same** sim runs
(sim tasks have score_mode="shape_norm", so each `score.json` carries
both `normalization.Delta` and `shape.relative_l2`):
  * x = log10(mean over replicates of `normalization.Delta`)
  * y = mean over replicates of `shape.relative_l2`

6 models × 10 sim tasks = up to 60 points. Same color/marker scheme as
the Pareto plot.

Reads utils/neurips/data/runs.json (produced by fetch_runs.py).

Usage:
    python -m utils.neurips.plot_delta_vs_shape_scatter
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns


@dataclass(frozen=True)
class ModelSpec:
    dirname: str
    label: str
    color: str
    marker: str
    family: str


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("claude_opus-4-7", "Opus 4.7", "#C44536", "o", "Anthropic"),
    ModelSpec("claude_sonnet-4-6", "Sonnet 4.6", "#E88C30", "o", "Anthropic"),
    ModelSpec("claude_haiku-4-5", "Haiku 4.5", "#D8B12D", "o", "Anthropic"),
    ModelSpec("codex_gpt-5.5", "GPT-5.5", "#2454A6", "^", "OpenAI"),
    ModelSpec("codex_gpt-5.4-mini", "GPT-5.4-mini", "#2A9FBF", "^", "OpenAI"),
    ModelSpec("forge_deepseek-v4-pro", "DeepSeek-V4", "#6D4BC3", "s", "DeepSeek"),
)


def _mean(field: str, entry: dict) -> float | None:
    # Only count replicates that judged as "pass" — cheat/hung/wall runs are
    # filtered out so they don't bias the per-task mean.
    vals = [
        float(r[field])
        for r in (entry.get("replicates") or [])
        if r.get(field) is not None and r.get("status", "pass") == "pass"
    ]
    return (sum(vals) / len(vals)) if vals else None


def task_pairs(model_block: dict) -> list[tuple[str, float, float]]:
    """For each sim task in this model, return (task_id, log10⟨Δ⟩, ⟨d_l2_shape⟩).

    Both metrics come from the same sim-task runs (no cross-variant pairing),
    averaged over the task's replicates. Tasks where Δ ≤ 0 or either metric
    is null are dropped.
    """
    out: list[tuple[str, float, float]] = []
    for tid, entry in sorted((model_block.get("tasks") or {}).items()):
        if "_sim-" not in tid:
            continue
        delta_mean = _mean("delta", entry)
        l2_shape_mean = _mean("relative_l2_shape", entry)
        if delta_mean is None or l2_shape_mean is None or delta_mean <= 0:
            continue
        out.append((tid, math.log10(delta_mean), l2_shape_mean))
    return out


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
        help="Output PNG path. Defaults to figures/delta_vs_shape_scatter[_avg].png.",
    )
    ap.add_argument(
        "--avg-tasks",
        action="store_true",
        help="Average each model's per-task pairs into one point (6 points total).",
    )
    ap.add_argument("--figwidth", type=float, default=6.0)
    ap.add_argument("--figheight", type=float, default=6.0)
    args = ap.parse_args()
    if args.out is None:
        suffix = "_avg" if args.avg_tasks else ""
        args.out = (
            repo_root / "utils" / "neurips" / "figures" / f"delta_vs_shape_scatter{suffix}.png"
        )

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}\nrun: python -m utils.neurips.fetch_runs")
    data = json.loads(args.data.read_text())
    models_block = data.get("models", {})

    sns.set_theme(style="whitegrid", context="paper")
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.facecolor": "#E8EDF3",
            "axes.edgecolor": "#333333",
            "axes.labelsize": 14.0,
            "xtick.labelsize": 11.0,
            "ytick.labelsize": 11.0,
            "legend.fontsize": 10.5,
            "font.family": "serif",
            "font.serif": ["Computer Modern Roman", "DejaVu Serif", "Times"],
            "mathtext.fontset": "cm",
            "mathtext.rm": "serif",
            "axes.unicode_minus": False,
        }
    )

    fig, ax = plt.subplots(figsize=(args.figwidth, args.figheight))
    family_handles: dict[str, list] = {"Anthropic": [], "OpenAI": [], "DeepSeek": []}
    n_total = 0
    marker_size = 180 if args.avg_tasks else 42
    marker_alpha = 1.0 if args.avg_tasks else 0.85
    for spec in MODEL_SPECS:
        block = models_block.get(spec.dirname) or {}
        pairs = task_pairs(block)
        if not pairs:
            print(f"  {spec.label:<14} no paired data — skipping")
            continue
        x_arr = np.array([p[1] for p in pairs])
        y_arr = np.array([p[2] for p in pairs])
        if args.avg_tasks:
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
        n_total += len(x_arr)
        if args.avg_tasks:
            print(
                f"  {spec.label:<14}  log10⟨Δ⟩={x_arr[0]:.3f}   ⟨d_shape⟩={y_arr[0]:.4f}  ({len(pairs)} task-pairs avg'd)"
            )
        else:
            print(f"  {spec.label:<14}  {len(pairs):>2} paired tasks")
    print(f"\n  total points plotted: {n_total}")

    ax.set_yscale("log")  # y-axis: shape l2 spans orders of magnitude
    ax.set_xlabel(r"$\log_{10}\langle \delta_{\rm norm} \rangle$")
    ax.set_ylabel(r"$\langle d(\hat p, p^\star)\rangle$")
    ax.set_box_aspect(1)
    ax.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax.set_axisbelow(True)

    family_order = ["Anthropic", "OpenAI", "DeepSeek"]
    ordered = [h for f in family_order for h in family_handles.get(f, [])]
    leg = ax.legend(
        handles=ordered,
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
    out_png = args.out
    fig.savefig(out_png, dpi=300)
    out_pdf = out_png.with_suffix(".pdf")
    fig.savefig(out_pdf)
    print(f"\nwrote {out_png} (PNG, dpi=300)")
    print(f"wrote {out_pdf} (PDF, vector)")


if __name__ == "__main__":
    main()
