#!/usr/bin/env python3
"""Fetch the **mean** `relative_l2` per (model, task) across replicate runs.

Same on-disk schema as `fetch_best_l2.py` so the existing plot scripts can
be pointed at the resulting JSON without changes — the only difference is
that the `best` field stores the across-replicate **mean** instead of the
minimum, and `cost_usd` / `wall_s` / `tokens_total_billed` are likewise
averaged over the same replicates. `n_replicates` is added.

Usage:
    python -m utils.neurips.fetch_mean_l2
    python -m utils.neurips.fetch_mean_l2 --metric normalization \
        --out utils/neurips/best_l2_norm_mean.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

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


def _avg(vals: list[float | None]) -> float | None:
    nums = [float(v) for v in vals if v is not None]
    return mean(nums) if nums else None


def collect(root: Path, models: list[tuple[str, str]], metric: str) -> dict:
    """Walk <model>/run-*/<run>/eval/score.json; aggregate by mean across replicates."""
    out: dict[str, dict[str, dict]] = {}
    for mdir, label in models:
        mroot = root / mdir
        if not mroot.is_dir():
            out[mdir] = {"_label": label, "_missing": True, "tasks": {}}
            continue
        # task_id → list of {l2, wall_s, cost_usd, tokens, n_turns, run_dir, run_label}
        groups: dict[str, list[dict]] = {}
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
            run_dir = sj.parent.parent
            cost = _load_run_info(run_dir)
            groups.setdefault(tid, []).append(
                {
                    "l2": float(v),
                    "wall_s": cost["wall_s"],
                    "cost_usd": cost["cost_usd"],
                    "tokens_total_billed": cost["tokens_total_billed"],
                    "n_turns": cost["n_turns"],
                    "run_dir": str(run_dir.relative_to(root)),
                    "run_label": run_dir.parent.name,
                }
            )

        per_task: dict[str, dict] = {}
        for tid, replicates in groups.items():
            mean_l2 = _avg([r["l2"] for r in replicates])
            if mean_l2 is None:
                continue
            per_task[tid] = {
                "best": round(mean_l2, 6),  # field name kept = "best" so the plot
                "n_replicates": len(replicates),  # script can read it unchanged.
                "wall_s": _avg([r["wall_s"] for r in replicates]),
                "cost_usd": _avg([r["cost_usd"] for r in replicates]),
                "tokens_total_billed": _avg([r["tokens_total_billed"] for r in replicates]),
                "n_turns": _avg([r["n_turns"] for r in replicates]),
                # run_dir/run_label refer to a representative replicate, since
                # "mean" does not pick a specific run.
                "run_dir": replicates[0]["run_dir"],
                "run_label": replicates[0]["run_label"],
            }
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
    ap.add_argument("--metric", choices=("shape", "normalization"), default="shape")
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "utils" / "neurips" / "data" / "best_l2_mean.json",
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"root not found: {args.root}")

    data = {
        "metric": args.metric,
        "aggregator": "mean",
        "source_root": str(args.root),
        "models": collect(args.root, MODELS, args.metric),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True))

    print(f"wrote {args.out}")
    for mdir, label in MODELS:
        block = data["models"].get(mdir, {})
        if block.get("_missing"):
            print(f"  {label:<14} (missing)")
            continue
        tasks = block.get("tasks", {})
        if not tasks:
            print(f"  {label:<14} no replicates")
            continue
        nrs = [t.get("n_replicates", 0) for t in tasks.values()]
        print(f"  {label:<14} {len(tasks):>2} tasks, n_replicates={min(nrs)}..{max(nrs)}")


if __name__ == "__main__":
    main()
