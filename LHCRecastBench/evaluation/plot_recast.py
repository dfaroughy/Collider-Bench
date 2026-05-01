#!/usr/bin/env python3
"""Histogram plots comparing CMS reference vs agent recast.

For each reference YAML (obs_high_ptmiss_distribution.yaml, etc.) produces
two figures:
    <stem>_yield.png   — event yields (raw bin contents)
    <stem>_shape.png   — unit-area normalised

Each figure has a main histogram panel with CMS truth filled, a hatched CMS
uncertainty band, and recast as a solid step, plus a ratio histogram sub-panel
showing recast / CMS.

Usage:
    python -m LHCRecastBench.evaluation.plot_recast <run_dir>
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
import mplhep as hep
import numpy as np
import yaml


plt.style.use(hep.style.CMS)

AXIS_LABEL_SIZE = (
    font_manager.FontProperties(size=plt.rcParams.get("axes.labelsize", 20)).get_size_in_points()
    / 2
)


def _extract_series(data: dict) -> list[dict]:
    """Return [{name, values, errors}] where errors are quadrature sums."""
    out = []
    for dep in data.get("dependent_variables", []):
        name = dep.get("header", {}).get("name", "unknown")
        vals, errs = [], []
        for entry in dep.get("values", []):
            vals.append(entry.get("value"))
            err_sq = 0.0
            has = False
            for e in entry.get("errors", []) or []:
                if "symerror" in e and e["symerror"] is not None:
                    err_sq += float(e["symerror"]) ** 2
                    has = True
                elif "asymerror" in e:
                    ae = e["asymerror"]
                    plus = abs(float(ae.get("plus", 0) or 0))
                    minus = abs(float(ae.get("minus", 0) or 0))
                    if plus or minus:
                        err_sq += max(plus, minus) ** 2
                        has = True
            errs.append(math.sqrt(err_sq) if has else None)
        out.append({"name": name, "values": vals, "errors": errs})
    return out


def _bin_edges(data: dict) -> tuple[np.ndarray, str, str]:
    """Return (edges, xlabel_name, xlabel_units) for the first indep. var."""
    for indep in data.get("independent_variables", []):
        name = indep.get("header", {}).get("name", "x")
        units = indep.get("header", {}).get("units", "")
        edges = []
        for entry in indep.get("values", []):
            if "low" in entry and "high" in entry:
                edges.append(float(entry["low"]))
                last = float(entry["high"])
            else:
                value = entry.get("value", len(edges))
                try:
                    edge = float(value)
                except (TypeError, ValueError):
                    edge = float(len(edges))
                edges.append(edge)
                last = edges[-1] + 1
        edges.append(last)
        return np.array(edges), name, units
    return np.array([]), "x", ""


def _align(ref_values, ref_errors, rec_values):
    """Convert parallel lists (with possible Nones) to aligned numpy arrays
    over the bins where both sides have a value. Returns (ref, ref_err, rec, mask)."""
    n = min(len(ref_values), len(rec_values))
    mask = np.array([ref_values[i] is not None and rec_values[i] is not None for i in range(n)])
    ref = np.array([ref_values[i] if mask[i] else 0.0 for i in range(n)], dtype=float)
    err = np.array(
        [ref_errors[i] if (mask[i] and ref_errors[i] is not None) else 0.0 for i in range(n)],
        dtype=float,
    )
    rec = np.array([rec_values[i] if mask[i] else 0.0 for i in range(n)], dtype=float)
    return ref, err, rec, mask


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
    # e.g. T5Wg_1600_100 → T5Wg(1600, 100)
    parts = name.split("_")
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
        return f"{'_'.join(parts[:-2])}({parts[-2]}, {parts[-1]})"
    return name


def _is_all_zero(arr: np.ndarray) -> bool:
    return bool(np.all(np.abs(arr) < 1e-12))


def _divide_by_width(vals: np.ndarray, edges: np.ndarray) -> np.ndarray:
    widths = np.diff(edges)
    return vals / widths


def _poisson_sigma(vals: np.ndarray) -> np.ndarray:
    """Poisson sigma for non-negative reference bin contents.

    For empty truth bins, sqrt(N) would give a zero-width visual band. Use
    the one-sided Garwood-style 68% upper interval for N=0 instead.
    This is plotting-only; scoring still uses the toy model in score.py.
    """
    clipped = np.clip(vals, 0.0, None)
    sigma = np.sqrt(clipped)
    return np.where(clipped == 0.0, 1.1394342831883648, sigma)


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
    """Draw a stepped uncertainty band around a histogram."""
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


def plot_table(
    ref_file: Path,
    recast_file: Path,
    out_dir: Path,
    arxiv_id: str,
    systematic_frac: float = 0.0,
    plot_mode: str = "Events/bin",
) -> list[Path]:
    """Produce <stem>_yield.png and <stem>_shape.png for one table."""
    if plot_mode not in {"Events/bin", "Events/GeV"}:
        raise ValueError(f"Unknown plot_mode {plot_mode!r}")

    # Templates (and the agent's filled output) are two YAML documents
    # (metadata + histogram); references are one. Pick the histogram doc
    # in either case.
    def _load_hist(path: Path) -> dict:
        with open(path) as f:
            docs = list(yaml.safe_load_all(f))
        return (
            next(
                (d for d in docs if isinstance(d, dict) and "dependent_variables" in d),
                None,
            )
            or {}
        )

    ref_data = _load_hist(ref_file)
    rec_data = _load_hist(recast_file)

    edges, xname, xunits = _bin_edges(ref_data)
    if len(edges) < 2:
        return []

    ref_series = {s["name"]: s for s in _extract_series(ref_data)}
    rec_series = {s["name"]: s for s in _extract_series(rec_data)}

    # Build aligned arrays per series, skipping all-zero series.
    series_plots = []
    for name, ref_s in ref_series.items():
        if name not in rec_series:
            continue
        rec_s = rec_series[name]
        ref, err, rec, mask = _align(ref_s["values"], ref_s["errors"], rec_s["values"])
        if _is_all_zero(ref) and _is_all_zero(rec):
            continue
        series_plots.append(
            {
                "name": name,
                "label": _series_label(name),
                "ref": ref,
                "err": err,
                "rec": rec,
                "mask": mask,
            }
        )

    if not series_plots:
        return []

    # Consistent colors per series, one color across both figures.
    cmap = plt.get_cmap("tab10")
    colors = {s["name"]: cmap(i % 10) for i, s in enumerate(series_plots)}
    # Data always black if present
    if any(s["name"] == "DATA" for s in series_plots):
        colors["DATA"] = "black"

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    xlabel = f"${xname}$ [{xunits}]" if xunits else f"${xname}$"

    for mode in ("yield", "shape"):
        fig = plt.figure(figsize=(9, 8))
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.08)
        ax = fig.add_subplot(gs[0])
        rx = fig.add_subplot(gs[1], sharex=ax)
        ratio_values = []
        ratio_band_drawn = False

        for s in series_plots:
            c = colors[s["name"]]
            ref = s["ref"].copy()
            rec = s["rec"].copy()
            err = s["err"].copy()

            if mode == "yield":
                if plot_mode == "Events/GeV":
                    ref_p = _divide_by_width(ref, edges)
                    rec_p = _divide_by_width(rec, edges)
                    stat_p = _divide_by_width(_poisson_sigma(ref), edges)
                else:
                    # Raw bin-integrated yields, matching the YAML values and scorer.
                    ref_p = ref
                    rec_p = rec
                    stat_p = _poisson_sigma(ref)
                sys_p = systematic_frac * ref_p
            else:
                # Unit-area shape as fraction per bin. Do not divide by bin width:
                # empty-bin Garwood bands are event-count limits, and showing
                # them as per-bin fractions avoids width-dependent visual bands.
                ref_int = ref.sum()
                rec_int = rec.sum()
                ref_p = ref / ref_int if ref_int > 0 else ref
                rec_p = rec / rec_int if rec_int > 0 else rec
                stat_p = _poisson_sigma(ref) / ref_int if ref_int > 0 else np.zeros_like(ref)
                sys_p = systematic_frac * ref_p

            # The visual band represents the same tolerance used in the
            # p-value toys, plus counting-statistical Poisson uncertainty.
            band_sigma = np.sqrt(sys_p**2 + stat_p**2)
            band_lower = np.clip(ref_p - band_sigma, 0.0, None)
            band_upper = ref_p + band_sigma

            # Skip series where the reference integrates to zero in this mode.
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
            band_label = "CMS stat. $\\oplus$ tol."
            if systematic_frac > 0:
                band_label = f"CMS stat. $\\oplus$ {100.0 * systematic_frac:g}% tol."
            _draw_uncertainty_band(
                ax,
                edges,
                band_lower,
                band_upper,
                color=c,
                label=band_label,
                zorder=2.5,
            )
            hep.histplot(
                rec_p,
                bins=edges,
                histtype="step",
                color=c,
                linestyle="-",
                label=f"{s['label']} (Recast)",
                ax=ax,
            )

            # Ratio: recast / CMS per bin
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(ref_p > 0, rec_p / ref_p, np.nan)
            finite_ratio = ratio[np.isfinite(ratio)]
            if finite_ratio.size:
                ratio_values.append(finite_ratio)
            hep.histplot(
                ratio,
                bins=edges,
                histtype="step",
                color=c,
                linestyle="-",
                linewidth=1,
                ax=rx,
            )
            if not ratio_band_drawn:
                with np.errstate(divide="ignore", invalid="ignore"):
                    rel_sigma = np.where(ref_p > 0, band_sigma / ref_p, np.nan)
                finite = rel_sigma[np.isfinite(rel_sigma)]
                if finite.size:
                    ratio_lower = np.clip(1.0 - rel_sigma, 0.0, None)
                    ratio_upper = 1.0 + rel_sigma
                    _draw_uncertainty_band(
                        rx,
                        edges,
                        ratio_lower,
                        ratio_upper,
                        color="gray",
                        label=None,
                        zorder=0.5,
                    )
                    ratio_band_drawn = True

        ax.set_yscale("log")
        if mode == "yield":
            ax.set_ylabel(plot_mode, fontsize=AXIS_LABEL_SIZE)
        else:
            ax.set_ylabel("Fraction / bin", fontsize=AXIS_LABEL_SIZE)
        ax.legend(loc="upper right", fontsize=12, frameon=False, ncol=1)
        ax.tick_params(labelbottom=False)
        # Slight headroom for the legend
        ymin, ymax = ax.get_ylim()
        ax.set_ylim(ymin, ymax * 5)

        rx.axhline(1.0, color="gray", linewidth=1, linestyle=":")
        if ratio_values:
            all_ratio = np.concatenate(ratio_values)
            ratio_band_values = []
            for s in series_plots:
                ref = s["ref"].copy()
                if mode == "yield":
                    if plot_mode == "Events/GeV":
                        ref_p = _divide_by_width(ref, edges)
                        stat_p = _divide_by_width(_poisson_sigma(ref), edges)
                    else:
                        ref_p = ref
                        stat_p = _poisson_sigma(ref)
                    sys_p = systematic_frac * ref_p
                else:
                    ref_int = ref.sum()
                    ref_p = ref / ref_int if ref_int > 0 else ref
                    stat_p = _poisson_sigma(ref) / ref_int if ref_int > 0 else np.zeros_like(ref)
                    sys_p = systematic_frac * ref_p
                band_sigma = np.sqrt(sys_p**2 + stat_p**2)
                with np.errstate(divide="ignore", invalid="ignore"):
                    rel_sigma = np.where(ref_p > 0, band_sigma / ref_p, np.nan)
                finite = rel_sigma[np.isfinite(rel_sigma)]
                if finite.size:
                    ratio_band_values.append(finite)
            if ratio_band_values:
                all_band = np.concatenate(ratio_band_values)
                band_lo = max(0.0, float(np.nanmin(1.0 - all_band)))
                band_hi = float(np.nanmax(1.0 + all_band))
            else:
                band_lo = max(0.0, 1.0 - systematic_frac)
                band_hi = 1.0 + systematic_frac
            lo = max(0.0, min(float(np.nanmin(all_ratio)) * 0.8, band_lo * 0.8))
            hi = max(2.0, float(np.nanmax(all_ratio)) * 1.2, band_hi * 1.2)
            if hi <= lo:
                hi = lo + 1.0
            rx.set_ylim(lo, hi)
        else:
            rx.set_ylim(0.2, 2.0)
        rx.set_ylabel("Recast / CMS", fontsize=AXIS_LABEL_SIZE)
        rx.set_xlabel(xlabel, fontsize=AXIS_LABEL_SIZE)
        rx.grid(True, alpha=0.3)

        out_path = out_dir / f"{ref_file.stem}_{mode}.png"
        fig.savefig(out_path, bbox_inches="tight", dpi=150)
        plt.close(fig)
        written.append(out_path)

    return written


def plot_recast(rp) -> dict:
    """Plot the one task histogram: agent result vs reference."""
    from ._resolve import RunPaths  # noqa: F401

    ref_path = rp.reference_file
    if not ref_path.is_file():
        return {"error": f"Reference missing: {ref_path}"}

    agent_path = rp.results_dir / rp.data_filename
    if not agent_path.is_file():
        return {"error": f"Agent output not found: {agent_path}"}

    plots_dir = rp.eval_dir / "plots"
    systematic_frac = getattr(rp, "systematic_pct", 0.0)
    plot_mode = getattr(rp, "plot_mode", "Events/bin")
    written = plot_table(
        ref_path,
        agent_path,
        plots_dir,
        rp.paper_ref,
        systematic_frac=systematic_frac,
        plot_mode=plot_mode,
    )
    return {
        "task_id": rp.task_id,
        "paper": rp.paper_ref,
        "reference": str(ref_path),
        "agent_output": str(agent_path),
        "systematic_pct": systematic_frac,
        "plot_mode": plot_mode,
        "files": [str(p) for p in written],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Plot reference vs agent histogram for a task run. "
        "Paths come from run_info.json + task.toml.",
    )
    parser.add_argument(
        "run_path",
        help="Run directory, workspace, iter dir, or results dir.",
    )
    args = parser.parse_args()

    from ._resolve import resolve_run

    try:
        rp = resolve_run(args.run_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return

    result = plot_recast(rp)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return
    print(f"\n  Plot output: {result['task_id']} (paper={result['paper']})")
    for f in result.get("files", []):
        print(f"    {f}")
    print()


if __name__ == "__main__":
    main()
