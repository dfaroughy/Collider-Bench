"""Declarative spec for a vendor agent CLI — Pydantic-based.

A `RunnerSpec` describes everything the harness needs to drive a vendor's
agentic CLI: how to compose the command line, where the prompt goes, how
effort is expressed, which stream parser to use, and (for vendors that
need it) named pre-launch / post-run imperative hooks.

Adding a new vendor is then either:
  - One `RunnerSpec(...)` literal + a call to `register_declarative()`.
  - For vendors with novel quirks: register a small named hook (Python
    function) and reference it from the spec by name.

This replaces the per-vendor `Runner` subclass pattern for the common case.
The abstract `Runner` ABC in `runners.py` is unchanged; subclassing it
directly is still fully supported for vendors that don't fit the spec
shape.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.runners import (
    Runner,
    _arm_walltime_watchdog,
    _find_binary,
    _is_result_line,
    _kill_process_group,
    register,
)

# ── Stream parser registry ───────────────────────────────────────────────────
#
# A "stream parser" is just a callable that gets each stdout line as it
# arrives and renders something to the terminal. The harness already has
# a unified renderer for all three JSON formats (claude, codex, gemini)
# in `stream_display.render_line` — it auto-detects format. So the JSON
# parsers all alias to it; only aider's plain-text output needs its own
# render rule.

LineRenderer = Callable[[str], None]


def _render_stream_json(line: str) -> None:
    from agent_runtime.stream_display import render_line

    render_line(line)


def _render_aider_text(line: str) -> None:
    # Aider streams plain text; surface only the lines a human cares about
    # (assistant turns, tool-call boxes, errors).
    if line.startswith(">") or "─" in line or "Error" in line:
        print(f"  {line}", flush=True)


def _render_raw(_line: str) -> None:
    """Passthrough — write to file, render nothing."""
    return None


STREAM_PARSERS: dict[str, LineRenderer] = {
    "stream_json": _render_stream_json,
    "aider_text": _render_aider_text,
    "raw": _render_raw,
}


# ── Hook registries ──────────────────────────────────────────────────────────
#
# Pre-launch hooks: imperative setup that has to happen before subprocess.Popen
# (e.g., copying OAuth creds into a workspace-relative state dir). They get the
# sandbox path, the subprocess env dict (mutable), and may also mutate
# os.environ for state that has to round-trip through PodmanSandbox.
#
# Post-run hooks: cleanup after the subprocess exits (e.g., dropping bulky
# vendor caches/logs).

PreLaunchHook = Callable[[Path, dict[str, str]], None]
PostRunHook = Callable[[Path], None]

PRE_LAUNCH_HOOKS: dict[str, PreLaunchHook] = {}
POST_RUN_HOOKS: dict[str, PostRunHook] = {}


def register_pre_launch(name: str, hook: PreLaunchHook) -> None:
    PRE_LAUNCH_HOOKS[name] = hook


def register_post_run(name: str, hook: PostRunHook) -> None:
    POST_RUN_HOOKS[name] = hook


# ── The spec ────────────────────────────────────────────────────────────────


class RunnerSpec(BaseModel):
    """Declarative description of a vendor agent CLI.

    Command construction order (skip any None / empty):
        [binary]
          + pre_subcommand_args
          + ([model_flag, model] if model)
          + ([effort_config_flag, "<key>=<mapped>"] if effort applies)
          + subcommand
          + post_subcommand_args                       (with {sandbox} substituted)
          + ([allowlist_flag, allowlist] if allowlist)
          + ([disallowed_tools_flag, ",".join(disallowed_tools)] if applicable)
          + ([thinking_tokens_flag, "N"] if max_thinking_tokens applies)
          + ([prompt_flag, prompt] if prompt_via == "flag")
          + final_args
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ── Identity ────────────────────────────────────────────────────────────
    name: str = Field(description="`--runner` value, e.g. 'claude'")
    binary: str = Field(description="Executable to find on PATH")
    binary_env_var: str | None = Field(
        default=None, description="Env var override for the binary path (e.g. 'CLAUDE_BIN')"
    )

    # ── Static command pieces ───────────────────────────────────────────────
    pre_subcommand_args: list[str] = Field(default_factory=list)
    subcommand: list[str] = Field(default_factory=list)
    post_subcommand_args: list[str] = Field(
        default_factory=list, description="Strings may include {sandbox} placeholder"
    )
    final_args: list[str] = Field(default_factory=list)

    # ── Prompt delivery ─────────────────────────────────────────────────────
    prompt_via: Literal["flag", "stdin"] = "flag"
    prompt_flag: str = "-p"

    # ── Model / allowlist / disallowed ──────────────────────────────────────
    model_flag: str | None = "--model"
    allowlist_flag: str | None = None
    disallowed_tools_flag: str | None = None
    disallowed_tools: list[str] = Field(default_factory=list)

    # ── Effort: two mutually-exclusive shapes ───────────────────────────────
    # 1) thinking_tokens_flag + max_thinking_tokens (Claude pattern).
    # 2) effort_config_flag + effort_config_key + effort_label_map (Codex pattern).
    thinking_tokens_flag: str | None = None
    effort_config_flag: str | None = None
    effort_config_key: str | None = None
    effort_label_map: dict[str, str] = Field(default_factory=dict)

    # ── Streaming + lifecycle ───────────────────────────────────────────────
    stream_format: Literal["stream_json", "aider_text", "raw"] = "raw"
    post_result_grace_s: float = Field(
        default=0.0, description=">0 enables 'kill the group N s after the result event'"
    )

    # ── Hook names (lookup keys into the registries above) ──────────────────
    pre_launch_hook: str | None = None
    post_run_hook: str | None = None

    # ── Static env to inject into the subprocess ────────────────────────────
    extra_env: dict[str, str] = Field(default_factory=dict)


