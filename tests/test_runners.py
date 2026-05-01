"""Runner build_command must produce well-formed CLI args for each backend.

Vendor runner subclasses live in `agent_runtime/vendors.py` and
auto-register on import. The build_command tests skip when the underlying
CLI binary isn't installed on the host.
"""

from __future__ import annotations


import pytest

from agent_runtime.runners import RUNNERS, get_runner


@pytest.mark.parametrize("name", sorted(RUNNERS))
def test_runner_instantiates(name):
    r = get_runner(name)
    assert r.name == name


def test_unknown_runner_rejected():
    with pytest.raises(ValueError, match="Unknown runner"):
        get_runner("nope")


def test_claude_build_command_disallows_schedulewakeup(tmp_path):
    """ScheduleWakeup strands sessions in -p mode; the runner must block it."""
    try:
        r = get_runner("claude")
        cmd = r.build_command(
            "noop", tmp_path, "claude-opus-4-7", allowlist=None, max_thinking_tokens=1000
        )
    except RuntimeError as exc:
        pytest.skip(f"claude binary not installed on this host: {exc}")
    assert "--disallowedTools" in cmd
    # Find the flag's argument
    idx = cmd.index("--disallowedTools")
    assert "ScheduleWakeup" in cmd[idx + 1]
    assert "-p" in cmd
    assert "--max-thinking-tokens" in cmd


def test_codex_build_command_shape(tmp_path):
    try:
        r = get_runner("codex")
        cmd = r.build_command("noop", tmp_path, "gpt-5.4", allowlist=None, max_thinking_tokens=None)
    except RuntimeError as exc:
        pytest.skip(f"codex binary not installed: {exc}")
    assert "exec" in cmd
    assert "--skip-git-repo-check" in cmd
    # danger-full-access is needed because bwrap provides isolation, not codex.
    assert "danger-full-access" in cmd


def test_claude_allowlist_threaded_through(tmp_path):
    try:
        r = get_runner("claude")
        cmd = r.build_command("noop", tmp_path, "claude-opus-4-7", allowlist="Read Write")
    except RuntimeError as exc:
        pytest.skip(f"claude binary not installed: {exc}")
    assert "--allowedTools" in cmd
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Read Write"


def test_runner_prepare_launch_applies_before_run(tmp_path, monkeypatch):
    """Runner-specific env must be available before sandbox command construction."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)

    r = get_runner("claude")
    prep = r.prepare_launch(tmp_path)
    assert prep.home_dir_name == ".claude_home"
    assert ".claude.json" in prep.home_files
    assert ".claude/.credentials.json" in prep.home_files
    assert ".claude/.credentials.json" in prep.home_credential_files
    assert ".codex/auth.json" not in prep.home_files
    assert ".gemini/oauth_creds.json" not in prep.home_files
    assert ".forge/.forge.toml" not in prep.home_files
    assert prep.secret_env_names == ("ANTHROPIC_API_KEY",)

    prep = r.prepare_launch(tmp_path, config={"auth": "api"})
    assert prep.home_dir_name == ".claude_api_home"
    assert prep.secret_env_names == ("ANTHROPIC_API_KEY",)
    assert ".claude/settings.json" in prep.home_files
    assert ".claude.json" not in prep.home_files
    assert ".claude/.credentials.json" not in prep.home_files
    assert prep.home_credential_files == ()

    r = get_runner("codex")
    prep = r.prepare_launch(tmp_path)
    assert prep.env["CODEX_HOME"] == str(tmp_path / ".codex_home")
    assert prep.home_dir_name == ".codex_container_home"
    assert ".codex/auth.json" not in prep.home_files
    assert "CODEX_HOME" not in __import__("os").environ
    assert prep.secret_env_names == ("OPENAI_API_KEY",)

    r = get_runner("gemini")
    prep = r.prepare_launch(tmp_path)
    assert prep.env["GEMINI_CLI_HOME"] == str(tmp_path / ".gemini_home")
    assert prep.secret_env_names == ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    r = get_runner("forge")
    prep = r.prepare_launch(tmp_path)
    assert prep.env["NO_COLOR"] == "1"
    assert prep.env["CI"] == "true"
    assert prep.env["TERM"] == "dumb"
    assert prep.secret_env_names == ("DEEPSEEK_API_KEY",)
    assert prep.home_dir_name == ".forge_home"
    assert ".forge/.forge.toml" in prep.home_files
    assert ".claude.json" not in prep.home_files

    r = get_runner("aider")
    prep = r.prepare_launch(tmp_path)
    assert "DEEPSEEK_API_KEY" in prep.secret_env_names
    assert "OPENAI_API_KEY" in prep.secret_env_names
