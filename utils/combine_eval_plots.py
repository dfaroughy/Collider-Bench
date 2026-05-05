#!/usr/bin/env python3
"""Bundle every run's eval plots into two PDFs (yield + shape) for cross-task comparison.

Plots are laid out as a paper-style grid (default 5 rows × 4 cols = 20 per page,
US-Letter portrait). Each cell is titled with the task_id. Yield and shape PDFs
share the same ordering, so you can flip between them and compare like-for-like.

Usage:
    python -m utils.combine_eval_plots                                # codex_gpt-5.5/run-1
    python -m utils.combine_eval_plots runs/claude_haiku-4-5/run-2
    python -m utils.combine_eval_plots --rows 4 --cols 3 --out-dir /tmp
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image


def collect(target: Path) -> list[tuple[str, str, Path]]:
    """Return [(task_id, run_name, plot_path), ...] for every eval/plots/*.png."""
    rows: list[tuple[str, str, Path]] = []
    for run_dir in sorted(target.iterdir()):
        if not run_dir.is_dir():
            continue
        info_path = run_dir / "run_info.json"
        if not info_path.is_file():
            continue
        try:
            task_id = json.loads(info_path.read_text() or "{}").get("task_id", run_dir.name)
        except Exception:
            task_id = run_dir.name
        plots_dir = run_dir / "eval" / "plots"
        if not plots_dir.is_dir():
            continue
        for png in sorted(plots_dir.glob("*.png")):
            rows.append((task_id, run_dir.name, png))
    return rows


def build_pdf(
    rows: list[tuple[str, str, Path]],
    out_path: Path,
    kind: str,
    *,
    nrows: int = 5,
    ncols: int = 4,
    page_size: tuple[float, float] = (8.5, 11.0),  # US-Letter, inches, portrait
) -> None:
    """Write one PDF, paginating into `nrows × ncols` grids."""
    suffix = f"_{kind}.png"
    selected = [(tid, rn, p) for tid, rn, p in rows if p.name.endswith(suffix)]
    selected.sort(key=lambda x: (x[0], x[1]))

    if not selected:
        print(f"  no {kind} plots found — skipping {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    per_page = nrows * ncols

    with PdfPages(out_path) as pdf:
        for page_start in range(0, len(selected), per_page):
            chunk = selected[page_start : page_start + per_page]
            fig, axes = plt.subplots(
                nrows, ncols, figsize=page_size, squeeze=False, constrained_layout=True
            )
            for idx, ax in enumerate(axes.flat):
                if idx < len(chunk):
                    task_id, run_name, png = chunk[idx]
                    img = Image.open(png)
                    ax.imshow(img)
                    short = run_name.split("_")[-2] if "_" in run_name else run_name
                    ax.set_title(f"{task_id}\n[{short}]", fontsize=6, pad=2)
                ax.axis("off")
            pdf.savefig(fig, dpi=200)
            plt.close(fig)

    n_pages = (len(selected) + per_page - 1) // per_page
    print(f"  wrote {out_path}  ({len(selected)} plots over {n_pages} page(s), {nrows}x{ncols})")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "target",
        nargs="?",
        default=str(repo_root / "runs" / "codex_gpt-5.5" / "run-1"),
        help="Directory containing per-task subdirectories (default: runs/codex_gpt-5.5/run-1).",
    )
    ap.add_argument(
        "--out-dir",
        default=str(repo_root / "utils"),
        help="Output directory for the two PDFs (default: utils/).",
    )
    ap.add_argument("--rows", type=int, default=5, help="Plots per page (rows).")
    ap.add_argument("--cols", type=int, default=4, help="Plots per page (cols).")
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if not target.is_dir():
        raise SystemExit(f"target not a directory: {target}")
    out_dir = Path(args.out_dir).resolve()

    # Build a stem from the target path: e.g. "codex_gpt-5.5_run-1"
    stem_parts = []
    if target.parent.name == "runs":
        stem_parts.append(target.name)  # e.g. just one level deep
    else:
        stem_parts.extend([target.parent.name, target.name])
    stem = "_".join(stem_parts) or "eval"

    rows = collect(target)
    if not rows:
        raise SystemExit(f"no plots under {target}/<run>/eval/plots/")

    print(f"target={target}  total plots={len(rows)}  layout={args.rows}x{args.cols}")
    build_pdf(rows, out_dir / f"{stem}_shape.pdf", "shape", nrows=args.rows, ncols=args.cols)
    build_pdf(rows, out_dir / f"{stem}_yield.pdf", "yield", nrows=args.rows, ncols=args.cols)


if __name__ == "__main__":
    main()
