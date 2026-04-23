#!/usr/bin/env python3
"""Rubric-based evaluation with cost metrics and token consumption.

Decomposes agent performance into weighted checkpoints (PaperBench-style)
and reports cost/efficiency metrics extracted from the session log.

This is an offline evaluation tool — not part of the agent loop.

Usage:
    python -m tools.evaluation.rubric_scorer \
        --agent-dir recast_1707.06193_claude-opus-4-6_yk13/validation/agent_002 \
        --arxiv 1707.06193

    python -m tools.evaluation.rubric_scorer \
        --compare recast_*/validation/agent_*/  --arxiv 1707.06193
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


# ── Cost & token extraction from session log ────────────────────────────────


def extract_session_stats(agent_dir: Path) -> dict:
    """Extract cost, token, and timing stats from the session log."""
    # Auto-discover session log (different agents use different names)
    session_path = agent_dir / "session_log.txt"
    stats = {
        "cost_usd": None,
        "duration_wall_s": None,
        "duration_api_s": None,
        "num_turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens_billed": 0,
        "total_tokens_context": 0,
        "tool_calls": 0,
        "tool_calls_by_type": {},
        "errors_encountered": 0,
        "analysis_runs": 0,
    }

    if not session_path.exists():
        return stats

    last_result = None
    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Codex session logs are plain text, not stream-json. A stray
            # line that happens to parse as a JSON array/scalar must not
            # blow up `.get()`.
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")

            if msg_type == "assistant":
                content = msg.get("message", {}).get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        stats["tool_calls"] += 1
                        name = block.get("name", "unknown")
                        stats["tool_calls_by_type"][name] = (
                            stats["tool_calls_by_type"].get(name, 0) + 1
                        )

                        # Count analysis runs
                        if name == "Bash":
                            cmd = block.get("input", {}).get("command", "")
                            if "run-analysis" in cmd:
                                stats["analysis_runs"] += 1

                # Count turns
                usage = msg.get("message", {}).get("usage", {})
                if usage:
                    stats["num_turns"] += 1
                    stats["input_tokens"] += usage.get("input_tokens", 0)
                    stats["output_tokens"] += usage.get("output_tokens", 0)
                    stats["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
                    stats["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)

            elif msg_type == "user":
                # Count errors
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            rc = block.get("content", "")
                            if isinstance(rc, str) and (
                                "Traceback" in rc
                                or "exit code 1" in rc.lower()
                                or rc.startswith("Error")
                                or rc.startswith("ERROR")
                            ):
                                stats["errors_encountered"] += 1

            elif msg_type == "result":
                last_result = msg

    # Extract final stats from the last result message
    if last_result:
        stats["cost_usd"] = last_result.get("total_cost_usd")
        stats["duration_api_s"] = (last_result.get("duration_api_ms") or 0) / 1000
        stats["duration_wall_s"] = (last_result.get("duration_ms") or 0) / 1000

        # If the session had multiple result messages (polling artifacts),
        # the last one has cumulative cost
        final_turns = last_result.get("num_turns")
        if final_turns and final_turns > stats["num_turns"]:
            stats["num_turns"] = final_turns

    # Billed tokens: input + output (what you pay full price for)
    # Cache reads are ~10x cheaper and cache writes are ~25% more expensive,
    # but for simplicity we report them separately
    stats["total_tokens_billed"] = stats["input_tokens"] + stats["output_tokens"]
    # Context tokens: total tokens the model actually processed per turn
    # (cache_read = reused prompt, cache_creation = new prompt cached for later)
    stats["total_tokens_context"] = (
        stats["input_tokens"] + stats["cache_creation_tokens"] + stats["cache_read_tokens"]
    )

    return stats


# ── Rubric checkpoints ──────────────────────────────────────────────────────


def evaluate_rubric(agent_dir: Path, arxiv_id: str, task: str = "recast") -> dict:
    """Evaluate agent against a weighted rubric of checkpoints.

    task selects which tasks/<task>/reference/HEPRecastData/ the accuracy
    checkpoints (shape, normalization, yield) are scored against.
    """

    checkpoints = []

    # 1. Code executes (15%)
    # Aggregate total code size across analysis.py + analysis/**/*.py so the
    # agent isn't penalised for splitting its code into multiple modules.
    analysis_files: list[Path] = []
    root_analysis = agent_dir / "analysis.py"
    if root_analysis.exists():
        analysis_files.append(root_analysis)
    analysis_dir = agent_dir / "analysis"
    if analysis_dir.is_dir():
        analysis_files.extend(analysis_dir.rglob("*.py"))
    total_code_size = sum(f.stat().st_size for f in analysis_files if f.is_file())
    has_code = total_code_size > 500

    recast_data_dir = agent_dir / "HEPRecastData"
    has_filled = False
    if recast_data_dir.exists():
        for yf in recast_data_dir.glob("*.yaml"):
            if yf.name in ("submission.yaml", "description.yaml"):
                continue
            try:
                data = yaml.safe_load(yf.read_text()) or {}
                for dep in data.get("dependent_variables", []):
                    for entry in dep.get("values", []):
                        if entry.get("value") is not None:
                            has_filled = True
                            break
                    if has_filled:
                        break
            except Exception:
                pass
            if has_filled:
                break

    checkpoints.append(
        {
            "name": "Code executes",
            "weight": 0.15,
            "score": 1.0 if has_filled else (0.5 if has_code else 0.0),
            "detail": "Filled HEPRecastData"
            if has_filled
            else ("Code exists but no filled data" if has_code else "No code"),
        }
    )

    # 2. Correct datasets found (15%)
    datasets_path = agent_dir / "datasets.yaml"
    n_datasets = 0
    n_with_source = 0  # file_urls (Open Data) OR file_dirs (local MC)
    n_with_xsec = 0
    if datasets_path.exists():
        try:
            ds = yaml.safe_load(datasets_path.read_text()) or []
            n_datasets = len(ds)
            for d in ds:
                if d.get("file_urls") or d.get("file_dirs"):
                    n_with_source += 1
                if d.get("cross_section_pb"):
                    n_with_xsec += 1
        except Exception:
            pass

    ds_score = min(1.0, n_with_source / max(n_datasets, 1)) if n_datasets > 0 else 0.0
    checkpoints.append(
        {
            "name": "Datasets discovered",
            "weight": 0.15,
            "score": ds_score,
            "detail": f"{n_with_source}/{n_datasets} with files, {n_with_xsec} with cross sections",
        }
    )

    # 3. Event selection matches paper — shape score (20%)
    # 4. Normalization correct (20%)
    # 5. Final yield within tolerance (20%)
    shape_score = 0.0
    norm_ratio = None
    norm_score = 0.0
    yield_score = 0.0

    if recast_data_dir.exists() and has_filled:
        try:
            from LHCRecastBench.evaluation.score import score_recast

            scores = score_recast(arxiv_id, str(recast_data_dir), task=task)
            yield_score = scores.get("overall_score", 0.0)
            shape_score = scores.get("overall_shape", 0.0)
            norm_score = scores.get("overall_normalization", 0.0)
            for table in scores.get("tables", []):
                for s in table.get("series", []):
                    if "normalization" in s:
                        norm_ratio = s["normalization"]["ratio"]
                        break
                if norm_ratio is not None:
                    break
        except Exception:
            pass

    checkpoints.append(
        {
            "name": "Event selection (shape)",
            "weight": 0.20,
            "score": shape_score,
            "detail": f"Shape score: {shape_score:.2f}",
        }
    )

    checkpoints.append(
        {
            "name": "Normalization",
            "weight": 0.20,
            "score": norm_score,
            "detail": f"Ratio: {norm_ratio:.3f}" if norm_ratio else "N/A",
        }
    )

    checkpoints.append(
        {
            "name": "Yield within tolerance",
            "weight": 0.20,
            "score": yield_score,
            "detail": f"Bins passing: {yield_score:.0%}",
        }
    )

    # 6. Documentation (10%)
    # Scored on report.md only. Size-graded: a stub report scores low, a
    # detailed write-up scores full. baseline and simple don't produce
    # audit.json (that's a full-baseline artifact).
    report_path = agent_dir / "report.md"
    doc_score = 0.0
    doc_detail = "missing"
    if report_path.exists():
        size = report_path.stat().st_size
        if size > 2000:
            doc_score = 1.0
        elif size > 500:
            doc_score = 0.7
        elif size > 100:
            doc_score = 0.3
        doc_detail = f"report.md: {size} bytes"

    checkpoints.append(
        {
            "name": "Documentation",
            "weight": 0.10,
            "score": doc_score,
            "detail": doc_detail,
        }
    )

    # Weighted total
    total = sum(cp["weight"] * cp["score"] for cp in checkpoints)

    return {
        "checkpoints": checkpoints,
        "rubric_score": round(total, 3),
    }


# ── Combined evaluation ─────────────────────────────────────────────────────


def evaluate_agent(agent_dir: Path, arxiv_id: str, task: str = "recast") -> dict:
    """Full evaluation: rubric + cost + efficiency."""
    agent_dir = Path(agent_dir)

    rubric = evaluate_rubric(agent_dir, arxiv_id, task)
    stats = extract_session_stats(agent_dir)

    # Derive efficiency metrics
    efficiency = {}
    if stats["cost_usd"] and rubric["rubric_score"] > 0:
        efficiency["cost_per_point"] = round(stats["cost_usd"] / rubric["rubric_score"], 2)
    if stats["total_tokens_billed"] and rubric["rubric_score"] > 0:
        efficiency["tokens_per_point"] = int(stats["total_tokens_billed"] / rubric["rubric_score"])
    if stats["tool_calls"] and stats["num_turns"]:
        efficiency["tools_per_turn"] = round(stats["tool_calls"] / stats["num_turns"], 1)
    if stats["errors_encountered"] and stats["tool_calls"]:
        efficiency["error_rate"] = round(stats["errors_encountered"] / stats["tool_calls"], 3)

    return {
        "agent_dir": str(agent_dir),
        "arxiv_id": arxiv_id,
        "rubric": rubric,
        "cost": {
            "usd": stats["cost_usd"],
            "duration_wall_s": stats["duration_wall_s"],
            "duration_api_s": stats["duration_api_s"],
        },
        "tokens": {
            "input": stats["input_tokens"],
            "output": stats["output_tokens"],
            "cache_creation": stats["cache_creation_tokens"],
            "cache_read": stats["cache_read_tokens"],
            "total_billed": stats["total_tokens_billed"],
            "total_context": stats["total_tokens_context"],
        },
        "activity": {
            "num_turns": stats["num_turns"],
            "tool_calls": stats["tool_calls"],
            "tool_calls_by_type": stats["tool_calls_by_type"],
            "errors_encountered": stats["errors_encountered"],
            "analysis_runs": stats["analysis_runs"],
        },
        "efficiency": efficiency,
    }


# ── Display ─────────────────────────────────────────────────────────────────


def print_single(result: dict) -> None:
    parts = Path(result["agent_dir"]).parts
    run_name = "/".join(parts[-3:]) if len(parts) >= 3 else result["agent_dir"]

    print(f"\n  Rubric Evaluation: {run_name}")
    print(f"  Paper: {result['arxiv_id']}")
    print(f"  {'=' * 65}")

    # Rubric
    print("\n  Rubric Checkpoints:")
    print(f"  {'─' * 65}")
    for cp in result["rubric"]["checkpoints"]:
        bar = "#" * int(cp["score"] * 10)
        bar_empty = "." * (10 - len(bar))
        print(
            f"  {cp['name']:<28s} [{bar}{bar_empty}] {cp['score']:.2f} x {cp['weight']:.0%}  {cp['detail']}"
        )
    print(f"  {'─' * 65}")
    print(f"  Rubric Score: {result['rubric']['rubric_score']:.1%}")

    # Cost
    cost = result["cost"]
    print("\n  Cost:")
    if cost["usd"]:
        print(f"    API cost:       ${cost['usd']:.2f}")
    if cost["duration_wall_s"]:
        mins = cost["duration_wall_s"] / 60
        print(f"    Wall time:      {mins:.1f} min")
    if cost["duration_api_s"]:
        mins = cost["duration_api_s"] / 60
        print(f"    API time:       {mins:.1f} min")

    # Tokens
    tok = result["tokens"]
    print("\n  Tokens:")
    print(f"    Input (new):    {tok['input']:>10,}  <- billed at full rate")
    print(f"    Output:         {tok['output']:>10,}  <- billed at full rate")
    print(f"    Cache creation: {tok['cache_creation']:>10,}  <- billed at 1.25x input")
    print(f"    Cache read:     {tok['cache_read']:>10,}  <- billed at 0.1x input")
    print(f"    Total billed:   {tok['total_billed']:>10,}  (input + output)")
    print(f"    Total context:  {tok['total_context']:>10,}  (what the model saw per turn)")

    # Activity
    act = result["activity"]
    print("\n  Activity:")
    print(f"    Turns:          {act['num_turns']}")
    print(f"    Tool calls:     {act['tool_calls']}")
    print(f"    Errors:         {act['errors_encountered']}")
    print(f"    Analysis runs:  {act['analysis_runs']}")
    top_tools = sorted(act["tool_calls_by_type"].items(), key=lambda x: -x[1])[:5]
    if top_tools:
        print(f"    Top tools:      {', '.join(f'{n}({c})' for n, c in top_tools)}")

    # Efficiency
    eff = result["efficiency"]
    if eff:
        print("\n  Efficiency:")
        if "cost_per_point" in eff:
            print(f"    $/point:        ${eff['cost_per_point']:.2f}")
        if "tokens_per_point" in eff:
            print(f"    tokens/point:   {eff['tokens_per_point']:,}")
        if "error_rate" in eff:
            print(f"    error rate:     {eff['error_rate']:.1%}")

    print()


def print_comparison(results: list[dict]) -> None:
    print(
        f"\n  {'Run':<40s} {'Rubric':>7s} {'Cost':>7s} {'Turns':>6s} {'Tokens':>10s} {'Time':>7s} {'$/pt':>6s}"
    )
    print(f"  {'─' * 90}")

    for r in results:
        parts = Path(r["agent_dir"]).parts
        run_name = "/".join(parts[-3:-1]) if len(parts) >= 3 else r["agent_dir"]
        run_name = run_name[:40]

        rubric = f"{r['rubric']['rubric_score']:.1%}"
        cost = f"${r['cost']['usd']:.2f}" if r["cost"]["usd"] else "—"
        turns = str(r["activity"]["num_turns"])
        tokens = f"{r['tokens']['total_billed']:,}" if r["tokens"]["total_billed"] else "—"
        time_min = (
            f"{r['cost']['duration_wall_s']/60:.0f}m" if r["cost"]["duration_wall_s"] else "—"
        )
        cpp = (
            f"${r['efficiency'].get('cost_per_point', 0):.2f}"
            if r["efficiency"].get("cost_per_point")
            else "—"
        )

        print(
            f"  {run_name:<40s} {rubric:>7s} {cost:>7s} {turns:>6s} {tokens:>10s} {time_min:>7s} {cpp:>6s}"
        )

    print()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Rubric-based evaluation with cost and token metrics. "
        "arxiv and task are read from run_info.json.",
    )
    parser.add_argument(
        "run_path",
        nargs="+",
        help="Run directory, workspace, iter dir, or HEPRecastData dir. Multiple paths compare.",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON to stdout")
    args = parser.parse_args()

    from ._resolve import resolve_run

    results: list[tuple] = []
    for p in args.run_path:
        try:
            rp = resolve_run(p)
        except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
            print(f"  ERROR: {p}: {exc}")
            continue
        result = evaluate_agent(rp.artifact_dir, rp.arxiv_id, rp.task)
        rp.eval_dir.mkdir(parents=True, exist_ok=True)
        out = rp.eval_dir / "rubric_scorer.json"
        out.write_text(json.dumps(result, indent=2))
        results.append((rp, result, out))

    if args.json:
        print(json.dumps([r[1] for r in results], indent=2))
        return

    if len(results) > 1:
        print_comparison([r[1] for r in results])
    for _rp, result, out in results:
        print_single(result)
        print(f"\n  Saved to {out}")


if __name__ == "__main__":
    main()
