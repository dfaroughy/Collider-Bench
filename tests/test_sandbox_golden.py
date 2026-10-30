"""Characterization (golden) tests for the exact `wrap()` command per backend.

These pin the *current* argv of every container backend for fixed, fully
synthetic inputs, so behavior-preserving refactors of agent_runtime/sandbox.py
can be proven to leave the command byte-for-byte unchanged. They are
deliberately literal: when a command legitimately changes, update the expected
list in the same commit as the production change.

Everything is offline and deterministic: the repo root, workspace, host home,
extra binds and tool-policy trees are built under `tmp_path`; the host env is
scrubbed; engine availability and /cvmfs are monkeypatched. No container
engine is ever executed. The tmp root is replaced by `<T>` before comparing.

Backend-specific differences pinned here (observed, not endorsed):
  - apptainer/singularity bind the fake HOME *after* the read-only benchmark
    mounts; podman/docker bind it *before* the workspace and the ro mounts.
  - podman/docker set PATH and PYTHONPATH; apptainer/singularity set neither.
  - apptainer/singularity pass `--cleanenv`; podman/docker pass `--rm -i`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime import sandbox as sandbox_mod
from agent_runtime.sandbox import sandbox_command
from agent_runtime.workspace import apply_tool_policy, scrub_tool_policy_docs

_IMAGE = "ghcr.io/dfaroughy/lhc-bench:latest"

_TOOL_POLICY = {"disabled": ["delphes", "prospino"]}

_CONTAINER_PATH = (
    "/opt/sim/MG5_aMC_v3_7_0/bin:/opt/sim/delphes:/opt/node-global/bin:"
    "/opt/conda/envs/lhc_analysis/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)

# Env scrubbed from the host so the passthrough allowlist is fully controlled
# by the explicit `container_env` below.
_SCRUB_ENV = (*sandbox_mod._RUNNER_ENV_PASSTHROUGH, "LHC_BENCH_IMAGE", "LHC_RECAST_SANDBOX")
_SCRUB_ENV += ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")

_ENGINES = {
    "podman": "podman",
    "docker": "docker",
    "apptainer": "apptainer",
    "singularity": "singularity",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Synthetic repo + workspace + host home. Returns a namespace of paths."""
    base = tmp_path.resolve()
    for var in _SCRUB_ENV:
        monkeypatch.delenv(var, raising=False)

    repo = base / "repo"
    tools = repo / "ColliderBench" / "tools"
    (tools / "CLI").mkdir(parents=True)
    (tools / "CLI" / "simulate.py").write_text("# stub\n")
    (tools / "__init__.py").write_text("")
    (tools / "streaming.py").write_text("")
    (tools / "TOOLS.md").write_text("# Tools\n- MG5, Pythia8, Delphes, Prospino\n")
    (repo / "ColliderBench" / "bin").mkdir()
    (repo / "ColliderBench" / "bin" / "simulate").write_text("#!/usr/bin/env bash\n")

    workspace = base / "run" / "workspace"
    (workspace / "bin").mkdir(parents=True)

    host_home = base / "host-home"
    cli_dir = host_home / ".local" / "bin"
    cli_dir.mkdir(parents=True)
    cli = cli_dir / "codex"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: host_home))

    extra = base / "extra-ro"
    extra.mkdir()

    return SimpleNamespace(
        base=base, repo=repo, workspace=workspace, cli=cli, cli_dir=cli_dir, extra=extra
    )


def _patch_host(monkeypatch, engine: str, *, cvmfs: bool) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: f"/usr/bin/{name}" if name == engine else None
    )
    real_is_dir = Path.is_dir

    def is_dir(self):
        if self == Path("/cvmfs"):
            return cvmfs
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)


def _norm(cmd: list[str], base: Path) -> list[str]:
    return [arg.replace(str(base), "<T>") for arg in cmd]


def _full(world, monkeypatch, backend: str) -> list[str]:
    """Full-featured call: tool policy, /cvmfs, extra bind, host runner CLI, env."""
    _patch_host(monkeypatch, _ENGINES[backend], cvmfs=True)
    apply_tool_policy(world.workspace, _TOOL_POLICY)
    scrub_tool_policy_docs(world.workspace, world.repo / "ColliderBench", _TOOL_POLICY)
    cmd, cleanup = sandbox_command(
        world.workspace,
        world.repo,
        [str(world.cli), "exec", "-"],
        extra_ro_binds=[world.extra, world.base / "does-not-exist"],
        container_env={
            "NO_COLOR": "1",
            "CLAUDE_BIN": "/opt/bin/claude",
            "ANTHROPIC_API_KEY": "sk-test",
            "UNLISTED_VAR": "must-not-leak",
        },
        secret_env_names=("ANTHROPIC_API_KEY",),
        sandbox=backend,
    )
    cleanup()
    return _norm(cmd, world.base)


