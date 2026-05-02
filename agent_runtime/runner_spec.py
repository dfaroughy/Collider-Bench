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
import re
import subprocess
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.runners import (
    LaunchPrep,
    Runner,
    _arm_walltime_watchdog,
    _find_binary,
    _is_result_line,
    _kill_process_group,
    register,
)

# ── Stream parser registry ───────────────────────────────────────────────────
#
# Each parser takes one stdout line, prints whatever a human should see to
# the terminal, and returns the line(s) that should be written to the run's
# session.jsonl. Returning [] suppresses the line from the file (forge uses
# this — its session.jsonl is built post-run from its SQLite DB, which has
# richer structure than the TTY stream).
#
# Why filter at file-write time? session.jsonl is consumed by the LLM judge
# during evaluation; spinner frames + ANSI clear codes are unreadable and
# pricey to feed to the judge. claude/codex/gemini already emit valid JSONL
# (one JSON object per line) so their parsers pass lines through unchanged.
LineRenderer = Callable[[str], list[str]]


def _render_stream_json(line: str) -> list[str]:
    # All JSON streams (claude / codex / gemini) → the line IS structured
    # data. Keep it verbatim in the file; render to terminal via the
    # unified auto-detecting renderer.
    from agent_runtime.stream_display import render_line

    render_line(line)
    return [line] if line else []


def _render_aider_text(line: str) -> list[str]:
    # Aider streams plain text. Surface only assistant turns, tool-call
    # boxes, and errors — both to terminal and to file.
    if line.startswith(">") or "─" in line or "Error" in line:
        print(f"  {line}", flush=True)
        return [line]
    return []


# Forge's TTY output: ANSI clear-line codes + a spinner that updates with
# CR (no LF) so a single Python "line" can hold dozens of status frames.
# We split on the ANSI escape, drop spinner frames, keep status bullets
# (`● [HH:MM:SS] ...`) and any plain assistant-content text.
_ANSI_ESC = re.compile(r"\x1b\[\d*[A-Za-z]")
# Braille spinner glyphs Forge cycles through.
_SPINNER_CHARS = "⠹⠸⠼⠴⠦⠧⠇⠏⠋⠙⠉⠩⠡⠪⠠⠐⠈⠌"
# A spinner line looks like:
#   <braille> Processing 2:27m · Ctrl+C to interrupt
#   <braille> Researching 04s · Ctrl+C to interrupt
#   <braille> Analyzing 1:23:45h · ...
# Time can be `\d+s`, `\d+:\d+m`, `\d+:\d+:\d+h`. Anchor on the middle
# dot (·) which is constant across all spinner messages.
_SPINNER_LINE = re.compile(rf"^[{re.escape(_SPINNER_CHARS)}]\s+\S+\s+\S+\s*·")


# Forge bullet event format: `● [HH:MM:SS] Verb [<arg>] <rest>`.
_FORGE_EVENT = re.compile(r"^●\s+\[[\d:]+\]\s+(\w+)")


class _ForgeStreamParser:
    """TTY-only Forge parser.

    Forge's TTY stream interleaves ANSI clear codes, a Braille spinner,
    bullet event lines (`● [HH:MM:SS] Verb [...] arg`), and the bash
    stdout transcription that follows `Execute` events. We strip the
    spinner / ANSI noise and print clean event lines to the terminal so
    the user sees progress, but return `[]` for every line — the
    file-side `session.jsonl` is built post-run from the SQLite DB by
    `_forge_post_run`, which has the full structured conversation
    (including per-call DeepSeek `usage`) instead of this TTY view.
    """

    # Verbs whose succeeding non-bullet lines are tool-output transcription.
    _DROP_AFTER = frozenset({"Execute"})

    def __init__(self) -> None:
        self._dropping = False

    def __call__(self, line: str) -> list[str]:
        if not line:
            return []
        for chunk in _ANSI_ESC.split(line):
            chunk = chunk.strip()
            if not chunk or _SPINNER_LINE.match(chunk):
                continue
            m = _FORGE_EVENT.match(chunk)
            if m:
                self._dropping = m.group(1) in self._DROP_AFTER
                print(f"  {chunk}", flush=True)
                continue
            if self._dropping:
                continue  # bash output / file content / etc.
            print(f"  {chunk}", flush=True)
        return []  # session.jsonl is written post-run from the forge DB


def _render_raw(line: str) -> list[str]:
    # Passthrough — keep verbatim, render nothing to terminal.
    return [line] if line else []


# Each entry is a factory: called once per run to produce a callable
# that processes lines. Stateless parsers return the function directly;
# stateful ones (forge_text) return a fresh instance per run.
STREAM_PARSERS: dict[str, Callable[[], LineRenderer]] = {
    "stream_json": lambda: _render_stream_json,
    "aider_text": lambda: _render_aider_text,
    "forge_text": lambda: _ForgeStreamParser(),
    "raw": lambda: _render_raw,
}


