#!/usr/bin/env python3
"""Histogram plots comparing CMS reference vs agent recast.

For each reference YAML (obs_high_ptmiss_distribution.yaml, etc.) produces
two figures:
    <stem>_yield.png   — event yields (Events / GeV)
    <stem>_shape.png   — unit-area normalised

Each figure has a main step-histogram panel with CMS solid + Recast dashed
per series, and a ratio sub-panel showing recast / CMS.

Usage:
    python -m LHCRecastBench.evaluation.plot_recast \
        --arxiv 1707.06193 \
        --recast-dir <workspace>/HEPRecastData
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import mplhep as hep
import numpy as np
import yaml


PAPERS_DIR = Path(__file__).resolve().parent.parent / "papers"

plt.style.use(hep.style.CMS)


def _reference_dir(arxiv_id: str) -> Path:
    return PAPERS_DIR / arxiv_id / "artifacts" / "HEPRecastData"


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
                edges.append(float(entry.get("value", len(edges))))
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


def plot_table(
    ref_file: Path,
    recast_file: Path,
    out_dir: Path,
    arxiv_id: str,
) -> list[Path]:
    """Produce <stem>_yield.png and <stem>_shape.png for one table."""
    with open(ref_file) as f:
        ref_data = yaml.safe_load(f)
    with open(recast_file) as f:
        rec_data = yaml.safe_load(f)

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

        for s in series_plots:
            c = colors[s["name"]]
            ref = s["ref"].copy()
            rec = s["rec"].copy()
            err = s["err"].copy()

            if mode == "yield":
                # Events / GeV: divide counts by bin width
                ref_p = _divide_by_width(ref, edges)
                rec_p = _divide_by_width(rec, edges)
                err_p = _divide_by_width(err, edges)
            else:
                # unit area: divide by integral of (counts)
                ref_int = ref.sum()
                rec_int = rec.sum()
                ref_p = _divide_by_width(ref / ref_int, edges) if ref_int > 0 else ref
                rec_p = _divide_by_width(rec / rec_int, edges) if rec_int > 0 else rec
                err_p = _divide_by_width(err / ref_int, edges) if ref_int > 0 else err

            # Skip series where the reference integrates to zero in this mode.
            if np.all(ref_p == 0) and np.all(rec_p == 0):
                continue

            hep.histplot(
                ref_p,
                bins=edges,
                yerr=err_p if np.any(err_p > 0) else False,
                histtype="step",
                color=c,
                linestyle="-",
                label=f"{s['label']} (CMS)",
                ax=ax,
            )
            hep.histplot(
                rec_p,
                bins=edges,
                histtype="step",
                color=c,
                linestyle="--",
                label=f"{s['label']} (Recast)",
                ax=ax,
            )

            # Ratio: recast / CMS per bin, propagated error
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(ref_p > 0, rec_p / ref_p, np.nan)
                ratio_err = np.where(ref_p > 0, err_p / ref_p, np.nan)
            rx.errorbar(
                0.5 * (edges[:-1] + edges[1:]),
                ratio,
                yerr=ratio_err,
                fmt="o",
                color=c,
                markersize=4,
                linewidth=1,
                capsize=0,
            )

        ax.set_yscale("log")
        if mode == "yield":
            ax.set_ylabel(f"Events / {xunits}" if xunits else "Events")
        else:
            # dN/dx / N (unit integral)
            denom = f"d{xname}" if not xunits else f"d{xname}\\;[{xunits}]^{{-1}}"
            ax.set_ylabel(f"$(1/N)\\;dN/{denom}$")
        ax.legend(loc="upper right", fontsize=12, frameon=False, ncol=1)
        ax.tick_params(labelbottom=False)
        # Slight headroom for the legend
        ymin, ymax = ax.get_ylim()
        ax.set_ylim(ymin, ymax * 5)

        rx.axhline(1.0, color="gray", linewidth=1, linestyle=":")
        rx.set_ylim(0.2, 2.0)
        rx.set_ylabel("Recast / CMS")
        rx.set_xlabel(xlabel)
        rx.grid(True, alpha=0.3)

        out_path = out_dir / f"{ref_file.stem}_{mode}.png"
        fig.savefig(out_path, bbox_inches="tight", dpi=150)
        plt.close(fig)
        written.append(out_path)

    return written


def plot_recast(arxiv_id: str, recast_dir: str) -> dict:
    ref_dir = _reference_dir(arxiv_id)
    recast_path = Path(recast_dir)

    if not ref_dir.exists():
        return {"error": f"No reference for {arxiv_id}"}
    if not recast_path.exists():
        return {"error": f"Recast dir not found: {recast_dir}"}

    # Plots land next to the other eval outputs: <run_dir>/eval/plots/
    # (sibling of workspace/, not inside it)
    eval_dir = recast_path.parent.parent / "eval" / "plots"

    results = {"paper": arxiv_id, "recast_dir": str(recast_dir), "plots": []}

    for ref_file in sorted(ref_dir.glob("*.yaml")):
        if ref_file.name in ("submission.yaml", "description.yaml"):
            continue
        recast_file = recast_path / ref_file.name
        if not recast_file.exists():
            results["plots"].append({"table": ref_file.stem, "error": "missing in recast"})
            continue
        written = plot_table(ref_file, recast_file, eval_dir, arxiv_id)
        results["plots"].append(
            {
                "table": ref_file.stem,
                "files": [str(p) for p in written],
            }
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Plot CMS vs recast distributions.")
    parser.add_argument("--arxiv", required=True)
    parser.add_argument("--recast-dir", required=True, help="Path to filled HEPRecastData/")
    args = parser.parse_args()

    result = plot_recast(args.arxiv, args.recast_dir)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return
    print(f"\n  Plot output: {result['paper']}")
    for p in result["plots"]:
        if "error" in p:
            print(f"    {p['table']}: {p['error']}")
        else:
            print(f"    {p['table']}: {len(p['files'])} file(s)")
            for f in p["files"]:
                print(f"      {f}")
    print()


if __name__ == "__main__":
    main()