def _minimal(world, monkeypatch, backend: str) -> list[str]:
    """No policy, no /cvmfs, no extra binds, no env, system CLI (image binary)."""
    _patch_host(monkeypatch, _ENGINES[backend], cvmfs=False)
    cmd, cleanup = sandbox_command(
        world.workspace,
        world.repo,
        ["/usr/local/bin/claude", "-p", "hi"],
        sandbox=backend,
    )
    cleanup()
    return _norm(cmd, world.base)


# ── Expected commands ───────────────────────────────────────────────────────

_WS = "<T>/run/workspace"
_FH = "<T>/run/.agent_home"
_TOOLS = "<T>/repo/ColliderBench/tools"
_BIN = "<T>/repo/ColliderBench/bin"
_SHADOW = "<T>/run/.tool_policy/_docs"
_TP = "<T>/run/.tool_policy"
_CLI_DIR = "<T>/host-home/.local/bin"


def _ro_mounts_plain() -> list[str]:
    """Read-only benchmark mounts (no tool policy), as src:dst:ro values."""
    return [
        f"{_TOOLS}/CLI:{_TOOLS}/CLI:ro",
        f"{_TOOLS}/__init__.py:{_TOOLS}/__init__.py:ro",
        f"{_TOOLS}/streaming.py:{_TOOLS}/streaming.py:ro",
        f"{_TOOLS}/TOOLS.md:{_TOOLS}/TOOLS.md:ro",
        f"{_BIN}:{_BIN}:ro",
    ]


def _ro_mounts_policy() -> list[str]:
    """Same, with CLI/TOOLS.md substituted by the shared shadow + ablation masks."""
    return [
        f"{_SHADOW}/CLI:{_TOOLS}/CLI:ro",
        f"{_TOOLS}/__init__.py:{_TOOLS}/__init__.py:ro",
        f"{_TOOLS}/streaming.py:{_TOOLS}/streaming.py:ro",
        f"{_SHADOW}/TOOLS.md:{_TOOLS}/TOOLS.md:ro",
        f"{_BIN}:{_BIN}:ro",
        f"{_TP}/delphes/opt_sim:/opt/sim/delphes:ro",
        f"{_TP}/prospino/opt_sim:/opt/sim/prospino:ro",
        f"{_SHADOW}/simulate:{_BIN}/simulate:ro",
    ]


