#!/usr/bin/env python3
"""Iterative agent loop: run the simple agent repeatedly until it converges.

Each iteration:
  1. Sets up a fresh workspace from agents/simple/ + LHCRecastBench/papers/<arxiv>/{shared,tasks/<task>/}.
  2. Seeds inherited artifacts from the previous iteration (analysis.py, status.md,
     datasets.yaml, partially filled HEPRecastData/).
  3. Runs the simple agent inside a bwrap sandbox.
  4. Scores the filled HEPRecastData/ with score_recast.
  5. Archives the workspace to validation/agent_NNN/.
  6. Stops when either the score passes or max_iters is reached.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.runners import Runner, get_runner, RUNNERS


# Files the loop expects to find (none are strictly required — missing ones
# are recorded but do not abort the iteration).
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_text(path: Path, text: str) -> None:
    path.write_text(text)


def _parse_status(report_path: Path) -> str:
    """Parse Status: STOP|CONTINUE from report.md. Default CONTINUE."""
    if not report_path.exists():
        return "CONTINUE"
    match = re.search(r"Status:\s*`?(STOP|CONTINUE)`?", report_path.read_text(), re.I)
    return match.group(1).upper() if match else "CONTINUE"


def _score_iteration(
    iter_dir: Path, paper_ref: str, task: str = "recast"
) -> tuple[bool | None, dict]:
    """Score the archived iteration. Writes score.json into the iteration dir."""
    try:
        from LHCRecastBench.evaluation.score import score_recast

        recast_dir = iter_dir / "HEPRecastData"
        if not recast_dir.exists():
            return None, {}
        scores = score_recast(paper_ref, str(recast_dir), task=task)
        (iter_dir / "score.json").write_text(json.dumps(scores, indent=2))
        return scores.get("overall_pass"), scores
    except Exception as exc:
        print(f"  score_recast failed: {exc}", file=sys.stderr)
        return None, {}


def _has_analysis_code(sandbox: Path) -> bool:
    """Did the agent produce any analysis code?"""
    if (sandbox / "analysis.py").is_file():
        return True
    analysis_dir = sandbox / "analysis"
    if analysis_dir.is_dir():
        return any(analysis_dir.rglob("*.py"))
    return False


def _has_filled_hepdata(sandbox: Path) -> bool:
    """Did the agent fill at least one HEPRecastData value?"""
    import yaml

    recast = sandbox / "HEPRecastData"
    if not recast.is_dir():
        return False
    for yf in recast.glob("*.yaml"):
        if yf.name in ("submission.yaml", "description.yaml"):
            continue
        try:
            data = yaml.safe_load(yf.read_text()) or {}
        except Exception:
            continue
        for dep in data.get("dependent_variables", []):
            for entry in dep.get("values", []):
                if entry.get("value") is not None:
                    return True
    return False


# ── Sandbox setup ──────────────────────────────────────────────────────────


def _next_agent_name(recast_path: Path) -> str:
    validation_dir = recast_path / "validation"
    workspace = recast_path / "workspace"
    names: list[str] = []
    for d in (recast_path, validation_dir, workspace):
        if d.exists():
            names.extend(p.name for p in d.iterdir() if p.is_dir() and p.name.startswith("agent_"))
    if names:
        last = max(int(n.split("_")[-1]) for n in names)
        return f"agent_{last + 1:03d}"
    return "agent_000"


def _init_sandbox(repo_root: Path, recast_path: Path) -> tuple[Path, str]:
    """Create a fresh workspace/ with templates, tools symlink, and bin/ shims.

    Returns (sandbox_path, iter_name). iter_name is where this workspace will
    be archived after the iteration completes.
    """
    iter_name = _next_agent_name(recast_path)
    sandbox = recast_path / "workspace"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)

    # Workspace templates (stubs like report.md, datasets.yaml)
    template_dir = repo_root / "agents" / "iterative" / "runtime" / "templates" / "workspace"
    if template_dir.is_dir():
        for f in template_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, sandbox / f.name)

    # tools/ symlink — single directory, no per-file symlinks needed
    (sandbox / "tools").symlink_to(repo_root / "LHCRecastBench" / "tools")

    # bin/ merges benchmark bin + our iteration bin (for run-analysis)
    bin_dir = sandbox / "bin"
    bin_dir.mkdir(exist_ok=True)
    for script in (repo_root / "LHCRecastBench" / "bin").iterdir():
        (bin_dir / script.name).symlink_to(script)
    iter_bin = repo_root / "agents" / "iterative" / "runtime" / "bin"
    for script in iter_bin.iterdir():
        dest = bin_dir / script.name
        if dest.exists():
            continue
        dest.symlink_to(script)

    return sandbox, iter_name


def _seed_workspace(
    repo_root: Path,
    sandbox: Path,
    previous_iter: Path | None,
    paper_ref: str | None,
    iter_index: int,
    task: str = "recast",
) -> None:
    """Copy agent_context/ + shared/ + task-specific templates + inherit from prior iteration."""
    # Agent instructions come from agents/simple/ (AGENTS.md, TOOLS.md) — the
    # iterative agent re-uses the simple agent's minimal guidance.
    simple_dir = repo_root / "agents" / "simple"
    agent_context = sandbox / "agent_context"
    agent_context.mkdir(exist_ok=True)
    for src in simple_dir.rglob("*.md"):
        rel = src.relative_to(simple_dir)
        if rel.parts[0] == "runtime":
            continue
        dest = agent_context / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text()
        if paper_ref:
            text = text.replace("{arxiv_id}", paper_ref)
        dest.write_text(text)

    # Per-paper inputs (paper PDF, efficiency ROOT) from shared/; TASK.md +
    # templates/HEPRecastData/ from tasks/<task>/. papers/ is symlinked so the
    # PDF isn't duplicated per iteration.
    if paper_ref:
        paper_dir = repo_root / "LHCRecastBench" / "papers" / paper_ref
        shared = paper_dir / "shared"
        if shared.is_dir():
            for item in shared.iterdir():
                dest = sandbox / item.name
                if item.is_dir() and item.name == "papers":
                    dest.symlink_to(item.resolve())
                elif item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        task_dir = paper_dir / "tasks" / task
        templates_src = task_dir / "templates" / "HEPRecastData"
        if templates_src.is_dir():
            dest_hep = sandbox / "HEPRecastData"
            if dest_hep.exists():
                shutil.rmtree(dest_hep)
            shutil.copytree(templates_src, dest_hep)
        task_md = task_dir / "TASK.md"
        if task_md.is_file():
            text = task_md.read_text()
            if paper_ref:
                text = text.replace("{arxiv_id}", paper_ref)
            (agent_context / "TASK.md").write_text(text)

    # Inheritance from the previous iteration
    if previous_iter is not None:
        # analysis.py (or analysis/ subdir) — the code to improve
        prev_py = previous_iter / "analysis.py"
        if prev_py.exists():
            shutil.copy2(prev_py, sandbox / "analysis.py")
        prev_analysis_dir = previous_iter / "analysis"
        if prev_analysis_dir.is_dir():
            dest = sandbox / "analysis"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(prev_analysis_dir, dest)

        # datasets.yaml — carry forward verbatim
        prev_ds = previous_iter / "datasets.yaml"
        if prev_ds.exists():
            shutil.copy2(prev_ds, sandbox / "datasets.yaml")

        # report.md → status.md (unverified notes, not ground truth)
        prev_report = previous_iter / "report.md"
        if prev_report.exists():
            shutil.copy2(prev_report, sandbox / "status.md")

        # HEPRecastData — carry the partially-filled values forward
        prev_recast = previous_iter / "HEPRecastData"
        if prev_recast.is_dir():
            dest = sandbox / "HEPRecastData"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(prev_recast, dest)

        # Prior score so the agent knows the starting point
        prev_score = previous_iter / "score.json"
        if prev_score.exists():
            shutil.copy2(prev_score, sandbox / "previous_score.json")


# ── Prompt ──────────────────────────────────────────────────────────────────


def _build_prompt(paper_ref: str | None, iter_index: int, has_prior: bool) -> str:
    parts = [
        f"You are recasting CMS paper {paper_ref or '<unknown>'}.",
        "",
        "Read these in order:",
        "  1. agent_context/AGENTS.md — your role and how to work.",
        "  2. agent_context/TASK.md   — the benchmark's task for this run.",
        "  3. agent_context/TOOLS.md  — CLI tool reference.",
        "",
        "Everything you need is in this directory. Do not look outside it.",
        "",
        "Run bin/run-analysis synchronously via Bash (never run_in_background, never &).",
        "It has a 4-hour internal timeout; blocking on it is safe and expected.",
    ]
    if has_prior:
        parts += [
            "",
            f"You are agent {iter_index} and this is iteration #{iter_index}. A previous attempt left these files for you:",
            "  analysis.py (or analysis/*.py) — prior code to validate and improve",
            "  datasets.yaml                  — prior dataset inventory",
            "  HEPRecastData/*.yaml           — prior filled values (may be partial or wrong)",
            "  status.md                      — prior report (unverified notes)",
            "  previous_score.json            — how the prior run scored per bin",
            "",
            "Treat inherited files as UNTRUSTED. Re-read the paper and verify before relying on them.",
            "Find errors or bugs in the previous analysis and fix them. Be skeptical.",
            "Your report.md must state Status: STOP only when you believe the recast faithfully",
            "reproduces the paper. Otherwise leave it as CONTINUE — the loop will run again.",
        ]
    else:
        parts += [
            "",
            "When you believe the recast faithfully reproduces the paper, set Status: STOP in",
            "report.md. Otherwise leave it as CONTINUE — the loop will run again to improve it.",
        ]
    return "\n".join(parts) + "\n"


# ── Agent execution ─────────────────────────────────────────────────────────


def _run_agent(
    repo_root: Path,
    prompt: str,
    runner: Runner,
    model: str | None,
    output_file: Path,
    max_thinking_tokens: int | None = None,
    sandbox: str | None = None,
    effort_label: str | None = None,
) -> None:
    ws = output_file.parent
    inner_cmd = runner.build_command(
        prompt,
        ws,
        model,
        allowlist=None,
        max_thinking_tokens=max_thinking_tokens,
        effort_label=effort_label,
    )

    env = os.environ.copy()
    env["PATH"] = str(ws / "bin") + ":" + env.get("PATH", "")
    env["PYTHONPATH"] = str(repo_root) + ":" + env.get("PYTHONPATH", "")
    env["REPO_ROOT"] = str(repo_root)

    from agent_runtime.sandbox import sandbox_command

    iter_runtime = repo_root / "agents" / "iterative" / "runtime"
    simple_runtime = repo_root / "agents" / "simple" / "runtime"
    shared_runtime = repo_root / "agent_runtime"
    cmd, cleanup = sandbox_command(
        ws,
        repo_root,
        inner_cmd,
        extra_ro_binds=[iter_runtime, simple_runtime, shared_runtime],
        sandbox=sandbox,
    )

    try:
        runner.run(cmd, prompt, ws, env, output_file)
    finally:
        cleanup()


# ── Main loop ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iterate the simple agent until score_recast passes or max_iters is reached.",
    )
    parser.add_argument(
        "--config", default="", help="YAML config file (CLI flags override; see configs/)"
    )
    parser.add_argument(
        "--paper-ref",
        default=None,
        help="arXiv ID, e.g. 1707.06193 (required unless set by --config)",
    )
    parser.add_argument("--max-iters", type=int, default=None)
    parser.add_argument(
        "--min-iters",
        type=int,
        default=None,
        help="Minimum iterations before an agent's STOP is honored",
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
    parser.add_argument(
        "--task",
        default=None,
        choices=["validate", "simulate", "recast"],
        help="Benchmark task (default: simulate).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]

    # Run metadata (agent_id, run_dir, effort)
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
        parser.error("--paper-ref is required (either on the CLI or via --config <yaml>:paper)")
    args.task = args.task or cfg.get("task") or "simulate"
    try:
        validate_launch_inputs(repo_root, args.paper_ref, args.task)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    args.runner = args.runner or cfg.get("runner") or "claude"
    args.model = args.model or cfg.get("model") or ""
    args.effort = args.effort or cfg.get("effort") or "medium"
    args.sandbox = args.sandbox or cfg.get("sandbox")
    args.max_iters = args.max_iters if args.max_iters is not None else int(cfg.get("max_iters", 5))
    args.min_iters = args.min_iters if args.min_iters is not None else int(cfg.get("min_iters", 1))
    paper_ref = args.paper_ref
    effort_label, max_thinking = resolve_effort(args.effort)

    run_info = generate_run_info(
        paper_ref=paper_ref,
        agent_name="iterative",
        model_name=args.model or args.runner,
        task=args.task,
    )
    recast_dir = run_info["run_dir"]
    run_info["runner"] = args.runner
    run_info["effort"] = effort_label
    run_info["max_thinking_tokens"] = max_thinking
    run_info["max_iters"] = args.max_iters
    run_info["sandbox"] = args.sandbox or "auto"
    run_info["task"] = args.task

    recast_path = repo_root / "runs" / recast_dir
    recast_path.mkdir(parents=True, exist_ok=True)
    validation_dir = recast_path / "validation"
    validation_dir.mkdir(exist_ok=True)
    write_run_info(recast_path, run_info)
    print(f"Recast directory: {recast_dir}")
    print(
        f"Agent ID: {run_info['agent_id']}   (effort={effort_label}, "
        f"max_thinking_tokens={max_thinking})"
    )

    # Paper PDF — fetch once into the shared location if missing.
    papers_dir = repo_root / "LHCRecastBench" / "papers" / paper_ref / "shared" / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = papers_dir / f"{paper_ref}.pdf"
    if not pdf_path.exists():
        url = f"https://arxiv.org/pdf/{paper_ref}"
        print(f"Downloading paper: {url}")
        urllib.request.urlretrieve(url, pdf_path)

    runner = get_runner(args.runner)
    previous_iter: Path | None = None

    history: list[IterationResult] = []
    sandbox: Path | None = None
    iter_name: str | None = None
    started_at = time.time()
    final_scores: dict | None = None

    def _collect_session_logs() -> list[Path]:
        logs: list[Path] = []
        if validation_dir.exists():
            for agent_dir in sorted(validation_dir.iterdir()):
                log = agent_dir / "session_log.txt"
                if log.exists():
                    logs.append(log)
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
        nonlocal sandbox, iter_name
        if _archive_lock[0]:
            return
        _archive_lock[0] = True
        try:
            if sandbox and sandbox.exists() and iter_name:
                (sandbox / "controller_interrupt.log").write_text(
                    f"Interrupted by signal={signum}\n"
                )
                dest = validation_dir / iter_name
                if dest.exists():
                    shutil.rmtree(dest)
                try:
                    shutil.move(str(sandbox), str(dest))
                    print(f"\nInterrupted. Partial work saved to {dest}")
                except Exception:
                    pass
                sandbox = None
        finally:
            _archive_lock[0] = False

    import atexit

    signal.signal(signal.SIGINT, _emergency_archive)
    signal.signal(signal.SIGTERM, _emergency_archive)
    atexit.register(_emergency_archive)

    for iter_index in range(args.max_iters):
        try:
            sandbox, iter_name = _init_sandbox(repo_root, recast_path)
            _seed_workspace(
                repo_root,
                sandbox,
                previous_iter=previous_iter,
                paper_ref=paper_ref,
                iter_index=iter_index,
                task=args.task,
            )

            prompt = _build_prompt(
                paper_ref=paper_ref,
                iter_index=iter_index,
                has_prior=(previous_iter is not None),
            )
            (sandbox / "prompt.txt").write_text(prompt)

            output_file = sandbox / "session_log.txt"
            _run_agent(
                repo_root=repo_root,
                prompt=prompt,
                runner=runner,
                model=args.model or None,
                output_file=output_file,
                max_thinking_tokens=max_thinking,
                sandbox=args.sandbox,
                effort_label=effort_label,
            )

            # Soft validation — log what's missing but don't abort
            missing = [name for name in EXPECTED_ARTIFACTS if not (sandbox / name).exists()]
            if missing:
                print(f"  Iteration {iter_name} missing: {missing}")
            if not _has_analysis_code(sandbox):
                print(f"  Iteration {iter_name} has no analysis code")
            if not _has_filled_hepdata(sandbox):
                print(f"  Iteration {iter_name} has no filled HEPRecastData values")

            # Archive
            archived = validation_dir / iter_name
            if archived.exists():
                shutil.rmtree(archived)
            shutil.move(str(sandbox), str(archived))
            sandbox = None
            iter_dir = archived

            # Score
            overall_pass, scores = _score_iteration(iter_dir, paper_ref, task=args.task)
            overall_score = scores.get("overall_score", 0.0) if scores else 0.0
            status = _parse_status(iter_dir / "report.md")
            print(f"  {iter_name}: score={overall_score:.0%}, pass={overall_pass}, status={status}")

            history.append(
                IterationResult(
                    directory=iter_dir,
                    status=status,
                    score=overall_score,
                    overall_pass=overall_pass,
                )
            )

            # Summary after every iteration
            summary = {
                "paper_ref": paper_ref,
                "agents_run": len(history),
                "last_agent": iter_dir.name,
                "last_status": status,
                "last_score": overall_score,
                "last_overall_pass": overall_pass,
                "history": [
                    {
                        "agent": h.directory.name,
                        "status": h.status,
                        "score": h.score,
                        "overall_pass": h.overall_pass,
                    }
                    for h in history
                ],
            }
            (recast_path / "controller_summary.json").write_text(json.dumps(summary, indent=2))

            # Stop when the score passes and we've done at least min_iters.
            # Agent's STOP/CONTINUE signal is recorded but not gating.
            stop = overall_pass is True and len(history) >= args.min_iters
            if stop:
                summary["status"] = "CONVERGED"
                (recast_path / "controller_summary.json").write_text(json.dumps(summary, indent=2))
                print(f"\nConverged after {len(history)} iteration(s).")
                print(json.dumps(summary, indent=2))
                _finalize(0, scores)
                return 0

            previous_iter = iter_dir
            final_scores = scores

        except (KeyboardInterrupt, subprocess.CalledProcessError) as exc:
            if sandbox and sandbox.exists() and iter_name:
                (sandbox / "controller_interrupt.log").write_text(
                    f"Interrupted: {type(exc).__name__}: {exc}\n"
                )
                dest = validation_dir / iter_name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(sandbox), str(dest))
                print(f"\nInterrupted. Partial work saved to {dest}")
            if isinstance(exc, KeyboardInterrupt):
                _finalize(1, final_scores)
                return 1
            _finalize(1, final_scores)
            raise

    # Exhausted max_iters
    summary = {
        "paper_ref": paper_ref,
        "agents_run": len(history),
        "status": "MAX_ITERS_REACHED",
        "history": [
            {
                "agent": h.directory.name,
                "status": h.status,
                "score": h.score,
                "overall_pass": h.overall_pass,
            }
            for h in history
        ],
    }
    (recast_path / "controller_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    _finalize(0, final_scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
