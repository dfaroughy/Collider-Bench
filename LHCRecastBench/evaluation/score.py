#!/usr/bin/env python3
"""Unified scorer for recast results.

Compares filled HEPRecastData/*.yaml against the reference in
LHCRecastBench/papers/{arxiv}/artifacts/HEPRecastData/ and emits:

  Per-bin metrics (how close is each prediction?)
    - pull = (recast - ref) / ref_err
    - rel_diff = |recast - ref| / |ref|
    - pass iff |pull| < 2 OR rel_diff < 50%  (OR handles large errors AND fractional yields)
    - overall_score = n_pass / n_filled
    - overall_pass  = overall_score >= 0.5

  Shape/normalization decomposition (is it the right shape? the right total?)
    - shape_chi2      : chi^2/bin after normalizing both distributions to unit area
    - shape_score     : exp(-chi2/2) ∈ [0, 1]
    - ks              : Kolmogorov-Smirnov on binned CDFs
    - norm_ratio      : sum(recast) / sum(ref)
    - norm_score      : 1 - |log10(ratio)|  clipped to [0, 1]
    - combined        : sqrt(shape_score * norm_score)
    - diagnosis       : "GOOD" | "SHAPE OK, NORM BAD" | "SHAPE BAD, NORM OK" | "BOTH BAD"

All of this lives in a single JSON written to <run_dir>/eval/score.json
(sibling of <run_dir>/workspace/).

Usage:
    python -m LHCRecastBench.evaluation.score 1707.06193 --recast-dir <ws>/HEPRecastData
    python -m LHCRecastBench.evaluation.score 1707.06193 --compare <ws1>/HEPRecastData <ws2>/HEPRecastData
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml


PAPERS_DIR = Path(__file__).resolve().parent.parent / "papers"


# ── Loading ─────────────────────────────────────────────────────────────────

def _reference_dir(arxiv_id: str) -> Path:
    return PAPERS_DIR / arxiv_id / "artifacts" / "HEPRecastData"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _extract_values(data: dict) -> list[dict]:
    """Extract dependent_variables as [{name, values, errors}].

    Errors sum all symerror/asymerror entries in quadrature (standard for
    uncorrelated uncertainties). `errors[i]` is None when no error was stated.
    """
    result = []
    for dep in data.get("dependent_variables", []):
        name = dep.get("header", {}).get("name", "unknown")
        values: list = []
        errors: list = []
        for entry in dep.get("values", []):
            values.append(entry.get("value"))
            err_sq = 0.0
            has_err = False
            for e in entry.get("errors", []) or []:
                if "symerror" in e and e["symerror"] is not None:
                    err_sq += float(e["symerror"]) ** 2
                    has_err = True
                elif "asymerror" in e:
                    ae = e["asymerror"]
                    plus = abs(float(ae.get("plus", 0) or 0))
                    minus = abs(float(ae.get("minus", 0) or 0))
                    if plus or minus:
                        err_sq += max(plus, minus) ** 2
                        has_err = True
            errors.append(math.sqrt(err_sq) if has_err else None)
        result.append({"name": name, "values": values, "errors": errors})
    return result


def _extract_bins(data: dict) -> list[dict]:
    """Extract independent_variables as [{name, units, bins}]."""
    result = []
    for indep in data.get("independent_variables", []):
        name = indep.get("header", {}).get("name", "unknown")
        units = indep.get("header", {}).get("units", "")
        bins = []
        for entry in indep.get("values", []):
            if "low" in entry and "high" in entry:
                bins.append(f"{entry['low']}-{entry['high']}")
            else:
                bins.append(str(entry.get("value", "?")))
        result.append({"name": name, "units": units, "bins": bins})
    return result


# ── Shape & normalization ───────────────────────────────────────────────────

def shape_chi2(
    observed: np.ndarray,
    reference: np.ndarray,
    ref_errors: np.ndarray | None = None,
) -> tuple[float, float]:
    """Unit-area chi^2 per bin + score = exp(-chi2/2)."""
    obs_sum = float(np.sum(observed))
    ref_sum = float(np.sum(reference))
    if obs_sum == 0 or ref_sum == 0:
        return float("inf"), 0.0

    obs_norm = observed / obs_sum
    ref_norm = reference / ref_sum
    n_bins = len(reference)
    if ref_errors is None:
        ref_errors = np.zeros(n_bins)

    chi2 = 0.0
    for i in range(n_bins):
        var_poiss = ref_norm[i] / ref_sum
        var_sys = (ref_errors[i] / ref_sum) ** 2 if ref_sum > 0 else 0.0
        sigma2 = var_poiss + var_sys + 1e-10
        chi2 += (obs_norm[i] - ref_norm[i]) ** 2 / sigma2

    chi2_per_bin = chi2 / n_bins
    score = math.exp(-chi2_per_bin / 2.0)
    return chi2_per_bin, score


def kolmogorov_smirnov(observed: np.ndarray, reference: np.ndarray) -> float:
    """KS statistic on binned CDFs."""
    obs_sum = float(np.sum(observed))
    ref_sum = float(np.sum(reference))
    if obs_sum == 0 or ref_sum == 0:
        return 1.0
    obs_cdf = np.cumsum(observed) / obs_sum
    ref_cdf = np.cumsum(reference) / ref_sum
    return float(np.max(np.abs(obs_cdf - ref_cdf)))


def normalization_ratio(observed: np.ndarray, reference: np.ndarray) -> tuple[float, float]:
    """sum(obs)/sum(ref) + score = 1 - |log10(ratio)| clipped to [0,1]."""
    obs_total = float(np.sum(observed))
    ref_total = float(np.sum(reference))
    if ref_total == 0:
        return float("inf"), 0.0
    ratio = obs_total / ref_total
    if ratio <= 0:
        return 0.0, 0.0
    score = max(0.0, 1.0 - abs(math.log10(ratio)))
    return ratio, score


# ── Scoring ─────────────────────────────────────────────────────────────────

def _as_float(x) -> float | None:
    """Coerce to float, returning None for non-numeric values (e.g. LaTeX upper-limit strings)."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _score_series(
    name: str,
    ref_vals: list,
    rec_vals: list,
    ref_errs: list,
    bins: list[dict],
) -> dict:
    """Score one dependent-variable series end-to-end."""
    n_bins = len(ref_vals)
    series: dict = {
        "name": name,
        "n_bins": n_bins,
        "n_filled": 0,
        "n_pass": 0,
        "bins": [],
    }

    chi2 = 0.0
    n_scored = 0
    for i in range(n_bins):
        ref_val_raw = ref_vals[i]
        rec_val_raw = rec_vals[i] if i < len(rec_vals) else None
        ref_err = ref_errs[i]

        parts = [iv["bins"][i] for iv in bins if i < len(iv.get("bins", []))]
        bin_label = "|".join(parts) if parts else str(i)
        bin_result = {
            "bin": bin_label,
            "reference": ref_val_raw,
            "recast": rec_val_raw,
            "error": ref_err,
        }

        if rec_val_raw is None:
            bin_result["status"] = "MISSING"
            series["bins"].append(bin_result)
            continue

        series["n_filled"] += 1

        ref_val = _as_float(ref_val_raw)
        rec_val = _as_float(rec_val_raw)
        if ref_val is None or rec_val is None:
            # Non-numeric (e.g. LaTeX upper-limit strings like '$<0.1$') or no ref.
            bin_result["pull"] = None
            bin_result["rel_diff"] = None
            bin_result["status"] = "NO_REF"
            series["bins"].append(bin_result)
            continue

        if ref_val == 0:
            # Zero-expectation bin: recast must also be ~0.
            bin_result["pull"] = None
            bin_result["rel_diff"] = None
            if abs(rec_val) < 1e-6:
                bin_result["status"] = "PASS"
                series["n_pass"] += 1
            else:
                bin_result["status"] = "FAIL"
            series["bins"].append(bin_result)
            continue

        total_err = float(ref_err) if ref_err is not None else math.sqrt(abs(ref_val))
        pull = (rec_val - ref_val) / total_err if total_err > 0 else 0.0
        rel_diff = abs(rec_val - ref_val) / abs(ref_val)
        passes = abs(pull) < 2.0 or rel_diff < 0.5

        chi2 += pull ** 2
        n_scored += 1
        if passes:
            series["n_pass"] += 1

        bin_result["pull"] = round(pull, 2)
        bin_result["rel_diff"] = round(rel_diff, 3)
        bin_result["status"] = "PASS" if passes else "FAIL"
        series["bins"].append(bin_result)

    if n_scored > 0:
        series["chi2_per_bin"] = round(chi2 / n_scored, 2)
        series["score"] = round(series["n_pass"] / n_scored, 3)
    else:
        series["chi2_per_bin"] = None
        series["score"] = 0.0

    # Shape/normalization decomposition on the aligned (both-numeric) subset.
    aligned = []
    for i in range(min(len(ref_vals), len(rec_vals))):
        rv, cv = _as_float(ref_vals[i]), _as_float(rec_vals[i])
        if rv is None or cv is None:
            continue
        err = ref_errs[i] if ref_errs[i] is not None else 0.0
        aligned.append((rv, cv, err))
    if aligned:
        ref_arr = np.array([p[0] for p in aligned], dtype=float)
        rec_arr = np.array([p[1] for p in aligned], dtype=float)
        err_arr = np.array([p[2] for p in aligned], dtype=float)

        s_chi2, s_score = shape_chi2(rec_arr, ref_arr, err_arr)
        ks = kolmogorov_smirnov(rec_arr, ref_arr)
        n_ratio, n_score = normalization_ratio(rec_arr, ref_arr)
        combined = math.sqrt(s_score * n_score) if s_score > 0 and n_score > 0 else 0.0
        if s_score > 0.7 and n_score > 0.7:
            diagnosis = "GOOD"
        elif s_score > 0.7:
            diagnosis = "SHAPE OK, NORM BAD"
        elif n_score > 0.7:
            diagnosis = "SHAPE BAD, NORM OK"
        else:
            diagnosis = "BOTH BAD"

        series["shape"] = {
            "chi2_per_bin": round(s_chi2, 3),
            "score": round(s_score, 3),
            "ks": round(ks, 3),
        }
        series["normalization"] = {"ratio": round(n_ratio, 3), "score": round(n_score, 3)}
        series["combined"] = round(combined, 3)
        series["diagnosis"] = diagnosis

    return series