def _flagged(flag: str, values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        out.extend([flag, v])
    return out


def _expected_podman_full() -> list[str]:
    return [
        "podman",
        "run",
        "--rm",
        "-i",
        "--workdir",
        _WS,
        "-e",
        f"HOME={_FH}",
        "-v",
        f"{_FH}:{_FH}",
        "-v",
        f"{_WS}:{_WS}",
        *_flagged("-v", _ro_mounts_policy()),
        *_flagged(
            "-v",
            [
                "/cvmfs:/cvmfs:ro",
                "<T>/extra-ro:<T>/extra-ro:ro",
                f"{_CLI_DIR}:{_CLI_DIR}:ro",
            ],
        ),
        "-e",
        f"PATH={_CONTAINER_PATH}",
        "-e",
        "PYTHONPATH=<T>/repo",
        "-e",
        "IS_SANDBOX=1",
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLAUDE_BIN=/opt/bin/claude",
        "-e",
        "ANTHROPIC_API_KEY=sk-test",
        _IMAGE,
        f"{_CLI_DIR}/codex",
        "exec",
        "-",
    ]


def _expected_podman_minimal() -> list[str]:
    return [
        "podman",
        "run",
        "--rm",
        "-i",
        "--workdir",
        _WS,
        "-e",
        f"HOME={_FH}",
        "-v",
        f"{_FH}:{_FH}",
        "-v",
        f"{_WS}:{_WS}",
        *_flagged("-v", _ro_mounts_plain()),
        "-e",
        f"PATH={_CONTAINER_PATH}",
        "-e",
        "PYTHONPATH=<T>/repo",
        "-e",
        "IS_SANDBOX=1",
        _IMAGE,
        "claude",
        "-p",
        "hi",
    ]


def _expected_apptainer_full() -> list[str]:
    return [
        "apptainer",
        "exec",
        "--cleanenv",
        "--pwd",
        _WS,
        "--bind",
        f"{_WS}:{_WS}",
        *_flagged("--bind", _ro_mounts_policy()),
        "--bind",
        f"{_FH}:{_FH}",
        "--env",
        f"HOME={_FH}",
        *_flagged(
            "--bind",
            [
                "/cvmfs:/cvmfs:ro",
                "<T>/extra-ro:<T>/extra-ro:ro",
                f"{_CLI_DIR}:{_CLI_DIR}:ro",
            ],
        ),
        "--env",
        "IS_SANDBOX=1",
        "--env",
        "NO_COLOR=1",
        "--env",
        "CLAUDE_BIN=/opt/bin/claude",
        "--env",
        "ANTHROPIC_API_KEY=sk-test",
        _IMAGE,
        f"{_CLI_DIR}/codex",
        "exec",
        "-",
    ]


def _expected_apptainer_minimal() -> list[str]:
    return [
        "apptainer",
        "exec",
        "--cleanenv",
        "--pwd",
        _WS,
        "--bind",
        f"{_WS}:{_WS}",
        *_flagged("--bind", _ro_mounts_plain()),
        "--bind",
        f"{_FH}:{_FH}",
        "--env",
        f"HOME={_FH}",
        "--env",
        "IS_SANDBOX=1",
        _IMAGE,
        "claude",
        "-p",
        "hi",
    ]


# ── Tests ───────────────────────────────────────────────────────────────────


def test_podman_full_command_is_pinned(world, monkeypatch):
    assert _full(world, monkeypatch, "podman") == _expected_podman_full()


def test_podman_minimal_command_is_pinned(world, monkeypatch):
    assert _minimal(world, monkeypatch, "podman") == _expected_podman_minimal()


def test_apptainer_full_command_is_pinned(world, monkeypatch):
    assert _full(world, monkeypatch, "apptainer") == _expected_apptainer_full()


def test_apptainer_minimal_command_is_pinned(world, monkeypatch):
    assert _minimal(world, monkeypatch, "apptainer") == _expected_apptainer_minimal()


def test_docker_full_is_podman_with_only_the_engine_name_changed(world, monkeypatch):
    cmd = _full(world, monkeypatch, "docker")
    assert cmd[0] == "docker"
    assert cmd[1:] == _expected_podman_full()[1:]


def test_docker_minimal_is_podman_with_only_the_engine_name_changed(world, monkeypatch):
    cmd = _minimal(world, monkeypatch, "docker")
    assert cmd[0] == "docker"
    assert cmd[1:] == _expected_podman_minimal()[1:]


def test_singularity_full_is_apptainer_with_only_the_engine_name_changed(world, monkeypatch):
    cmd = _full(world, monkeypatch, "singularity")
    assert cmd[0] == "singularity"
    assert cmd[1:] == _expected_apptainer_full()[1:]


def test_singularity_minimal_is_apptainer_with_only_the_engine_name_changed(world, monkeypatch):
    cmd = _minimal(world, monkeypatch, "singularity")
    assert cmd[0] == "singularity"
    assert cmd[1:] == _expected_apptainer_minimal()[1:]


@pytest.mark.parametrize("backend", ["podman", "docker", "apptainer", "singularity"])
def test_unlisted_env_and_missing_extra_bind_never_reach_the_command(world, monkeypatch, backend):
    cmd = _full(world, monkeypatch, backend)
    joined = "\n".join(cmd)
    assert "UNLISTED_VAR" not in joined and "must-not-leak" not in joined
    assert "does-not-exist" not in joined


@pytest.mark.parametrize("backend", ["podman", "docker", "apptainer", "singularity"])
def test_default_image_is_last_argument_before_runner_command(world, monkeypatch, backend):
    cmd = _minimal(world, monkeypatch, backend)
    assert cmd[cmd.index(_IMAGE) + 1 :] == ["claude", "-p", "hi"]
    assert cmd.count(_IMAGE) == 1


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_image_env_override_replaces_default_image(world, monkeypatch, backend):
    monkeypatch.setenv("LHC_BENCH_IMAGE", "/imgs/custom.sif")
    cmd = _minimal(world, monkeypatch, backend)
    assert "/imgs/custom.sif" in cmd
    assert _IMAGE not in cmd


def test_podman_family_binds_home_before_workspace_apptainer_after_ro_mounts(world, monkeypatch):
    """The one structural ordering difference between the two backend families."""
    pod = _minimal(world, monkeypatch, "podman")
    app = _minimal(world, monkeypatch, "apptainer")
    assert pod.index(f"{_FH}:{_FH}") < pod.index(f"{_WS}:{_WS}")
    assert app.index(f"{_WS}:{_WS}") < app.index(f"{_BIN}:{_BIN}:ro") < app.index(f"{_FH}:{_FH}")
    assert not any(a.startswith(("PATH=", "PYTHONPATH=")) for a in app)
