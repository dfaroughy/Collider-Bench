#!/usr/bin/env python3
"""Two-panel composite: delta-vs-shape (left) and shape-vs-sim (right) scatters.

Both panels share the same ModelSpec catalog and use the run-status filter
("pass" only) when computing per-task means. Reads
utils/neurips/data/runs.json (produced by fetch_runs.py).

Usage:
    python -m utils.neurips.plot_scatter_combined
    python -m utils.neurips.plot_scatter_combined --avg-tasks
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


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("claude_opus-4-7", "Opus 4.7", "#C44536", "o", "Anthropic"),
    ModelSpec("claude_sonnet-4-6", "Sonnet 4.6", "#E88C30", "o", "Anthropic"),
    ModelSpec("claude_haiku-4-5", "Haiku 4.5", "#D8B12D", "o", "Anthropic"),
    ModelSpec("codex_gpt-5.5", "GPT-5.5", "#2454A6", "^", "OpenAI"),
    ModelSpec("codex_gpt-5.4-mini", "GPT-5.4-mini", "#2A9FBF", "^", "OpenAI"),
    ModelSpec("forge_deepseek-v4-pro", "DeepSeek-V4", "#6D4BC3", "s", "DeepSeek"),
    ModelSpec("forge_deepseek-r1", "DeepSeek-R1", "#A88EE0", "s", "DeepSeek"),
)


def _base_task(task_id: str) -> str | None:
    """Strip `_sim-`/`_shape-` infix to a base-task key (e.g. "paper|target")."""
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
    """Mean `relative_l2_shape` over pass-only replicates."""
    vals = [
        float(r["relative_l2_shape"])
        for r in (entry.get("replicates") or [])
        if r.get("relative_l2_shape") is not None and r.get("status", "pass") == "pass"
    ]
    return (sum(vals) / len(vals)) if vals else None


def shape_pairs(model_block: dict) -> list[tuple[str, float, float]]:
    """Per base-task: (base, mean shape l2 on sim variant, on shape variant)."""
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


def _draw_scatter(
    ax,
    models_block: dict,
    pairs_fn,
    specs,
    *,
    avg_tasks: bool,
):
    """Draw one panel exactly the way the single-panel modules do."""
    family_handles: dict[str, list] = {}
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
        family_handles.setdefault(spec.family, []).append(h)
        xs.extend(x_arr.tolist())
        ys.extend(y_arr.tolist())

    family_order = ["Anthropic", "OpenAI", "DeepSeek"]
    # Append any unknown families at the end in first-seen order.
    extras = [f for f in family_handles if f not in family_order]
    ordered = [h for f in (*family_order, *extras) for h in family_handles.get(f, [])]
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

    # Panels swapped per user request: delta_vs_shape on the LEFT, shape_vs_sim
    # on the RIGHT. Both panels render on linear axes (no log). y-axis is no
    # longer shared because the two panels' y-fields have different meanings
    # (sim-run shape l2 vs shape-variant shape l2).
    fig, (ax_left, ax_right) = plt.subplots(
        1,
        2,
        figsize=(args.figwidth, args.figheight),
    )

    # ── Left panel: delta vs relative_l2_shape ───────────────────────────
    # We need x = <delta_norm> (linear, not log10) and y = <relative_l2_shape>.
    # delta_pairs() from the standalone module gives us (log10⟨Δ⟩, ⟨l2_shape⟩);
    # we replace x with the linear delta and pull y from relative_l2_shape.
    def delta_pairs_linear(block):
        out = []
        for tid, entry in sorted((block.get("tasks") or {}).items()):
            if "_sim-" not in tid:
                continue
            reps = [r for r in (entry.get("replicates") or []) if r.get("status", "pass") == "pass"]
            d_vals = [float(r["delta"]) for r in reps if r.get("delta") is not None]
            n_vals = [
                float(r["relative_l2_shape"])
                for r in reps
                if r.get("relative_l2_shape") is not None
            ]
            if not d_vals or not n_vals:
                continue
            d_mean = sum(d_vals) / len(d_vals)
            n_mean = sum(n_vals) / len(n_vals)
            if d_mean <= 0:
                continue
            out.append((tid, d_mean, n_mean))
        return out

    # Legend lives on this (left) panel; right panel has none.
    _xs1, _ys1, ordered_left = _draw_scatter(
        ax_left,
        models_block,
        delta_pairs_linear,
        MODEL_SPECS,
        avg_tasks=args.avg_tasks,
    )
    ax_left.set_xlabel(r"$\langle \delta_{\rm norm} \rangle$")
    ax_left.set_xlim(0, 2)
    ax_left.set_ylim(0, 2)
    ax_left.set_ylabel(r"$\langle d(\hat p, p^\star)\rangle$")
    ax_left.set_box_aspect(1)
    ax_left.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax_left.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax_left.set_axisbelow(True)
    leg = ax_left.legend(
        handles=ordered_left,
        loc="upper right",
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

    # ── Right panel: shape_vs_sim ────────────────────────────────────────
    # No legend on this panel — the left panel carries the shared legend.
    xs, ys, _ordered_right = _draw_scatter(
        ax_right,
        models_block,
        shape_pairs,
        MODEL_SPECS,
        avg_tasks=args.avg_tasks,
    )
    if xs and ys:
        # Linear y=x diagonal across the data range.
        lo = min(xs + ys)
        hi = max(xs + ys)
        pad = 0.05 * (hi - lo) if hi > lo else 0.1
        lo -= pad
        hi += pad
        ax_right.plot([lo, hi], [lo, hi], color="#506A85", ls="--", lw=1.0, alpha=0.8, zorder=2)
        ax_right.set_xlim(lo, hi)
        ax_right.set_ylim(lo, hi)
    ax_right.set_xlabel(r"$\langle d(\hat p,p^\star) \rangle$ ($\mathtt{shape}$ tasks)")
    ax_right.set_ylabel(r"$\langle d(\hat p,p^\star) \rangle$ ($\mathtt{sim}$ tasks)")
    ax_right.set_box_aspect(1)
    ax_right.grid(True, which="major", linewidth=0.55, color="#D8DDE3")
    ax_right.grid(True, which="minor", linewidth=0.35, color="#E6E9ED")
    ax_right.set_axisbelow(True)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=300)
    fig.savefig(args.out.with_suffix(".pdf"))
    print(f"wrote {args.out}")
    print(f"wrote {args.out.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()
