#!/usr/bin/env python3
"""Pareto front of accuracy vs total cost.

Per model:
  y = fraction of attempted tasks where the model's best run achieved
      d_L2 < tau (default tau=0.25).  This is `mean_t Ind[best_l2(t) < tau]`.
  x = total API cost in USD, summed over every run of that model
      (sum of run_info.json["usage"]["api_cost_usd"] across all replicates).

Each model is one point.  The Pareto front (lower cost AND higher accuracy
is better) is the staircase of non-dominated points, drawn as a polyline.

Usage:
    python -m utils.neurips.plot_pareto                     # tau=0.25, shape
    python -m utils.neurips.plot_pareto --tau 0.1
    python -m utils.neurips.plot_pareto --metric normalization \
           --data utils/neurips/best_l2_norm.json \
           --out  utils/neurips/pareto_norm.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


# (model_dir, display_label, color)  same scheme as the bar plots
MODEL_PALETTE: list[tuple[str, str, str]] = [
    ("claude_opus-4-7", "Opus 4.7", "#d62728"),  # red
    ("claude_sonnet-4-6", "Sonnet 4.6", "#ff7f0e"),  # orange
    ("claude_haiku-4-5", "Haiku 4.5", "#fbbf24"),  # yellow
    ("codex_gpt-5.5", "GPT-5.5", "#1f3a8a"),  # dark blue
    ("codex_gpt-5.4-mini", "GPT-5.4-mini", "#22d3ee"),  # cyan
    ("forge_deepseek-v4-pro", "DeepSeek-V4", "#7c3aed"),  # purple
]


def total_resource(
    model_root: Path,
    *,
    kind: str = "cost",
    task_contains: str | None = None,
) -> float:
    """Aggregate a per-run resource ('cost' USD or 'wall' seconds) across runs.

    Sums over every run_info.json under <model>/run-*/<run>/. When
    `task_contains` is given, only runs whose `task_id` contains that
    substring contribute.
    """
    if kind not in ("cost", "wall", "tokens"):
        raise ValueError(f"kind must be 'cost', 'wall', or 'tokens', got {kind!r}")
    total = 0.0
    for ri in model_root.glob("run-*/*/run_info.json"):
        try:
            info = json.loads(ri.read_text() or "{}")
        except Exception:
            continue
        if task_contains and task_contains not in (info.get("task_id") or ""):
            continue
        if kind == "cost":
            usage = info.get("usage") or {}
            v = usage.get("api_cost_usd") or usage.get("total_cost_usd")
        elif kind == "tokens":
            usage = info.get("usage") or {}
            v = usage.get("tokens_total_billed") or usage.get("total_tokens")
        else:  # wall
            v = info.get("duration_wall_s")
        if v is not None:
            try:
                total += float(v)
            except (TypeError, ValueError):
                pass
    return total


def total_cost(model_root: Path, task_contains: str | None = None) -> float:
    """Backward-compat wrapper: sum of usage.api_cost_usd in USD."""
    return total_resource(model_root, kind="cost", task_contains=task_contains)


def pareto_front(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Lower x is better, higher y is better. Returns non-dominated points sorted by x."""
    sorted_pts = sorted(points, key=lambda p: (p[0], -p[1]))
    front: list[tuple[float, float, str]] = []
    best_y = -float("inf")
    for x, y, lab in sorted_pts:
        if y > best_y:
            front.append((x, y, lab))
            best_y = y
    return front


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2.json",
        help="best_l2.json file (shape by default; pass best_l2_norm.json for normalization).",
    )
    ap.add_argument(
        "--runs-root",
        type=Path,
        default=Path("/global/cfs/cdirs/m4539/ColliderBench"),
        help="Source root for total-cost aggregation across replicate runs.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "pareto.png",
    )
    ap.add_argument(
        "--tau",
        type=float,
        default=0.25,
        help="d_L2 threshold for the accuracy indicator (default 0.25).",
    )
    ap.add_argument(
        "--task-contains",
        default=None,
        help="Restrict to tasks whose ID contains this substring (e.g. 'sim').",
    )
    ap.add_argument(
        "--linear-y", action="store_true", help="Use linear y-axis (default: log y, i.e. log-log)."
    )
    ap.add_argument(
        "--xaxis",
        choices=("cost", "wall", "tokens"),
        default="cost",
        help="x-axis quantity: 'cost' (USD), 'wall' (hours), or 'tokens' (total billed). Default cost.",
    )
    ap.add_argument("--figwidth", type=float, default=7.5)
    ap.add_argument("--figheight", type=float, default=5.0)
    args = ap.parse_args()

    if not args.data.is_file():
        raise SystemExit(f"missing {args.data}; run fetch_best_l2.py first")

    data = json.loads(args.data.read_text())
    metric = data.get("metric", "shape")
    models_block = data["models"]

    rows = []
    for mdir, label, color in MODEL_PALETTE:
        block = models_block.get(mdir) or {}
        tasks = block.get("tasks") or {}
        if args.task_contains:
            tasks = {tid: v for tid, v in tasks.items() if args.task_contains in tid}
        if not tasks:
            print(f"  {label}: no task data — skipping")
            continue
        bests = [v.get("best") for v in tasks.values()]
        bests = [float(b) for b in bests if b is not None]
        if not bests:
            print(f"  {label}: no usable d_L2 — skipping")
            continue
        accuracy = float(np.mean([1.0 if b < args.tau else 0.0 for b in bests]))
        if args.xaxis == "cost":
            x_value = total_resource(
                args.runs_root / mdir, kind="cost", task_contains=args.task_contains
            )
        elif args.xaxis == "tokens":
            x_value = total_resource(
                args.runs_root / mdir, kind="tokens", task_contains=args.task_contains
            )
        else:  # wall — convert seconds → hours
            x_value = (
                total_resource(args.runs_root / mdir, kind="wall", task_contains=args.task_contains)
                / 3600.0
            )
        rows.append(
            {
                "label": label,
                "color": color,
                "x_value": x_value,
                "accuracy": accuracy,
                "n_tasks": len(bests),
                "n_below_tau": int(sum(b < args.tau for b in bests)),
            }
        )

    if not rows:
        raise SystemExit("no models had usable data")

    # Print a small text summary
    x_unit = {"cost": "USD", "wall": "hours", "tokens": "tokens"}[args.xaxis]
    x_label_short = {"cost": "cost", "wall": "wall", "tokens": "tokens"}[args.xaxis]
    print(f"\nmetric={metric}  tau={args.tau}  xaxis={args.xaxis}")
    for r in rows:
        print(
            f"  {r['label']:<14} {x_label_short}={r['x_value']:>9.2f} {x_unit}  "
            f"acc={r['accuracy']:>5.2%}  ({r['n_below_tau']}/{r['n_tasks']} tasks)"
        )

    # Plot
    fig, ax = plt.subplots(figsize=(args.figwidth, args.figheight))
    pts_for_front: list[tuple[float, float, str]] = []
    for r in rows:
        ax.scatter(
            r["x_value"],
            r["accuracy"],
            s=120,
            color=r["color"],
            edgecolor="black",
            linewidth=0.8,
            zorder=3,
        )
        ax.annotate(
            r["label"],
            xy=(r["x_value"], r["accuracy"]),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=9,
        )
        pts_for_front.append((r["x_value"], r["accuracy"], r["label"]))

    front = pareto_front(pts_for_front)
    if len(front) >= 2:
        fx = [p[0] for p in front]
        fy = [p[1] for p in front]
        ax.plot(
            fx,
            fy,
            "--",
            color="black",
            lw=1.0,
            alpha=0.6,
            zorder=2,
            label="Pareto front (lower-cost ∧ higher-acc)",
        )
        ax.legend(loc="lower right", fontsize=8, frameon=True)

    ax.set_xscale("log")
    if args.xaxis == "cost":
        ax.set_xlabel("Total API cost across all runs (USD, log)", fontsize=10)
    elif args.xaxis == "tokens":
        ax.set_xlabel("Total billed tokens across all runs (log)", fontsize=10)
    else:
        ax.set_xlabel("Total wall time across all runs (hours, log)", fontsize=10)
    ax.set_ylabel(
        rf"Accuracy = $\langle\,\mathbf{{1}}[\,d_{{L^2}} < {args.tau}\,]\,\rangle_\mathrm{{tasks}}$",
        fontsize=10,
    )
    title_filt = f", tasks∋'{args.task_contains}'" if args.task_contains else ""
    ax.set_title(
        f"Pareto: accuracy vs cost  ({metric}.relative_l2, τ={args.tau}{title_filt})", fontsize=11
    )

    if args.linear_y:
        ax.set_ylim(-0.03, 1.03)
    else:
        # Log-y: floor below the smallest positive accuracy. Zero-accuracy
        # points (DeepSeek can hit 0/10 on norm) get dropped onto an open
        # marker at the floor so they remain visible.
        positives = [r["accuracy"] for r in rows if r["accuracy"] > 0]
        floor = (min(positives) * 0.5) if positives else 1e-2
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor, top=1.05)
        for r in rows:
            if r["accuracy"] <= 0:
                ax.scatter(
                    r["x_value"],
                    floor,
                    s=120,
                    facecolor="white",
                    edgecolor=r["color"],
                    linewidth=1.2,
                    zorder=4,
                    marker="o",
                )

    ax.grid(alpha=0.3, which="both")
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
