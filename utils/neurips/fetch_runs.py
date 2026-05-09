#!/usr/bin/env python3
"""Single-source data fetcher for the NeurIPS plots.

Walks /global/cfs/cdirs/m4539/ColliderBench/<model>/run-N/<run>/eval/score.json
across the canonical six models and dumps **every** replicate's metrics +
cost into one JSON file. This replaces the per-aggregation fetchers
(fetch_best_l2, fetch_mean_l2, fetch_delta) — downstream plot scripts
should consume this file and aggregate as they wish.

Output schema (utils/neurips/data/runs.json by default):
    {
      "source_root": "/global/cfs/cdirs/m4539/ColliderBench",
      "models": {
        "<model_dir>": {
          "_label": "Opus 4.7",
          "tasks": {
            "<task_id>": {
              "replicates": [
                {
                  "run_label":           "run-1",
                  "run_dir":             "claude_opus-4-7/run-1/...",
                  "wall_s":              float | null,
                  "cost_usd":            float | null,
                  "tokens_total_billed": float | null,
                  "delta":               float | null,   # normalization.Delta
                  "relative_l2_norm":    float | null,   # normalization.relative_l2
                  "relative_l2_shape":   float | null    # shape.relative_l2
                },
                ...
              ]
            }
          }
        }
      }
    }

Both shape-only and sim tasks are covered. For pure shape tasks the
`delta` and `relative_l2_norm` fields are null (no normalization block).
For yield-only tasks the `relative_l2_shape` field is null.

Usage:
    python -m utils.neurips.fetch_runs
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
    ("forge_deepseek-r1", "DeepSeek-R1"),
]


def _to_float(x):
    return None if x is None else float(x)


def _load_run_info(run_dir: Path) -> dict:
    """Pull cost/usage fields from run_info.json (best-effort, never raises)."""
    info_path = run_dir / "run_info.json"
    out = {"wall_s": None, "cost_usd": None, "tokens_total_billed": None, "n_turns": None}
    if not info_path.is_file():
        return out
    try:
        info = json.loads(info_path.read_text() or "{}")
    except Exception:
        return out
    usage = info.get("usage") or {}
    out["wall_s"] = _to_float(info.get("duration_wall_s"))
    out["cost_usd"] = _to_float(usage.get("api_cost_usd") or usage.get("total_cost_usd"))
    out["tokens_total_billed"] = _to_float(
        usage.get("tokens_total_billed") or usage.get("total_tokens")
    )
    out["n_turns"] = usage.get("n_turns") or usage.get("num_turns")
    return out


def _load_score(sj_path: Path) -> tuple[str | None, dict]:
    """Return (task_id, {delta, relative_l2_norm, relative_l2_shape}) or (None, {})."""
    try:
        s = json.loads(sj_path.read_text())
    except Exception:
        return None, {}
    tid = s.get("task_id")
    norm = s.get("normalization") or {}
    shape = s.get("shape") or {}
    return tid, {
        "delta": _to_float(norm.get("Delta")),
        "relative_l2_norm": _to_float(norm.get("relative_l2")),
        "relative_l2_shape": _to_float(shape.get("relative_l2")),
    }


def collect(root: Path, models: list[tuple[str, str]]) -> dict:
    out: dict[str, dict] = {}
    for mdir, label in models:
        mroot = root / mdir
        if not mroot.is_dir():
            out[mdir] = {"_label": label, "_missing": True, "tasks": {}}
            continue
        per_task: dict[str, dict] = {}
        for sj in mroot.glob("run-*/*/eval/score.json"):
            tid, metrics = _load_score(sj)
            if tid is None:
                continue
            run_dir = sj.parent.parent
            cost = _load_run_info(run_dir)
            replicate = {
                "run_label": run_dir.parent.name,
                "run_dir": str(run_dir.relative_to(root)),
                "wall_s": cost["wall_s"],
                "cost_usd": cost["cost_usd"],
                "tokens_total_billed": cost["tokens_total_billed"],
                "n_turns": cost["n_turns"],
                **metrics,
            }
            per_task.setdefault(tid, {"replicates": []})["replicates"].append(replicate)

        # stable per-task ordering by run_label
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
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "data" / "runs.json",
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"root not found: {args.root}")

    data = {"source_root": str(args.root), "models": collect(args.root, MODELS)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True))

    # Console summary
    print(f"wrote {args.out}")
    for mdir, label in MODELS:
        block = data["models"].get(mdir, {})
        if block.get("_missing"):
            print(f"  {label:<14} (missing)")
            continue
        tasks = block.get("tasks", {}) or {}
        if not tasks:
            print(f"  {label:<14} no replicates")
            continue
        ns = [len(t["replicates"]) for t in tasks.values()]
        sim_tasks = sum(1 for tid in tasks if "_sim" in tid)
        shape_tasks = len(tasks) - sim_tasks
        print(
            f"  {label:<14} {len(tasks):>2} tasks "
            f"({shape_tasks} shape, {sim_tasks} sim), "
            f"{sum(ns)} replicates (n={min(ns)}..{max(ns)} per task)"
        )


if __name__ == "__main__":
    main()
