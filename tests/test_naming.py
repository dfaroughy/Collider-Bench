from agent_runtime.naming import run_group


def test_run_group_strips_claude_prefix_for_oauth_runner():
    assert run_group("claude", "claude-opus-4-7") == "claude_opus-4-7"


def test_claude_api_auth_keeps_normal_runner_group():
    assert run_group("claude", "claude-sonnet-4-6") == "claude_sonnet-4-6"