def _score_table(ref_data: dict, recast_data: dict, table_name: str) -> dict:
    """Compare one HEPData table."""
    ref_series = _extract_values(ref_data)
    recast_series = _extract_values(recast_data)
    bins = _extract_bins(ref_data)

    result = {
        "table": table_name,
        "bins": bins,
        "series": [],
        "n_filled": 0,
        "n_total": 0,
        "n_pass": 0,
    }

    for ref_s in ref_series:
        rec_s = next((r for r in recast_series if r["name"] == ref_s["name"]), None)
        if rec_s is None:
            result["series"].append({
                "name": ref_s["name"],
                "error": f"Series '{ref_s['name']}' not found in recast",
            })
            continue
        series = _score_series(
            ref_s["name"], ref_s["values"], rec_s["values"], ref_s["errors"], bins,
        )
        result["series"].append(series)
        result["n_total"]  += series["n_bins"]
        result["n_filled"] += series["n_filled"]
        result["n_pass"]   += series["n_pass"]

    result["overall_score"] = (
        round(result["n_pass"] / result["n_filled"], 3) if result["n_filled"] else 0.0
    )
    return result


def score_recast(arxiv_id: str, recast_dir: str) -> dict:
    """Score all tables for a paper — per-bin metrics + shape/norm decomposition."""
    ref_dir = _reference_dir(arxiv_id)
    recast_path = Path(recast_dir)

    if not ref_dir.exists():
        available = [d.name for d in PAPERS_DIR.iterdir() if d.is_dir()]
        return {"error": f"No reference for {arxiv_id}. Available: {available}"}
    if not recast_path.exists():
        return {"error": f"Recast directory not found: {recast_dir}"}

    output = {
        "paper": arxiv_id,
        "recast_dir": str(recast_dir),
        "tables": [],
        "n_total": 0,
        "n_filled": 0,
        "n_pass": 0,
    }

    shape_scores: list[float] = []
    norm_scores:  list[float] = []

    for ref_file in sorted(ref_dir.glob("*.yaml")):
        if ref_file.name in ("submission.yaml", "description.yaml"):
            continue
        recast_file = recast_path / ref_file.name
        if not recast_file.exists():
            output["tables"].append({
                "table": ref_file.stem,
                "error": f"Not found in recast: {ref_file.name}",
            })
            continue
        table = _score_table(_load_yaml(ref_file), _load_yaml(recast_file), ref_file.stem)
        output["tables"].append(table)
        output["n_total"]  += table["n_total"]
        output["n_filled"] += table["n_filled"]
        output["n_pass"]   += table["n_pass"]
        for s in table["series"]:
            if "shape" in s:
                shape_scores.append(s["shape"]["score"])
                norm_scores.append(s["normalization"]["score"])

    if output["n_filled"] > 0:
        output["overall_score"] = round(output["n_pass"] / output["n_filled"], 3)
        output["overall_pass"] = output["overall_score"] >= 0.5
    else:
        output["overall_score"] = 0.0
        output["overall_pass"] = False

    if shape_scores:
        output["overall_shape"] = round(float(np.mean(shape_scores)), 3)
        output["overall_normalization"] = round(float(np.mean(norm_scores)), 3)
        output["overall_combined"] = round(
            math.sqrt(output["overall_shape"] * output["overall_normalization"]), 3
        )

    return output


