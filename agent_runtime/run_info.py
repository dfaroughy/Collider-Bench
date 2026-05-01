"""Read / write `<recast_dir>/run_info.json`.

`generate_run_info` (the *generation* of the canonical run-dir name +
metadata dict) lives in `agent_runtime.naming` because it's part of the
naming scheme. This module owns the I/O side: writing the file at run
start, and merging end-of-run metadata (exit code, scores, token usage)
into it at run finalize.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from agent_runtime.usage import parse_usage


def write_run_info(recast_dir: Path, info: dict) -> Path:
    """Write <recast_dir>/run_info.json and return the path."""
    recast_dir = Path(recast_dir)
    recast_dir.mkdir(parents=True, exist_ok=True)
    out = recast_dir / "run_info.json"
    # Add UTC timestamp on write so the file captures when the run started.
    info = {
        **info,
        "started_at": info.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out.write_text(json.dumps(info, indent=2))
    return out


def finalize_run_info(
    recast_dir: Path,
    exit_code: int,
    started_at: float | None = None,
    scores: dict | None = None,
    session_logs: list[Path] | None = None,
) -> Path:
    """Merge end-of-run metadata into run_info.json. Best-effort; never raises.

    Adds: ended_at, exit_code, duration_wall_s, final_score (from scores),
    and `usage` (summed across session_logs) if a parser found anything.
    """
    try:
        recast_dir = Path(recast_dir)
        info_path = recast_dir / "run_info.json"
        info: dict = {}
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text())
            except json.JSONDecodeError:
                info = {}
        info["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        info["exit_code"] = int(exit_code)
        if started_at is not None:
            info["duration_wall_s"] = round(time.time() - started_at, 2)
        if scores is not None:
            info["final_score"] = {
                "shape": scores.get("overall_shape"),
                "normalization": scores.get("overall_normalization"),
                "combined": scores.get("overall_combined"),
                "n_filled": scores.get("n_filled"),
                "n_bins": scores.get("n_bins"),
            }
        usage_total = {
            "api_cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tokens_total_billed": 0,
            "n_turns": 0,
        }
        any_usage = False
        priced_all = True
        runner = info.get("runner") or ""
        model = info.get("model") or ""
        for log_path in session_logs or []:
            if not log_path:
                continue
            u = parse_usage(runner, model, Path(log_path))
            if not u:
                continue
            any_usage = True
            priced_all = priced_all and u.get("cost_priced", True)
            for k in usage_total:
                usage_total[k] += u.get(k, 0) or 0
        if any_usage:
            usage_total["api_cost_usd"] = round(usage_total["api_cost_usd"], 6)
            # Claude reports its own cost; codex/gemini/forge cost is computed
            # from a static price table — flag that so a stale price isn't silent.
            if runner in ("codex", "gemini", "forge"):
                usage_total["cost_priced"] = priced_all
            info["usage"] = usage_total
        info_path.write_text(json.dumps(info, indent=2))
        return info_path
    except Exception as exc:
        sys.stderr.write(f"finalize_run_info: {exc}\n")
        return Path(recast_dir) / "run_info.json"
