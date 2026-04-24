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
        "--task",
        default=None,
        help="Task id, matching a directory under LHCRecastBench/tasks/ "
        "(e.g. sus-16-046-simulate-TChiWg-STgamma).",
    )
    parser.add_argument("--runner", default=None, choices=sorted(RUNNERS))
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument(
        "--effort",
        default=None,
        help="Reasoning effort: low | medium | high | max | xhigh | <int tokens>",
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
    """Merge CLI args over config-file values. Validates task input exists."""
    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    args.task = args.task or cfg.get("task")
    if not args.task:
        parser.error("--task is required (CLI or --config <yaml>:task)")
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
    effort_label: str | None = None,
) -> None:
    from agent_runtime.sandbox import sandbox_command

    runner = get_runner(runner_name)
    inner_cmd = runner.build_command(
        prompt,
        workspace,
        model,
        allowlist=None,
        max_thinking_tokens=max_thinking_tokens,
        effort_label=effort_label,
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


def _default_score(workspace: Path) -> dict:
    """Default scoring hook — calls the in-repo LHCRecastBench evaluator.

    Kept as a separate function so alternate scorers can be injected via
    `launch_single_run(..., score=<callable>)`. Returns an {"error": ...}
    dict rather than raising, so a scoring failure doesn't mask the run.
    """
    try:
        from LHCRecastBench.evaluation._resolve import resolve_run
        from LHCRecastBench.evaluation.score import score_run
    except ImportError as exc:
        return {"error": f"eval import failed: {exc}"}
    try:
        rp = resolve_run(workspace)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return {"error": str(exc)}
    return score_run(rp)


# Scoring hook type — injectable via launch_single_run(..., score=...).
ScoreFn = Callable[[Path], dict]


def launch_single_run(
    agent_name: str,
    build_prompt: PromptBuilder,
    repo_root: Path,
    argv: list[str] | None = None,
    score: ScoreFn | None = None,
) -> int:
    """Run one agent session end-to-end. Returns exit code.

    agent_name    — matches a directory under agents/ (e.g. "simple").
    build_prompt  — callable(paper_ref) -> prompt string.
    repo_root     — absolute repo root; usually Path(__file__).resolve().parents[2]
                    from the caller.
    argv          — override sys.argv for testing; None uses sys.argv[1:].
    score         — optional scorer: (workspace) -> dict. Defaults to
                    _default_score which calls the in-repo LHCRecastBench
                    evaluator. Pass a no-op `lambda w: {}` to skip scoring.
    """
    _score = score or _default_score
    args, parser = _parse_args(agent_name, argv)
    _resolve(args, parser)
    task_id = args.task
    effort_label, max_thinking = resolve_effort(args.effort)

    try:
        task_toml = validate_launch_inputs(repo_root, task_id)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    paper_ref = task_toml["task"]["paper"]

    if args.run_name:
        info = {
            "agent_id": args.run_name,
            "run_dir": args.run_name,
            "task_id": task_id,
            "paper_ref": paper_ref,
            "agent": agent_name,
            "runner": args.runner,
            "model": args.model or args.runner,
        }
        run_dir = args.run_name
    else:
        info = generate_run_info(
            task_id=task_id,
            agent_name=agent_name,
            runner_name=args.runner,
            model_name=args.model or args.runner,
            paper_ref=paper_ref,
        )
        run_dir = info["run_dir"]
    info.update(
        {
            "effort": effort_label,
            "max_thinking_tokens": max_thinking,
            "sandbox": args.sandbox or "auto",
        }
    )

    print(f"Setting up workspace: {run_dir}")
    print(
        f"Agent ID: {info['agent_id']}   "
        f"(effort={effort_label}, max_thinking_tokens={max_thinking})"
    )
    workspace = build_workspace(repo_root, agent_name, task_id, run_dir)
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
            effort_label=effort_label,
        )

        print("\nScoring results...")
        scores = _score(workspace)
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
