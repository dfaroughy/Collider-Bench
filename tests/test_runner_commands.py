"""Characterization of DeclarativeRunner.build_command and prepare_launch.

Pins the *current* argv and LaunchPrep produced for every registered vendor spec
(claude, codex, gemini, aider, forge): exact argument order, model / effort /
allowlist / thinking-token / prompt-delivery variations, `{sandbox}` substitution,
and how env, binds, secret names and per-run fake-home settings are merged from
the spec, the Claude API-key/DeepSeek overrides and the pre-launch hook, so the
extraction of the merge into `_merge_launch_prep` is shown to be behavior-preserving.
Most tests drive `build_command` / `prepare_launch`; the last section tests
`_merge_launch_prep` directly.

Offline: `_find_binary` is monkeypatched in `agent_runtime.runner_spec` (no CLI is
required), pre-launch hooks are replaced by fakes wherever merge order matters,
the few real hook bodies are run against a fake HOME under tmp_path, and no
process is ever spawned. `DeclarativeRunner.run` is out of scope here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# `agent_runtime.runners` must be imported before `agent_runtime.runner_spec`: the other
# order hits a circular import (runner_spec -> runners -> vendors -> runner_spec).
from agent_runtime.runners import RUNNERS, LaunchPrep, get_runner  # isort: skip
from agent_runtime import runner_spec  # isort: skip
from agent_runtime.runner_spec import DeclarativeRunner, RunnerSpec  # isort: skip

_SB = Path("/ws/sandbox")


@pytest.fixture
def find_calls(monkeypatch):
    """Replace _find_binary with a recorder returning /bin/<binary>."""
    calls: list[tuple[str, str | None]] = []

    def fake(name, env_var=None):
        calls.append((name, env_var))
        return f"/bin/{name}"

    monkeypatch.setattr(runner_spec, "_find_binary", fake)
    return calls


def _cmd(name, prompt="PROMPT", model="M", allowlist=None, tokens=None, effort=None):
    return get_runner(name).build_command(prompt, _SB, model, allowlist, tokens, effort)


# ── registry ────────────────────────────────────────────────────────────────


def test_registered_runner_names_and_classes():
    assert set(RUNNERS) >= {"claude", "codex", "gemini", "aider", "forge"}
    for name in ("claude", "codex", "gemini", "aider", "forge"):
        runner = get_runner(name)
        assert isinstance(runner, DeclarativeRunner)
        assert runner.name == name
        assert type(runner).__name__ == f"DeclarativeRunner_{name}"


# ── build_command: exact argv per vendor ────────────────────────────────────


def test_claude_full_command_order(find_calls):
    cmd = _cmd("claude", allowlist="Bash,Read", tokens=8000, effort="high")
    assert cmd == [
        "/bin/claude",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--model",
        "M",
        "--allowedTools",
        "Bash,Read",
        "--disallowedTools",
        "ScheduleWakeup",
        "--max-thinking-tokens",
        "8000",
        "-p",
        "PROMPT",
    ]
    assert find_calls == [("claude", "CLAUDE_BIN")]


def test_claude_omits_optional_pieces_when_absent(find_calls):
    cmd = _cmd("claude", model=None, allowlist=None, tokens=None)
    assert cmd == [
        "/bin/claude",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--disallowedTools",
        "ScheduleWakeup",
        "-p",
        "PROMPT",
    ]


@pytest.mark.parametrize("tokens", [0, None])
def test_claude_zero_or_none_thinking_tokens_are_omitted(find_calls, tokens):
    assert "--max-thinking-tokens" not in _cmd("claude", tokens=tokens)


def test_claude_thinking_tokens_are_coerced_through_int(find_calls):
    assert _cmd("claude", tokens="12000")[-4:-2] == ["--max-thinking-tokens", "12000"]


def test_claude_ignores_effort_label(find_calls):
    assert _cmd("claude", effort="xhigh") == _cmd("claude", effort=None)


def test_codex_full_command_order_and_prompt_is_not_in_argv(find_calls):
    cmd = _cmd("codex", model="gpt-x", effort="high", allowlist="ignored", tokens=99)
    assert cmd == [
        "/bin/codex",
        "-a",
        "never",
        "-m",
        "gpt-x",
        "-c",
        "model_reasoning_effort=high",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-C",
        str(_SB),
        "-s",
        "danger-full-access",
        "-",
    ]
    assert "PROMPT" not in cmd
    assert find_calls == [("codex", "CODEX_BIN")]


@pytest.mark.parametrize(
    ("label", "mapped"),
    [("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "xhigh"), ("max", "xhigh")],
)
def test_codex_effort_label_mapping(find_calls, label, mapped):
    cmd = _cmd("codex", effort=label)
    assert cmd[cmd.index("-c") + 1] == f"model_reasoning_effort={mapped}"


def test_codex_effort_label_is_case_insensitive(find_calls):
    cmd = _cmd("codex", effort="MAX")
    assert cmd[cmd.index("-c") + 1] == "model_reasoning_effort=xhigh"


@pytest.mark.parametrize("label", [None, "", "custom(12000)", "unknown"])
def test_codex_unmapped_effort_adds_no_config_flag(find_calls, label):
    assert "-c" not in _cmd("codex", effort=label)


def test_codex_without_model_has_no_model_flag(find_calls):
    cmd = _cmd("codex", model=None, effort=None)
    assert "-m" not in cmd and cmd[:3] == ["/bin/codex", "-a", "never"]


def test_gemini_command(find_calls):
    assert _cmd("gemini", model="g-1", allowlist="x", tokens=5, effort="high") == [
        "/bin/gemini",
        "-o",
        "stream-json",
        "--approval-mode",
        "yolo",
        "-m",
        "g-1",
        "-p",
        "PROMPT",
    ]
    assert find_calls == [("gemini", "GEMINI_BIN")]


def test_aider_command(find_calls):
    assert _cmd("aider", model="a-1") == [
        "/bin/aider",
        "--yes-always",
        "--no-git",
        "--no-auto-commits",
        "--no-suggest-shell-commands",
        "--model",
        "a-1",
        "--message",
        "PROMPT",
    ]
    assert find_calls == [("aider", "AIDER_BIN")]


def test_forge_command_has_no_model_flag_and_substitutes_sandbox(find_calls):
    cmd = _cmd("forge", model="ignored-model")
    assert cmd == ["/bin/forge", "-C", str(_SB), "-p", "PROMPT"]
    assert find_calls == [("forge", "FORGE_BIN")]


@pytest.mark.parametrize("name", ["claude", "gemini", "aider", "forge"])
def test_prompt_with_braces_is_never_formatted(find_calls, name):
    prompt = "use {sandbox} and {arxiv_id} {0}"
    assert prompt in _cmd(name, prompt=prompt)


def test_build_command_does_not_mutate_the_spec_lists(find_calls):
    runner = get_runner("codex")
    spec = runner._spec
    before = (list(spec.pre_subcommand_args), list(spec.post_subcommand_args))
    runner.build_command("p", _SB, "m", None, None, "high")
    runner.build_command("p", Path("/other"), "m", None, None, "high")
    assert (spec.pre_subcommand_args, spec.post_subcommand_args) == before


# ── build_command: synthetic spec for rules no vendor exercises ────────────


def _spec(**kw) -> RunnerSpec:
    return RunnerSpec(name="t", binary="tbin", **kw)


def test_command_piece_order_for_a_spec_using_every_slot(find_calls):
    spec = _spec(
        binary_env_var="T_BIN",
        pre_subcommand_args=["PRE", "{sandbox}"],
        model_flag="--m",
        effort_config_flag="-c",
        effort_config_key="k",
        effort_label_map={"high": "H"},
        subcommand=["SUB"],
        post_subcommand_args=["POST"],
        allowlist_flag="--allow",
        disallowed_tools_flag="--deny",
        disallowed_tools=["a", "b"],
        thinking_tokens_flag="--think",
        prompt_flag="--prompt",
        final_args=["FINAL", "{sandbox}"],
    )
    cmd = DeclarativeRunner(spec).build_command("PR", _SB, "mm", "al", 5, "high")
    assert cmd == [
        "/bin/tbin",
        "PRE",
        str(_SB),
        "--m",
        "mm",
        "-c",
        "k=H",
        "SUB",
        "POST",
        "--allow",
        "al",
        "--deny",
        "a,b",
        "--think",
        "5",
        "--prompt",
        "PR",
        "FINAL",
        str(_SB),
    ]
    assert find_calls == [("tbin", "T_BIN")]


def test_stdin_prompt_spec_leaves_the_prompt_out_of_argv(find_calls):
    cmd = DeclarativeRunner(_spec(prompt_via="stdin")).build_command("SECRET", _SB, None, None)
    assert cmd == ["/bin/tbin"]


# ── prepare_launch: vendor defaults ─────────────────────────────────────────


def _prep(name, config=None, sandbox=_SB):
    return get_runner(name).prepare_launch(sandbox, config)


def test_claude_default_launch_prep():
    assert _prep("claude") == LaunchPrep(
        env={},
        extra_ro_binds=[],
        secret_env_names=("ANTHROPIC_API_KEY",),
        home_dir_name=".claude_home",
        home_files=(".claude.json", ".claude/.credentials.json", ".claude/settings.json"),
        home_credential_files=(".claude.json", ".claude/.credentials.json"),
    )


@pytest.mark.parametrize("config", [None, {}, {"auth": "oauth"}, {"provider": "deepseek"}])
def test_claude_non_api_configs_keep_the_default_prep(config):
    assert _prep("claude", config) == _prep("claude")


def test_claude_api_auth_switches_home_and_drops_credential_files():
    prep = _prep("claude", {"auth": "api"})
    assert prep.home_dir_name == ".claude_api_home"
    assert prep.home_files == (".claude/settings.json",)
    assert prep.home_credential_files == ()
    assert prep.secret_env_names == ("ANTHROPIC_API_KEY",)
    assert prep.env == {}


def test_claude_deepseek_api_prep_env_and_secret_names(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk-123")
    prep = _prep("claude", {"auth": "api", "provider": "deepseek", "effort": "high"})
    assert prep.env == {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_MODEL": "deepseek-v4-pro[1m]",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1m]",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
        "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-flash",
        "CLAUDE_CODE_EFFORT_LEVEL": "high",
        "ANTHROPIC_AUTH_TOKEN": "dk-123",
    }
    assert list(prep.env)[-1] == "ANTHROPIC_AUTH_TOKEN"
    assert prep.secret_env_names == ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    assert prep.home_dir_name == ".claude_api_home"


def test_claude_deepseek_without_key_omits_auth_token_but_still_names_secrets(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    prep = _prep("claude", {"auth": "api", "provider": "deepseek", "model": "dsm"})
    assert "ANTHROPIC_AUTH_TOKEN" not in prep.env
    assert prep.env["ANTHROPIC_MODEL"] == "dsm"
    assert prep.env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"  # default when no effort in config
    assert prep.secret_env_names == ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def test_aider_launch_prep():
    assert _prep("aider") == LaunchPrep(
        env={},
        extra_ro_binds=[],
        secret_env_names=(
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        ),
        home_dir_name=".aider_home",
        home_files=(),
        home_credential_files=(),
    )


def test_forge_launch_prep_has_static_env_and_fake_home_files():
    assert _prep("forge") == LaunchPrep(
        env={"NO_COLOR": "1", "CI": "true", "TERM": "dumb"},
        extra_ro_binds=[],
        secret_env_names=("DEEPSEEK_API_KEY",),
        home_dir_name=".forge_home",
        home_files=(".forge/.credentials.json", ".forge/.forge.toml"),
        home_credential_files=(),
    )


# ── prepare_launch: hook merge order (fake hook) ────────────────────────────


def _hooked(monkeypatch, hook_prep, **spec_kw):
    monkeypatch.setitem(runner_spec.PRE_LAUNCH_HOOKS, "h", lambda sandbox: hook_prep)
    spec = _spec(pre_launch_hook="h", **spec_kw)
    return DeclarativeRunner(spec).prepare_launch(_SB)


def test_hook_values_are_merged_after_the_specs_own(monkeypatch):
    hook = LaunchPrep(
        env={"B": "hook", "C": "3"},
        extra_ro_binds=[Path("/hook/bind")],
        secret_env_names=("S2", "S1"),
        home_dir_name=".hook_home",
        home_files=("hook-f",),
        home_credential_files=("hook-c",),
    )
    prep = _hooked(
        monkeypatch,
        hook,
        extra_env={"A": "1", "B": "spec"},
        secret_env_names=("S1", "S0"),
        home_dir_name=".spec_home",
        home_files=("spec-f",),
        home_credential_files=("spec-c",),
    )
    assert prep.env == {"A": "1", "B": "hook", "C": "3"}  # hook wins on conflicts
    assert list(prep.env) == ["A", "B", "C"]
    assert prep.extra_ro_binds == [Path("/hook/bind")]
    assert prep.secret_env_names == ("S1", "S0", "S2")  # deduplicated, first occurrence kept
    assert prep.home_dir_name == ".hook_home"
    assert prep.home_files == ("spec-f", "hook-f")
    assert prep.home_credential_files == ("spec-c", "hook-c")


def test_hook_with_default_home_dir_name_overrides_the_spec_home_dir(monkeypatch):
    # LaunchPrep's default home_dir_name is ".agent_home" (truthy), so it wins.
    prep = _hooked(monkeypatch, LaunchPrep(), home_dir_name=".spec_home")
    assert prep.home_dir_name == ".agent_home"


def test_hook_with_empty_home_dir_name_falls_back_to_the_spec(monkeypatch):
    prep = _hooked(monkeypatch, LaunchPrep(home_dir_name=""), home_dir_name=".spec_home")
    assert prep.home_dir_name == ".spec_home"


def test_hook_runs_after_claude_api_overrides_are_applied(monkeypatch):
    seen = {}

    def hook(sandbox):
        seen["sandbox"] = sandbox
        return LaunchPrep(home_dir_name="", home_files=("extra",))

    monkeypatch.setitem(runner_spec.PRE_LAUNCH_HOOKS, "h", hook)
    spec = RunnerSpec(
        name="claude",
        binary="claude",
        home_dir_name=".claude_home",
        home_files=(".claude.json",),
        pre_launch_hook="h",
    )
    prep = DeclarativeRunner(spec).prepare_launch(Path("/sb"), {"auth": "api"})
    assert seen["sandbox"] == Path("/sb")
    assert prep.home_dir_name == ".claude_api_home"
    assert prep.home_files == (".claude/settings.json", "extra")


def test_unknown_hook_name_raises_key_error_with_a_clear_message():
    spec = _spec(pre_launch_hook="nope")
    with pytest.raises(KeyError, match="runner 't': pre_launch_hook 'nope' not registered"):
        DeclarativeRunner(spec).prepare_launch(_SB)


def test_prepare_launch_is_repeatable_and_does_not_mutate_the_spec():
    runner = get_runner("claude")
    spec_before = runner._spec.model_dump()
    first = runner.prepare_launch(_SB, {"auth": "api"})
    second = runner.prepare_launch(_SB, {"auth": "api"})
    assert first == second
    assert runner._spec.model_dump() == spec_before


# ── prepare_launch: real pre-launch hook bodies against a fake HOME ────────


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_codex_prep_creates_codex_home_copies_known_files_and_sets_env(tmp_path, fake_home):
    (fake_home / ".codex").mkdir()
    (fake_home / ".codex" / "auth.json").write_text('{"k": 1}')
    (fake_home / ".codex" / "config.toml").write_text("x = 1\n")
    (fake_home / ".codex" / "other.txt").write_text("not copied")
    sandbox = tmp_path / "ws"
    sandbox.mkdir()
    prep = get_runner("codex").prepare_launch(sandbox, None)
    codex_home = sandbox / ".codex_home"
    assert prep.env == {"CODEX_HOME": str(codex_home)}
    assert prep.home_dir_name == ".codex_container_home"
    assert prep.secret_env_names == ("OPENAI_API_KEY",)
    assert sorted(p.name for p in codex_home.iterdir()) == ["auth.json", "config.toml"]
    assert (codex_home / "auth.json").read_text() == '{"k": 1}'


def test_codex_prep_without_host_state_still_creates_an_empty_codex_home(tmp_path, fake_home):
    sandbox = tmp_path / "ws"
    sandbox.mkdir()
    prep = get_runner("codex").prepare_launch(sandbox, None)
    assert list((sandbox / ".codex_home").iterdir()) == []
    assert prep.env == {"CODEX_HOME": str(sandbox / ".codex_home")}


def test_gemini_prep_writes_default_settings_and_copies_known_state(tmp_path, fake_home):
    state = fake_home / ".gemini"
    state.mkdir()
    (state / "oauth_creds.json").write_text("creds")
    (state / "installation_id").write_text("iid")
    (state / "junk").write_text("not copied")
    sandbox = tmp_path / "ws"
    sandbox.mkdir()
    prep = get_runner("gemini").prepare_launch(sandbox, None)
    gemini_state = sandbox / ".gemini_home" / ".gemini"
    assert prep.env == {"GEMINI_CLI_HOME": str(sandbox / ".gemini_home")}
    assert prep.home_dir_name == ".gemini_container_home"
    assert prep.secret_env_names == ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    assert sorted(p.name for p in gemini_state.iterdir()) == [
        "installation_id",
        "oauth_creds.json",
        "settings.json",
    ]
    settings = json.loads((gemini_state / "settings.json").read_text())
    assert settings == {
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "ide": {"enabled": False},
    }


def test_gemini_prep_keeps_host_auth_type_but_forces_ide_off(tmp_path, fake_home):
    state = fake_home / ".gemini"
    state.mkdir()
    host = {"security": {"auth": {"selectedType": "api-key"}}, "ide": {"enabled": True}, "x": 1}
    (state / "settings.json").write_text(json.dumps(host))
    sandbox = tmp_path / "ws"
    sandbox.mkdir()
    get_runner("gemini").prepare_launch(sandbox, None)
    saved = json.loads((sandbox / ".gemini_home" / ".gemini" / "settings.json").read_text())
    assert saved == {
        "security": {"auth": {"selectedType": "api-key"}},
        "ide": {"enabled": False},
        "x": 1,
    }


def test_gemini_prep_ignores_invalid_host_settings_json(tmp_path, fake_home):
    state = fake_home / ".gemini"
    state.mkdir()
    (state / "settings.json").write_text("{not json")
    sandbox = tmp_path / "ws"
    sandbox.mkdir()
    get_runner("gemini").prepare_launch(sandbox, None)
    saved = json.loads((sandbox / ".gemini_home" / ".gemini" / "settings.json").read_text())
    assert saved == {
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "ide": {"enabled": False},
    }


# ── Direct tests of the extracted helper: _merge_launch_prep(base, hook) ───


def _lp(**kw) -> LaunchPrep:
    return LaunchPrep(**kw)


def test_merge_env_hook_overrides_but_keeps_base_key_positions():
    base = _lp(env={"A": "1", "B": "base", "C": "3"})
    hook = _lp(env={"D": "4", "B": "hook"})
    merged = runner_spec._merge_launch_prep(base, hook)
    assert merged.env == {"A": "1", "B": "hook", "C": "3", "D": "4"}
    assert list(merged.env) == ["A", "B", "C", "D"]


def test_merge_binds_and_files_concatenate_base_then_hook_keeping_duplicates():
    base = _lp(
        extra_ro_binds=[Path("/a"), Path("/b")],
        home_files=("f1", "f2"),
        home_credential_files=("c1",),
    )
    hook = _lp(
        extra_ro_binds=[Path("/b"), Path("/c")],
        home_files=("f2", "f3"),
        home_credential_files=("c1", "c2"),
    )
    merged = runner_spec._merge_launch_prep(base, hook)
    assert merged.extra_ro_binds == [Path("/a"), Path("/b"), Path("/b"), Path("/c")]
    assert merged.home_files == ("f1", "f2", "f2", "f3")
    assert merged.home_credential_files == ("c1", "c1", "c2")
    assert isinstance(merged.extra_ro_binds, list) and isinstance(merged.home_files, tuple)


def test_merge_secret_names_are_concatenated_without_deduplication():
    # Deduplication happens afterwards, in prepare_launch (see the hook-merge test above).
    base = _lp(secret_env_names=("S1", "S0"))
    hook = _lp(secret_env_names=("S2", "S1"))
    merged = runner_spec._merge_launch_prep(base, hook)
    assert merged.secret_env_names == ("S1", "S0", "S2", "S1")


@pytest.mark.parametrize(
    ("base_home", "hook_home", "expected"),
    [
        (".base", ".hook", ".hook"),  # truthy hook value wins
        (".base", "", ".base"),  # empty hook value falls back to the base
        (".base", ".agent_home", ".agent_home"),  # LaunchPrep's default is truthy and wins
        ("", "", ""),
    ],
)
def test_merge_home_dir_name_rule(base_home, hook_home, expected):
    merged = runner_spec._merge_launch_prep(
        _lp(home_dir_name=base_home), _lp(home_dir_name=hook_home)
    )
    assert merged.home_dir_name == expected


def test_merge_of_two_defaults_equals_a_default_prep():
    assert runner_spec._merge_launch_prep(_lp(), _lp()) == _lp()


def test_merge_does_not_mutate_either_input_and_returns_fresh_containers():
    base = _lp(env={"A": "1"}, extra_ro_binds=[Path("/a")], home_files=("f",))
    hook = _lp(env={"B": "2"}, extra_ro_binds=[Path("/b")], home_files=("g",))
    merged = runner_spec._merge_launch_prep(base, hook)
    assert base.env == {"A": "1"} and hook.env == {"B": "2"}
    assert base.extra_ro_binds == [Path("/a")] and hook.extra_ro_binds == [Path("/b")]
    assert base.home_files == ("f",) and hook.home_files == ("g",)
    assert merged.env is not base.env and merged.env is not hook.env
    assert merged.extra_ro_binds is not base.extra_ro_binds
    merged.env["X"] = "mutate"
    merged.extra_ro_binds.append(Path("/x"))
    assert "X" not in base.env and Path("/x") not in base.extra_ro_binds


def test_merged_launch_prep_is_a_frozen_dataclass():
    merged = runner_spec._merge_launch_prep(_lp(), _lp())
    with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
        merged.home_dir_name = "changed"  # type: ignore[misc]
