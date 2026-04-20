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
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path


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
    ) -> list[str]:
        """Return the CLI command list to invoke the agent.

        max_thinking_tokens: reasoning budget. Passed through by runners
        that support it (e.g. Claude's --max-thinking-tokens); ignored by
        runners that don't.
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

    def build_command(self, prompt, sandbox, model, allowlist, max_thinking_tokens=None):
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
        display_script = str(Path(__file__).parent / "stream_display.py")

        # start_new_session puts the agent + all children in a fresh process
        # group; once the agent exits we can TERM/KILL any stragglers
        # (MCP servers, IDE hooks, file watchers) that would otherwise keep
        # the stdout pipe open and hang our wait().
        agent = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        pgid = os.getpgid(agent.pid)
        tee = subprocess.Popen(
            ["tee", str(output_file)],
            stdin=agent.stdout,
            stdout=subprocess.PIPE,
        )
        display = subprocess.Popen(
            [sys.executable, display_script],
            stdin=tee.stdout,
        )
        agent.stdout.close()
        tee.stdout.close()
        try:
            rc = agent.wait()
        finally:
            # Reap any lingering children in the agent's session before we
            # return, so the caller's process tree can exit cleanly.
            _kill_process_group(pgid)
            display.wait()
            tee.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


class CodexRunner(Runner):
    """OpenAI Codex CLI."""

    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, prompt, sandbox, model, allowlist, max_thinking_tokens=None):
        # Codex CLI has no direct thinking-tokens flag; effort is ignored here.
        binary = _find_binary("codex", "CODEX_BIN")
        cmd = [binary]
        if model:
            cmd.extend(["-m", model])
        cmd.extend(
            [
                "-a",
                "never",
                "exec",
                "--skip-git-repo-check",
                "-C",
                str(sandbox),
                "-s",
                "danger-full-access",
                "-o",
                str(sandbox / "session_log.txt"),
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

        proc = subprocess.Popen(
            cmd,
            cwd=sandbox,
            env=env,
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        try:
            proc.communicate(input=prompt)
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
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)


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

    def build_command(self, prompt, sandbox, model, allowlist, max_thinking_tokens=None):
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
    "aider": AiderRunner,
}


def get_runner(name: str) -> Runner:
    """Instantiate a runner by name. Raises ValueError for unknown names."""
    cls = RUNNERS.get(name)
    if cls is None:
        available = ", ".join(sorted(RUNNERS))
        raise ValueError(f"Unknown runner: {name!r}. Available: {available}")
    return cls()
