"""Shared single-run launch scaffolding for single-shot agents.

Collapses the boilerplate of arg parsing, config resolution, run-info
generation, workspace build, runner invocation, scoring, and finalization.

Agents that fit the "parse config → build workspace → run once → score →
finalize" shape (e.g. agents/simple) call launch_single_run(). Agents
with a custom control loop compose from naming.py helpers directly.
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

from agent_runtime.config import load_config, validate_api_auth_env, validate_launch_inputs
from agent_runtime.effort import resolve_effort
from agent_runtime.naming import generate_run_info
from agent_runtime.run_info import finalize_run_info, write_run_info
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
        "(e.g. sus-16-046_sim-TChiWg).",
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
        choices=["auto", "bwrap", "apptainer", "singularity", "podman", "none"],
        help="Filesystem isolation backend (default: auto)",
    )
    parser.add_argument(
        "--auth",
        default=None,
        choices=["oauth", "api"],
        help="Runner auth mode. Claude supports oauth (default) and api.",
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
    args.auth = args.auth or cfg.get("auth") or "oauth"
    try:
        validate_api_auth_env(vars(args))
    except ValueError as exc:
        parser.error(str(exc))
    return cfg


_WALLTIME_RE = __import__("re").compile(r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\s*$")


def _parse_walltime_s(s: str | None) -> float | None:
    """Convert a `task.toml [metadata].walltime` string to seconds.

    Accepts "4h", "30m", "1h30m", "2h45m30s", "120s". Returns None for
    None/empty input. Raises ValueError on malformed input.
    """
    if not s:
        return None
    m = _WALLTIME_RE.match(str(s))
    if not m or not any(m.groups()):
        raise ValueError(f"invalid walltime {s!r} — expected e.g. '4h', '30m', '1h30m', '120s'")
    h, mi, sec = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mi * 60 + sec)


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
    walltime_s: float | None = None,
    config: dict | None = None,
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
    tool_policy = (config or {}).get("tool_policy") or {}
    if "delphes" in set(tool_policy.get("disabled", [])):
        env["DELPHES_DIR"] = str(workspace / "disabled_tools" / "delphes")
    prep = runner.prepare_launch(workspace, config=config)
    env.update(prep.env)

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=[*extra_ro_binds, *prep.extra_ro_binds],
        container_env=env,
        secret_env_names=prep.secret_env_names,
        home_dir_name=prep.home_dir_name,
        home_files=prep.home_files,
        home_credential_files=prep.home_credential_files,
        sandbox=sandbox,
    )
    try:
        runner.run(
            cmd,
            prompt,
            workspace,
            env,
            workspace / "session.jsonl",
            walltime_s=walltime_s,
        )
    finally:
        cleanup()


def _default_score(workspace: Path) -> dict:
    """Default scoring hook — calls the in-repo LHCRecastBench evaluator.

    Kept as a separate function so alternate scorers can be injected via
    `launch_single_run(..., score=<callable>)`. Returns an {"error": ...}
    dict rather than raising, so a scoring failure doesn't mask the run.
    """
    try:
        from LHCRecastBench.Evals import score
    except ImportError as exc:
        return {"error": f"eval import failed: {exc}"}
    try:
        return score.score_run(workspace.parent)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return {"error": str(exc)}


# Scoring hook type — injectable via launch_single_run(..., score=...).
ScoreFn = Callable[[Path], dict]


def _run_offline_evals(workspace: Path, eval_dir: Path) -> None:
    """Run the offline (LLM-free) evaluators after the agent completes.

    Writes:
      eval/plots/*.png   — reference vs agent histogram + ratio panel

    `eval/score.json` is written by score_run() before this is called, and
    is the only metric artifact — no derived summary file is produced.
    Best-effort: any single evaluator that errors is logged.
    """
    try:
        from LHCRecastBench.Evals import score
    except ImportError as exc:
        print(f"  WARN: skipping offline evals — eval modules not importable: {exc}")
        return
    score_path = eval_dir / "score.json"
    if not score_path.is_file():
        print(f"  WARN: skipping plots — no {score_path}")
        return
    try:
        result = json.loads(score_path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: skipping plots — could not parse score.json: {exc}")
        return
    if "error" in result:
        return  # nothing to plot from a failed score

    try:
        score._emit(workspace.parent, result, with_plots=True)
        files = list((eval_dir / "plots").glob("*.png")) if (eval_dir / "plots").is_dir() else []
        if files:
            print(f"  Plots: {len(files)} file(s) in {eval_dir / 'plots'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: plot generation failed: {exc}")


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
    cfg = _resolve(args, parser)
    task_id = args.task
    effort_label, max_thinking = resolve_effort(args.effort)

    try:
        task_toml = validate_launch_inputs(repo_root, task_id)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    paper_ref = task_toml["task"]["paper"]
    # Per-task walltime: kill the agent process group when this expires.
    # `task.toml [metadata].walltime` is "4h" / "30m" / "1h30m" form.
    # Independent of any SLURM time — useful when one slurm allocation
    # hosts multiple tasks (array jobs, multi-run-interactive scripts).
    try:
        walltime_s = _parse_walltime_s((task_toml.get("metadata") or {}).get("walltime"))
    except ValueError as exc:
        parser.error(str(exc))

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
    if cfg.get("tool_policy"):
        info["tool_policy"] = cfg["tool_policy"]

    print(f"Setting up workspace: {run_dir}")
    walltime_str = (
        f"{walltime_s / 3600:.1f}h"
        if walltime_s and walltime_s >= 3600
        else f"{walltime_s:.0f}s"
        if walltime_s
        else "unbounded"
    )
    print(
        f"Agent ID: {info['agent_id']}   "
        f"(effort={effort_label}, max_thinking_tokens={max_thinking}, "
        f"walltime={walltime_str})"
    )
    workspace = build_workspace(
        repo_root,
        agent_name,
        task_id,
        run_dir,
        tool_policy=cfg.get("tool_policy"),
    )
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
        try:
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
                walltime_s=walltime_s,
                config={**cfg, **vars(args)},
            )
        except subprocess.CalledProcessError as exc:
            # Agent CLI failed (e.g. exit 127 = command not found, missing
            # binary in the container image). Mark the run as failed but
            # still try to score whatever the agent produced before dying.
            print(
                f"  Agent CLI exited with code {exc.returncode} — marking run as failed.",
                file=sys.stderr,
            )
            exit_code = 1

        print("\nScoring results...")
        scores = _score(workspace)
        eval_dir = recast_path / "eval"
        eval_dir.mkdir(exist_ok=True)
        (eval_dir / "score.json").write_text(json.dumps(scores, indent=2))

        if "error" in scores:
            print(f"  ERROR: {scores['error']}")
            exit_code = 1
        else:
            n_filled = scores.get("n_filled", 0)
            n_bins = scores.get("n_bins", 0)
            shape_block = scores.get("shape") or {}
            norm_block = scores.get("normalization") or {}
            print(f"  Filled: {n_filled}/{n_bins} bins")
            if shape_block:
                p = shape_block.get("p_value")
                jsd = shape_block.get("jensen_shannon")
                if p is not None:
                    print(
                        f"  Shape: bin_err={shape_block.get('mean_abs_frac_error_pct')}%  p={p}  JSD={jsd}"
                    )
            if norm_block:
                fe = norm_block.get("mean_abs_frac_error_pct")
                if fe is not None:
                    print(f"  Norm: frac_err={fe}%")
            # Agent CLIs sometimes exit cleanly (rc=0) but never reach the
            # scoring step — e.g. Claude Code returns rc=0 with a 429 result
            # event when the 5-hour subscription cap rejects the request, and
            # codex/gemini have analogous "soft failure" exits. The
            # CalledProcessError path above doesn't fire in those cases, so
            # we demote here based on the observed output: n_filled == 0
            # with n_bins > 0 means the agent didn't complete the task.
            if exit_code == 0 and n_bins > 0 and n_filled == 0:
                print(
                    "  Agent exited cleanly but produced no filled bins — marking run as failed.",
                    file=sys.stderr,
                )
                exit_code = 1

        # Offline evals: plot ref vs agent histogram. score.json is already
        # written by score_run() above. The LLM judge stays opt-in via
        # scripts/launch_eval.sh because it costs subscription tokens.
        _run_offline_evals(workspace, eval_dir)
    except BaseException:
        exit_code = 1
        raise
    finally:
        finalize_run_info(
            recast_path,
            exit_code=exit_code,
            started_at=started_at,
            scores=scores,
            session_logs=[workspace / "session.jsonl"],
        )

    print(f"\nDone. Results in {workspace}")
    return exit_code
