"""sandbox_command must produce a valid command list + cleanup callable for
every backend that advertises itself as available."""

from __future__ import annotations

import shutil

import pytest

from agent_runtime.sandbox import SANDBOXES, get_sandbox, sandbox_command
from agent_runtime.sandbox import (
    _materialize_papers_dir,
    _prepare_isolated_home,
    _prepare_runner_cli,
)


@pytest.mark.parametrize("name", sorted(SANDBOXES))
def test_backend_instantiates(name):
    # Skip backends whose required tools aren't present on this host
    # (e.g. apptainer on a podman-only box, or vice versa).
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


def test_auto_prefers_apptainer_over_singularity(monkeypatch):
    def fake_which(name):
        if name in {"apptainer", "singularity"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert get_sandbox("auto").name == "apptainer"


def test_auto_falls_back_to_singularity(monkeypatch):
    def fake_which(name):
        return "/usr/bin/singularity" if name == "singularity" else None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert get_sandbox("auto").name == "singularity"


def test_apptainer_does_not_implicitly_select_singularity(monkeypatch):
    def fake_which(name):
        return "/usr/bin/singularity" if name == "singularity" else None

    monkeypatch.setattr(shutil, "which", fake_which)

    with pytest.raises(RuntimeError, match="apptainer"):
        get_sandbox("apptainer")
    assert get_sandbox("singularity").name == "singularity"


def test_default_image_is_canonical_ghcr_ref():
    """Regression guard: the default image must point at the published
    canonical image, not a localhost / lhc-recast-* leftover."""
    from agent_runtime.sandbox import _DEFAULT_IMAGE

    assert _DEFAULT_IMAGE == "ghcr.io/dfaroughy/lhc-bench:latest"


def test_prepare_isolated_home_copies_only_requested_vendor_files(tmp_path):
    host = tmp_path / "host"
    (host / ".claude").mkdir(parents=True)
    (host / ".codex").mkdir(parents=True)
    (host / ".gemini").mkdir(parents=True)
    (host / ".forge").mkdir(parents=True)
    (host / ".claude.json").write_text("{}")
    (host / ".claude" / ".credentials.json").write_text("{}")
    (host / ".codex" / "auth.json").write_text("{}")
    (host / ".gemini" / "oauth_creds.json").write_text("{}")
    (host / ".forge" / ".forge.toml").write_text("model = 'x'")

    fake_home = _prepare_isolated_home(
        host,
        tmp_path / "fake_home",
        (".claude.json", ".claude/.credentials.json"),
    )

    assert (fake_home / ".claude.json").is_file()
    assert (fake_home / ".claude" / ".credentials.json").is_file()
    assert not (fake_home / ".codex" / "auth.json").exists()
    assert not (fake_home / ".gemini" / "oauth_creds.json").exists()
    assert not (fake_home / ".forge" / ".forge.toml").exists()


def test_materialize_papers_dir_replaces_symlink_with_copy(tmp_path):
    workspace = tmp_path / "ws"
    shared = tmp_path / "shared" / "paper"
    workspace.mkdir()
    shared.mkdir(parents=True)
    (shared / "CMS-TEST.pdf").write_text("pdf")
    (workspace / "papers").symlink_to(shared)

    _materialize_papers_dir(workspace)

    papers = workspace / "papers"
    assert papers.is_dir()
    assert not papers.is_symlink()
    assert (papers / "CMS-TEST.pdf").read_text() == "pdf"
    assert (papers / "CMS-TEST.pdf").resolve().is_relative_to(workspace.resolve())


def test_sandbox_command_materializes_papers_before_wrapping(repo_root, tmp_path):
    workspace = tmp_path / "ws"
    shared = tmp_path / "shared" / "paper"
    workspace.mkdir()
    shared.mkdir(parents=True)
    (shared / "CMS-TEST.pdf").write_text("pdf")
    (workspace / "papers").symlink_to(shared)

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/bin/true"],
        sandbox="none",
    )

    assert cmd == ["/bin/true"]
    assert (workspace / "papers").is_dir()
    assert not (workspace / "papers").is_symlink()
    assert (workspace / "papers" / "CMS-TEST.pdf").is_file()
    cleanup()


@pytest.mark.parametrize(
    "bad",
    ("../escape", "/tmp/escape", ".", "..", "nested/home", r"nested\home"),
)
def test_fake_home_rejects_unsafe_home_dir_names(repo_root, tmp_path, monkeypatch, bad):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError, match="invalid fake-home directory name"):
        sandbox_command(
            workspace,
            repo_root,
            ["/bin/true"],
            sandbox="podman",
            home_dir_name=bad,
        )


@pytest.mark.parametrize(
    "bad",
    ("../secret.json", "/tmp/secret.json", ".", "dir/../secret.json", r"dir\secret.json"),
)
def test_prepare_isolated_home_rejects_unsafe_home_files(tmp_path, bad):
    host = tmp_path / "host"
    host.mkdir()
    with pytest.raises(ValueError, match="invalid fake-home file path"):
        _prepare_isolated_home(host, tmp_path / "fake_home", (bad,))


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="Unknown sandbox"):
        get_sandbox("nonexistent-backend")


