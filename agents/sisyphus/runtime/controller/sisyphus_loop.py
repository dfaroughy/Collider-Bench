#!/usr/bin/env python3
"""Sisyphus recast loop.

Three roles per run:
  - Planner  — runs once at the start, writes <run_dir>/plan.md.
  - Executor — runs every iteration, produces HEPRecastData/*, analysis.py, report.md.
  - Critic   — runs after every non-converged iteration, writes critique.md seeded
               into the next executor's workspace.

The planner and critic run on a cheaper model (e.g. Sonnet) with Read+Write tools
only; the executor runs on the main model with the full tool surface.

Workspaces
  - <run_dir>/planner_workspace/        ephemeral, holds plan.md target
  - <run_dir>/plan.md                   canonical (survives across iters)
  - <run_dir>/workspace/                executor's working tree (iteration N)
  - <run_dir>/validation/iter_NNN/      archived executor workspace
  - <run_dir>/validation/iter_NNN/critic_workspace/   ephemeral, holds critique.md
  - <run_dir>/validation/iter_NNN/critique.md         promoted; seeds next iter
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.runners import Runner, get_runner, RUNNERS


EXPECTED_ARTIFACTS = [
    "HEPRecastData",
    "analysis.py",
    "datasets.yaml",
    "report.md",
]


@dataclass
class IterationResult:
    directory: Path
    status: str
    score: float | None
    overall_pass: bool | None


# ── Small helpers ──────────────────────────────────────────────────────────


def _parse_status(report_path: Path) -> str:
    if not report_path.exists():
        return "CONTINUE"
    m = re.search(r"Status:\s*`?(STOP|CONTINUE)`?", report_path.read_text(), re.I)
    return m.group(1).upper() if m else "CONTINUE"


def _has_analysis_code(ws: Path) -> bool:
    if (ws / "analysis.py").exists():
        return True
    a = ws / "analysis"
    return a.is_dir() and any(a.rglob("*.py"))


def _has_filled_hepdata(ws: Path) -> bool:
    d = ws / "HEPRecastData"
    if not d.is_dir():
        return False
    for y in d.glob("*.yaml"):
        txt = y.read_text()
        if re.search(r"value:\s*[-+]?\d", txt):
            return True
    return False


def _score_iteration(iter_dir: Path, paper_ref: str) -> tuple[bool | None, dict]:
    hep = iter_dir / "HEPRecastData"
    if not hep.is_dir():
        return None, {}
    try:
        from LHCRecastBench.evaluation.score import score_recast

        scores = score_recast(paper_ref, str(hep))
    except Exception as exc:
        print(f"  scoring failed: {exc}")
        return None, {}
    (iter_dir / "score.json").write_text(json.dumps(scores, indent=2))
    return scores.get("overall_pass"), scores


# ── Sandbox plumbing ───────────────────────────────────────────────────────


def _run_in_sandbox(
    repo_root: Path,
    workspace: Path,
    prompt: str,
    runner: Runner,
    model: str | None,
    output_file: Path,
    max_thinking_tokens: int | None,
    allowlist: str | None,
    sandbox: str | None,
    extra_ro_binds: list[Path] | None = None,
) -> None:
    inner_cmd = runner.build_command(
        prompt,
        workspace,
        model,
        allowlist=allowlist,
        max_thinking_tokens=max_thinking_tokens,
    )
    env = os.environ.copy()
    env["PATH"] = str(workspace / "bin") + ":" + env.get("PATH", "")
    env["PYTHONPATH"] = str(repo_root) + ":" + env.get("PYTHONPATH", "")
    env["REPO_ROOT"] = str(repo_root)

    from agent_runtime.sandbox import sandbox_command

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=extra_ro_binds or [],
        sandbox=sandbox,
    )
    try:
        runner.run(cmd, prompt, workspace, env, output_file)
    finally:
        cleanup()


# ── Planner ────────────────────────────────────────────────────────────────


def _setup_planner_workspace(repo_root: Path, run_dir: Path, paper_ref: str) -> Path:
    """Create a small workspace containing paper PDF, template YAMLs, PLANNER.md."""
    ws = run_dir / "planner_workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)

    # Role card
    roles_dir = repo_root / "agents" / "sisyphus" / "runtime" / "roles"
    shutil.copy2(roles_dir / "PLANNER.md", ws / "PLANNER.md")

    # Paper PDF
    pdf = (
        repo_root
        / "LHCRecastBench"
        / "papers"
        / paper_ref
        / "for_agent"
        / "papers"
        / f"{paper_ref}.pdf"
    )
    papers = ws / "papers"
    papers.mkdir()
    if pdf.exists():
        shutil.copy2(pdf, papers / f"{paper_ref}.pdf")

    # Null-valued templates (what the executor must fill)
    tmpl_src = repo_root / "LHCRecastBench" / "papers" / paper_ref / "for_agent" / "HEPRecastData"
    if tmpl_src.is_dir():
        shutil.copytree(tmpl_src, ws / "HEPRecastData_templates")

    # Empty target
    (ws / "plan.md").write_text("")
    return ws


def _run_planner(
    repo_root: Path,
    run_dir: Path,
    paper_ref: str,
    runner: Runner,
    model: str | None,
    effort_max_tokens: int,
    sandbox_choice: str | None,
) -> Path | None:
    """Run the planner. Returns path to the promoted plan.md, or None on failure."""
    from .roles import build_planner_prompt

    ws = _setup_planner_workspace(repo_root, run_dir, paper_ref)
    prompt = build_planner_prompt(paper_ref)
    (ws / "prompt.txt").write_text(prompt)
    output_file = ws / "session_log.txt"

    print(f"[planner] running (model={model or 'default'}, effort≈{effort_max_tokens})")
    try:
        _run_in_sandbox(
            repo_root,
            ws,
            prompt,
            runner,
            model,
            output_file,
            max_thinking_tokens=effort_max_tokens,
            allowlist="Read Write Glob Grep",
            sandbox=sandbox_choice,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[planner] failed: {exc}")
        return None

    plan_src = ws / "plan.md"
    if not plan_src.exists() or plan_src.stat().st_size == 0:
        print("[planner] produced no plan.md — continuing without plan")
        return None

    plan_dest = run_dir / "plan.md"
    shutil.copy2(plan_src, plan_dest)
    # Keep the planner session log next to the run_dir for later audit
    shutil.copy2(output_file, run_dir / "planner_session_log.txt")
    shutil.rmtree(ws)
    print(f"[planner] wrote {plan_dest} ({plan_dest.stat().st_size} bytes)")
    return plan_dest


# ── Executor ───────────────────────────────────────────────────────────────


def _init_executor_workspace(repo_root: Path, run_dir: Path, paper_ref: str) -> Path:
    """Fresh executor workspace at <run_dir>/workspace/."""
    from agent_runtime.workspace import build_workspace

    return build_workspace(repo_root, "sisyphus", paper_ref, run_dir.name)


def _seed_executor_workspace(
    workspace: Path,
    plan_path: Path | None,
    previous_iter: Path | None,
) -> None:
    """Add plan.md + critique.md + prior-iter artifacts to an executor workspace."""
    ctx = workspace / "agent_context"
    ctx.mkdir(exist_ok=True)
    if plan_path is not None and plan_path.exists():
        shutil.copy2(plan_path, ctx / "plan.md")

    if previous_iter is None:
        return

    # Critique from the critic's review of the previous iter
    prev_critique = previous_iter / "critique.md"
    if prev_critique.exists():
        shutil.copy2(prev_critique, ctx / "critique.md")

    # analysis.py / analysis/
    prev_py = previous_iter / "analysis.py"
    if prev_py.exists():
        shutil.copy2(prev_py, workspace / "analysis.py")
    prev_a = previous_iter / "analysis"
    if prev_a.is_dir():
        dest = workspace / "analysis"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(prev_a, dest)

    # datasets.yaml
    prev_ds = previous_iter / "datasets.yaml"
    if prev_ds.exists():
        shutil.copy2(prev_ds, workspace / "datasets.yaml")

    # report.md → status.md (unverified)
    prev_report = previous_iter / "report.md"
    if prev_report.exists():
        shutil.copy2(prev_report, workspace / "status.md")

    # HEPRecastData (partially filled)
    prev_hep = previous_iter / "HEPRecastData"
    if prev_hep.is_dir():
        dest = workspace / "HEPRecastData"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(prev_hep, dest)

    # Prior score
    prev_score = previous_iter / "score.json"
    if prev_score.exists():
        shutil.copy2(prev_score, workspace / "previous_score.json")


def _run_executor(
    repo_root: Path,
    workspace: Path,
    paper_ref: str,
    iter_index: int,
    has_prior: bool,
    runner: Runner,
    model: str | None,
    max_thinking_tokens: int | None,
    sandbox_choice: str | None,
) -> None:
    from .roles import build_executor_prompt

    prompt = build_executor_prompt(paper_ref, iter_index, has_prior)
    (workspace / "prompt.txt").write_text(prompt)
    output_file = workspace / "session_log.txt"

    sisy_runtime = repo_root / "agents" / "sisyphus" / "runtime"
    simple_runtime = repo_root / "agents" / "simple" / "runtime"
    shared_runtime = repo_root / "agent_runtime"
    _run_in_sandbox(
        repo_root,
        workspace,
        prompt,
        runner,
        model,
        output_file,
        max_thinking_tokens=max_thinking_tokens,
        allowlist=None,
        sandbox=sandbox_choice,
        extra_ro_binds=[sisy_runtime, simple_runtime, shared_runtime],
    )


# ── Critic ─────────────────────────────────────────────────────────────────


def _setup_critic_workspace(
    repo_root: Path,
    iter_dir: Path,
    plan_path: Path | None,
    paper_ref: str,
) -> Path:
    """Assemble a read-mostly workspace with copies of the artifacts + reference."""
    ws = iter_dir / "critic_workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir()

    # Role card
    roles_dir = repo_root / "agents" / "sisyphus" / "runtime" / "roles"
    shutil.copy2(roles_dir / "CRITIC.md", ws / "CRITIC.md")

    # Executor artifacts
    artifacts = ws / "artifacts"
    artifacts.mkdir()
    for name in (
        "report.md",
        "score.json",
        "analysis.py",
        "datasets.yaml",
        "results.json",
        "status.md",
        "previous_score.json",
    ):
        src = iter_dir / name
        if src.exists():
            shutil.copy2(src, artifacts / name)
    analysis_dir = iter_dir / "analysis"
    if analysis_dir.is_dir():
        shutil.copytree(analysis_dir, artifacts / "analysis")
    hep_src = iter_dir / "HEPRecastData"
    if hep_src.is_dir():
        shutil.copytree(hep_src, artifacts / "HEPRecastData")

    # Paper reference answers
    ref_src = repo_root / "LHCRecastBench" / "papers" / paper_ref / "artifacts" / "HEPRecastData"
    if ref_src.is_dir():
        ref_dest = ws / "reference" / "HEPRecastData_reference"
        ref_dest.parent.mkdir()
        shutil.copytree(ref_src, ref_dest)

    # Plan + paper
    if plan_path is not None and plan_path.exists():
        shutil.copy2(plan_path, ws / "plan.md")
    pdf = (
        repo_root
        / "LHCRecastBench"
        / "papers"
        / paper_ref
        / "for_agent"
        / "papers"
        / f"{paper_ref}.pdf"
    )
    if pdf.exists():
        shutil.copy2(pdf, ws / "paper.pdf")

    # Empty target
    (ws / "critique.md").write_text("")
    return ws


def _run_critic(
    repo_root: Path,
    iter_dir: Path,
    paper_ref: str,
    iter_index: int,
    plan_path: Path | None,
    runner: Runner,
    model: str | None,
    effort_max_tokens: int,
    sandbox_choice: str | None,
) -> Path | None:
    """Run the critic for a given archived iteration. Returns critique.md path or None."""
    from .roles import build_critic_prompt

    ws = _setup_critic_workspace(repo_root, iter_dir, plan_path, paper_ref)
    prompt = build_critic_prompt(paper_ref, iter_index)
    (ws / "prompt.txt").write_text(prompt)
    output_file = ws / "session_log.txt"

    print(
        f"[critic ] iter {iter_index:03d} (model={model or 'default'}, effort≈{effort_max_tokens})"
    )
    try:
        _run_in_sandbox(
            repo_root,
            ws,
            prompt,
            runner,
            model,
            output_file,
            max_thinking_tokens=effort_max_tokens,
            allowlist="Read Write Glob Grep",
            sandbox=sandbox_choice,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[critic ] failed: {exc}")
        return None

    crit_src = ws / "critique.md"
    if not crit_src.exists() or crit_src.stat().st_size == 0:
        print("[critic ] produced no critique.md")
        return None

    crit_dest = iter_dir / "critique.md"
    shutil.copy2(crit_src, crit_dest)
    # Keep session log but drop the bulky artifact copies
    shutil.copy2(output_file, iter_dir / "critic_session_log.txt")
    shutil.rmtree(ws)
    print(f"[critic ] wrote {crit_dest} ({crit_dest.stat().st_size} bytes)")
    return crit_dest


# ── Main loop ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sisyphus recast loop: planner + iter(executor→score→critic).",
    )
    parser.add_argument("--config", default="")
    parser.add_argument("--paper-ref", default=None)
    parser.add_argument("--max-iters", type=int, default=None)
    parser.add_argument("--min-iters", type=int, default=None)
    parser.add_argument("--runner", default=None, choices=sorted(RUNNERS))
    parser.add_argument("--model", default=None, help="Main executor model")
    parser.add_argument(
        "--effort", default=None, help="Executor reasoning effort (low|medium|high|<int>)"
    )
    parser.add_argument("--critic-model", default=None)
    parser.add_argument("--critic-effort", default=None)
    parser.add_argument("--planner-effort", default=None)
    parser.add_argument("--sandbox", default=None, choices=["auto", "bwrap", "none"])
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]

    from agent_runtime.naming import (
        generate_run_info,
        resolve_effort,
        write_run_info,
        load_config,
        validate_launch_inputs,
        finalize_run_info,
    )

    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))

    args.paper_ref = args.paper_ref or cfg.get("paper")
    if not args.paper_ref:
        parser.error("--paper-ref is required (CLI or config)")
    try:
        validate_launch_inputs(repo_root, args.paper_ref)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    args.runner = args.runner or cfg.get("runner") or "claude"
    args.model = args.model or cfg.get("model") or ""
    args.effort = args.effort or cfg.get("effort") or "medium"
    args.critic_model = args.critic_model or cfg.get("critic_model") or args.model or ""
    args.critic_effort = args.critic_effort or cfg.get("critic_effort") or "low"
    args.planner_effort = args.planner_effort or cfg.get("planner_effort") or "low"
    args.sandbox = args.sandbox or cfg.get("sandbox")
    args.max_iters = args.max_iters if args.max_iters is not None else int(cfg.get("max_iters", 5))
    args.min_iters = args.min_iters if args.min_iters is not None else int(cfg.get("min_iters", 1))

    paper_ref = args.paper_ref
    exec_effort_label, exec_max_thinking = resolve_effort(args.effort)
    _, critic_max_thinking = resolve_effort(args.critic_effort)
    _, planner_max_thinking = resolve_effort(args.planner_effort)

    # Run directory and metadata
    run_info = generate_run_info(
        paper_ref=paper_ref,
        agent_name="sisyphus",
        model_name=args.model or args.runner,
    )
    recast_dir = run_info["run_dir"]
    run_info["runner"] = args.runner
    run_info["effort"] = exec_effort_label
    run_info["max_thinking_tokens"] = exec_max_thinking
    run_info["max_iters"] = args.max_iters
    run_info["sandbox"] = args.sandbox or "auto"
    run_info["critic_model"] = args.critic_model or None
    run_info["critic_effort"] = args.critic_effort
    run_info["planner_effort"] = args.planner_effort

    recast_path = repo_root / "runs" / recast_dir
    recast_path.mkdir(parents=True, exist_ok=True)
    validation_dir = recast_path / "validation"
    validation_dir.mkdir(exist_ok=True)
    write_run_info(recast_path, run_info)
    print(f"Recast directory: {recast_dir}")
    print(
        f"Agent ID: {run_info['agent_id']} "
        f"(executor effort={exec_effort_label}/{exec_max_thinking}; "
        f"critic effort={args.critic_effort}/{critic_max_thinking}; "
        f"planner effort={args.planner_effort}/{planner_max_thinking})"
    )

    runner = get_runner(args.runner)

    # ── Planner (runs once, always) ────────────────────────────────────────
    plan_path = _run_planner(
        repo_root,
        recast_path,
        paper_ref,
        runner,
        model=args.critic_model or args.model or None,
        effort_max_tokens=planner_max_thinking,
        sandbox_choice=args.sandbox,
    )

    # ── Iteration loop ─────────────────────────────────────────────────────
    previous_iter: Path | None = None
    history: list[IterationResult] = []
    sandbox_ws: Path | None = None
    iter_name: str | None = None
    started_at = time.time()
    final_scores: dict | None = None
    rc = 0

    def _collect_session_logs() -> list[Path]:
        logs: list[Path] = []
        if validation_dir.exists():
            for d in sorted(validation_dir.iterdir()):
                log = d / "session_log.txt"
                if log.exists():
                    logs.append(log)
        planner_log = recast_path / "planner_session_log.txt"
        if planner_log.exists():
            logs.append(planner_log)
        return logs

    def _finalize(exit_code: int, scores: dict | None) -> None:
        finalize_run_info(
            recast_path,
            exit_code=exit_code,
            started_at=started_at,
            scores=scores,
            session_logs=_collect_session_logs(),
        )

    _archive_lock = [False]

    def _emergency_archive(signum=None, frame=None):
        nonlocal sandbox_ws, iter_name
        if _archive_lock[0]:
            return
        _archive_lock[0] = True
        try:
            if sandbox_ws and sandbox_ws.exists() and iter_name:
                (sandbox_ws / "controller_interrupt.log").write_text(
                    f"Interrupted by signal={signum}\n"
                )
                dest = validation_dir / iter_name
                if dest.exists():
                    shutil.rmtree(dest)
                try:
                    shutil.move(str(sandbox_ws), str(dest))
                    print(f"\nInterrupted. Partial work saved to {dest}")
                except Exception:
                    pass
                sandbox_ws = None
        finally:
            _archive_lock[0] = False

    signal.signal(signal.SIGINT, _emergency_archive)
    signal.signal(signal.SIGTERM, _emergency_archive)
    atexit.register(_emergency_archive)

    try:
        for iter_index in range(args.max_iters):
            try:
                iter_name = f"iter_{iter_index:03d}"
                sandbox_ws = _init_executor_workspace(repo_root, recast_path, paper_ref)
                _seed_executor_workspace(sandbox_ws, plan_path, previous_iter)

                _run_executor(
                    repo_root,
                    sandbox_ws,
                    paper_ref,
                    iter_index,
                    has_prior=(previous_iter is not None),
                    runner=runner,
                    model=args.model or None,
                    max_thinking_tokens=exec_max_thinking,
                    sandbox_choice=args.sandbox,
                )

                missing = [n for n in EXPECTED_ARTIFACTS if not (sandbox_ws / n).exists()]
                if missing:
                    print(f"  {iter_name} missing: {missing}")
                if not _has_analysis_code(sandbox_ws):
                    print(f"  {iter_name} has no analysis code")
                if not _has_filled_hepdata(sandbox_ws):
                    print(f"  {iter_name} has no filled HEPRecastData values")

                archived = validation_dir / iter_name
                if archived.exists():
                    shutil.rmtree(archived)
                shutil.move(str(sandbox_ws), str(archived))
                sandbox_ws = None
                iter_dir = archived

                overall_pass, scores = _score_iteration(iter_dir, paper_ref)
                overall_score = scores.get("overall_score", 0.0) if scores else 0.0
                status = _parse_status(iter_dir / "report.md")
                print(
                    f"  {iter_name}: score={overall_score:.0%}, "
                    f"pass={overall_pass}, status={status}"
                )

                history.append(
                    IterationResult(
                        directory=iter_dir,
                        status=status,
                        score=overall_score,
                        overall_pass=overall_pass,
                    )
                )
                summary = {
                    "paper_ref": paper_ref,
                    "iterations": len(history),
                    "last_iter": iter_dir.name,
                    "last_status": status,
                    "last_score": overall_score,
                    "last_overall_pass": overall_pass,
                    "history": [
                        {
                            "iter": h.directory.name,
                            "status": h.status,
                            "score": h.score,
                            "overall_pass": h.overall_pass,
                        }
                        for h in history
                    ],
                }
                (recast_path / "controller_summary.json").write_text(json.dumps(summary, indent=2))

                stop = overall_pass is True and len(history) >= args.min_iters
                if stop:
                    summary["status"] = "CONVERGED"
                    (recast_path / "controller_summary.json").write_text(
                        json.dumps(summary, indent=2)
                    )
                    print(f"\nConverged after {len(history)} iteration(s).")
                    final_scores = scores
                    return 0

                # Not converged → run the critic to seed the next iter.
                _run_critic(
                    repo_root,
                    iter_dir,
                    paper_ref,
                    iter_index,
                    plan_path=plan_path,
                    runner=runner,
                    model=args.critic_model or args.model or None,
                    effort_max_tokens=critic_max_thinking,
                    sandbox_choice=args.sandbox,
                )

                previous_iter = iter_dir
                final_scores = scores

            except (KeyboardInterrupt, subprocess.CalledProcessError) as exc:
                if sandbox_ws and sandbox_ws.exists() and iter_name:
                    (sandbox_ws / "controller_interrupt.log").write_text(
                        f"Interrupted: {type(exc).__name__}: {exc}\n"
                    )
                    dest = validation_dir / iter_name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.move(str(sandbox_ws), str(dest))
                    sandbox_ws = None
                if isinstance(exc, KeyboardInterrupt):
                    rc = 130
                    raise
                rc = 1
                continue

        # Loop exhausted without converging
        summary = recast_path / "controller_summary.json"
        if summary.exists():
            data = json.loads(summary.read_text())
            data["status"] = "MAX_ITERS"
            summary.write_text(json.dumps(data, indent=2))
        return rc
    finally:
        _finalize(rc, final_scores)


if __name__ == "__main__":
    sys.exit(main())
