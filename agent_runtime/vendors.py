"""Vendor agent-CLI runner specs (Claude Code, Codex, Gemini CLI, Aider, Forge).

Each vendor is one `RunnerSpec` literal plus, where needed, named pre-launch
or post-run hooks that prepare per-run state (e.g. seed OAuth creds into a
workspace-relative dir, mine the forge SQLite DB into session.jsonl).

Auto-imported from `agent_runtime/runners.py` so the runners self-register.
Adding a new vendor is a single RunnerSpec at the bottom — no class hierarchy.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from agent_runtime.runner_spec import (
    LaunchPrep,
    RunnerSpec,
    register_declarative,
    register_post_run,
    register_pre_launch,
)


# ── Pre-launch / post-run hooks (the imperative bits) ───────────────────────


def _codex_pre_launch(sandbox: Path, config: dict | None = None) -> LaunchPrep:
    """Set up CODEX_HOME under workspace/.codex_home and seed creds.

    Codex stores session state + helper binaries under $CODEX_HOME. We
    point it at a workspace-relative path (rw, NOT under /tmp — codex 0.x
    refuses to create helper binaries under /tmp) and populate it from
    the host's ~/.codex/ before launch.

    The returned LaunchPrep.env is passed both to the subprocess and to
    container backends before they build `podman/apptainer ... -e`.
    """
    codex_home = sandbox / ".codex_home"
    codex_home.mkdir(parents=True, exist_ok=True)
    real_home = Path.home() / ".codex"
    for name in ("auth.json", "config.toml"):
        src = real_home / name
        if src.is_file():
            shutil.copy2(src, codex_home / name)
    return LaunchPrep(env={"CODEX_HOME": str(codex_home)}, home_dir_name=".codex_container_home")


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


def _gemini_pre_launch(sandbox: Path, config: dict | None = None) -> LaunchPrep:
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
    return LaunchPrep(
        env={"GEMINI_CLI_HOME": str(gemini_home)}, home_dir_name=".gemini_container_home"
    )


_FORGE_TRUNC_LEN = 8192  # chars per string field; bash output / file dumps cap here
_FORGE_TRUNC_HEAD = 2048  # head/tail kept around the truncation marker
_FORGE_TRUNC_TAIL = 2048


def _truncate_long_strings(obj: object) -> object:
    """Walk a JSON-deserialized tree and shorten any string > _FORGE_TRUNC_LEN.

    This is what keeps `session.jsonl` from re-acquiring the multi-MB
    bash-output bloat we used to filter out of the TTY stream. We trim
    every string field uniformly (vs. role-specific filtering) so that
    new forge message types don't silently start emitting full file
    dumps when forge upstream changes its schema.
    """
    if isinstance(obj, str):
        if len(obj) <= _FORGE_TRUNC_LEN:
            return obj
        cut = len(obj) - _FORGE_TRUNC_HEAD - _FORGE_TRUNC_TAIL
        return f"{obj[:_FORGE_TRUNC_HEAD]}\n[... truncated {cut} chars ...]\n{obj[-_FORGE_TRUNC_TAIL:]}"
    if isinstance(obj, dict):
        return {k: _truncate_long_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_long_strings(x) for x in obj]
    return obj


def _forge_post_run(sandbox: Path) -> None:
    """Convert forge's SQLite conversation DB into <workspace>/session.jsonl.

    Forge stores conversation state in `~/.forge/.forge.db`; the
    `conversations.context` JSON column holds the full message history
    including each turn's verbatim DeepSeek `usage` block
    (`prompt_tokens`, `completion_tokens`, `cached_tokens` — the same
    fields the OpenAI-compatible API returns). With fake HOME
    persisted at `<recast>/.forge_home`, that DB survives container
    exit so we can mine it on the host.

    We emit one JSON object per message, matching the streaming-JSONL
    shape the other vendors (claude/codex/gemini) write directly. Any
    string field longer than _FORGE_TRUNC_LEN is collapsed
    (head + marker + tail) to keep the file under a few MB even for
    long agentic runs that produce huge command outputs.

    Best-effort: any failure is logged and swallowed so a busted DB
    can't break the run finalize path.
    """
    import sqlite3

    recast = Path(sandbox).parent
    db_path = recast / ".forge_home" / ".forge" / ".forge.db"
    if not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        # Newest conversation = the run we just finished. Older rows would
        # only appear if the fake HOME got reused across runs.
        row = conn.execute(
            "SELECT context FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return
        ctx = json.loads(row[0])
    except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"_forge_post_run: {exc}\n")
        return
    messages = ctx.get("messages") or []
    out_path = Path(sandbox) / "session.jsonl"
    try:
        with open(out_path, "w") as f:
            # First line: a synthetic `init` event holding conversation-level
            # metadata so the LLM judge has the model id + system prompt
            # bounds without scanning every message.
            head = {
                "type": "init",
                "conversation_id": ctx.get("conversation_id", ""),
                "model": ctx.get("model", ""),
                "max_tokens": ctx.get("max_tokens"),
                "n_messages": len(messages),
            }
            f.write(json.dumps(head, separators=(",", ":")) + "\n")
            for m in messages:
                f.write(json.dumps(_truncate_long_strings(m), separators=(",", ":")) + "\n")
    except OSError as exc:
        sys.stderr.write(f"_forge_post_run: write {out_path}: {exc}\n")


def _opencode_pre_launch(sandbox: Path, config: dict | None = None) -> LaunchPrep:
    """Seed opencode's project-level provider config into the workspace.

    OpenCode reads its provider registry from `opencode.json` in the
    directory passed via `--dir`. We copy the harness-owned config
    (configs/opencode/opencode.json — declares both `glm-local` and
    `glm-air-local` as OpenAI-compatible providers that pull
    baseURL/apiKey from per-provider `GLM_*_API_BASE` env vars) into
    the sandbox so the same provider definition is found inside the
    container.

    `small_model` rewrite: opencode runs a separate small-model stream
    for title generation. The repo-checked-in opencode.json pins it to
    Flash, which 400s with "URL cannot be parsed" when only Air's
    server is up (or vice versa). We rewrite `small_model` to match
    the run's main model so the title pass uses the same provider —
    eliminates the cross-server leak when only one local server is
    live.

    Per-run HOME is redirected to `<sandbox>/.opencode_home` so
    opencode's XDG state (./local/share/opencode/opencode.db, plugin
    cache, etc.) stays inside the run dir and parallel runs don't
    race on the same sqlite files.
    """
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "configs" / "opencode" / "opencode.json"
    dst = sandbox / "opencode.json"
    if src.is_file():
        spec = json.loads(src.read_text())
        run_model = (config or {}).get("model")
        if run_model:
            spec["model"] = run_model
            spec["small_model"] = run_model
        dst.write_text(json.dumps(spec, indent=2))
    return LaunchPrep(home_dir_name=".opencode_home")


register_pre_launch("codex", _codex_pre_launch)
register_post_run("codex", _codex_post_run)
register_pre_launch("gemini", _gemini_pre_launch)
register_post_run("forge", _forge_post_run)
register_pre_launch("opencode", _opencode_pre_launch)


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
    secret_env_names=("ANTHROPIC_API_KEY",),
    home_dir_name=".claude_home",
    home_files=(
        ".claude.json",
        ".claude/.credentials.json",
        ".claude/settings.json",
    ),
    home_credential_files=(
        ".claude.json",
        ".claude/.credentials.json",
    ),
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
    secret_env_names=("OPENAI_API_KEY",),
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
    secret_env_names=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
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
    secret_env_names=(
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ),
    home_dir_name=".aider_home",
)


FORGE_SPEC = RunnerSpec(
    name="forge",
    binary="forge",
    binary_env_var="FORGE_BIN",
    # Forge has no --model flag; model is set via `forge config set model`
    # in the host's ~/.forge/.forge.toml, which the sandbox copies into this
    # runner's per-run fake $HOME. Switching models means `forge config
    # set model <id>` on the host, not editing this YAML.
    model_flag=None,
    # Run inside the workspace; -p enables headless single-turn mode.
    pre_subcommand_args=["-C", "{sandbox}"],
    prompt_flag="-p",
    # Forge emits a TTY-style stream (ANSI clear-line codes + a Braille
    # spinner with no LF between updates). The forge_text parser strips
    # the noise and surfaces status bullets + assistant content.
    stream_format="forge_text",
    # Suppress the spinner: without these the CR-only progress updates
    # accumulate in the pipe with no newline to flush them through Python's
    # line-buffered `for raw in proc.stdout` reader, blocking the harness
    # for the entire run. NO_COLOR is the cross-tool standard
    # (https://no-color.org); CI=true is what most CLIs check to switch
    # to non-interactive output; TERM=dumb is the belt-and-suspenders.
    extra_env={"NO_COLOR": "1", "CI": "true", "TERM": "dumb"},
    secret_env_names=("DEEPSEEK_API_KEY",),
    # Capture token totals from `forge conversation stats` after the run.
    post_run_hook="forge",
    home_dir_name=".forge_home",
    home_files=(
        ".forge/.credentials.json",
        ".forge/.forge.toml",
    ),
)


OPENCODE_SPEC = RunnerSpec(
    name="opencode",
    binary="opencode",
    binary_env_var="OPENCODE_BIN",
    # `opencode run --model <provider/model> --dir <ws> --dangerously-skip-permissions
    #   --format json -- <prompt>`
    # `--format json` makes opencode emit one JSON event per line on stdout,
    # so the unified `stream_json` parser already handles it like
    # claude/codex/gemini do.
    subcommand=["run"],
    post_subcommand_args=[
        "--dir",
        "{sandbox}",
        "--dangerously-skip-permissions",
        "--format",
        "json",
    ],
    # `--` makes a leading-dash prompt unambiguous against the flag parser.
    final_args=["--"],
    prompt_via="positional",
    model_flag="--model",
    # Effort is provider-specific in opencode (`--variant high|max|...`) and
    # only meaningful for proprietary backends; the local vLLM server ignores
    # it, so we don't wire it through.
    stream_format="stream_json",
    # GLM_*_API_{BASE,KEY} forwarded into the sandbox; opencode.json's
    # provider definitions read them via `{env:…}`. Both Flash and Air
    # pairs are listed so a single shared image / single sandbox spec
    # serves both providers — `_container_env_pairs` silently drops
    # whichever vars aren't actually set on the host, so unused entries
    # cost nothing. Add new pairs here when wiring more local providers.
    secret_env_names=(
        "GLM_API_BASE",
        "GLM_API_KEY",
        "GLM_AIR_API_BASE",
        "GLM_AIR_API_KEY",
    ),
    pre_launch_hook="opencode",
)


# ── Registration ────────────────────────────────────────────────────────────

for spec in (CLAUDE_SPEC, CODEX_SPEC, GEMINI_SPEC, AIDER_SPEC, FORGE_SPEC, OPENCODE_SPEC):
    register_declarative(spec)
