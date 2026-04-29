#!/usr/bin/env python3
"""Anneal recast loop.

Three roles, all running through the same agent runner (claude / codex /
gemini / aider) in the same sandbox + image:

  - Planner  — runs ONCE at start, writes <run_root>/plan.md.
  - Executor — runs every iteration in iter_NNN/workspace/, fills
               results/*.yml + analysis code + report.md.
  - Examiner — runs after every non-converged iteration in the same
               workspace as the executor it's reviewing. Reads the
               executor's code/report/results plus the paper, then
               rewrites agent_context/plan.md with concrete fixes for
               the next iteration. Maintains an examiner-private log of
               proposals + outcomes (proposals_log.md) that the executor
               never sees.

The examiner does NOT see eval/score.json, the reference histograms,
or any other side channel that would leak ground truth — by construction.
Convergence (and rollback) decisions are made by the controller from
score.json, outside both roles' view.

Annealing
---------
A single knob T ∈ [0, T0] cools across iterations under one of:

  - none    : T = 0 always (deterministic, every effect disabled)
  - linear  : T = T0 * (1 - i/max_iters)
  - cosine  : T = T0 * cos(π * i / (2 * max_iters))    (starts at T0, ends at 0)

T drives three coupled effects:

  1. Carry-forward depth (mechanical). High T wipes more state between
     iterations to force re-derivation; low T preserves everything.
  2. Stochastic rollback (mechanical, controller-side). On regression,
     accept the new iter with prob exp(-Δ/T); else rewind the next
     iter's seed to the best-so-far iter's workspace.
  3. Prompt language (soft). Executor + examiner prompts are stamped
     with the current T and a one-liner about how to behave.

Layout
------

  runs/<runner>_<model>/<task_id>_<id>/
    plan.md                    living document, evolves across iters
    planner_log.txt
    run_info.json
    examiner_state/
      proposals_log.md         examiner-only memory (never seeded into
                               executor or planner workspaces)
    validation/
      iter_001/
        workspace/             built fresh per iter, post-seeded
        session_log.txt        executor
        examiner_log.txt
        eval/score.json
      iter_002/...
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_runtime.launch import _default_score, _run_offline_evals
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
from agents.anneal.runtime.controller.roles import (
    build_examiner_prompt,
    build_executor_prompt,
    build_planner_prompt,
)


DEFAULT_THRESHOLD = 0.5  # min(shape, norm) ≥ this and iter ≥ min_iters → converged.
DEFAULT_T0 = 1.0
DEFAULT_SCHEDULE = "cosine"
DEFAULT_MAX_ROLLBACKS = 2
DEFAULT_ROLLBACK_NOISE_FLOOR = 0.05


# ── CLI / config ───────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Anneal recast loop: planner + iter(executor → score → examiner) "
        "with a temperature schedule and stochastic rollback.",
    )
    parser.add_argument("--config", default="")
    parser.add_argument("--task", default=None, help="Task id (e.g. sus-16-046-...)")
    parser.add_argument("--runner", default=None, choices=sorted(RUNNERS))
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--examiner-model", default=None)
    parser.add_argument("--examiner-effort", default=None)
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
    parser.add_argument(
        "--anneal-t0",
        type=float,
        default=None,
        help=f"Initial temperature for the schedule (default: {DEFAULT_T0}).",
    )
    parser.add_argument(
        "--anneal-schedule",
        choices=["none", "linear", "cosine"],
        default=None,
        help=f"Temperature schedule shape (default: {DEFAULT_SCHEDULE}). "
        "'none' disables annealing entirely (T=0, no rollback, max carry-forward).",
    )
    parser.add_argument(
        "--max-rollbacks",
        type=int,
        default=None,
        help=f"Cap on stochastic rollbacks per run (default: {DEFAULT_MAX_ROLLBACKS}).",
    )
    parser.add_argument(
        "--rollback-noise-floor",
        type=float,
        default=None,
        help="Min regression in min(shape,norm) to consider rollback "
        f"(default: {DEFAULT_ROLLBACK_NOISE_FLOOR}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for the rollback RNG (default: nondeterministic).",
    )
    return parser.parse_args(argv)


def _resolve_config(args: argparse.Namespace) -> dict:
    cfg = load_config(args.config) if args.config else {}
    args.task = args.task or cfg.get("task")
    args.runner = args.runner or cfg.get("runner") or "claude"
    args.model = args.model or cfg.get("model") or ""
    args.effort = args.effort or cfg.get("effort") or "medium"
    args.examiner_model = args.examiner_model or cfg.get("examiner_model") or args.model
    args.examiner_effort = args.examiner_effort or cfg.get("examiner_effort") or args.effort
    args.planner_effort = args.planner_effort or cfg.get("planner_effort") or "low"
    args.sandbox = args.sandbox or cfg.get("sandbox")
    args.max_iters = args.max_iters or cfg.get("max_iters") or 5
    args.min_iters = args.min_iters or cfg.get("min_iters") or 1
    args.combined_threshold = (
        args.combined_threshold
        if args.combined_threshold is not None
        else cfg.get("combined_threshold", DEFAULT_THRESHOLD)
    )
    args.anneal_t0 = (
        args.anneal_t0 if args.anneal_t0 is not None else cfg.get("anneal_t0", DEFAULT_T0)
    )
    args.anneal_schedule = args.anneal_schedule or cfg.get("anneal_schedule") or DEFAULT_SCHEDULE
    args.max_rollbacks = (
        args.max_rollbacks
        if args.max_rollbacks is not None
        else cfg.get("max_rollbacks", DEFAULT_MAX_ROLLBACKS)
    )
    args.rollback_noise_floor = (
        args.rollback_noise_floor
        if args.rollback_noise_floor is not None
        else cfg.get("rollback_noise_floor", DEFAULT_ROLLBACK_NOISE_FLOOR)
    )
    if not args.task:
        sys.exit("anneal: --task is required (CLI or --config <yaml>:task)")
    return cfg


# ── Annealing schedule ─────────────────────────────────────────────────────


def temperature(iter_index: int, max_iters: int, t0: float, schedule: str) -> float:
    """Return T at the *start* of iter `iter_index` (1-based).

    Schedules:
      none    → T = 0
      linear  → T = t0 * (1 - (i-1)/max_iters)            (T0 at iter 1, → 0 at end)
      cosine  → T = t0 * cos(π * (i-1) / (2 * max_iters)) (T0 at iter 1, → 0 at end)
    """
    if schedule == "none" or t0 <= 0 or max_iters <= 1:
        return 0.0
    frac = max(0, min(iter_index - 1, max_iters)) / max_iters
    if schedule == "linear":
        return float(t0) * (1.0 - frac)
    if schedule == "cosine":
        return float(t0) * math.cos(math.pi * frac / 2.0)
    return 0.0


def carry_forward_depth(t: float) -> tuple[bool, bool, bool]:
    """Map T → (carry_results, carry_analysis, carry_sims_data).

      T > 0.8        : results + report.md only        (force re-derivation)
      0.5 < T ≤ 0.8 : + analysis.py / analysis/ / datasets.yaml
      T ≤ 0.5        : + sims/ / data/ (everything)

    results and report.md always carry forward — the executor needs them
    to know what was attempted.
    """
    return True, t <= 0.8, t <= 0.5


def should_rollback(
    delta: float,
    t: float,
    rng: random.Random,
) -> bool:
    """Classic Metropolis: accept worse with prob exp(-Δ/T), else rollback.

    Δ here is `best_min - current_min` (positive when current iter regressed).
    At T → 0 this is strict greedy (always rollback on regression). At
    high T regressions are tolerated (rarely rollback).
    """
    if delta <= 0 or t <= 0:
        return False
    accept_prob = math.exp(-delta / t)
    return rng.random() > accept_prob


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
    """Copy agents/anneal/runtime/roles/<name>.md into agent_context/.

    build_workspace excludes everything under runtime/, so role cards
    never auto-flow into the workspace. We seed them per-role on demand.
    """
    src = repo_root / "agents" / "anneal" / "runtime" / "roles" / f"{name}.md"
    if src.is_file():
        (workspace / "agent_context" / f"{name}.md").write_text(src.read_text())


_PROPOSALS_LOG_HEADER = """# Examiner proposals log

