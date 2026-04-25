#!/usr/bin/env python3
"""Sisyphus recast loop.

Three roles, all running through the same agent runner (claude / codex /
gemini / aider) in the same sandbox + image:

  - Planner  — runs ONCE at start, writes <run_root>/plan.md.
  - Executor — runs every iteration in iter_NNN/workspace/, fills
               results/*.yml + analysis code + report.md.
  - Critic   — runs after every non-converged iteration in the same
               workspace as the executor it's reviewing. Reads the
               executor's code/report/results plus the paper, then
               rewrites agent_context/plan.md with concrete fixes for
               the next iteration. The updated plan.md is copied back
               to <run_root>/plan.md as the new source of truth.

The critic does NOT see eval/score.json, the reference histograms, or
any other side channel that would leak ground truth — by construction.
Convergence is decided by the controller from score.json, outside both
roles' view.

Layout:

  runs/<runner>_<model>/<task_id>_<id>/
    plan.md                    living document, evolves across iters
    planner_log.txt
    run_info.json
    validation/
      iter_001/
        workspace/             built fresh per iter, post-seeded
        session_log.txt        executor
        critic_log.txt
        eval/score.json
      iter_002/...
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_runtime.launch import _default_score
from agent_runtime.naming import (
    finalize_run_info,
    generate_run_info,
    load_config,
    resolve_effort,
    validate_launch_inputs,
    write_run_info,
)
from agent_runtime.runners import RUNNERS, get_runner
from agent_runtime.workspace import build_workspace
from agents.sisyphus.runtime.controller.roles import (
    build_critic_prompt,
    build_executor_prompt,
    build_planner_prompt,
)


DEFAULT_THRESHOLD = 0.5  # min(shape, norm) ≥ this and iter ≥ min_iters → converged.


# ── CLI / config ───────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sisyphus recast loop: planner + iter(executor → score → critic).",
    )
    parser.add_argument("--config", default="")
    parser.add_argument("--task", default=None, help="Task id (e.g. sus-16-046-...)")
    parser.add_argument("--runner", default=None, choices=sorted(RUNNERS))
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--critic-model", default=None)
    parser.add_argument("--critic-effort", default=None)
    parser.add_argument("--planner-effort", default=None)
    parser.add_argument(
        "--sandbox",
        default=None,
        choices=["auto", "bwrap", "apptainer", "podman", "none"],
    )
    parser.add_argument("--max-iters", type=int, default=None)
    parser.add_argument("--min-iters", type=int, default=None)
    parser.add_argument(
        "--combined-threshold",
        type=float,
        default=None,
        help="Stop when min(shape, norm) ≥ this and iter ≥ min_iters (default: 0.5). "
        "Requires BOTH BC axes to clear the bar — geometric mean would let one "
        "bad axis hide behind a good one.",
    )
    return parser.parse_args(argv)


def _resolve_config(args: argparse.Namespace) -> dict:
    cfg = load_config(args.config) if args.config else {}
    args.task = args.task or cfg.get("task")
    args.runner = args.runner or cfg.get("runner") or "claude"
    args.model = args.model or cfg.get("model") or ""
    args.effort = args.effort or cfg.get("effort") or "medium"
    args.critic_model = args.critic_model or cfg.get("critic_model") or args.model
    args.critic_effort = args.critic_effort or cfg.get("critic_effort") or args.effort
    args.planner_effort = args.planner_effort or cfg.get("planner_effort") or "low"
    args.sandbox = args.sandbox or cfg.get("sandbox")
    args.max_iters = args.max_iters or cfg.get("max_iters") or 5
    args.min_iters = args.min_iters or cfg.get("min_iters") or 1
    args.combined_threshold = (
        args.combined_threshold
        if args.combined_threshold is not None
        else cfg.get("combined_threshold", DEFAULT_THRESHOLD)
    )
    if not args.task:
        sys.exit("sisyphus: --task is required (CLI or --config <yaml>:task)")
    return cfg


# ── Sandbox invocation ─────────────────────────────────────────────────────


def _run_role(
    *,
    workspace: Path,
    repo_root: Path,
    prompt: str,
    runner_name: str,
    model: str | None,
    effort: str,
    sandbox: str | None,
    output_file: Path,
    extra_ro_binds: list[Path] | None = None,
) -> int:
    """Invoke a single agent run in the sandbox. Returns exit code (0 on
    success). Mirrors agent_runtime.launch._run_in_sandbox."""
    from agent_runtime.sandbox import sandbox_command

    effort_label, max_thinking = resolve_effort(effort)
    runner = get_runner(runner_name)
    inner_cmd = runner.build_command(
        prompt,
        workspace,
        model,
        allowlist=None,
        max_thinking_tokens=max_thinking,
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
        extra_ro_binds=extra_ro_binds or [],
        sandbox=sandbox,
    )
    try:
        runner.run(cmd, prompt, workspace, env, output_file)
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"  Agent exited with code {exc.returncode}", file=sys.stderr)
        return int(exc.returncode or 1)
    finally:
        cleanup()


# ── Workspace seeding ──────────────────────────────────────────────────────


def _seed_role_card(workspace: Path, repo_root: Path, name: str) -> None:
    """Copy agents/sisyphus/runtime/roles/<name>.md into agent_context/.

    build_workspace excludes everything under runtime/, so role cards
    never auto-flow into the workspace. We seed them per-role on demand.
    """
    src = repo_root / "agents" / "sisyphus" / "runtime" / "roles" / f"{name}.md"
    if src.is_file():
        (workspace / "agent_context" / f"{name}.md").write_text(src.read_text())


_CARRY_FORWARD_FILES = ("analysis.py", "datasets.yaml", "report.md")
_CARRY_FORWARD_DIRS = ("analysis", "sims", "data")


def _seed_iter_workspace(
    workspace: Path,
    plan_md: Path,
    prev_workspace: Path | None,
) -> None:
    """Drop the current plan.md into agent_context/, and (if iter > 1) copy
    the previous iter's outputs on top of the freshly-built workspace.

    Carries forward:
      results/**             — partially-filled histogram + any sub-files
      analysis.py            — top-level analysis script
      analysis/**            — multi-file analysis package
      datasets.yaml          — sample inventory
      report.md              — prior self-report
      sims/, data/           — selected events / sim cards if present
    """
    ctx = workspace / "agent_context"
    ctx.mkdir(parents=True, exist_ok=True)
    if plan_md.is_file():
        shutil.copy2(plan_md, ctx / "plan.md")

    if not (prev_workspace and prev_workspace.is_dir()):
        return

    # results/: file + subdir overlay onto the new iter's null-filled template.
    prev_results = prev_workspace / "results"
    if prev_results.is_dir():
        for src in prev_results.rglob("*"):
            if src.is_file():
                rel = src.relative_to(prev_results)
                dest = workspace / "results" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

    # Top-level files the executor expects (TASK.md/AGENTS.md/etc. came from
    # build_workspace; we only need to carry forward files the agent wrote).
    for name in _CARRY_FORWARD_FILES:
        src = prev_workspace / name
        if src.is_file():
            shutil.copy2(src, workspace / name)

    # Whole directories (analysis package, sim outputs, data files)
    for name in _CARRY_FORWARD_DIRS:
        src = prev_workspace / name
        if src.is_dir():
            shutil.copytree(src, workspace / name, dirs_exist_ok=True)


# ── Roles ──────────────────────────────────────────────────────────────────


def _planner_step(
    repo_root: Path,
    run_root: Path,
    agent_name: str,
    task_id: str,
    paper_ref: str,
    args: argparse.Namespace,
    extra_ro_binds: list[Path],
) -> Path:
    """Run the planner ONCE. Returns path to <run_root>/plan.md."""
    print("\n=== Planner ===")
    # Build a minimal workspace under <run_root>/planner_workspace/.
    planner_run_dir = f"{run_root.relative_to(repo_root / 'runs')}/planner_workspace_tmp"
    # build_workspace insists on `runs/<run_dir>/workspace`, so we point
    # it at a sub-path under run_root and grab the workspace it produces.
    ws = build_workspace(repo_root, agent_name, task_id, planner_run_dir)
    _seed_role_card(ws, repo_root, "PLANNER")
    # Empty placeholder plan.md so the prompt's reference is not confusing.
    (ws / "agent_context" / "plan.md").write_text(
        "# (empty — planner has not written a plan yet)\n"
    )
    log = run_root / "planner_log.txt"
    rc = _run_role(
        workspace=ws,
        repo_root=repo_root,
        prompt=build_planner_prompt(paper_ref, task_id),
        runner_name=args.runner,
        model=args.model or None,
        effort=args.planner_effort,
        sandbox=args.sandbox,
        output_file=log,
        extra_ro_binds=extra_ro_binds,
    )
    if rc != 0:
        print(f"  WARN: planner exit {rc}; using whatever was written")

    plan_src = ws / "agent_context" / "plan.md"
    plan_dst = run_root / "plan.md"
    if plan_src.is_file():
        shutil.copy2(plan_src, plan_dst)
        print(f"  plan.md → {plan_dst}")
    else:
        print("  WARN: planner did not write agent_context/plan.md; seeding empty plan")
        plan_dst.write_text("# (planner produced no plan)\n")
    # Clean up planner workspace (stays under <run_root>/planner_workspace_tmp/)
    return plan_dst


def _executor_step(
    workspace: Path,
    repo_root: Path,
    paper_ref: str,
    task_id: str,
    iter_index: int,
    has_prior: bool,
    args: argparse.Namespace,
    extra_ro_binds: list[Path],
    log: Path,
) -> int:
    print(f"\n=== Iter {iter_index:03d} executor ===")
    return _run_role(
        workspace=workspace,
        repo_root=repo_root,
        prompt=build_executor_prompt(paper_ref, task_id, iter_index, has_prior),
        runner_name=args.runner,
        model=args.model or None,
        effort=args.effort,
        sandbox=args.sandbox,
        output_file=log,
        extra_ro_binds=extra_ro_binds,
    )


def _critic_step(
    workspace: Path,
    repo_root: Path,
    paper_ref: str,
    task_id: str,
    iter_index: int,
    args: argparse.Namespace,
    extra_ro_binds: list[Path],
    log: Path,
) -> int:
    print(f"\n=== Iter {iter_index:03d} critic ===")
    _seed_role_card(workspace, repo_root, "CRITIC")
    return _run_role(
        workspace=workspace,
        repo_root=repo_root,
        prompt=build_critic_prompt(paper_ref, task_id, iter_index),
        runner_name=args.runner,
        model=args.critic_model or None,
        effort=args.critic_effort,
        sandbox=args.sandbox,
        output_file=log,
        extra_ro_binds=extra_ro_binds,
    )


# ── Main loop ──────────────────────────────────────────────────────────────


def main() -> int:
    args = _parse_args(None)
    _resolve_config(args)
    repo_root = Path(__file__).resolve().parents[4]

    try:
        task_toml = validate_launch_inputs(repo_root, args.task)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(f"sisyphus: {exc}")
    paper_ref = task_toml["task"]["paper"]

    info = generate_run_info(
        task_id=args.task,
        agent_name="sisyphus",
        runner_name=args.runner,
        model_name=args.model or args.runner,
        paper_ref=paper_ref,
    )
    info.update(
        {
            "effort": resolve_effort(args.effort)[0],
            "max_iters": args.max_iters,
            "min_iters": args.min_iters,
            "combined_threshold": args.combined_threshold,
            "critic_model": args.critic_model,
            "sandbox": args.sandbox or "auto",
        }
    )
    run_root = repo_root / "runs" / info["run_dir"]
    run_root.mkdir(parents=True, exist_ok=True)
    write_run_info(run_root, info)
    print(f"Run: {run_root}")

    runtime_dir = repo_root / "agents" / "sisyphus" / "runtime"
    shared_runtime = repo_root / "agent_runtime"
    extra_ro_binds = [runtime_dir, shared_runtime]

    started_at = time.time()
    exit_code = 0
    last_score: dict | None = None

    try:
        # 1. Planner — once at the start.
        plan_md = _planner_step(
            repo_root, run_root, "sisyphus", args.task, paper_ref, args, extra_ro_binds
        )

        # 2. Iter loop.
        prev_workspace: Path | None = None
        for i in range(1, args.max_iters + 1):
            iter_dir = run_root / "validation" / f"iter_{i:03d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            iter_run_dir = f"{run_root.relative_to(repo_root / 'runs')}/validation/iter_{i:03d}"
            ws = build_workspace(repo_root, "sisyphus", args.task, iter_run_dir)
            _seed_iter_workspace(ws, plan_md, prev_workspace)

            # Executor
            rc_exec = _executor_step(
                ws,
                repo_root,
                paper_ref,
                args.task,
                i,
                has_prior=(i > 1),
                args=args,
                extra_ro_binds=extra_ro_binds,
                log=iter_dir / "session_log.txt",
            )
            if rc_exec != 0:
                print(f"  Iter {i:03d}: executor exit {rc_exec}")

            # Score
            scores = _default_score(ws)
            (iter_dir / "eval").mkdir(exist_ok=True)
            (iter_dir / "eval" / "score.json").write_text(json.dumps(scores, indent=2))
            last_score = scores
            combined = scores.get("overall_combined")
            shape = scores.get("overall_shape")
            norm = scores.get("overall_normalization")
            if combined is not None:
                print(
                    f"  Iter {i:03d} score: combined={combined:.2f}  "
                    f"shape={shape:.2f}  norm={norm:.2f}"
                )

            # Convergence: BOTH shape and norm must clear the threshold
            # (min, not the geometric mean — otherwise a perfect shape can
            # mask a catastrophically wrong norm and vice versa).
            if (
                shape is not None
                and norm is not None
                and min(shape, norm) >= args.combined_threshold
                and i >= args.min_iters
            ):
                print(
                    f"  CONVERGED at iter {i:03d} "
                    f"(shape={shape:.2f}, norm={norm:.2f}, both ≥ {args.combined_threshold})"
                )
                prev_workspace = ws
                break

            # Critic — only if we'll actually run another iter.
            if i < args.max_iters:
                rc_crit = _critic_step(
                    ws,
                    repo_root,
                    paper_ref,
                    args.task,
                    i,
                    args=args,
                    extra_ro_binds=extra_ro_binds,
                    log=iter_dir / "critic_log.txt",
                )
                if rc_crit != 0:
                    print(f"  Iter {i:03d}: critic exit {rc_crit}")
                # The critic was supposed to overwrite agent_context/plan.md.
                # Promote it to <run_root>/plan.md as the new source of truth.
                new_plan = ws / "agent_context" / "plan.md"
                if new_plan.is_file():
                    shutil.copy2(new_plan, plan_md)
                    print(f"  plan.md updated → {plan_md}")
                else:
                    print("  WARN: critic did not produce updated plan.md; reusing prior")

            prev_workspace = ws

    except BaseException:
        exit_code = 1
        raise
    finally:
        # Tally token usage across all role logs (planner + every iter's
        # executor + critic). Non-Claude logs parse to {} which is harmless.
        all_logs: list[Path] = [run_root / "planner_log.txt"]
        all_logs += sorted((run_root / "validation").glob("iter_*/session_log.txt"))
        all_logs += sorted((run_root / "validation").glob("iter_*/critic_log.txt"))
        finalize_run_info(
            run_root,
            exit_code=exit_code,
            started_at=started_at,
            scores=last_score,
            session_logs=[p for p in all_logs if p.exists()],
        )

    print(f"\nDone. Run: {run_root}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
