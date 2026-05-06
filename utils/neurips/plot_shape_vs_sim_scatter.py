#!/usr/bin/env python3
"""Per-(model, task) scatter of shape relative_l2: sim vs shape variants.

Each (model, base_task) gives one point — 6 models × 10 base tasks = 60
points. Each point's coordinates are the mean of `relative_l2_shape` over
the 3 replicate runs of that task variant:
  * x = mean over runs of `relative_l2_shape` on the `_sim-` variant
  * y = mean over runs of `relative_l2_shape` on the `_shape-` variant

A diagonal `y = x` reference line is drawn. Points above the diagonal mean
the shape variant produced a *higher* shape-l2 than the sim variant of the
same task (counter-intuitive — shape tasks should usually do better since
they don't have to nail the absolute yield).

Reads utils/neurips/data/runs.json (produced by fetch_runs.py).

Usage:
    python -m utils.neurips.plot_shape_vs_sim_scatter
"""

from __future__ import annotations

import argparse
import json
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


# Match the Pareto plot's color + marker scheme exactly.
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("claude_opus-4-7", "Opus 4.7", "#C44536", "o", "Anthropic"),
    ModelSpec("claude_sonnet-4-6", "Sonnet 4.6", "#E88C30", "o", "Anthropic"),
    ModelSpec("claude_haiku-4-5", "Haiku 4.5", "#D8B12D", "o", "Anthropic"),
    ModelSpec("codex_gpt-5.5", "GPT-5.5", "#2454A6", "^", "OpenAI"),
    ModelSpec("codex_gpt-5.4-mini", "GPT-5.4-mini", "#2A9FBF", "^", "OpenAI"),
    ModelSpec("forge_deepseek-v4-pro", "DeepSeek-V4", "#6D4BC3", "s", "DeepSeek"),
)


def _base_task(task_id: str) -> str | None:
    """Strip the `_sim-` or `_shape-` infix to get a base-task key.

    e.g. "sus-16-046_shape-T5Wg" → "sus-16-046|T5Wg".
    Returns None if the task_id contains neither token.
    """
    for kind in ("sim", "shape"):
        marker = f"_{kind}-"
        if marker in task_id:
            paper, target = task_id.split(marker, 1)
            return f"{paper}|{target}"
    return None


def _kind_of(task_id: str) -> str | None:
    if "_sim-" in task_id:
        return "sim"
    if "_shape-" in task_id:
        return "shape"
    return None


def _mean_replicate_l2_shape(entry: dict) -> float | None:
    # Only count replicates that judged as "pass" — cheat/hung/wall runs are
    # filtered out so they don't bias the per-task mean.
    vals = [
        float(r["relative_l2_shape"])
        for r in (entry.get("replicates") or [])
        if r.get("relative_l2_shape") is not None and r.get("status", "pass") == "pass"
    ]
    return (sum(vals) / len(vals)) if vals else None


def task_pairs(model_block: dict) -> list[tuple[str, float, float]]:
    """For each base task with both sim and shape variants, return (base, x, y).

    x = mean replicate `relative_l2_shape` on the sim variant
    y = mean replicate `relative_l2_shape` on the shape variant
    Skips base tasks where either variant is missing or null.
    """
    by_base: dict[str, dict[str, dict]] = {}
    for tid, entry in (model_block.get("tasks") or {}).items():
        base = _base_task(tid)
        kind = _kind_of(tid)
        if base is None or kind is None:
            continue
        by_base.setdefault(base, {})[kind] = entry

    out: list[tuple[str, float, float]] = []
    for base, variants in sorted(by_base.items()):
        sim_entry = variants.get("sim")
        shape_entry = variants.get("shape")
        if sim_entry is None or shape_entry is None:
            continue
        x = _mean_replicate_l2_shape(sim_entry)
        y = _mean_replicate_l2_shape(shape_entry)
        if x is None or y is None:
            continue
        out.append((base, x, y))
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
        help="Output PNG path. Defaults to figures/shape_vs_sim_scatter[_avg].png.",
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
        args.out = repo_root / "utils" / "neurips" / "figures" / f"shape_vs_sim_scatter{suffix}.png"

    if not args.data.is_file():
        raise SystemExit(f"missing data file: {args.data}\nrun: python -m utils.neurips.fetch_runs")
    data = json.loads(args.data.read_text())
    models_block = data.get("models", {})

    # Theme matches the Pareto / bars panel — light cool-gray panel face.
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
    xs: list[float] = []
    ys: list[float] = []
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
            # Reduce 10 paired tasks → 1 point per model.
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
        n_total += len(x_arr)
        if args.avg_tasks:
            print(
                f"  {spec.label:<14}  sim={x_arr[0]:.4f}   shape={y_arr[0]:.4f}  ({len(pairs)} task-pairs avg'd)"
            )
        else:
            print(f"  {spec.label:<14}  {len(pairs):>2} paired tasks")
    print(f"\n  total points plotted: {n_total}")

    if xs and ys:
        # y = x diagonal across the data range.
        lo = min(xs + ys) * 0.7
        hi = max(xs + ys) * 1.3
        ax.plot([lo, hi], [lo, hi], color="#506A85", ls="--", lw=1.0, alpha=0.8, zorder=2)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(r"$\langle d(\hat p,p^\star) \rangle$, {\texttt shape} tasks")
    ax.set_ylabel(r"$\langle d(\hat p,p^\star) \rangle$, {\texttt yield} tasks")
    ax.set_box_aspect(1)
    ax.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax.set_axisbelow(True)

    # Legend grouped Anthropic → OpenAI → DeepSeek.
    family_order = ["Anthropic", "OpenAI", "DeepSeek"]
    ordered = [h for f in family_order for h in family_handles.get(f, [])]
    leg = ax.legend(
        handles=ordered,
        loc="upper left",
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