Examiner-only running record of fixes proposed across iterations and
whether each one improved the fit. The executor never sees this file.

| Iter | Proposed fix | Outcome |
|------|--------------|---------|
"""


def _ensure_proposals_log(run_root: Path) -> Path:
    """Initialize <run_root>/examiner_state/proposals_log.md if missing.

    Returns the canonical path. The file is the source of truth — each
    examiner step copies it in, edits, and copies it back.
    """
    state_dir = run_root / "examiner_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "proposals_log.md"
    if not path.exists():
        path.write_text(_PROPOSALS_LOG_HEADER)
    return path


def _seed_iter_workspace(
    workspace: Path,
    plan_md: Path,
    prev_workspace: Path | None,
    *,
    carry_analysis: bool,
    carry_sims: bool,
) -> None:
    """Drop the current plan.md into agent_context/, then (if iter > 1)
    copy the previous iter's outputs onto the freshly-built workspace
    according to the temperature-driven carry-forward depth.

    Always carried (when prev_workspace is given):
      results/**             — partially-filled histogram + any sub-files
      report.md              — prior self-report

    Carried iff carry_analysis (T ≤ 0.8):
      analysis.py            — top-level analysis script
      analysis/**            — multi-file analysis package
      datasets.yaml          — sample inventory

    Carried iff carry_sims (T ≤ 0.5):
      sims/, data/           — selected events / sim cards
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

    report = prev_workspace / "report.md"
    if report.is_file():
        shutil.copy2(report, workspace / "report.md")

    if carry_analysis:
        for name in ("analysis.py", "datasets.yaml"):
            src = prev_workspace / name
            if src.is_file():
                shutil.copy2(src, workspace / name)
        analysis_dir = prev_workspace / "analysis"
        if analysis_dir.is_dir():
            shutil.copytree(analysis_dir, workspace / "analysis", dirs_exist_ok=True)

    if carry_sims:
        for name in ("sims", "data"):
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
    planner_run_dir = f"{run_root.relative_to(repo_root / 'runs')}/planner_workspace_tmp"
    ws = build_workspace(repo_root, agent_name, task_id, planner_run_dir)
    _seed_role_card(ws, repo_root, "PLANNER")
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
    return plan_dst


def _executor_step(
    workspace: Path,
    repo_root: Path,
    paper_ref: str,
    task_id: str,
    iter_index: int,
    has_prior: bool,
    t: float,
    args: argparse.Namespace,
    extra_ro_binds: list[Path],
    log: Path,
) -> int:
    print(f"\n=== Iter {iter_index:03d} executor (T={t:.2f}) ===")
    return _run_role(
        workspace=workspace,
        repo_root=repo_root,
        prompt=build_executor_prompt(paper_ref, task_id, iter_index, has_prior, t),
        runner_name=args.runner,
        model=args.model or None,
        effort=args.effort,
        sandbox=args.sandbox,
        output_file=log,
        extra_ro_binds=extra_ro_binds,
    )


def _examiner_step(
    workspace: Path,
    repo_root: Path,
    run_root: Path,
    paper_ref: str,
    task_id: str,
    iter_index: int,
    t: float,
    args: argparse.Namespace,
    extra_ro_binds: list[Path],
    log: Path,
) -> int:
    print(f"\n=== Iter {iter_index:03d} examiner (T={t:.2f}) ===")
    _seed_role_card(workspace, repo_root, "EXAMINER")
    proposals_src = _ensure_proposals_log(run_root)
    proposals_in_ws = workspace / "agent_context" / "proposals_log.md"
    shutil.copy2(proposals_src, proposals_in_ws)

    rc = _run_role(
        workspace=workspace,
        repo_root=repo_root,
        prompt=build_examiner_prompt(paper_ref, task_id, iter_index, t),
        runner_name=args.runner,
        model=args.examiner_model or None,
        effort=args.examiner_effort,
        sandbox=args.sandbox,
        output_file=log,
        extra_ro_binds=extra_ro_binds,
    )

    # Persist the examiner's edited log back to the run-root state dir
    # so the next iter sees the updated table. The executor never sees
    # this file (we only copy it into examiner workspaces).
    if proposals_in_ws.is_file():
        shutil.copy2(proposals_in_ws, proposals_src)
    return rc


# ── Main loop ──────────────────────────────────────────────────────────────


def main() -> int:
    args = _parse_args(None)
    _resolve_config(args)
    repo_root = Path(__file__).resolve().parents[4]

    try:
        task_toml = validate_launch_inputs(repo_root, args.task)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(f"anneal: {exc}")
    paper_ref = task_toml["task"]["paper"]

    info = generate_run_info(
        task_id=args.task,
        agent_name="anneal",
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
            "examiner_model": args.examiner_model,
            "anneal_t0": args.anneal_t0,
            "anneal_schedule": args.anneal_schedule,
            "max_rollbacks": args.max_rollbacks,
            "sandbox": args.sandbox or "auto",
        }
    )
    run_root = repo_root / "runs" / info["run_dir"]
    run_root.mkdir(parents=True, exist_ok=True)
    write_run_info(run_root, info)
    print(f"Run: {run_root}")
    print(
        f"Anneal: T0={args.anneal_t0} schedule={args.anneal_schedule} "
        f"max_rollbacks={args.max_rollbacks}"
    )

    runtime_dir = repo_root / "agents" / "anneal" / "runtime"
    shared_runtime = repo_root / "agent_runtime"
    extra_ro_binds = [runtime_dir, shared_runtime]

    rng = random.Random(args.seed) if args.seed is not None else random.Random()

    started_at = time.time()
    exit_code = 0
    last_score: dict | None = None
    best_iter: int | None = None
    best_min: float | None = None
    best_workspace: Path | None = None
    n_rollbacks = 0

    try:
        plan_md = _planner_step(
            repo_root, run_root, "anneal", args.task, paper_ref, args, extra_ro_binds
        )

        # `next_seed_workspace` is what we copy outputs from into the next
        # iter — usually the just-finished iter's workspace, but on a
        # stochastic rollback it points at the best-so-far.
        next_seed_workspace: Path | None = None
        for i in range(1, args.max_iters + 1):
            t = temperature(i, args.max_iters, args.anneal_t0, args.anneal_schedule)
            _, carry_analysis, carry_sims = carry_forward_depth(t)

            iter_dir = run_root / "validation" / f"iter_{i:03d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            iter_run_dir = f"{run_root.relative_to(repo_root / 'runs')}/validation/iter_{i:03d}"
            ws = build_workspace(repo_root, "anneal", args.task, iter_run_dir)
            _seed_iter_workspace(
                ws,
                plan_md,
                next_seed_workspace,
                carry_analysis=carry_analysis,
                carry_sims=carry_sims,
            )

            rc_exec = _executor_step(
                ws,
                repo_root,
                paper_ref,
                args.task,
                i,
                has_prior=(next_seed_workspace is not None),
                t=t,
                args=args,
                extra_ro_binds=extra_ro_binds,
                log=iter_dir / "session_log.txt",
            )
            if rc_exec != 0:
                print(f"  Iter {i:03d}: executor exit {rc_exec}")

            scores = _default_score(ws)
            iter_eval_dir = iter_dir / "eval"
            iter_eval_dir.mkdir(exist_ok=True)
            (iter_eval_dir / "score.json").write_text(json.dumps(scores, indent=2))
            last_score = scores
            # Offline (no-LLM) evals per iter: plots + summary.md, same as the
            # single-shot path. Best-effort; never blocks the loop.
            try:
                _run_offline_evals(ws, iter_eval_dir)
            except Exception as exc:  # noqa: BLE001
                print(f"  WARN: offline evals failed for iter {i:03d}: {exc}")
            shape = scores.get("overall_shape")
            norm = scores.get("overall_normalization")
            combined = scores.get("overall_combined")
            cur_min = min(shape, norm) if (shape is not None and norm is not None) else None
            if combined is not None:
                print(
                    f"  Iter {i:03d} score: combined={combined:.2f}  "
                    f"shape={shape:.2f}  norm={norm:.2f}  T={t:.2f}"
                )

            if cur_min is not None and (best_min is None or cur_min > best_min):
                best_min = cur_min
                best_iter = i
                best_workspace = ws

            if cur_min is not None and cur_min >= args.combined_threshold and i >= args.min_iters:
                print(
                    f"  CONVERGED at iter {i:03d} "
                    f"(shape={shape:.2f}, norm={norm:.2f}, "
                    f"both ≥ {args.combined_threshold})"
                )
                break

            if i >= args.max_iters:
                break

            rc_crit = _examiner_step(
                ws,
                repo_root,
                run_root,
                paper_ref,
                args.task,
                i,
                t,
                args=args,
                extra_ro_binds=extra_ro_binds,
                log=iter_dir / "examiner_log.txt",
            )
            if rc_crit != 0:
                print(f"  Iter {i:03d}: examiner exit {rc_crit}")

            new_plan = ws / "agent_context" / "plan.md"
            if new_plan.is_file():
                shutil.copy2(new_plan, plan_md)
                print(f"  plan.md updated → {plan_md}")
            else:
                print("  WARN: examiner did not produce updated plan.md; reusing prior")

            # Stochastic rollback decision — controller only, score never
            # leaks to any agent role.
            next_seed_workspace = ws
            if (
                cur_min is not None
                and best_min is not None
                and best_workspace is not None
                and best_iter != i
                and n_rollbacks < args.max_rollbacks
            ):
                delta = best_min - cur_min
                if delta > args.rollback_noise_floor and should_rollback(delta, t, rng):
                    n_rollbacks += 1
                    next_seed_workspace = best_workspace
                    note = (
                        f"\n\n> **Rollback notice (iter {i+1:03d}):** "
                        f"iter {i:03d} regressed by Δ={delta:.2f} at T={t:.2f}; "
                        f"reseeding executor state from iter {best_iter:03d}. "
                        f"Examiner's fixes apply to that earlier state.\n"
                    )
                    plan_md.write_text(plan_md.read_text() + note)
                    print(
                        f"  ROLLBACK ({n_rollbacks}/{args.max_rollbacks}): "
                        f"next iter reseeds from iter {best_iter:03d} "
                        f"(Δ={delta:.2f}, T={t:.2f})"
                    )

    except BaseException:
        exit_code = 1
        raise
    finally:
        all_logs: list[Path] = [run_root / "planner_log.txt"]
        all_logs += sorted((run_root / "validation").glob("iter_*/session_log.txt"))
        all_logs += sorted((run_root / "validation").glob("iter_*/examiner_log.txt"))
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
