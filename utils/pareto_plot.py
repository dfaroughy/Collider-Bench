#!/usr/bin/env python3
"""Cost-vs-performance Pareto plot.

For each vendor:
  x = total USD spent across all of that vendor's runs (sum of
      run_info.json["usage"]["api_cost_usd"]).
  y = fraction of attempted tasks where the vendor's best run achieved
      p-value == 1.0 (perfect Baker-Cousins shape agreement). Best =
      max p-value across that vendor's runs of that task.

Each vendor is one point. The Pareto front (lower cost, higher %
perfect) is drawn as a line connecting non-dominated points.

Usage:
    python -m utils.pareto_plot                    # writes utils/pareto.png
    python -m utils.pareto_plot --out /tmp/p.png
    python -m utils.pareto_plot --threshold 0.99   # count "p >= 0.99" as perfect
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


# (vendor_dir_name, display_label, color)
DEFAULT_VENDORS: list[tuple[str, str, str]] = [
    ("claude_opus-4-7", "Opus 4.7", "#d73502"),
    ("claude_sonnet-4-6", "Sonnet 4.6", "#f59e0b"),
    ("claude_haiku-4-5", "Haiku 4.5", "#fbbf24"),
    ("codex_gpt-5.5", "GPT-5.5", "#1f77b4"),
    ("codex_gpt-5.4-mini", "GPT-5.4-mini", "#6baed6"),
    ("gemini_3-pro-preview", "Gemini-3 Pro", "#2ca02c"),
    ("forge_deepseek-v4-pro", "DeepSeek-V4 Pro", "#7c3aed"),
]


def collect_vendor_stats(
    runs_root: Path, vendor_dirs: list[str], *, p_threshold: float = 1.0
) -> dict:
    """Walk score.json + run_info.json; aggregate per-vendor stats.

    Returns {vendor_dir: {"n_tasks": N, "n_perfect": K, "total_cost": $}}.
    `p_threshold` is the p-value cutoff for "perfect" (default 1.0 — only
    runs where the toy-calibrated p saturates count).
    """
    out: dict = {}
    for vdir in vendor_dirs:
        root = runs_root / vdir
        if not root.is_dir():
            continue

        # Best p per (task) across all runs of this vendor.
        best_p: dict[str, float] = {}
        for sj in root.rglob("score.json"):
            if "/old/" in str(sj):
                continue
            try:
                s = json.loads(sj.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if "error" in s:
                continue
            shape = s.get("shape") or {}
            p = shape.get("p_value")
            if p is None or not isinstance(p, (int, float)):
                continue
            tid = s["task_id"]
            if tid not in best_p or p > best_p[tid]:
                best_p[tid] = float(p)

        # Sum cost across every run_info.json under this vendor (active runs
        # only — `old/` excluded). We count cost for failed/non-best runs
        # too, since you paid for those tokens.
        total_cost = 0.0
        n_runs_priced = 0
        for ri in root.rglob("run_info.json"):
            if "/old/" in str(ri):
                continue
            try:
                info = json.loads(ri.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            usage = info.get("usage") or {}
            c = usage.get("api_cost_usd")
            if isinstance(c, (int, float)):
                total_cost += float(c)
                n_runs_priced += 1

        if not best_p:
            continue
        n_tasks = len(best_p)
        n_perfect = sum(1 for p in best_p.values() if p >= p_threshold)
        out[vdir] = {
            "n_tasks": n_tasks,
            "n_perfect": n_perfect,
            "frac_perfect": n_perfect / n_tasks,
            "total_cost": total_cost,
            "n_runs_priced": n_runs_priced,
        }
    return out


def render(stats: dict, vendors: list[tuple[str, str, str]], out_path: Path) -> Path:
    """Seaborn scatter — one blob per vendor, identified by the legend."""
    items: list[tuple[str, str, str, dict]] = [
        (vdir, label, color, stats[vdir]) for vdir, label, color in vendors if vdir in stats
    ]
    if not items:
        raise SystemExit("No vendor stats to plot.")

    df = pd.DataFrame(
        {
            "vendor": [label for _, label, _, _ in items],
            "cost": [s["total_cost"] for *_, s in items],
            "perfect_pct": [100.0 * s["frac_perfect"] for *_, s in items],
        }
    )
    palette = {label: color for _, label, color in vendors}

    sns.set_theme(style="whitegrid", context="talk", font="DejaVu Sans")
    fig, ax = plt.subplots(figsize=(10, 6.8))

    sns.scatterplot(
        data=df,
        x="cost",
        y="perfect_pct",
        hue="vendor",
        hue_order=[label for _, label, _ in vendors if label in df["vendor"].values],
        palette=palette,
        s=240,
        edgecolor="white",
        linewidth=1.8,
        ax=ax,
    )

    ax.set_xscale("log")
    ax.set_xlabel("Total API cost  [USD]", labelpad=10)
    ax.set_ylabel(r"Tasks with shape $p$-value $= 1$   (% of attempted)", labelpad=10)
    ax.set_ylim(-4, 105)
    xmin = float(df["cost"].min()) * 0.5
    xmax = float(df["cost"].max()) * 2.0
    ax.set_xlim(xmin, xmax)
    ax.set_title("Cost vs perfect-task rate", pad=14)

    sns.despine(ax=ax, top=True, right=True)
    leg = ax.legend(
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        title="Vendor",
        frameon=False,
        handletextpad=0.6,
    )
    if leg.get_title():
        leg.get_title().set_color("#333333")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.02, 0.84, 0.99))
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--out", type=Path, default=Path("utils/pareto.png"))
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="p-value cutoff for 'perfect' (default 1.0; try 0.99 if toy "
        "saturation makes the all-or-nothing split too coarse).",
    )
    parser.add_argument(
        "--vendors",
        nargs="*",
        default=None,
        help="Optional subset of vendor display labels (default: all 7).",
    )
    args = parser.parse_args()

    if args.vendors:
        wanted = set(args.vendors)
        vendors = [v for v in DEFAULT_VENDORS if v[1] in wanted]
        if not vendors:
            raise SystemExit(f"No matching vendors in {sorted(wanted)}")
    else:
        vendors = DEFAULT_VENDORS

    stats = collect_vendor_stats(args.runs, [v[0] for v in vendors], p_threshold=args.threshold)
    if not stats:
        raise SystemExit("No vendor data found.")

    print(f"Threshold: p-value >= {args.threshold}")
    print(f"{'vendor':<18s} {'tasks':>6s} {'perfect':>8s} {'frac':>6s} {'cost':>8s} {'priced':>7s}")
    for vdir, label, _ in vendors:
        if vdir not in stats:
            continue
        s = stats[vdir]
        print(
            f"  {label:<16s} {s['n_tasks']:>6d} {s['n_perfect']:>8d} "
            f"{100*s['frac_perfect']:>5.1f}% {s['total_cost']:>7.2f}$ "
            f"{s['n_runs_priced']:>7d}"
        )

    out = render(stats, vendors, args.out)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
