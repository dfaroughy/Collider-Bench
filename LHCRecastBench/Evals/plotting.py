"""CMS-style comparison plots: reference vs agent recast.

Produces two PNGs per histogram:
    <stem>_yield.png   — bin contents (Events/bin or Events/GeV)
    <stem>_shape.png   — unit-area-normalized fractions per bin

Each figure has a top panel (CMS as filled, Recast as step, hatched
uncertainty band combining task tolerance + Poisson stat) and a
ratio sub-panel of `Recast / CMS`.

The actual plotting logic is lifted from the previous evaluation/plot_recast.py.
We dropped the YAML-parsing duplicates from that file in favor of histograms.py.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import mplhep as hep
import numpy as np
from matplotlib import font_manager

from . import histograms


plt.style.use(hep.style.CMS)


_AXIS_LABEL_SIZE = (
    font_manager.FontProperties(size=plt.rcParams.get("axes.labelsize", 20)).get_size_in_points()
    / 2
)


def _series_label(name: str) -> str:
    """Turn a HEPData series name into a legend-friendly label."""
    mapping = {
        "DATA": "Data",
        "TOTAL_BKG": "Total bkg",
        "BACKGROUND": "Background",
        "IRREDUCIBLE_BKG": "Irreducible bkg",
    }
    if name in mapping:
        return mapping[name]
    parts = name.split("_")
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
        return f"{'_'.join(parts[:-2])}({parts[-2]}, {parts[-1]})"
    return name


def _is_all_zero(arr: np.ndarray) -> bool:
    return bool(np.all(np.abs(arr) < 1e-12))


def _divide_by_width(vals: np.ndarray, edges: np.ndarray) -> np.ndarray:
    return vals / np.diff(edges)


def _draw_uncertainty_band(
    ax,
    edges: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    color,
    label: str | None = None,
    zorder: float = 2.0,
) -> None:
    """Draw a stepped hatched uncertainty band around a histogram."""
    if lower.size == 0 or upper.size == 0:
        return
    if not np.any(np.isfinite(upper)) or np.all(upper <= 0):
        return
    ax.fill_between(
        edges,
        np.r_[lower, lower[-1]],
        np.r_[upper, upper[-1]],
        step="post",
        facecolor="none",
        edgecolor=color,
        linewidth=0.0,
        hatch="////",
        alpha=0.8,
        label=label,
        zorder=zorder,
    )


def plot_comparison(
    ref_file: Path,
    pred_file: Path,
    out_dir: Path,
    *,
    tolerance: float = 0.0,
    plot_mode: str = "Events/bin",
    which: tuple[str, ...] = ("yield", "shape"),
) -> list[Path]:
    """Render comparison PNGs (ref vs prediction).

    `which` selects the variants to render:
      - ("yield", "shape") — both, default.
      - ("shape",)         — shape-only tasks: yield plot would be misleading.
      - ("yield",)         — yield-only tasks: shape plot would be redundant.
    """
    if plot_mode not in {"Events/bin", "Events/GeV"}:
        raise ValueError(f"Unknown plot_mode {plot_mode!r}")
    valid = {"yield", "shape"}
    bad = set(which) - valid
    if bad:
        raise ValueError(f"`which` must be a subset of {sorted(valid)}; got {sorted(bad)}")

    ref_hist = histograms.load_histogram(ref_file)
    pred_hist = histograms.load_histogram(pred_file)

    edges = histograms.bin_edges(ref_hist.bins)
    if len(edges) < 2:
        return []

    pred_by_name = {s.name: s for s in pred_hist.series}

    # Build aligned numpy arrays per series, skipping all-zero series.
    series_plots = []
    for ref_s in ref_hist.series:
        pred_s = pred_by_name.get(ref_s.name)
        if pred_s is None:
            continue
        aligned = histograms.align(ref_s, pred_s, ref_hist.bins)
        if _is_all_zero(aligned.reference) and _is_all_zero(aligned.prediction):
            continue
        series_plots.append(
            {
                "name": ref_s.name,
                "label": _series_label(ref_s.name),
                "ref": aligned.reference,
                "rec": aligned.prediction,
            }
        )

    if not series_plots:
        return []

    cmap = plt.get_cmap("tab10")
    colors = {s["name"]: cmap(i % 10) for i, s in enumerate(series_plots)}
    if any(s["name"] == "DATA" for s in series_plots):
        colors["DATA"] = "black"

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    xlabel = (
        f"${ref_hist.x_name}$ [{ref_hist.x_units}]" if ref_hist.x_units else f"${ref_hist.x_name}$"
    )

    for mode in ("yield", "shape"):
        if mode not in which:
            continue
        fig = plt.figure(figsize=(9, 8))
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.08)
        ax = fig.add_subplot(gs[0])
        rx = fig.add_subplot(gs[1], sharex=ax)
        ratio_values: list[np.ndarray] = []
        ratio_band_drawn = False

        for s in series_plots:
            c = colors[s["name"]]
            ref = s["ref"].copy()
            rec = s["rec"].copy()

            if mode == "yield":
                if plot_mode == "Events/GeV":
                    ref_p = _divide_by_width(ref, edges)
                    rec_p = _divide_by_width(rec, edges)
                else:
                    ref_p, rec_p = ref, rec
            else:
                # Unit-area shape; do not divide by bin width.
                ref_int = ref.sum()
                rec_int = rec.sum()
                ref_p = ref / ref_int if ref_int > 0 else ref
                rec_p = rec / rec_int if rec_int > 0 else rec

            # Band shows tolerance-only uncertainty per bin: ±tolerance·ref.
            # No Poisson stat component — the metrics already account for
            # bin-by-bin agreement; the band is just the task-declared
            # tolerance the agent should fall within.
            band_sigma = tolerance * ref_p
            band_lower = np.clip(ref_p - band_sigma, 0.0, None)
            band_upper = ref_p + band_sigma

            if np.all(ref_p == 0) and np.all(rec_p == 0):
                continue

            hep.histplot(
                ref_p,
                bins=edges,
                histtype="fill",
                color=c,
                alpha=0.5,
                edgecolor="none",
                label=f"{s['label']} (CMS)",
                ax=ax,
            )
            band_label = f"$\\pm$ {100.0 * tolerance:g}% tol." if tolerance > 0 else "tolerance"
            _draw_uncertainty_band(
                ax, edges, band_lower, band_upper, color=c, label=band_label, zorder=2.5
            )
            hep.histplot(
                rec_p,
                bins=edges,
                histtype="step",
                color=c,
                linestyle="-",
                linewidth=2.5,
                label=f"{s['label']} (Recast)",
                ax=ax,
            )

            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(ref_p > 0, rec_p / ref_p, np.nan)
            finite_ratio = ratio[np.isfinite(ratio)]
            if finite_ratio.size:
                ratio_values.append(finite_ratio)
            hep.histplot(
                ratio, bins=edges, histtype="step", color=c, linestyle="-", linewidth=2, ax=rx
            )

            if not ratio_band_drawn:
                with np.errstate(divide="ignore", invalid="ignore"):
                    rel_sigma = np.where(ref_p > 0, band_sigma / ref_p, np.nan)
                if np.isfinite(rel_sigma).any():
                    _draw_uncertainty_band(
                        rx,
                        edges,
                        np.clip(1.0 - rel_sigma, 0.0, None),
                        1.0 + rel_sigma,
                        color="gray",
                        label=None,
                        zorder=0.5,
                    )
                    ratio_band_drawn = True

        ax.set_yscale("log")
        ax.set_ylabel(plot_mode if mode == "yield" else "Fraction / bin", fontsize=_AXIS_LABEL_SIZE)
        ax.legend(loc="upper right", fontsize=12, frameon=False, ncol=1)
        ax.tick_params(labelbottom=False)
        ymin, ymax = ax.get_ylim()
        ax.set_ylim(ymin, ymax * 5)

        rx.axhline(1.0, color="gray", linewidth=1, linestyle=":")
        if ratio_values:
            all_ratio = np.concatenate(ratio_values)
            rx.set_ylim(
                max(0.0, float(np.nanmin(all_ratio)) * 0.8),
                max(2.0, float(np.nanmax(all_ratio)) * 1.2),
            )
        else:
            rx.set_ylim(0.2, 2.0)
        rx.set_ylabel("Recast / CMS", fontsize=_AXIS_LABEL_SIZE)
        rx.set_xlabel(xlabel, fontsize=_AXIS_LABEL_SIZE)
        rx.grid(True, alpha=0.3)

        out_path = out_dir / f"{ref_file.stem}_{mode}.png"
        fig.savefig(out_path, bbox_inches="tight", dpi=150)
        plt.close(fig)
        written.append(out_path)

    return written
