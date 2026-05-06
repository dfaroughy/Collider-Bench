#!/usr/bin/env python3
"""Fetch every replicate's `normalization.Delta` value for each (model, task).

Walks /global/cfs/cdirs/m4539/ColliderBench/<model>/run-N/<run>/eval/score.json
for the canonical six models and dumps every replicate's δ_norm = Δ value
(i.e. |Σ_obs − Σ_ref| / Σ_ref) along with run metadata into one JSON file.

Output schema (utils/neurips/data/delta_norm.json by default):
    {
      "metric": "normalization.Delta",
      "source_root": "/global/cfs/cdirs/m4539/ColliderBench",
      "models": {
        "<model_dir>": {
          "_label": "Opus 4.7",
          "tasks": {
            "<task_id>": {
              "replicates": [
                {"delta": float, "run_label": "run-1", "wall_s": float, "cost_usd": float,
                 "tokens_total_billed": float, "n_turns": int}, ...
              ]
            }
          }
        }
      }
    }

Usage:
    python -m utils.neurips.fetch_delta
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MODELS: list[tuple[str, str]] = [
    ("claude_opus-4-7", "Opus 4.7"),
    ("claude_sonnet-4-6", "Sonnet 4.6"),
    ("claude_haiku-4-5", "Haiku 4.5"),
    ("codex_gpt-5.5", "GPT-5.5"),
    ("codex_gpt-5.4-mini", "GPT-5.4-mini"),
    ("forge_deepseek-v4-pro", "DeepSeek-V4"),
]


def _load_run_info(run_dir: Path) -> dict:
    info_path = run_dir / "run_info.json"
    out = {"wall_s": None, "cost_usd": None, "tokens_total_billed": None, "n_turns": None}
    if not info_path.is_file():
        return out
    try:
        info = json.loads(info_path.read_text() or "{}")
    except Exception:
        return out
    usage = info.get("usage") or {}
    out["wall_s"] = info.get("duration_wall_s")
    out["cost_usd"] = usage.get("api_cost_usd") or usage.get("total_cost_usd")
    out["tokens_total_billed"] = usage.get("tokens_total_billed") or usage.get("total_tokens")
    out["n_turns"] = usage.get("n_turns") or usage.get("num_turns")
    return out


def collect(root: Path, models: list[tuple[str, str]]) -> dict:
    out: dict[str, dict] = {}
    for mdir, label in models:
        mroot = root / mdir
        if not mroot.is_dir():
            out[mdir] = {"_label": label, "_missing": True, "tasks": {}}
            continue
        per_task: dict[str, dict] = {}
        for sj in mroot.glob("run-*/*/eval/score.json"):
            try:
                s = json.loads(sj.read_text())
            except Exception:
                continue
            tid = s.get("task_id")
            if not tid:
                continue
            delta = (s.get("normalization") or {}).get("Delta")
            if delta is None:
                continue
            run_dir = sj.parent.parent
            cost = _load_run_info(run_dir)
            per_task.setdefault(tid, {"replicates": []})["replicates"].append(
                {
                    "delta": round(float(delta), 6),
                    "run_label": run_dir.parent.name,
                    "wall_s": cost["wall_s"],
                    "cost_usd": cost["cost_usd"],
                    "tokens_total_billed": cost["tokens_total_billed"],
                    "n_turns": cost["n_turns"],
                }
            )
        # sort each task's replicates by run_label for stable ordering
        for tid in per_task:
            per_task[tid]["replicates"].sort(key=lambda r: r["run_label"])
        out[mdir] = {"_label": label, "tasks": per_task}
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
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "data" / "delta_norm.json",
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"root not found: {args.root}")

    data = {
        "metric": "normalization.Delta",
        "source_root": str(args.root),
        "models": collect(args.root, MODELS),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True))

    print(f"wrote {args.out}")
    for mdir, label in MODELS:
        block = data["models"].get(mdir, {})
        if block.get("_missing"):
            print(f"  {label:<14} (missing)")
            continue
        tasks = block.get("tasks", {}) or {}
        if not tasks:
            print(f"  {label:<14} no Delta values")
            continue
        ns = [len(t["replicates"]) for t in tasks.values()]
        total_repl = sum(ns)
        print(
            f"  {label:<14} {len(tasks):>2} tasks, "
            f"{total_repl:>3} replicates total (n={min(ns)}..{max(ns)} per task)"
        )


if __name__ == "__main__":
    main()
