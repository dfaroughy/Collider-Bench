"""Shared single-run launch scaffolding for single-shot agents.

Collapses the boilerplate that used to be duplicated across
agents/simple/run.py and agents/baseline/run.py: arg parsing, config
resolution, run-info generation, workspace build, runner invocation,
scoring, and finalization.

Agents that fit the "parse config → build workspace → run once → score →
finalize" shape should call launch_single_run(). Agents with a custom
control loop (iterative, sisyphus) compose from naming.py helpers
directly.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from agent_runtime.naming import (
    generate_run_info,
    resolve_effort,
    write_run_info,
    load_config,
    validate_launch_inputs,
    finalize_run_info,
)
from agent_runtime.runners import RUNNERS, get_runner
from agent_runtime.workspace import build_workspace


PromptBuilder = Callable[[str], str]


def _parse_args(agent_name: str, argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"{agent_name.title()} agent runner for the LHC-Recast benchmark.",
    )
    parser.add_argument(
        "--config", default="", help="YAML config file (CLI flags override; see configs/)"
    )
    parser.add_argument(
        "--paper-ref", default=None, help="arXiv ID (required unless set by --config)"
    )
    parser.add_argument("--runner", default=None, choices=sorted(RUNNERS))
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument(
        "--effort", default=None, help="Reasoning effort: low | medium | high | <int tokens>"
    )
    parser.add_argument(
        "--sandbox",
        default=None,
        choices=["auto", "bwrap", "none"],
        help="Filesystem isolation backend (default: auto)",
    )
    parser.add_argument("--run-name", default="", help="Custom run directory name")
    return parser.parse_args(argv), parser


def _resolve(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    """Merge CLI args over config-file values. Validates paper inputs."""
    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    args.paper_ref = args.paper_ref or cfg.get("paper")
    if not args.paper_ref:
        parser.error("--paper-ref is required (CLI or --config <yaml>:paper)")
    args.runner = args.runner or cfg.get("runner") or "claude"
    args.model = args.model or cfg.get("model") or ""
    args.effort = args.effort or cfg.get("effort") or "medium"
    args.sandbox = args.sandbox or cfg.get("sandbox")
    return cfg


def _run_in_sandbox(
    workspace: Path,
    repo_root: Path,
    prompt: str,
    runner_name: str,
    model: str | None,
    max_thinking_tokens: int | None,
    sandbox: str | None,
    extra_ro_binds: list[Path],
) -> None:
    from agent_runtime.sandbox import sandbox_command

    runner = get_runner(runner_name)
    inner_cmd = runner.build_command(
        prompt,
        workspace,
        model,
        allowlist=None,
        max_thinking_tokens=max_thinking_tokens,
    )
    env = os.environ.copy()
    env["PATH"] = str(workspace / "bin") + ":" + env.get("PATH", "")
    env["PYTHONPATH"] = str(repo_root) + ":" + env.get("PYTHONPATH", "")
    env["REPO_ROOT"] = str(repo_root)

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=extra_ro_binds,
        sandbox=sandbox,
    )
    try:
        runner.run(cmd, prompt, workspace, env, workspace / "session_log.txt")
    except subprocess.CalledProcessError as exc:
        print(f"  Agent exited with code {exc.returncode}", file=sys.stderr)
    finally:
        cleanup()


def _score(workspace: Path, paper_ref: str) -> dict:
    recast_dir = workspace / "HEPRecastData"
    if not recast_dir.exists():
        return {"error": "No HEPRecastData directory"}
    from LHCRecastBench.evaluation.score import score_recast

    return score_recast(paper_ref, str(recast_dir))


def launch_single_run(
    agent_name: str,
    build_prompt: PromptBuilder,
    repo_root: Path,
    argv: list[str] | None = None,
) -> int:
    """Run one agent session end-to-end. Returns exit code.

    agent_name    — matches a directory under agents/ (e.g. "simple").
    build_prompt  — callable(paper_ref) -> prompt string.
    repo_root     — absolute repo root; usually Path(__file__).resolve().parents[2]
                    from the caller.
    argv          — override sys.argv for testing; None uses sys.argv[1:].
    """
    args, parser = _parse_args(agent_name, argv)
    _resolve(args, parser)
    paper_ref = args.paper_ref
    effort_label, max_thinking = resolve_effort(args.effort)

    try:
        validate_launch_inputs(repo_root, paper_ref)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    if args.run_name:
        info = {
            "agent_id": args.run_name,
            "run_dir": args.run_name,
            "paper_ref": paper_ref,
            "agent": agent_name,
            "model": args.model or args.runner,
        }
        run_name = args.run_name
    else:
        info = generate_run_info(
            paper_ref=paper_ref,
            agent_name=agent_name,
            model_name=args.model or args.runner,
        )
        run_name = info["run_dir"]
    info.update(
        {
            "runner": args.runner,
            "effort": effort_label,
            "max_thinking_tokens": max_thinking,
            "sandbox": args.sandbox or "auto",
        }
    )

    print(f"Setting up workspace: {run_name}")
    print(
        f"Agent ID: {info['agent_id']}   "
        f"(effort={effort_label}, max_thinking_tokens={max_thinking})"
    )
    workspace = build_workspace(repo_root, agent_name, paper_ref, run_name)
    recast_path = workspace.parent
    write_run_info(recast_path, info)
    print(f"Workspace: {workspace}")

    prompt = build_prompt(paper_ref)
    (workspace / "prompt.txt").write_text(prompt)

    runtime_dir = repo_root / "agents" / agent_name / "runtime"
    shared_runtime = repo_root / "agent_runtime"
    extra_ro_binds = [runtime_dir, shared_runtime]

    started_at = time.time()
    exit_code = 0
    scores: dict | None = None
    try:
        print(f"Running {args.runner} agent (sandbox={args.sandbox or 'auto'})...")
        _run_in_sandbox(
            workspace,
            repo_root,
            prompt,
            runner_name=args.runner,
            model=args.model or None,
            max_thinking_tokens=max_thinking,
            sandbox=args.sandbox,
            extra_ro_binds=extra_ro_binds,
        )

        print("\nScoring results...")
        scores = _score(workspace, paper_ref)
        eval_dir = recast_path / "eval"
        eval_dir.mkdir(exist_ok=True)
        (eval_dir / "score.json").write_text(json.dumps(scores, indent=2))

        if "error" in scores:
            print(f"  ERROR: {scores['error']}")
            exit_code = 1
        else:
            n_pass = scores.get("n_pass", 0)
            n_filled = scores.get("n_filled", 0)
            overall = scores.get("overall_score", 0)
            print(f"  Overall: {n_pass}/{n_filled} bins pass ({overall:.0%})")
    except BaseException:
        exit_code = 1
        raise
    finally:
        finalize_run_info(
            recast_path,
            exit_code=exit_code,
            started_at=started_at,
            scores=scores,
            session_logs=[workspace / "session_log.txt"],
        )

    print(f"\nDone. Results in {workspace}")
    return exit_code
