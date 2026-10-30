"""Characterization of the read-only mount list built by sandbox_command.

Complements tests/test_sandbox_golden.py (whole-command goldens for the "all
features" and "nothing optional" cases) with the in-between cases that a
refactor of the read-only mount assembly could break: partial tool policies,
masks without the doc shadow and vice versa, missing agent-visible tool files,
/cvmfs and extra binds in every combination (missing, duplicate, str paths,
ordering), and every branch of the runner-CLI rewrite.

Only the *read-only* mount values (`src:dst:ro`, in command order) and the
runner command after the image are compared, for both flag families (podman's
`-v` and apptainer's `--bind`). All paths are synthetic under tmp_path, the host
env is scrubbed, and no container engine is ever executed. The tmp root is
replaced by `<T>` before comparing.
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

_BACKENDS = [("podman", "podman", "-v"), ("apptainer", "apptainer", "--bind")]

_T = "<T>/repo/ColliderBench/tools"
_B = "<T>/repo/ColliderBench/bin"
_SH = "<T>/run/.tool_policy/_docs"
_TP = "<T>/run/.tool_policy"

_TOOLS_PLAIN = [
    f"{_T}/CLI:{_T}/CLI:ro",
    f"{_T}/__init__.py:{_T}/__init__.py:ro",
    f"{_T}/streaming.py:{_T}/streaming.py:ro",
    f"{_T}/TOOLS.md:{_T}/TOOLS.md:ro",
    f"{_B}:{_B}:ro",
]
_TOOLS_SHADOWED = [
    f"{_SH}/CLI:{_T}/CLI:ro",
    f"{_T}/__init__.py:{_T}/__init__.py:ro",
    f"{_T}/streaming.py:{_T}/streaming.py:ro",
    f"{_SH}/TOOLS.md:{_T}/TOOLS.md:ro",
    f"{_B}:{_B}:ro",
]
_MASK_DELPHES = f"{_TP}/delphes/opt_sim:/opt/sim/delphes:ro"
_MASK_PROSPINO = f"{_TP}/prospino/opt_sim:/opt/sim/prospino:ro"
_SIMULATE = f"{_SH}/simulate:{_B}/simulate:ro"


@pytest.fixture
def world(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    for var in (*sandbox_mod._RUNNER_ENV_PASSTHROUGH, "LHC_BENCH_IMAGE", "LHC_RECAST_SANDBOX"):
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
    (host_home / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: host_home))
    return SimpleNamespace(base=base, repo=repo, tools=tools, workspace=workspace, home=host_home)


def _patch_host(monkeypatch, engine: str, *, cvmfs: bool = False) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: f"/usr/bin/{name}" if name == engine else None
    )
    real_is_dir = Path.is_dir

    def is_dir(self):
        if self == Path("/cvmfs"):
            return cvmfs
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)


def _run(world, monkeypatch, backend, *, cmd=("claude",), extra=None, cvmfs=False):
    """-> (ro mount values in order, runner command after the image), tmp root = <T>."""
    name, engine, flag = next(b for b in _BACKENDS if b[0] == backend)
    _patch_host(monkeypatch, engine, cvmfs=cvmfs)
    argv, cleanup = sandbox_command(
        world.workspace,
        world.repo,
        list(cmd),
        extra_ro_binds=extra,
        sandbox=name,
    )
    cleanup()
    argv = [a.replace(str(world.base), "<T>") for a in argv]
    values = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]
    return [v for v in values if v.endswith(":ro")], argv[argv.index(_IMAGE) + 1 :]


def _policy(world, tools: list[str], *, scrub: bool = True, apply: bool = True) -> None:
    policy = {"disabled": tools}
    if apply:
        apply_tool_policy(world.workspace, policy)
    if scrub:
        scrub_tool_policy_docs(world.workspace, world.repo / "ColliderBench", policy)


def _make_cli(world, name="codex", *, rel=".local/bin") -> Path:
    path = world.home / rel / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


backends = pytest.mark.parametrize("backend", [b[0] for b in _BACKENDS])


# ── tool-policy variants ────────────────────────────────────────────────────


@backends
def test_no_policy_binds_plain_tools_then_bin(world, monkeypatch, backend):
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == _TOOLS_PLAIN


@backends
def test_only_delphes_disabled_masks_delphes_and_shadows_docs_and_simulate(
    world, monkeypatch, backend
):
    _policy(world, ["delphes"])
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [*_TOOLS_SHADOWED, _MASK_DELPHES, _SIMULATE]


@backends
def test_only_prospino_disabled_masks_prospino_and_shadows_docs_and_simulate(
    world, monkeypatch, backend
):
    _policy(world, ["prospino"])
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [*_TOOLS_SHADOWED, _MASK_PROSPINO, _SIMULATE]


@backends
def test_masks_are_ordered_delphes_then_prospino_regardless_of_policy_order(
    world, monkeypatch, backend
):
    _policy(world, ["prospino", "delphes"])
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [*_TOOLS_SHADOWED, _MASK_DELPHES, _MASK_PROSPINO, _SIMULATE]


@backends
def test_masks_without_doc_shadow_leave_tool_sources_unshadowed(world, monkeypatch, backend):
    _policy(world, ["delphes", "prospino"], scrub=False)
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [*_TOOLS_PLAIN, _MASK_DELPHES, _MASK_PROSPINO]


@backends
def test_doc_shadow_without_masks_shadows_docs_and_simulate_only(world, monkeypatch, backend):
    _policy(world, ["delphes"], apply=False)
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [*_TOOLS_SHADOWED, _SIMULATE]


# ── missing agent-visible tool files ────────────────────────────────────────


@backends
def test_missing_tool_subpaths_are_skipped_and_order_is_kept(world, monkeypatch, backend):
    (world.tools / "streaming.py").unlink()
    (world.tools / "TOOLS.md").unlink()
    ro, _cmd = _run(world, monkeypatch, backend)
    assert ro == [f"{_T}/CLI:{_T}/CLI:ro", f"{_T}/__init__.py:{_T}/__init__.py:ro", f"{_B}:{_B}:ro"]


@backends
def test_missing_tools_md_means_no_tools_md_shadow_either(world, monkeypatch, backend):
    (world.tools / "TOOLS.md").unlink()
    _policy(world, ["delphes"])
    ro, _cmd = _run(world, monkeypatch, backend)
    assert not any("TOOLS.md" in v for v in ro)
    assert f"{_SH}/CLI:{_T}/CLI:ro" in ro and _SIMULATE in ro


@backends
def test_tools_sim_is_never_mounted_even_when_present(world, monkeypatch, backend):
    (world.tools / "sim").mkdir()
    ro, _cmd = _run(world, monkeypatch, backend)
    assert not any("/sim" in v and "/opt/sim" not in v for v in ro)


# ── /cvmfs and extra binds ──────────────────────────────────────────────────


@backends
def test_cvmfs_is_mounted_after_benchmark_mounts_only_when_present(world, monkeypatch, backend):
    ro_off, _ = _run(world, monkeypatch, backend, cvmfs=False)
    ro_on, _ = _run(world, monkeypatch, backend, cvmfs=True)
    assert "/cvmfs:/cvmfs:ro" not in ro_off
    assert ro_on == [*ro_off, "/cvmfs:/cvmfs:ro"]


@backends
def test_extra_binds_keep_order_skip_missing_and_do_not_deduplicate(world, monkeypatch, backend):
    a, b = world.base / "a-extra", world.base / "b-extra"
    a.mkdir()
    b.mkdir()
    extra = [b, world.base / "missing", a, b, str(a)]
    ro, _cmd = _run(world, monkeypatch, backend, extra=extra)
    assert ro[len(_TOOLS_PLAIN) :] == [
        "<T>/b-extra:<T>/b-extra:ro",
        "<T>/a-extra:<T>/a-extra:ro",
        "<T>/b-extra:<T>/b-extra:ro",
        "<T>/a-extra:<T>/a-extra:ro",
    ]


@backends
def test_extra_bind_that_is_a_file_is_mounted_like_a_directory(world, monkeypatch, backend):
    f = world.base / "extra.txt"
    f.write_text("x")
    ro, _cmd = _run(world, monkeypatch, backend, extra=[f])
    assert ro[-1] == "<T>/extra.txt:<T>/extra.txt:ro"


@backends
def test_extra_binds_come_after_cvmfs_and_before_the_runner_cli_bind(world, monkeypatch, backend):
    extra = world.base / "extra"
    extra.mkdir()
    cli = _make_cli(world)
    ro, _cmd = _run(world, monkeypatch, backend, cmd=[str(cli), "x"], extra=[extra], cvmfs=True)
    assert ro[len(_TOOLS_PLAIN) :] == [
        "/cvmfs:/cvmfs:ro",
        "<T>/extra:<T>/extra:ro",
        "<T>/host-home/.local/bin:<T>/host-home/.local/bin:ro",
    ]


@backends
def test_policy_mounts_precede_cvmfs_extras_and_cli(world, monkeypatch, backend):
    extra = world.base / "extra"
    extra.mkdir()
    cli = _make_cli(world)
    _policy(world, ["prospino"])
    ro, _cmd = _run(world, monkeypatch, backend, cmd=[str(cli)], extra=[extra], cvmfs=True)
    assert ro == [
        *_TOOLS_SHADOWED,
        _MASK_PROSPINO,
        _SIMULATE,
        "/cvmfs:/cvmfs:ro",
        "<T>/extra:<T>/extra:ro",
        "<T>/host-home/.local/bin:<T>/host-home/.local/bin:ro",
    ]


# ── runner CLI rewrite ──────────────────────────────────────────────────────


@backends
def test_host_agent_cli_under_home_is_rewritten_to_its_resolved_path_and_dir_bound(
    world, monkeypatch, backend
):
    cli = _make_cli(world, "codex")
    ro, cmd = _run(world, monkeypatch, backend, cmd=[str(cli), "exec", "-"])
    assert cmd == ["<T>/host-home/.local/bin/codex", "exec", "-"]
    assert ro[-1] == "<T>/host-home/.local/bin:<T>/host-home/.local/bin:ro"


@backends
def test_symlinked_host_cli_uses_the_symlink_target_and_its_directory(world, monkeypatch, backend):
    real = _make_cli(world, "codex-real", rel="pkgs/codex/bin")
    link = world.home / ".local" / "bin" / "codex"
    link.symlink_to(real)
    ro, cmd = _run(world, monkeypatch, backend, cmd=[str(link), "-p", "hi"])
    assert cmd == ["<T>/host-home/pkgs/codex/bin/codex-real", "-p", "hi"]
    assert ro[-1] == "<T>/host-home/pkgs/codex/bin:<T>/host-home/pkgs/codex/bin:ro"
    assert not any(v.startswith("<T>/host-home/.local/bin") for v in ro)


@backends
@pytest.mark.parametrize("name", ["claude", "codex", "gemini", "aider", "forge"])
def test_every_host_agent_cli_name_is_treated_alike(world, monkeypatch, backend, name):
    cli = _make_cli(world, name)
    ro, cmd = _run(world, monkeypatch, backend, cmd=[str(cli)])
    assert cmd == [f"<T>/host-home/.local/bin/{name}"]
    assert ro[-1].endswith(":ro") and ro[-1].startswith("<T>/host-home/.local/bin:")


@backends
def test_host_cli_outside_home_falls_back_to_the_image_binary(world, monkeypatch, backend):
    outside = world.base / "opt" / "bin" / "claude"
    outside.parent.mkdir(parents=True)
    outside.write_text("#!/bin/sh\n")
    ro, cmd = _run(world, monkeypatch, backend, cmd=[str(outside), "-p", "x"])
    assert cmd == ["claude", "-p", "x"]
    assert ro == _TOOLS_PLAIN


@backends
def test_missing_absolute_agent_cli_falls_back_to_the_image_binary(world, monkeypatch, backend):
    ghost = world.home / ".local" / "bin" / "codex"
    ro, cmd = _run(world, monkeypatch, backend, cmd=[str(ghost), "exec"])
    assert cmd == ["codex", "exec"]
    assert ro == _TOOLS_PLAIN


@backends
def test_system_path_agent_cli_falls_back_to_the_image_binary(world, monkeypatch, backend):
    ro, cmd = _run(world, monkeypatch, backend, cmd=["/usr/local/bin/gemini", "--x"])
    assert cmd == ["gemini", "--x"]
    assert ro == _TOOLS_PLAIN


@backends
@pytest.mark.parametrize(
    ("head", "expected"),
    [("/usr/bin/python3", "python3"), ("/opt/venv/bin/python", "python")],
)
def test_absolute_python_is_replaced_by_the_image_python_without_binds(
    world, monkeypatch, backend, head, expected
):
    ro, cmd = _run(world, monkeypatch, backend, cmd=[head, "-m", "x"])
    assert cmd == [expected, "-m", "x"]
    assert ro == _TOOLS_PLAIN


@backends
@pytest.mark.parametrize("cmd_in", [["claude", "-p"], ["./claude"], ["/bin/echo", "hi"], []])
def test_other_commands_are_passed_through_unchanged_without_binds(
    world, monkeypatch, backend, cmd_in
):
    ro, cmd = _run(world, monkeypatch, backend, cmd=cmd_in)
    assert cmd == cmd_in
    assert ro == _TOOLS_PLAIN