# ── Hook registries ──────────────────────────────────────────────────────────
#
# Pre-launch hooks: imperative setup that has to happen before sandbox command
# construction (e.g., copying OAuth creds into a workspace-relative state dir).
# They return LaunchPrep so env/bind requirements are explicit.
#
# Post-run hooks: cleanup after the subprocess exits (e.g., dropping bulky
# vendor caches/logs).

PreLaunchHook = Callable[[Path], LaunchPrep]
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
    stream_format: Literal["stream_json", "aider_text", "forge_text", "raw"] = "raw"
    post_result_grace_s: float = Field(
        default=0.0, description=">0 enables 'kill the group N s after the result event'"
    )

    # ── Hook names (lookup keys into the registries above) ──────────────────
    pre_launch_hook: str | None = None
    post_run_hook: str | None = None

    # ── Static env to inject into the subprocess ────────────────────────────
    extra_env: dict[str, str] = Field(default_factory=dict)
    secret_env_names: tuple[str, ...] = Field(default_factory=tuple)

    # ── Per-run fake HOME ───────────────────────────────────────────────────
    # Container backends create `<run>/<home_dir_name>` and copy only these
    # host-relative files into it. Keep this runner-specific so a Claude run
    # cannot inherit Codex/Gemini/Forge state.
    home_dir_name: str = ".agent_home"
    home_files: tuple[str, ...] = Field(default_factory=tuple)
    home_credential_files: tuple[str, ...] = Field(default_factory=tuple)


# ── Generic runner driven by a spec ──────────────────────────────────────────


class DeclarativeRunner(Runner):
    """A `Runner` whose entire behavior is described by a `RunnerSpec`."""

    def __init__(self, spec: RunnerSpec):
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

    def prepare_launch(self, sandbox: Path, config: dict | None = None) -> LaunchPrep:
        s = self._spec
        env: dict[str, str] = dict(s.extra_env)
        extra_ro_binds: list[Path] = []
        secret_env_names = list(s.secret_env_names)
        home_dir_name = s.home_dir_name
        home_files = list(s.home_files)
        home_credential_files = list(s.home_credential_files)
        auth = (config or {}).get("auth")
        provider = (config or {}).get("provider")
        if s.name == "claude" and auth == "api":
            home_dir_name = ".claude_api_home"
            home_files = [".claude/settings.json"]
            home_credential_files = []
        if s.name == "claude" and auth == "api" and provider == "deepseek":
            secret_env_names = []
            deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
            env.update(
                {
                    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                    "ANTHROPIC_MODEL": str((config or {}).get("model") or "deepseek-v4-pro[1m]"),
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1m]",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
                    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-flash",
                    "CLAUDE_CODE_EFFORT_LEVEL": str((config or {}).get("effort") or "max"),
                }
            )
            if deepseek_key:
                env["ANTHROPIC_AUTH_TOKEN"] = deepseek_key
            secret_env_names.extend(("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"))
        if s.pre_launch_hook:
            hook = PRE_LAUNCH_HOOKS.get(s.pre_launch_hook)
            if hook is None:
                raise KeyError(
                    f"runner {s.name!r}: pre_launch_hook {s.pre_launch_hook!r} not registered"
                )
            prep = hook(Path(sandbox))
            env.update(prep.env)
            extra_ro_binds.extend(prep.extra_ro_binds)
            secret_env_names.extend(prep.secret_env_names)
            home_dir_name = prep.home_dir_name or home_dir_name
            home_files.extend(prep.home_files)
            home_credential_files.extend(prep.home_credential_files)
        return LaunchPrep(
            env=env,
            extra_ro_binds=extra_ro_binds,
            secret_env_names=tuple(dict.fromkeys(secret_env_names)),
            home_dir_name=home_dir_name,
            home_files=tuple(home_files),
            home_credential_files=tuple(home_credential_files),
        )

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

        popen_kwargs: dict = {
            "cwd": sandbox,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "start_new_session": True,
        }
        if s.prompt_via == "stdin":
            popen_kwargs["stdin"] = subprocess.PIPE
        else:
            # Without an explicit /dev/null, the subprocess inherits the
            # harness's stdin. Combined with podman's -i flag, that pipes
            # the host's (possibly TTY-attached) stdin into the container,
            # which can make some CLIs (e.g. forge) wait for stdin EOF
            # before processing -p, hanging indefinitely.
            popen_kwargs["stdin"] = subprocess.DEVNULL

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

        parser = STREAM_PARSERS[s.stream_format]()  # factory: fresh state per run
        try:
            if s.prompt_via == "stdin":
                assert proc.stdin is not None
                proc.stdin.write(prompt.encode())
                proc.stdin.close()

            with open(output_file, "wb") as f:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    # Parser returns the cleaned line(s) to write to the
                    # session.jsonl (e.g. forge_text returns [] — file is
                    # Empty list means "this line is noise — skip writing".
                    for clean in parser(line):
                        f.write(clean.encode("utf-8") + b"\n")
                    f.flush()
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
