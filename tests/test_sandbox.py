"""sandbox_command must produce a valid command list + cleanup callable for
every backend that advertises itself as available."""

from __future__ import annotations

import shutil

import pytest

from agent_runtime.sandbox import SANDBOXES, get_sandbox, sandbox_command


@pytest.mark.parametrize("name", sorted(SANDBOXES))
def test_backend_instantiates(name):
    # Skip backends whose required tools aren't present on this host
    # (e.g. apptainer on a bwrap-only box, or vice versa).
    cls = SANDBOXES[name]
    if not cls().available():
        pytest.skip(f"{name} backend tools not installed")
    sb = get_sandbox(name)
    assert sb.name == name


def test_auto_picks_something():
    sb = get_sandbox("auto")
    assert sb.name in SANDBOXES


def test_auto_prefers_podman_when_available():
    """Auto-select must prefer podman (the canonical container path) over
    everything else. Skips when podman / podman-hpc isn't installed."""
    if not (shutil.which("podman") or shutil.which("podman-hpc")):
        pytest.skip("podman not installed")
    sb = get_sandbox("auto")
    assert sb.name == "podman", f"auto-select returned {sb.name!r}; podman should win when present"


def test_auto_never_picks_bwrap():
    """Bwrap bypasses the canonical container; auto-select must never land
    on it. (It remains opt-in via --sandbox bwrap.)"""
    sb = get_sandbox("auto")
    assert (
        sb.name != "bwrap"
    ), "auto-select returned bwrap; it should be excluded from the auto chain"


def test_default_image_is_canonical_ghcr_ref():
    """Regression guard: the default image must point at the published
    canonical image, not a localhost / lhc-recast-* leftover."""
    from agent_runtime.sandbox import _DEFAULT_IMAGE

    assert _DEFAULT_IMAGE == "ghcr.io/dfaroughy/lhc-bench:latest"


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="Unknown sandbox"):
        get_sandbox("nonexistent-backend")


def test_sandbox_command_roundtrip(repo_root, tmp_path):
    """End-to-end: wrap a trivial inner command, get back a runnable list.

    Prefers podman (the production path) when installed; falls back to
    bwrap, then 'none' on stripped-down hosts (CI/macOS) — we just need
    *some* available backend so command construction is exercised.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = ["/bin/true"]
    if shutil.which("podman") or shutil.which("podman-hpc"):
        backend = "podman"
    elif shutil.which("bwrap"):
        backend = "bwrap"
    else:
        backend = "none"
    cmd, cleanup = sandbox_command(workspace, repo_root, inner, sandbox=backend)
    assert isinstance(cmd, list)
    assert all(isinstance(a, str) for a in cmd)
    cleanup()


def test_none_backend_is_passthrough(repo_root, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = ["/bin/true", "--flag"]
    cmd, cleanup = sandbox_command(workspace, repo_root, inner, sandbox="none")
    # The 'none' backend just passes the inner command through.
    assert cmd == inner
    cleanup()