# ── Generic runner driven by a spec ──────────────────────────────────────────


class DeclarativeRunner(Runner):
    """A `Runner` whose entire behavior is described by a `RunnerSpec`."""

    def __init__(self, spec: RunnerSpec):
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

    def build_command(
        self,
        prompt,
        sandbox,
        model,
        allowlist,
        max_thinking_tokens=None,
        effort_label=None,
    ):
        s = self._spec
        substitutions = {"sandbox": str(sandbox)}

        def _fmt(args: list[str]) -> list[str]:
            return [a.format(**substitutions) if "{" in a else a for a in args]

        cmd: list[str] = [_find_binary(s.binary, s.binary_env_var)]
        cmd.extend(_fmt(s.pre_subcommand_args))

        if s.model_flag and model:
            cmd.extend([s.model_flag, model])

        if s.effort_config_flag and s.effort_config_key:
            mapped = s.effort_label_map.get((effort_label or "").lower())
            if mapped:
                cmd.extend([s.effort_config_flag, f"{s.effort_config_key}={mapped}"])

        cmd.extend(_fmt(s.subcommand))
        cmd.extend(_fmt(s.post_subcommand_args))

        if s.allowlist_flag and allowlist:
            cmd.extend([s.allowlist_flag, allowlist])

        if s.disallowed_tools_flag and s.disallowed_tools:
            cmd.extend([s.disallowed_tools_flag, ",".join(s.disallowed_tools)])

        if s.thinking_tokens_flag and max_thinking_tokens:
            cmd.extend([s.thinking_tokens_flag, str(int(max_thinking_tokens))])

        if s.prompt_via == "flag":
            cmd.extend([s.prompt_flag, prompt])
        # prompt_via == "stdin" → fed in run()

        cmd.extend(_fmt(s.final_args))
        return cmd

    def run(self, cmd, prompt, sandbox, env, output_file, walltime_s=None):
        s = self._spec
        env = dict(env)

        if s.pre_launch_hook:
            hook = PRE_LAUNCH_HOOKS.get(s.pre_launch_hook)
            if hook is None:
                raise KeyError(
                    f"runner {s.name!r}: pre_launch_hook {s.pre_launch_hook!r} not registered"
                )
            hook(Path(sandbox), env)
        env.update(s.extra_env)

        popen_kwargs: dict = {
            "cwd": sandbox,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "start_new_session": True,
        }
        if s.prompt_via == "stdin":
            popen_kwargs["stdin"] = subprocess.PIPE

        proc = subprocess.Popen(cmd, **popen_kwargs)
        pgid = os.getpgid(proc.pid)

        walltime_timer = _arm_walltime_watchdog(pgid, walltime_s)
        watchdog = None
        killed_for_hang = False

        def _fire_watchdog() -> None:
            nonlocal killed_for_hang
            if proc.poll() is None:
                print(
                    f"\n[runner] {s.name} stalled {s.post_result_grace_s:.0f}s "
                    "after result event; terminating session.",
                    flush=True,
                )
                killed_for_hang = True
                _kill_process_group(pgid)

        parser = STREAM_PARSERS[s.stream_format]
        try:
            if s.prompt_via == "stdin":
                assert proc.stdin is not None
                proc.stdin.write(prompt.encode())
                proc.stdin.close()

            with open(output_file, "wb") as f:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    f.write(raw)
                    f.flush()
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    parser(line)
                    if s.post_result_grace_s > 0 and watchdog is None and _is_result_line(line):
                        import threading

                        watchdog = threading.Timer(s.post_result_grace_s, _fire_watchdog)
                        watchdog.daemon = True
                        watchdog.start()
            rc = proc.wait()
        finally:
            if walltime_timer is not None:
                walltime_timer.cancel()
            if watchdog is not None:
                watchdog.cancel()
            _kill_process_group(pgid)
            if s.post_run_hook:
                post_hook = POST_RUN_HOOKS.get(s.post_run_hook)
                if post_hook is not None:
                    post_hook(Path(sandbox))

        # A session we killed post-result is a successful run from the
        # benchmark's POV — the model finished; only cleanup hung.
        if rc != 0 and not killed_for_hang:
            raise subprocess.CalledProcessError(rc, cmd)


def register_declarative(spec: RunnerSpec) -> None:
    """Register a `RunnerSpec` so `--runner <spec.name>` resolves to it.

    Equivalent to subclassing Runner + decorating with @register, but
    without the boilerplate.
    """
    name = spec.name

    class _Bound(DeclarativeRunner):
        def __init__(self):
            super().__init__(spec)

    _Bound.__name__ = f"DeclarativeRunner_{name}"
    _Bound.__qualname__ = _Bound.__name__
    register(_Bound)
