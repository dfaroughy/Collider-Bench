"""Runner build_command must produce well-formed CLI args for each backend."""

from __future__ import annotations

from pathlib import Path

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
        cmd = r.build_command("noop", tmp_path, "claude-opus-4-7",
                              allowlist=None, max_thinking_tokens=1000)
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
        cmd = r.build_command("noop", tmp_path, "gpt-5.4",
                              allowlist=None, max_thinking_tokens=None)
    except RuntimeError as exc:
        pytest.skip(f"codex binary not installed: {exc}")
    assert "exec" in cmd
    assert "--skip-git-repo-check" in cmd
    # danger-full-access is needed because bwrap provides isolation, not codex.
    assert "danger-full-access" in cmd


def test_claude_allowlist_threaded_through(tmp_path):
    try:
        r = get_runner("claude")
        cmd = r.build_command("noop", tmp_path, "claude-opus-4-7",
                              allowlist="Read Write")
    except RuntimeError as exc:
        pytest.skip(f"claude binary not installed: {exc}")
    assert "--allowedTools" in cmd
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Read Write"
