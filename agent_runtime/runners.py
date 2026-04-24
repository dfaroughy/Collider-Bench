"""Agent runner abstraction.

Each runner knows how to locate its CLI binary, build a command,
and execute the agent subprocess.  To add a new backend, subclass
Runner and register it in RUNNERS at the bottom of this file.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path


def _is_result_line(line: str) -> bool:
    """True if this stream-json line is the session's final `result` event."""
    # Structural match on the unambiguous `"type":"result"` key avoids a
    # json.loads on every line — this runs in the hot path of ClaudeRunner.
    return '"type":"result"' in line or '"type": "result"' in line


# ── Process-group cleanup ──────────────────────────────────────────────────


def _kill_process_group(pgid: int, grace_seconds: float = 2.0) -> None:
    """SIGTERM the process group, wait briefly, then SIGKILL survivors.

    Portable across Linux and macOS. No-op if the group is already gone.
    Handles the common case where an agent CLI leaves stray children (MCP
    servers, IDE hooks, file watchers) that hold a pipe open and prevent
    the wrapper from exiting.
    """
    if pgid <= 0:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)  # 0 = probe existence
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# ── Binary discovery ────────────────────────────────────────────────────────


def _find_binary(name: str, env_var: str | None = None) -> str:
    """Locate a CLI binary by name, env var override, or VS Code extension path."""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            path = Path(override).expanduser()
            if path.exists():
                return str(path)
            raise RuntimeError(f"{env_var} is set but does not exist: {path}")
    discovered = shutil.which(name)
    if discovered:
        return discovered
    home = Path.home()
    vscode_patterns = {
        "claude": ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude",
        "codex": ".vscode-server/extensions/openai.chatgpt-*/bin/linux-x86_64/codex",
    }
    pattern = vscode_patterns.get(name)
    if pattern:
        matches = sorted(home.glob(pattern), reverse=True)
        for match in matches:
            if match.exists():
                return str(match)
    raise RuntimeError(
        f"Could not find `{name}` CLI. Add it to PATH"
        + (f" or set {env_var} to the full path." if env_var else ".")
    )


# ── Base class ──────────────────────────────────────────────────────────────


class Runner(ABC):
    """Base class for agent runners.

    To add a new backend (e.g. Gemini CLI, local Ollama, OpenAI Agents SDK):
      1. Subclass Runner and implement the three abstract members.
      2. Add an entry to RUNNERS at the bottom of this file.
      3. The controller will pick it up via --runner <name>.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (used in --runner flag and directory naming)."""

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        sandbox: Path,
        model: str | None,
        allowlist: str | None,
        max_thinking_tokens: int | None = None,
        effort_label: str | None = None,
    ) -> list[str]:
        """Return the CLI command list to invoke the agent.

        max_thinking_tokens: reasoning budget. Passed through by runners
        that support it (e.g. Claude's --max-thinking-tokens); ignored by
        runners that don't.

        effort_label: symbolic effort level ("low"|"medium"|"high"|"max"|
        "xhigh" or "custom(N)"). Used by runners with enum-style effort
        configs (e.g. Codex's model_reasoning_effort); ignored by runners
        that key off max_thinking_tokens alone.
        """

    @abstractmethod
    def run(
        self,
        cmd: list[str],
        prompt: str,
        sandbox: Path,
        env: dict[str, str],
        output_file: Path,
    ) -> None:
        """Execute the agent subprocess.

        Must raise subprocess.CalledProcessError on failure.
        """


# ── Built-in runners ───────────────────────────────────────────────────────


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

    def run(self, cmd, prompt, sandbox, env, output_file):
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

    def run(self, cmd, prompt, sandbox, env, output_file):
        # Codex stores session state + helper binaries under $CODEX_HOME (default
        # ~/.codex). Two reasons to redirect it per run:
        #   1. NERSC Perlmutter compute nodes: $HOME is autofs-mounted GPFS,
        #      which returns ENOTSUPP (os error 524) on the directory locks
        #      codex uses at session init.
        #   2. bwrap tmpfs's /tmp so we can't reuse that, and ro-binds $HOME
        #      so we can't have codex mutate the real ~/.codex under isolation.
        # Path lives inside the workspace (which is rw-bound by bwrap) so codex
        # can see and write to it from inside the sandbox.
        codex_home = Path(sandbox) / ".codex_home"
        codex_home.mkdir(exist_ok=True)
        real_home = Path.home() / ".codex"
        for name in ("auth.json", "config.toml"):
            src = real_home / name
            if src.exists():
                shutil.copy2(src, codex_home / name)
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

    def run(self, cmd, prompt, sandbox, env, output_file):
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

    def run(self, cmd, prompt, sandbox, env, output_file):
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
        try:
            out, _ = proc.communicate()
        finally:
            _kill_process_group(pgid)
        output_file.write_text(out or "")
        for line in (out or "").splitlines():
            if line.startswith(">") or "─" in line or "Error" in line:
                print(f"  {line}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)


# ── Registry ────────────────────────────────────────────────────────────────

RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,
    "codex": CodexRunner,
    "gemini": GeminiRunner,
    "aider": AiderRunner,
}


def get_runner(name: str) -> Runner:
    """Instantiate a runner by name. Raises ValueError for unknown names."""
    cls = RUNNERS.get(name)
    if cls is None:
        available = ", ".join(sorted(RUNNERS))
        raise ValueError(f"Unknown runner: {name!r}. Available: {available}")
    return cls()