# ── Display ─────────────────────────────────────────────────────────────────

def print_scores(result: dict) -> None:
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    print(f"\n  Recast score: {result['paper']}")
    print(f"  {'=' * 68}")

    for table in result["tables"]:
        if "error" in table:
            print(f"\n  {table['table']}: {table['error']}")
            continue

        print(f"\n  {table['table']}")
        for s in table.get("series", []):
            if "error" in s:
                print(f"    {s['name']}: {s['error']}")
                continue

            n_pass = s["n_pass"]; n_filled = s["n_filled"]
            score = s.get("score", 0); chi2 = s.get("chi2_per_bin", "—")
            extra = ""
            if "shape" in s:
                extra = (f"  shape={s['shape']['score']:.2f}  "
                         f"norm={s['normalization']['score']:.2f}  [{s['diagnosis']}]")
            print(f"    {s['name']}: {n_pass}/{n_filled} pass ({score:.0%}), "
                  f"chi2/bin={chi2}{extra}")
            print(f"    {'─' * 62}")
            print(f"    {'Bin':<20s} {'Recast':>10s} {'CMS':>10s} {'Pull':>7s} {'Rel%':>6s} {'':>5s}")
            print(f"    {'─' * 62}")
            for b in s["bins"]:
                rec = f"{b['recast']:.2f}" if b["recast"] is not None else "null"
                ref = f"{b['reference']}" if b["reference"] is not None else "null"
                pull = f"{b['pull']:+.2f}" if b.get("pull") is not None else "—"
                rel = f"{b['rel_diff']:.0%}" if b.get("rel_diff") is not None else "—"
                status = b.get("status", "?")
                print(f"    {b['bin']:<20s} {rec:>10s} {ref:>10s} {pull:>7s} {rel:>6s} {status:>5s}")

    print(f"\n  {'=' * 68}")
    print(f"  Bins: {result['n_pass']}/{result['n_filled']} pass "
          f"({result['overall_score']:.0%})   status: "
          f"{'PASS' if result['overall_pass'] else 'FAIL'}")
    if "overall_shape" in result:
        print(f"  Shape: {result['overall_shape']:.2f}   "
              f"Norm: {result['overall_normalization']:.2f}   "
              f"Combined: {result['overall_combined']:.2f}")
    print()


