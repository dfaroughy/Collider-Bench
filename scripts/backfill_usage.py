#!/usr/bin/env python3
"""Backfill `run_info.json["usage"]` for finished codex/gemini runs.

Claude runs already have usage written at finalize-time (Anthropic's CLI
reports `total_cost_usd` in the stream). Codex and Gemini emit token
counts only — this script reparses their session.jsonl with the new
parsers in `agent_runtime.usage` and a static price table in
`agent_runtime.pricing`, then rewrites run_info.json in place.

Usage:
    scripts/backfill_usage.py runs/codex_*  runs/gemini_*
    scripts/backfill_usage.py --dry-run runs/codex_gpt-5.5
    scripts/backfill_usage.py --force runs/claude_opus-4-7   # also reprices claude

Idempotent: re-running on a populated run_info.json overwrites the existing
`usage` block with the freshly-priced one. `--dry-run` prints the diff
instead of writing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_runtime.runners  # noqa: E402,F401  (resolves the import chain)
from agent_runtime.usage import parse_usage  # noqa: E402


def _find_session_log(recast_dir: Path) -> Path | None:
    """Session log lives at <recast>/workspace/session.jsonl (current) or
    session_log.txt (legacy runs from before the rename)."""
    workspace = recast_dir / "workspace"
    for name in ("session.jsonl", "session_log.txt"):
        p = workspace / name
        if p.is_file():
            return p
    return None


def _expand(targets: list[str]) -> list[Path]:
    """Resolve user-supplied paths to a flat list of run_info.json files."""
    out: list[Path] = []
    for raw in targets:
        p = Path(raw)
        if p.is_file() and p.name == "run_info.json":
            out.append(p)
        elif p.is_dir():
            out.extend(p.rglob("run_info.json"))
    seen: set[Path] = set()
    result: list[Path] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _process(info_path: Path, *, force: bool, dry_run: bool) -> str:
    """Returns a one-line status for the report row."""
    try:
        info = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"SKIP   ({exc})"

    runner = info.get("runner") or ""
    model = info.get("model") or ""
    if runner not in ("codex", "gemini") and not force:
        return f"skip   runner={runner} (use --force to reprice)"

    log = _find_session_log(info_path.parent)
    if log is None:
        return "skip   no session.jsonl / session_log.txt"

    usage = parse_usage(runner, model, log)
    if not usage:
        return "skip   no usage events in log"

    new_usage = {
        "api_cost_usd": round(usage.get("api_cost_usd", 0.0), 6),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
        "tokens_total_billed": usage.get("tokens_total_billed", 0),
        "n_turns": usage.get("n_turns", 0),
    }
    if runner in ("codex", "gemini"):
        new_usage["cost_priced"] = usage.get("cost_priced", True)

    cost = new_usage["api_cost_usd"]
    in_t = new_usage["input_tokens"]
    out_t = new_usage["output_tokens"]
    summary = f"${cost:>7.4f}  in={in_t:>10,}  out={out_t:>7,}"

    if dry_run:
        return f"DRY    {summary}"

    info["usage"] = new_usage
    info_path.write_text(json.dumps(info, indent=2))
    return f"WROTE  {summary}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="Run dirs or run_info.json paths")
    parser.add_argument("--dry-run", action="store_true", help="Don't write")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also reprice claude/aider/forge runs (otherwise skipped)",
    )
    args = parser.parse_args(argv)

    targets = _expand(args.paths)
    if not targets:
        print("No run_info.json files found.", file=sys.stderr)
        return 1

    total_cost = 0.0
    n_priced = 0
    for t in targets:
        msg = _process(t, force=args.force, dry_run=args.dry_run)
        try:
            rel = t.parent.relative_to(Path.cwd())
        except ValueError:
            rel = t.parent
        print(f"  {msg}  {rel}")
        if msg.startswith(("WROTE", "DRY")):
            n_priced += 1
            try:
                total_cost += float(msg.split("$", 1)[1].split()[0])
            except (IndexError, ValueError):
                pass

    print()
    print(f"TOTAL: {n_priced} runs priced, ${total_cost:.4f} aggregate")
    if args.dry_run:
        print("(dry-run — no files modified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
