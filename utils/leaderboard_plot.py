#!/usr/bin/env python3
"""Render the leaderboard as grouped horizontal bar charts.

For each task, draw one tight bar per (model, agent) showing its best-run
Baker-Cousins shape p-value (toy-calibrated, in [~1e-5, 1]; higher = better,
so p≈1 means recast and reference are statistically indistinguishable, p≈0
means strong shape disagreement). X-axis is log scale because p-values span
~5 orders of magnitude under realistic disagreement.

Inputs are walked from `runs/<vendor>/.../eval/score.json` (the new schema
with `shape.p_value`). For each (task, vendor) the best run — *highest*
p-value across that vendor's runs of that task — is shown.

Usage:
    python -m utils.leaderboard_plot                          # writes utils/leaderboard.png
    python -m utils.leaderboard_plot --out /tmp/lb.png
    python -m utils.leaderboard_plot --runs runs/ --vendors gpt-5.5 opus-4-7
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


# (vendor_dir_name, display_label, color). Order = top→bottom within a task group.
DEFAULT_VENDORS: list[tuple[str, str, str]] = [
    ("claude_opus-4-7", "Opus 4.7", "#d73502"),  # deep orange
    ("claude_sonnet-4-6", "Sonnet 4.6", "#f59e0b"),  # amber
    ("codex_gpt-5.5", "GPT-5.5", "#1f77b4"),  # blue
    ("codex_gpt-5.4-mini", "GPT-5.4-mini", "#6baed6"),  # light blue
    ("gemini_3-pro-preview", "Gemini-3 Pro", "#2ca02c"),  # green
    ("forge_deepseek-v4-pro", "DeepSeek-V4 Pro", "#7c3aed"),  # violet
]


def load_best_p_value(runs_root: Path, vendor_dirs: list[str]) -> dict:
    """Walk score.json files; return {task_id: {vendor_dir: best_p_value}}.

    Filters to results with a non-null `shape.p_value`. Best = *highest*
    p-value (larger p = better agreement under the BC null). Skips error
    results and runs in `old/` subdirs.
    """
    best: dict = defaultdict(dict)
    for vdir in vendor_dirs:
        for sj in (runs_root / vdir).rglob("score.json"):
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
            task_id = s["task_id"]
            cur = best[task_id].get(vdir)
            if cur is None or p > cur:
                best[task_id][vdir] = float(p)
    return dict(best)


def render(
    best: dict,
    vendors: list[tuple[str, str, str]],
    out_path: Path,
    *,
    floor: float = 1e-5,
) -> Path:
    """Render the grouped horizontal bar chart and save to `out_path`."""
    tasks = sorted(best)
    if not tasks:
        raise SystemExit("No tasks with p-value data found.")

    n_tasks = len(tasks)
    n_v = len(vendors)
    bar_h = 0.85 / n_v

    fig_h = max(4.0, 0.55 * n_tasks + 1.0)
    fig, ax = plt.subplots(figsize=(11, fig_h))

    # Lay each vendor on its own offset within the per-task group.
    # i=0 sits at the top of each group, i=N-1 at the bottom.
    for i, (vdir, label, color) in enumerate(vendors):
        offsets = np.array([t + (n_v / 2 - 0.5 - i) * bar_h for t in range(n_tasks)])
        values = np.array([best.get(task, {}).get(vdir, np.nan) for task in tasks])
        finite = np.isfinite(values)
        if not finite.any():
            continue
        # log-x: clamp tiny p-values to `floor` so the bar still draws (toy
        # calibration with N toys saturates at p ~ 1/(N+1); 1M toys → 1e-6).
        plot_vals = np.where(finite & (values < floor), floor, values)
        ax.barh(
            offsets[finite],
            plot_vals[finite],
            height=bar_h,
            color=color,
            edgecolor="black",
            linewidth=0.4,
            label=label,
            zorder=3,
        )

    ax.set_xscale("log")
    ax.set_xlabel("Baker-Cousins shape p-value  (toy-calibrated; higher = better)", fontsize=12)
    ax.set_yticks(range(n_tasks))
    ax.set_yticklabels(tasks, fontsize=9)
    ax.invert_yaxis()  # first task at top
    ax.tick_params(axis="x", which="both", labelsize=9)
    ax.grid(axis="x", which="both", linestyle=":", linewidth=0.5, alpha=0.6, zorder=0)

    # Reference vertical lines at common p thresholds (5%, 0.3%/3σ-ish, 1.0).
    for tick in (0.05, 0.003, 1.0):
        ax.axvline(tick, color="black", linewidth=0.4, alpha=0.3, zorder=1)

    ax.set_xlim(left=floor, right=1.0)

    # Legend on the right; one row per vendor preserves the visual top-to-bottom mapping.
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        frameon=False,
        fontsize=9,
        handlelength=1.5,
    )

    fig.suptitle("Leaderboard — best run per (task, model)", fontsize=13, y=0.995)
    fig.tight_layout(rect=(0.0, 0.0, 0.85, 0.985))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="Runs root directory (default: ./runs)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("utils/leaderboard.png"),
        help="Output PNG path (default: utils/leaderboard.png)",
    )
    parser.add_argument(
        "--vendors",
        nargs="*",
        default=None,
        help="Optional subset of vendor display labels to include "
        "(e.g. 'GPT-5.5 Opus 4.7'). Default: all six.",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=1e-5,
        help="Lower x-axis bound and clamp for tiny p-values (default: 1e-5; "
        "use 1e-6 if you ran with the full 1M toys).",
    )
    args = parser.parse_args()

    if args.vendors:
        wanted = set(args.vendors)
        vendors = [v for v in DEFAULT_VENDORS if v[1] in wanted]
        if not vendors:
            raise SystemExit(
                f"No vendors matched {sorted(wanted)}. "
                f"Known labels: {[v[1] for v in DEFAULT_VENDORS]}"
            )
    else:
        vendors = DEFAULT_VENDORS

    best = load_best_p_value(args.runs, [v[0] for v in vendors])
    out = render(best, vendors, args.out, floor=args.floor)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