def print_comparison(results: list[dict]) -> None:
    print(f"\n  {'Run':<45s} {'Pass%':>7s} {'Shape':>7s} {'Norm':>7s} {'Comb':>7s}")
    print(f"  {'─' * 76}")
    for r in results:
        if "error" in r:
            path = r.get("recast_dir", "?")
            print(f"  {path:<45s}  {r['error']}")
            continue
        parts = Path(r.get("recast_dir", "")).parts
        run_name = ("/".join(parts[-4:-1]) if len(parts) >= 4 else r.get("recast_dir", ""))[:45]
        print(f"  {run_name:<45s} "
              f"{r.get('overall_score', 0):>7.2f} "
              f"{r.get('overall_shape', 0):>7.2f} "
              f"{r.get('overall_normalization', 0):>7.2f} "
              f"{r.get('overall_combined', 0):>7.2f}")
    print()


def _save_to_eval_dir(recast_dir: str, payload) -> Path | None:
    """Write JSON into <run_dir>/eval/score.json.

    Given recast_dir = <run_dir>/workspace/HEPRecastData, the eval/ dir lives
    at <run_dir>/eval/ — sibling of workspace/, not inside it.
    """
    workspace = Path(recast_dir).parent
    if not workspace.exists():
        return None
    eval_dir = workspace.parent / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    out = eval_dir / "score.json"
    out.write_text(json.dumps(payload, indent=2))
    return out


def main():
    parser = argparse.ArgumentParser(
        prog="score",
        description="Score filled HEPData against reference: per-bin + shape/normalization.",
    )
    parser.add_argument("arxiv_id", help="arXiv ID of the paper")
    parser.add_argument("--recast-dir", help="Single HEPRecastData directory")
    parser.add_argument("--compare", nargs="+", help="Multiple HEPRecastData directories")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if args.compare:
        results = [score_recast(args.arxiv_id, d) for d in args.compare]
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print_comparison(results)
        for d, r in zip(args.compare, results):
            _save_to_eval_dir(d, r)
    elif args.recast_dir:
        result = score_recast(args.arxiv_id, args.recast_dir)
        saved = _save_to_eval_dir(args.recast_dir, result)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_scores(result)
            if saved:
                print(f"\n  Saved to {saved}")
    else:
        parser.error("Provide --recast-dir or --compare")


if __name__ == "__main__":
    main()
