"""Agent runner abstraction.

Each runner knows how to locate its CLI binary, build a command, and
execute the agent subprocess. This module defines the abstract `Runner`
base, the `register` decorator, the `RUNNERS` registry, and `get_runner`.
Concrete vendor runner subclasses (Claude Code, Codex, Gemini CLI, Aider)
live in the sibling module `_runners_vendor.py`. The try-import at the
bottom of this file registers them automatically.

Adding a new backend (e.g. an OSS-model harness, a different vendor
CLI, a local Ollama wrapper):
  1. Subclass `Runner` and implement the three abstract members.
  2. Decorate the class with `@register` (or call `register(MyRunner)`
     at module bottom). The `name` property of the class becomes the
     `--runner` flag value.
  3. The controller will pick it up via --runner <name>.
"""

from __future__ import annotations

import os
import shutil
import signal
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


def _arm_walltime_watchdog(pgid: int, walltime_s: float | None):
    """Arm a background timer that kills `pgid` after `walltime_s`.

    Returns the Timer (so the caller can cancel it on clean exit) or
    None if no walltime was supplied. The timer prints a one-line
    notice before killing so users can distinguish "agent ran out of
    time" from generic CalledProcessError failures.

    Used by every vendor runner to enforce the per-task walltime from
    `task.toml [metadata].walltime`. Plays well with the runner's own
    finally-block kill — calling _kill_process_group twice on a dead
    group is a no-op.
    """
    if not walltime_s or walltime_s <= 0:
        return None
    import threading

    def _on_timeout() -> None:
        print(
            f"\n[runner] task walltime ({walltime_s:.0f} s) exceeded; "
            "terminating agent process group.",
            flush=True,
        )
        _kill_process_group(pgid)

    t = threading.Timer(walltime_s, _on_timeout)
    t.daemon = True
    t.start()
    return t


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
        walltime_s: float | None = None,
    ) -> None:
        """Execute the agent subprocess.

        Must raise subprocess.CalledProcessError on failure.

        walltime_s: per-task walltime in seconds (sourced from
        `task.toml [metadata].walltime`). When set, runners arm a
        background timer that SIGTERM/SIGKILLs the process group on
        expiry — pair with `_arm_walltime_watchdog` from this module.
        Treat the post-kill non-zero exit as a normal failure (let
        `CalledProcessError` propagate so the run is marked failed).
        """


# ── Registry ────────────────────────────────────────────────────────────────

RUNNERS: dict[str, type[Runner]] = {}


def register(cls: type[Runner]) -> type[Runner]:
    """Add a `Runner` subclass to the `RUNNERS` registry under its `name`."""
    name = cls().name
    RUNNERS[name] = cls
    return cls


def get_runner(name: str) -> Runner:
    """Instantiate a runner by name. Raises ValueError for unknown names."""
    cls = RUNNERS.get(name)
    if cls is None:
        available = ", ".join(sorted(RUNNERS)) or "(none registered)"
        raise ValueError(
            f"Unknown runner: {name!r}. Available: {available}. "
            "Add a new backend by subclassing `Runner` and calling "
            "`register(...)` from this module."
        )
    return cls()


# ── Built-in runners ───────────────────────────────────────────────────────
# Vendor `Runner` subclasses (Claude / Codex / Gemini / Aider) live in
# `_runners_vendor.py` and self-register on import.
from agent_runtime import _runners_vendor  # noqa: E402,F401
