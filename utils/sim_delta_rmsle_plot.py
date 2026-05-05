#!/usr/bin/env python3
"""Scatter plot of `Delta` vs `rmsle` for all `sim` runs across vendors.

For each run whose task_id contains "sim" (i.e. the agent had to predict
absolute event yields, not just shape):

  x = `normalization.Delta`   = |Σobs - Σref| / Σref          (raw fraction)
  y = `normalization.rmsle`   = sqrt((1/K) Σ [ln(N_obs+1) - ln(N_ref+1)]²)

`Delta` measures total-yield error in linear units; `rmsle` measures
per-bin yield error in log units. Together they distinguish "got the
total right but bins wrong" from "got the total wrong but bins right".

Each vendor's runs are plotted in a single color; one point per run.

Usage:
    python -m utils.sim_delta_rmsle_plot                # writes utils/sim_delta_rmsle.png
    python -m utils.sim_delta_rmsle_plot --out /tmp/x.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


# (vendor_dir_name, display_label, color) — same vendor list as pareto_plot.py
DEFAULT_VENDORS: list[tuple[str, str, str]] = [
    ("claude_opus-4-7", "Opus 4.7", "#d73502"),
    ("claude_sonnet-4-6", "Sonnet 4.6", "#f59e0b"),
    ("claude_haiku-4-5", "Haiku 4.5", "#fbbf24"),
    ("codex_gpt-5.5", "GPT-5.5", "#1f77b4"),
    ("codex_gpt-5.4-mini", "GPT-5.4-mini", "#6baed6"),
    ("gemini_3-pro-preview", "Gemini-3 Pro", "#2ca02c"),
    ("forge_deepseek-v4-pro", "DeepSeek-V4 Pro", "#7c3aed"),
]


def collect(runs_root: Path, vendors: list[tuple[str, str, str]]) -> pd.DataFrame:
    rows = []
    for vendor_dir, label, color in vendors:
        vroot = runs_root / vendor_dir
        if not vroot.is_dir():
            continue
        for sj in vroot.rglob("eval/score.json"):
            try:
                s = json.loads(sj.read_text())
            except Exception:
                continue
            tid = s.get("task_id", "")
            if "sim" not in tid:
                continue
            norm = s.get("normalization") or {}
            delta = norm.get("Delta")
            rmsle = norm.get("rmsle")
            if delta is None or rmsle is None:
                continue
            rows.append(
                {
                    "vendor": label,
                    "color": color,
                    "task_id": tid,
                    "Delta": float(delta),
                    "rmsle": float(rmsle),
                    "run": sj.parent.parent.name,
                }
            )
    return pd.DataFrame(rows)


def make_plot(df: pd.DataFrame, out_path: Path, vendors: list[tuple[str, str, str]]) -> None:
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(9, 7))

    palette = {label: color for _, label, color in vendors}
    for label, sub in df.groupby("vendor"):
        ax.scatter(
            sub["Delta"],
            sub["rmsle"],
            s=70,
            alpha=0.8,
            color=palette.get(label, "gray"),
            edgecolor="black",
            linewidth=0.5,
            label=f"{label}  (n={len(sub)})",
        )

    ax.set_xscale("symlog", linthresh=0.1)
    ax.set_yscale("log")
    ax.set_xlabel(r"$\Delta = |\Sigma_\mathrm{obs} - \Sigma_\mathrm{ref}| / \Sigma_\mathrm{ref}$")
    ax.set_ylabel(r"$\mathrm{RMSLE}$")
    ax.set_title(f"sim runs: total-yield error vs per-bin log error  (n={len(df)})")
    ax.legend(loc="best", frameon=True, fontsize=11)
    ax.grid(True, which="both", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"wrote {out_path}  ({len(df)} points)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parent.parent
    ap.add_argument("--runs-root", type=Path, default=repo_root / "runs")
    ap.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parent / "sim_delta_rmsle.png"
    )
    args = ap.parse_args()

    df = collect(args.runs_root, DEFAULT_VENDORS)
    if df.empty:
        raise SystemExit("no sim runs with both Delta and rmsle found")
    make_plot(df, args.out, DEFAULT_VENDORS)


if __name__ == "__main__":
    main()
