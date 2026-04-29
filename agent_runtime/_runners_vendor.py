"""Vendor agent-CLI runner specs (Claude Code, Codex, Gemini CLI, Aider).

Each vendor is now a single `RunnerSpec` literal plus, for the two that
need it (Codex, Gemini), a small named hook function that prepares the
per-run state directory and seeds host OAuth credentials into it.

This file is auto-imported from `agent_runtime/runners.py` and registers
the four built-in runners. Adding a new vendor (e.g. Grok) is a single
RunnerSpec at the bottom of this file — no class hierarchy required.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from agent_runtime.runner_spec import (
    RunnerSpec,
    register_declarative,
    register_post_run,
    register_pre_launch,
)


# ── Pre-launch / post-run hooks (the imperative bits) ───────────────────────


def _codex_pre_launch(sandbox: Path, env: dict[str, str]) -> None:
    """Set up CODEX_HOME under workspace/.codex_home and seed creds.

    Codex stores session state + helper binaries under $CODEX_HOME. We
    point it at a workspace-relative path (rw, NOT under /tmp — codex 0.x
    refuses to create helper binaries under /tmp) and populate it from
    the host's ~/.codex/ before launch.

    We must set CODEX_HOME on os.environ (not just the local env dict)
    so PodmanSandbox picks it up and propagates via -e CODEX_HOME=<path>
    into the container; the staging-$HOME design otherwise gives codex a
    default CODEX_HOME of $HOME/.codex which lives on /tmp.
    """
    codex_home = sandbox / ".codex_home"
    codex_home.mkdir(exist_ok=True)
    real_home = Path.home() / ".codex"
    for name in ("auth.json", "config.toml"):
        src = real_home / name
        if src.is_file():
            shutil.copy2(src, codex_home / name)
    os.environ["CODEX_HOME"] = str(codex_home)
    env["CODEX_HOME"] = str(codex_home)


def _codex_post_run(sandbox: Path) -> None:
    """Drop bulky codex caches/logs after run; keep auth.json for inspection."""
    codex_home = sandbox / ".codex_home"
    for sub in (
        "tmp",
        "cache",
        "logs_2.sqlite",
        "logs_2.sqlite-shm",
        "logs_2.sqlite-wal",
    ):
        p = codex_home / sub
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink(missing_ok=True)


def _gemini_pre_launch(sandbox: Path, env: dict[str, str]) -> None:
    """Set up GEMINI_CLI_HOME and rewrite settings.json to disable IDE probe.

    Mirrors codex's per-run redirect so:
      1. parallel runs don't race on the same state sqlite,
      2. nothing mutates the real ~/.gemini under the sandbox,
      3. NERSC autofs $HOME doesn't trip the CLI's file locks.
    """
    gemini_home = sandbox / ".gemini_home"
    gemini_state = gemini_home / ".gemini"
    gemini_state.mkdir(parents=True, exist_ok=True)
    real_state = Path.home() / ".gemini"
    if real_state.is_dir():
        for name in ("oauth_creds.json", "google_accounts.json", "installation_id"):
            src = real_state / name
            if src.exists():
                shutil.copy2(src, gemini_state / name)
    # Settings: keep auth method, disable the IDE-companion probe (otherwise
    # the CLI spams ECONNREFUSED trying to reach VS Code from inside the sandbox).
    host_settings = real_state / "settings.json" if real_state.is_dir() else None
    settings: dict = {}
    if host_settings and host_settings.exists():
        try:
            settings = json.loads(host_settings.read_text())
        except json.JSONDecodeError:
            settings = {}
    settings.setdefault("security", {}).setdefault("auth", {}).setdefault(
        "selectedType", "oauth-personal"
    )
    settings["ide"] = {"enabled": False}
    (gemini_state / "settings.json").write_text(json.dumps(settings, indent=2))
    env["GEMINI_CLI_HOME"] = str(gemini_home)


register_pre_launch("codex", _codex_pre_launch)
register_post_run("codex", _codex_post_run)
register_pre_launch("gemini", _gemini_pre_launch)


# ── Vendor specs (the declarative bits) ─────────────────────────────────────


CLAUDE_SPEC = RunnerSpec(
    name="claude",
    binary="claude",
    binary_env_var="CLAUDE_BIN",
    pre_subcommand_args=[
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ],
    prompt_flag="-p",
    model_flag="--model",
    allowlist_flag="--allowedTools",
    disallowed_tools_flag="--disallowedTools",
    # ScheduleWakeup is interactive-only; in `-p` one-shot mode it strands
    # the session. Disallow so the agent can't background bin/run-analysis.
    disallowed_tools=["ScheduleWakeup"],
    thinking_tokens_flag="--max-thinking-tokens",
    stream_format="stream_json",
    # Claude sometimes hangs after emitting `result` (MCP/hooks/watchers
    # holding stdout). 15s grace, then SIGTERM the group.
    post_result_grace_s=15.0,
)


CODEX_SPEC = RunnerSpec(
    name="codex",
    binary="codex",
    binary_env_var="CODEX_BIN",
    # `-a never` and `-c` configs go BEFORE the `exec` subcommand.
    pre_subcommand_args=["-a", "never"],
    subcommand=["exec"],
    post_subcommand_args=[
        "--json",
        "--skip-git-repo-check",
        "-C",
        "{sandbox}",
        "-s",
        "danger-full-access",
    ],
    # `-` tells codex to read prompt from stdin.
    final_args=["-"],
    prompt_via="stdin",
    model_flag="-m",
    # Codex effort: -c model_reasoning_effort=<low|medium|high|xhigh>.
    # Our `max` label is an alias for codex's `xhigh` (highest tier
    # accepted by GPT-5 family).
    effort_config_flag="-c",
    effort_config_key="model_reasoning_effort",
    effort_label_map={
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "xhigh",
    },
    stream_format="stream_json",
    pre_launch_hook="codex",
    post_run_hook="codex",
)


GEMINI_SPEC = RunnerSpec(
    name="gemini",
    binary="gemini",
    binary_env_var="GEMINI_BIN",
    # `--approval-mode yolo` = auto-approve all tool calls; required for
    # headless runs in the sandbox where we can't answer prompts.
    pre_subcommand_args=["-o", "stream-json", "--approval-mode", "yolo"],
    prompt_flag="-p",
    model_flag="-m",
    # Gemini exposes neither thinking-tokens nor an enum reasoning-effort
    # flag — the server-side router auto-selects between gemini-3-pro,
    # flash, and flash-lite based on the query.
    stream_format="stream_json",
    pre_launch_hook="gemini",
)


AIDER_SPEC = RunnerSpec(
    name="aider",
    binary="aider",
    binary_env_var="AIDER_BIN",
    pre_subcommand_args=[
        "--yes-always",  # auto-approve all edits and commands
        "--no-git",  # sandbox has no git repo
        "--no-auto-commits",  # we manage artifacts, not git
        "--no-suggest-shell-commands",  # just run them
    ],
    prompt_flag="--message",
    model_flag="--model",
    # Aider has no universal thinking-tokens flag across its 75+ backends.
    stream_format="aider_text",
)


# ── Registration ────────────────────────────────────────────────────────────

for spec in (CLAUDE_SPEC, CODEX_SPEC, GEMINI_SPEC, AIDER_SPEC):
    register_declarative(spec)
