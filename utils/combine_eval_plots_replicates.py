#!/usr/bin/env python3
"""Grid PDFs of mean ± 1σ agent histograms over replicate runs (run-1, run-2, run-3, ...).

For every task in the target vendor directory we:
  1. Find every run of that task across <vendor>/run-N/
  2. Load each run's filled histogram and align to the shared reference
  3. Compute per-bin mean and 1σ across replicates
  4. Plot the reference + mean ± 1σ band in a single grid cell

Two PDFs are written: one in `yield` view (raw counts), one in `shape` view
(unit-area normalized). Same task ordering across both files.

Usage:
    python -m utils.combine_eval_plots_replicates                     # codex_gpt-5.5
    python -m utils.combine_eval_plots_replicates runs/claude_haiku-4-5
    python -m utils.combine_eval_plots_replicates --rows 4 --cols 3
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from LHCRecastBench.Evals import histograms, score


def collect_replicates(vendor_dir: Path) -> dict[str, list[Path]]:
    """task_id → [run_dir, ...] across run-N subfolders."""
    out: dict[str, list[Path]] = defaultdict(list)
    for run_root in sorted(vendor_dir.glob("run-*")):
        if not run_root.is_dir():
            continue
        for run_dir in sorted(run_root.iterdir()):
            info = run_dir / "run_info.json"
            if not info.is_file():
                continue
            try:
                tid = json.loads(info.read_text() or "{}").get("task_id")
            except Exception:
                tid = None
            if tid:
                out[tid].append(run_dir)
    return out


def load_run(run_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str, str] | None:
    """Return (edges, ref, pred, x_name, x_units, paper) or None if not loadable."""
    try:
        paths = score.resolve_paths(run_dir)
    except Exception:
        return None
    ref_file = paths["reference_file"]
    pred_file = paths["results_dir"] / paths["data_filename"]
    if not ref_file.is_file() or not pred_file.is_file():
        return None
    try:
        ref_hist = histograms.load_histogram(ref_file)
        pred_hist = histograms.load_histogram(pred_file)
    except Exception:
        return None
    edges = histograms.bin_edges(ref_hist.bins)
    if len(edges) < 2:
        return None
    pred_by_name = {s.name: s for s in pred_hist.series}
    # Pick the first series that exists in both (typical: 1 series per task)
    for ref_s in ref_hist.series:
        pred_s = pred_by_name.get(ref_s.name)
        if pred_s is None:
            continue
        aligned = histograms.align(ref_s, pred_s, ref_hist.bins)
        return (
            edges,
            np.asarray(aligned.reference, dtype=float),
            np.asarray(aligned.prediction, dtype=float),
            ref_hist.x_name,
            ref_hist.x_units,
            paths.get("paper", ""),
        )
    return None


def plot_cell(ax, task_id: str, runs: list[Path], view: str, *, log_y: bool = False) -> bool:
    """Draw one panel. Returns True if a cell was rendered."""
    loaded = [load_run(r) for r in runs]
    loaded = [run_data for run_data in loaded if run_data is not None]
    if not loaded:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(task_id, fontsize=6)
        ax.axis("off")
        return False

    # Pick the first replicate with a non-trivial reference for the baseline.
    # (Different replicates can yield K=0 if the agent left every bin null.)
    baseline = next(
        (run_data for run_data in loaded if len(run_data[1]) > 0 and len(run_data[0]) >= 2), None
    )
    if baseline is None:
        ax.text(0.5, 0.5, "all replicates empty", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(task_id, fontsize=6)
        ax.axis("off")
        return False
    edges, ref0, _, x_name, x_units, _ = baseline
    K = len(ref0)

    # Drop replicates whose prediction doesn't align (e.g. agent picked a
    # different mass point so the series name didn't match).
    pred_arrays = [
        np.asarray(run_data[2], dtype=float) for run_data in loaded if len(run_data[2]) == K
    ]
    if not pred_arrays:
        ax.text(0.5, 0.5, "no aligned data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(task_id, fontsize=6)
        ax.axis("off")
        return False
    preds = np.stack(pred_arrays, axis=0)
    ref = ref0.astype(float)

    if view == "shape":
        ref_int = ref.sum()
        ref_p = ref / ref_int if ref_int > 0 else ref
        # Normalize each replicate to unit area
        preds_p = np.zeros_like(preds)
        for i, row in enumerate(preds):
            tot = row.sum()
            preds_p[i] = row / tot if tot > 0 else row
    else:  # yield
        ref_p = ref
        preds_p = preds

    # Reference: black step (thicker so it stays visible behind the replicas)
    ax.step(edges, np.r_[ref_p[0], ref_p], where="pre", color="black", lw=1.4, label="ref")

    # One line per replicate, distinct color from tab10 (skip black at index 7).
    cmap = plt.get_cmap("tab10")
    color_idx = [i for i in range(10) if i != 7]
    for i, row in enumerate(preds_p):
        ax.step(
            edges,
            np.r_[row[0], row],
            where="pre",
            color=cmap(color_idx[i % len(color_idx)]),
            lw=0.9,
            alpha=0.85,
            label=f"r{i+1}",
        )

    if log_y:
        # Log scale needs a strictly positive lower bound — use the smallest
        # non-zero across ref + every replica so all curves stay visible.
        positives = np.concatenate([ref_p[ref_p > 0]] + [row[row > 0] for row in preds_p])
        floor = float(positives.min()) * 0.5 if positives.size else 1e-3
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor)
    elif view == "shape":
        ax.set_ylim(bottom=0)
    ax.tick_params(axis="both", which="both", labelsize=5, pad=1)
    n = preds_p.shape[0]
    ax.set_title(f"{task_id}  (n={n})", fontsize=6, pad=2)
    return True


def build_pdf(
    target: Path,
    out_path: Path,
    view: str,
    *,
    nrows: int = 5,
    ncols: int = 4,
    page_size: tuple[float, float] = (8.5, 11.0),
    log_y: bool = False,
) -> None:
    reps = collect_replicates(target)
    # Filter to tasks that match the view's mode (e.g. shape-only tasks have no yield plot)
    items: list[tuple[str, list[Path]]] = []
    for tid in sorted(reps):
        runs = reps[tid]
        # Check first run's score_mode to decide if this view is applicable
        first = runs[0]
        sj = first / "eval" / "score.json"
        mode = None
        if sj.is_file():
            try:
                mode = json.loads(sj.read_text()).get("score_mode")
            except Exception:
                pass
        if mode == "shape" and view == "yield":
            continue
        if mode == "yield" and view == "shape":
            continue
        items.append((tid, runs))

    if not items:
        print(f"  no tasks for view={view} — skipping {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    per_page = nrows * ncols
    n_pages = (len(items) + per_page - 1) // per_page

    written: list[Path] = []
    for page_idx, page_start in enumerate(range(0, len(items), per_page)):
        chunk = items[page_start : page_start + per_page]
        fig, axes = plt.subplots(
            nrows, ncols, figsize=page_size, squeeze=False, constrained_layout=True
        )
        for idx, ax in enumerate(axes.flat):
            if idx < len(chunk):
                tid, runs = chunk[idx]
                plot_cell(ax, tid, runs, view, log_y=log_y)
            else:
                ax.axis("off")
        # Multi-page → suffix each PNG with _pN; single page → bare name.
        if n_pages > 1:
            page_path = out_path.with_name(f"{out_path.stem}_p{page_idx + 1}{out_path.suffix}")
        else:
            page_path = out_path
        fig.savefig(page_path, dpi=200)
        plt.close(fig)
        written.append(page_path)

    if n_pages == 1:
        print(f"  wrote {written[0]}  ({len(items)} tasks, {nrows}x{ncols})")
    else:
        print(
            f"  wrote {n_pages} files: {written[0]}..{written[-1]}  ({len(items)} tasks, {nrows}x{ncols})"
        )


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "target",
        nargs="?",
        default=str(repo_root / "runs" / "codex_gpt-5.5"),
        help="Vendor directory containing run-N subfolders (default: runs/codex_gpt-5.5).",
    )
    ap.add_argument("--out-dir", default=str(repo_root / "utils"))
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--no-log-yield", action="store_true", help="Use linear y on yield plots.")
    ap.add_argument("--log-shape", action="store_true", help="Use log y on shape plots.")
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if not target.is_dir():
        raise SystemExit(f"target not a directory: {target}")
    out_dir = Path(args.out_dir).resolve()
    stem = target.name + "_replicates"

    print(f"target={target}  layout={args.rows}x{args.cols}")
    build_pdf(
        target,
        out_dir / f"{stem}_shape.png",
        "shape",
        nrows=args.rows,
        ncols=args.cols,
        log_y=args.log_shape,
    )
    build_pdf(
        target,
        out_dir / f"{stem}_yield.png",
        "yield",
        nrows=args.rows,
        ncols=args.cols,
        log_y=not args.no_log_yield,
    )


if __name__ == "__main__":
    main()
