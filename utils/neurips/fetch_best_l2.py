#!/usr/bin/env python3
"""Fetch the lowest `shape.relative_l2` per (model, task) and dump to JSON.

Walks /global/cfs/cdirs/m4539/ColliderBench/<model>/run-N/<run>/eval/score.json
for the canonical six models, finds the best (smallest) relative_l2 across
replicate runs of each task, and writes utils/neurips/best_l2.json keyed by
model → task_id → {best, run_label}.

Usage:
    python -m utils.neurips.fetch_best_l2
    python -m utils.neurips.fetch_best_l2 --metric normalization
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Canonical model order matches the bar-plot color order:
#  red, orange, yellow (Anthropic) | dark blue, cyan (OpenAI) | purple (DeepSeek)
MODELS: list[tuple[str, str]] = [
    ("claude_opus-4-7", "Opus 4.7"),
    ("claude_sonnet-4-6", "Sonnet 4.6"),
    ("claude_haiku-4-5", "Haiku 4.5"),
    ("codex_gpt-5.5", "GPT-5.5"),
    ("codex_gpt-5.4-mini", "GPT-5.4-mini"),
    ("forge_deepseek-v4-pro", "DeepSeek-V4"),
]


def _load_run_info(run_dir: Path) -> dict:
    """Return a small dict of cost/usage fields from run_info.json (best-effort)."""
    info_path = run_dir / "run_info.json"
    fields = {"wall_s": None, "cost_usd": None, "tokens_total_billed": None, "n_turns": None}
    if not info_path.is_file():
        return fields
    try:
        info = json.loads(info_path.read_text() or "{}")
    except Exception:
        return fields
    fields["wall_s"] = info.get("duration_wall_s")
    usage = info.get("usage") or {}
    fields["cost_usd"] = usage.get("api_cost_usd") or usage.get("total_cost_usd")
    fields["tokens_total_billed"] = usage.get("tokens_total_billed") or usage.get("total_tokens")
    fields["n_turns"] = usage.get("n_turns") or usage.get("num_turns")
    return fields


def collect(root: Path, models: list[tuple[str, str]], metric: str) -> dict:
    """Walk only the fixed depth `<model>/run-N/<run>/eval/score.json`.

    We do *not* `rglob`: the workspace/ subtrees contain millions of MG5
    intermediate files and a recursive glob over CFS Lustre takes minutes.
    The exact depth-4 pattern is hit directly.

    For each (model, task) we keep the run with the lowest `relative_l2`
    in the requested metric block, and record its wall-time, API cost,
    and total billed tokens from run_info.json — needed for Pareto plots.
    """
    out: dict[str, dict[str, dict]] = {}
    for mdir, label in models:
        mroot = root / mdir
        if not mroot.is_dir():
            out[mdir] = {"_label": label, "_missing": True, "tasks": {}}
            continue
        per_task_best: dict[str, dict] = {}
        # exact pattern: <model>/run-*/<run>/eval/score.json
        for sj in mroot.glob("run-*/*/eval/score.json"):
            try:
                s = json.loads(sj.read_text())
            except Exception:
                continue
            tid = s.get("task_id")
            if not tid:
                continue
            v = (s.get(metric) or {}).get("relative_l2")
            if v is None:
                continue
            v = float(v)
            run_dir = sj.parent.parent
            run_label = run_dir.parent.name  # run-N folder
            entry = per_task_best.get(tid)
            if entry is not None and v >= entry["best"]:
                continue
            cost = _load_run_info(run_dir)
            per_task_best[tid] = {
                "best": round(v, 6),
                "run_label": run_label,
                "wall_s": cost["wall_s"],
                "cost_usd": cost["cost_usd"],
                "tokens_total_billed": cost["tokens_total_billed"],
                "n_turns": cost["n_turns"],
            }
        out[mdir] = {"_label": label, "tasks": per_task_best}
    return out


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("/global/cfs/cdirs/m4539/ColliderBench"),
        help="Source directory containing <model>/run-N/<run>/eval/score.json",
    )
    ap.add_argument(
        "--metric",
        choices=("shape", "normalization"),
        default="shape",
        help="Which score.json block to read relative_l2 from (default: shape).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "best_l2.json",
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"root not found: {args.root}")

    data = {
        "metric": args.metric,
        "source_root": str(args.root),
        "models": collect(args.root, MODELS, args.metric),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True))

    # Console summary
    print(f"wrote {args.out}")
    for mdir, label in MODELS:
        block = data["models"].get(mdir, {})
        if block.get("_missing"):
            print(f"  {label:<14} (missing)")
            continue
        n = len(block.get("tasks", {}))
        print(f"  {label:<14} {n:>2} tasks scored")


if __name__ == "__main__":
    main()