def test_sandbox_command_roundtrip(repo_root, tmp_path):
    """End-to-end: wrap a trivial inner command, get back a runnable list.

    Prefers podman (the production path) when installed, then apptainer /
    singularity, finally 'none' on stripped-down hosts (CI). We just need
    *some* available backend so command construction is exercised.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = ["/bin/true"]
    if shutil.which("podman") or shutil.which("podman-hpc"):
        backend = "podman"
    elif shutil.which("apptainer"):
        backend = "apptainer"
    elif shutil.which("singularity"):
        backend = "singularity"
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
    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        inner,
        container_env={"CODEX_HOME": str(tmp_path / ".codex_home")},
        sandbox="none",
    )
    # The 'none' backend just passes the inner command through.
    assert cmd == inner
    cleanup()


def _env_values(cmd, flag):
    return [cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == flag]


def _bind_values(cmd, flag):
    return [cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == flag]


def test_podman_masks_disabled_delphes_paths(repo_root, tmp_path, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    workspace = tmp_path / "ws"
    disabled = workspace / "disabled_tools" / "delphes"
    disabled.mkdir(parents=True)

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/codex", "exec"],
        container_env={"CODEX_HOME": str(workspace / ".codex_home")},
        home_files=(),
        sandbox="podman",
    )
    binds = _bind_values(cmd, "-v")
    assert f"{disabled}:/opt/sim/delphes:ro" in binds
    assert f"{disabled}:{repo_root / 'ColliderBench' / 'tools' / 'sim' / 'delphes'}:ro" in binds
    cleanup()


def test_apptainer_masks_disabled_delphes_paths(repo_root, tmp_path, monkeypatch):
    def fake_which(name):
        return "/usr/bin/apptainer" if name == "apptainer" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    workspace = tmp_path / "ws"
    disabled = workspace / "disabled_tools" / "delphes"
    disabled.mkdir(parents=True)

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/gemini", "-p", "hi"],
        container_env={"GEMINI_CLI_HOME": str(workspace / ".gemini_home")},
        home_files=(),
        sandbox="apptainer",
    )
    binds = _bind_values(cmd, "--bind")
    assert f"{disabled}:/opt/sim/delphes:ro" in binds
    assert f"{disabled}:{repo_root / 'ColliderBench' / 'tools' / 'sim' / 'delphes'}:ro" in binds
    cleanup()


def test_podman_command_includes_runner_env(repo_root, tmp_path, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("GROK_API_KEY", "should-not-pass")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/codex", "exec"],
        container_env={
            "CODEX_HOME": str(workspace / ".codex_home"),
            "NO_COLOR": "1",
            "SHOULD_NOT_PASS": "x",
        },
        home_files=(),
        secret_env_names=("DEEPSEEK_API_KEY",),
        sandbox="podman",
    )
    envs = _env_values(cmd, "-e")
    assert f"CODEX_HOME={workspace / '.codex_home'}" in envs
    assert "NO_COLOR=1" in envs
    assert "DEEPSEEK_API_KEY=secret-key" in envs
    assert "GROK_API_KEY=should-not-pass" not in envs
    assert not any(e.startswith("SHOULD_NOT_PASS=") for e in envs)
    cleanup()


def test_podman_command_does_not_forward_wildcard_api_keys(repo_root, tmp_path, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/codex", "exec"],
        container_env={"CODEX_HOME": str(workspace / ".codex_home")},
        home_files=(),
        sandbox="podman",
    )
    envs = _env_values(cmd, "-e")
    assert not any(e.startswith("DEEPSEEK_API_KEY=") for e in envs)
    cleanup()


def test_apptainer_command_includes_runner_env(repo_root, tmp_path, monkeypatch):
    def fake_which(name):
        return "/usr/bin/apptainer" if name == "apptainer" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setenv("GROK_API_KEY", "secret-key")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/gemini", "-p", "hi"],
        container_env={
            "GEMINI_CLI_HOME": str(workspace / ".gemini_home"),
            "TERM": "dumb",
            "SHOULD_NOT_PASS": "x",
        },
        home_files=(),
        secret_env_names=("GROK_API_KEY",),
        sandbox="apptainer",
    )
    envs = _env_values(cmd, "--env")
    assert f"GEMINI_CLI_HOME={workspace / '.gemini_home'}" in envs
    assert "TERM=dumb" in envs
    assert "GROK_API_KEY=secret-key" in envs
    assert not any(e.startswith("SHOULD_NOT_PASS=") for e in envs)
    cleanup()


def test_singularity_command_includes_runner_env(repo_root, tmp_path, monkeypatch):
    def fake_which(name):
        return "/usr/bin/singularity" if name == "singularity" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cmd, cleanup = sandbox_command(
        workspace,
        repo_root,
        ["/usr/bin/claude", "-p", "hi"],
        container_env={
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "SHOULD_NOT_PASS": "x",
        },
        home_files=(),
        secret_env_names=("DEEPSEEK_API_KEY",),
        sandbox="singularity",
    )
    assert cmd[:2] == ["singularity", "exec"]
    envs = _env_values(cmd, "--env")
    assert "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic" in envs
    assert "DEEPSEEK_API_KEY=secret-key" in envs
    assert not any(e.startswith("SHOULD_NOT_PASS=") for e in envs)
    cleanup()


def test_prepare_runner_cli_narrowly_binds_host_agent_cli(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cli_dir = home / ".local" / "share" / "gemini-cli" / "bundle"
    cli_dir.mkdir(parents=True)
    cli = cli_dir / "gemini.js"
    cli.write_text("#!/usr/bin/env node\n")
    cli.chmod(0o755)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    link = bin_dir / "gemini"
    link.symlink_to(cli)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    rewritten, binds = _prepare_runner_cli([str(link), "-p", "hi"])

    assert rewritten == [str(cli.resolve()), "-p", "hi"]
    assert binds == [cli_dir.resolve()]


def test_prepare_runner_cli_uses_image_binary_for_system_paths():
    rewritten, binds = _prepare_runner_cli(["/usr/bin/gemini", "-p", "hi"])
    assert rewritten == ["gemini", "-p", "hi"]
    assert binds == []
