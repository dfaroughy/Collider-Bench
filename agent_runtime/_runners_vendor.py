"""Vendor agent-CLI runner subclasses (Claude Code, Codex, Gemini CLI, Aider).

This file is **private**: it is gitignored and never shipped in the public
benchmark image. It is loaded automatically by `agent_runtime/runners.py`
when present (try-import at the bottom of that module). Anyone running the
public benchmark without these CLIs available simply gets an empty
`RUNNERS` registry for these names; their own runner subclasses can
register via the `register` decorator from runners.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agent_runtime.runners import (
    Runner,
    _arm_walltime_watchdog,
    _find_binary,
    _is_result_line,
    _kill_process_group,
    register,
)


class ClaudeRunner(Runner):
    """Anthropic Claude Code CLI."""

    @property
    def name(self) -> str:
        return "claude"

    def build_command(
        self, prompt, sandbox, model, allowlist, max_thinking_tokens=None, effort_label=None
    ):
        binary = _find_binary("claude", "CLAUDE_BIN")
        cmd = [
            binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            # ScheduleWakeup is an interactive-Code tool; in `-p` one-shot
            # mode there is no harness to fire the wake-up, so calls to it
            # just strand the session. Disallow to prevent the agent from
            # backgrounding bin/run-analysis and "scheduling" a resume.
            "--disallowedTools",
            "ScheduleWakeup",
        ]
        if model:
            cmd.extend(["--model", model])
        if allowlist:
            cmd.extend(["--allowedTools", allowlist])
        if max_thinking_tokens:
            cmd.extend(["--max-thinking-tokens", str(int(max_thinking_tokens))])
        return cmd

    def run(self, cmd, prompt, sandbox, env, output_file, walltime_s=None):
        # We read stream-json in-process (instead of teeing to
        # stream_display.py) so we can watchdog the post-result hang: claude
        # emits its final `result` event and then sometimes stalls on MCP /
        # hook / watcher shutdown, holding the main process alive and
        # blocking agent.wait(). Once we see `result`, arm a background
        # timer; if the process hasn't exited by then, kill its group —
        # that closes stdout, the for-loop sees EOF, and agent.wait()
        # returns cleanly.
        import threading

        from agent_runtime.stream_display import render_line

        # start_new_session puts the agent + all children in a fresh process
        # group so we can TERM/KILL the whole session if the main hangs.
        agent = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            bufsize=1,
        )
        pgid = os.getpgid(agent.pid)

        POST_RESULT_GRACE_S = 15.0
        watchdog: threading.Timer | None = None
        killed_for_hang = False
        walltime_timer = _arm_walltime_watchdog(pgid, walltime_s)

        def _fire_watchdog() -> None:
            nonlocal killed_for_hang
            if agent.poll() is None:
                print(
                    f"\n[runner] claude stalled {POST_RESULT_GRACE_S:.0f}s "
                    "after result event; terminating session.",
                    flush=True,
                )
                killed_for_hang = True
                _kill_process_group(pgid)

        try:
            with open(output_file, "wb") as f:
                assert agent.stdout is not None
                for raw in agent.stdout:
                    f.write(raw)
                    f.flush()
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    render_line(line)
                    if watchdog is None and _is_result_line(line):
                        watchdog = threading.Timer(POST_RESULT_GRACE_S, _fire_watchdog)
                        watchdog.daemon = True
                        watchdog.start()
            rc = agent.wait()
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if walltime_timer is not None:
                walltime_timer.cancel()
            _kill_process_group(pgid)

        # A session we killed post-result is a successful run from the
        # benchmark's POV — the model finished; only cleanup hung.
        if rc != 0 and not killed_for_hang:
            raise subprocess.CalledProcessError(rc, cmd)


class CodexRunner(Runner):
    """OpenAI Codex CLI."""

    @property
    def name(self) -> str:
        return "codex"

    def build_command(
        self, prompt, sandbox, model, allowlist, max_thinking_tokens=None, effort_label=None
    ):
        # Codex has no thinking-tokens flag; it takes an enum reasoning-effort
        # setting via -c model_reasoning_effort=<minimal|low|medium|high|xhigh>.
        # Our "max" label is an alias for "xhigh" on codex (the highest
        # reasoning tier the CLI accepts — GPT-5 family models support it).
        # Unknown / custom(N) labels are omitted so codex falls back to its
        # default.

        # Codex stores session state + helper binaries under $CODEX_HOME.
        # We point it at a workspace-relative path (rw, NOT under /tmp —
        # codex 0.x refuses to create helper binaries under /tmp) and
        # populate it from the host's ~/.codex/ before launch. We must set
        # CODEX_HOME on os.environ (not the local env dict) so PodmanSandbox
        # picks it up and propagates via -e CODEX_HOME=<path> into the
        # container; the staging-$HOME design otherwise gives codex a
        # default CODEX_HOME of $HOME/.codex which lives on /tmp.
        codex_home = Path(sandbox) / ".codex_home"
        codex_home.mkdir(exist_ok=True)
        real_home = Path.home() / ".codex"
        for name in ("auth.json", "config.toml"):
            src = real_home / name
            if src.is_file():
                shutil.copy2(src, codex_home / name)
        os.environ["CODEX_HOME"] = str(codex_home)

        binary = _find_binary("codex", "CODEX_BIN")
        cmd = [binary]
        if model:
            cmd.extend(["-m", model])

        codex_effort_map = {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
            "max": "xhigh",
        }
        codex_effort = codex_effort_map.get((effort_label or "").lower())
        if codex_effort:
            cmd.extend(["-c", f"model_reasoning_effort={codex_effort}"])

        cmd.extend(
            [
                "-a",
                "never",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "-C",
                str(sandbox),
                "-s",
                "danger-full-access",
                # Prompt is read from stdin (`-`) so long prompts aren't
                # constrained by argv limits and we don't have to escape.
                "-",
            ]
        )
        return cmd

    def run(self, cmd, prompt, sandbox, env, output_file, walltime_s=None):
        # CODEX_HOME setup happens in build_command (must run before
        # PodmanSandbox.wrap captures os.environ). Mirror it onto the env
        # dict so non-container backends (bwrap, none) also pick it up.
        codex_home = Path(sandbox) / ".codex_home"
        env = dict(env)
        env["CODEX_HOME"] = str(codex_home)

        from agent_runtime.stream_display import render_line

        proc = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        walltime_timer = _arm_walltime_watchdog(pgid, walltime_s)
        try:
            # Prompt on stdin; closed before we start reading stdout so the
            # agent sees EOF and can begin producing events immediately.
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(prompt.encode())
            proc.stdin.close()
            with open(output_file, "wb") as f:
                for raw in proc.stdout:
                    f.write(raw)
                    f.flush()
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    render_line(line)
            rc = proc.wait()
        finally:
            if walltime_timer is not None:
                walltime_timer.cancel()
            _kill_process_group(pgid)
            # Codex may leave large plugin caches / arg0 temp dirs behind; we
            # keep auth.json for post-run inspection but drop the bulk.
            for sub in ("tmp", "cache", "logs_2.sqlite", "logs_2.sqlite-shm", "logs_2.sqlite-wal"):
                p = codex_home / sub
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file():
                    p.unlink(missing_ok=True)
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


class GeminiRunner(Runner):
    """Google Gemini CLI (@google/gemini-cli).

    Headless contract is close to Claude's: -p <prompt> plus
    -o stream-json streams newline-delimited JSON events. Auth is via
    ~/.gemini/oauth_creds.json (one-time `gemini` interactive login on
    the host); runs here draw against the Google AI subscription tied
    to that account, not a per-token API key.
    """

    @property
    def name(self) -> str:
        return "gemini"

    def build_command(
        self, prompt, sandbox, model, allowlist, max_thinking_tokens=None, effort_label=None
    ):
        # Gemini CLI exposes neither a thinking-tokens budget nor an enum
        # reasoning-effort flag — the server-side router auto-routes between
        # gemini-3-pro, flash, and flash-lite based on the query. We pass
        # model through if set; otherwise let the router pick ("auto-gemini-3").
        binary = _find_binary("gemini", "GEMINI_BIN")
        cmd = [
            binary,
            "-p",
            prompt,
            "-o",
            "stream-json",
            # yolo = auto-approve all tool calls; required for headless
            # runs inside the bwrap sandbox where we can't answer prompts.
            "--approval-mode",
            "yolo",
        ]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def run(self, cmd, prompt, sandbox, env, output_file, walltime_s=None):
        # Gemini keeps session state, oauth creds, and a sqlite history
        # under $GEMINI_CLI_HOME (default ~/.gemini). Mirror CodexRunner's
        # per-run redirect so:
        #   1. parallel runs don't race on the same state sqlite,
        #   2. nothing mutates the real ~/.gemini under the sandbox,
        #   3. NERSC autofs $HOME doesn't trip the CLI's file locks.
        from agent_runtime.stream_display import render_line

        # GEMINI_CLI_HOME is a HOME-like prefix: the CLI reads and writes
        # <GEMINI_CLI_HOME>/.gemini/*, so seed the creds into the .gemini/
        # subdir rather than directly into the prefix.
        # GEMINI_CLI_HOME is a HOME-like prefix: the CLI reads and writes
        # <GEMINI_CLI_HOME>/.gemini/*, so seed the creds into the .gemini/
        # subdir rather than directly into the prefix.
        gemini_home = Path(sandbox) / ".gemini_home"
        gemini_state = gemini_home / ".gemini"
        gemini_state.mkdir(parents=True, exist_ok=True)
        real_state = Path.home() / ".gemini"
        if real_state.is_dir():
            for name in ("oauth_creds.json", "google_accounts.json", "installation_id"):
                src = real_state / name
                if src.exists():
                    shutil.copy2(src, gemini_state / name)
        # Rewrite settings.json: keep the auth method but disable the IDE
        # companion probe (the CLI otherwise spams ECONNREFUSED trying to
        # reach a VS Code extension that isn't running inside the sandbox).
        import json as _json

        host_settings = real_state / "settings.json" if real_state.is_dir() else None
        settings: dict = {}
        if host_settings and host_settings.exists():
            try:
                settings = _json.loads(host_settings.read_text())
            except _json.JSONDecodeError:
                settings = {}
        settings.setdefault("security", {}).setdefault("auth", {}).setdefault(
            "selectedType", "oauth-personal"
        )
        settings["ide"] = {"enabled": False}
        (gemini_state / "settings.json").write_text(_json.dumps(settings, indent=2))
        env = dict(env)
        env["GEMINI_CLI_HOME"] = str(gemini_home)
        # The CLI also reads HOME to locate ~/.gemini in some paths; keep the
        # real HOME (bwrap doesn't tmpfs it on NERSC) so other google libs
        # still find their caches.

        agent = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pgid = os.getpgid(agent.pid)
        walltime_timer = _arm_walltime_watchdog(pgid, walltime_s)

        try:
            with open(output_file, "wb") as f:
                assert agent.stdout is not None
                for raw in agent.stdout:
                    f.write(raw)
                    f.flush()
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    render_line(line)
            rc = agent.wait()
        finally:
            if walltime_timer is not None:
                walltime_timer.cancel()
            _kill_process_group(pgid)

        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


class AiderRunner(Runner):
    """Aider — open-source coding CLI supporting 75+ models via OpenRouter, Gemini, etc.

    Install: pip install aider-chat
    Models:  --model gemini/gemini-2.5-pro
             --model openrouter/anthropic/claude-sonnet-4
             --model deepseek/deepseek-chat
             --model ollama/llama3
    """

    @property
    def name(self) -> str:
        return "aider"

    def build_command(
        self, prompt, sandbox, model, allowlist, max_thinking_tokens=None, effort_label=None
    ):
        # Aider has no universal thinking-tokens flag across the 75+ model backends.
        binary = _find_binary("aider", "AIDER_BIN")
        cmd = [
            binary,
            "--message",
            prompt,
            "--yes-always",  # auto-approve all edits and commands
            "--no-git",  # sandbox has no git repo
            "--no-auto-commits",  # we manage artifacts, not git
            "--no-suggest-shell-commands",  # just run them
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def run(self, cmd, prompt, sandbox, env, output_file, walltime_s=None):
        proc = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        walltime_timer = _arm_walltime_watchdog(pgid, walltime_s)
        try:
            out, _ = proc.communicate()
        finally:
            if walltime_timer is not None:
                walltime_timer.cancel()
            _kill_process_group(pgid)
        output_file.write_text(out or "")
        for line in (out or "").splitlines():
            if line.startswith(">") or "─" in line or "Error" in line:
                print(f"  {line}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)


# ── Registration ────────────────────────────────────────────────────────────
# Register each runner under its `name` property. The `register` decorator
# is provided by agent_runtime.runners and adds to the RUNNERS registry.

register(ClaudeRunner)
register(CodexRunner)
register(GeminiRunner)
register(AiderRunner)
