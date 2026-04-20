"""sandbox_command must produce a valid command list + cleanup callable for
every backend that advertises itself as available."""

from __future__ import annotations

import shutil

import pytest

from agent_runtime.sandbox import SANDBOXES, get_sandbox, sandbox_command


@pytest.mark.parametrize("name", sorted(SANDBOXES))
def test_backend_instantiates(name):
    sb = get_sandbox(name)
    assert sb.name == name


def test_auto_picks_something():
    sb = get_sandbox("auto")
    assert sb.name in SANDBOXES


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="Unknown sandbox"):
        get_sandbox("nonexistent-backend")


def test_sandbox_command_roundtrip(repo_root, tmp_path):
    """End-to-end: wrap a trivial inner command, get back a runnable list."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = ["/bin/true"]
    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        inner,
        sandbox="bwrap" if shutil.which("bwrap") else "none",
    )
    assert isinstance(cmd, list)
    assert all(isinstance(a, str) for a in cmd)
    # Cleanup must be a zero-arg callable and safe to call once.
    cleanup()


def test_none_backend_is_passthrough(repo_root, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = ["/bin/true", "--flag"]
    cmd, cleanup = sandbox_command(workspace, repo_root, inner, sandbox="none")
    # The 'none' backend just passes the inner command through.
    assert cmd == inner
    cleanup()
