"""Characterization of the side effects and ordering inside Sandbox.wrap().

Complements tests/test_sandbox_golden.py and tests/test_sandbox_mounts.py (which pin
the argv) by pinning *when* things happen: the order in which fake-home
preparation, the read-only mount builders and the optional-mount builder run for
the podman family and the apptainer family, exactly what `_prepare_isolated_home`
receives, that `wrap()` itself never syncs credentials while `cleanup()` does (and
evaluates `Path.home()` at that moment), image and env selection, and the
`sandbox_command` shim's ordering (papers materialization, then backend lookup,
then wrap). It also characterizes the current `_prepare_isolated_home` /
`_sync_credentials_back_to_host` rules against synthetic tmp directories. This lets a
refactor of the wrap bodies be shown to be behavior-preserving.

Fakes only: the fake-home preparation, the credential sync and the mount builders are
replaced by (delegating) recorders; the two filesystem helpers are exercised only on
tmp_path trees. No container engine is run, no real home directory or credential is
read, and no isolation or security policy is changed or asserted as desirable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agent_runtime import sandbox as sb

# Captured at import time, before any fixture replaces the module-level names.
_REAL_PREPARE = sb._prepare_isolated_home
_REAL_SYNC = sb._sync_credentials_back_to_host

_IMAGE = "ghcr.io/dfaroughy/lhc-bench:latest"
_FAMILIES = {
    "podman": (sb.PodmanSandbox, "-e", "-v"),
    "docker": (sb.DockerSandbox, "-e", "-v"),
    "apptainer": (sb.ApptainerSandbox, "--env", "--bind"),
    "singularity": (sb.SingularitySandbox, "--env", "--bind"),
}
_ALL = sorted(_FAMILIES)
_PODMAN_FAMILY = ["docker", "podman"]
_APPTAINER_FAMILY = ["apptainer", "singularity"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    for var in (*sb._RUNNER_ENV_PASSTHROUGH, "LHC_BENCH_IMAGE", "LHC_RECAST_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)  # podman-hpc absent -> "podman"
    host_home = base / "host-home"
    host_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: host_home))

    events: list = []
    rec = type("Rec", (), {})()
    rec.events = events
    rec.prepare_calls = []
    rec.sync_calls = []
    rec.fake_home = base / "run" / ".agent_home"
    rec.workspace = base / "run" / "workspace"
    rec.repo = base / "repo"
    rec.base = base
    rec.host_home = host_home

    real_agent, real_additional = sb._agent_readonly_mount_args, sb._additional_readonly_mount_args

    def fake_prepare(host, target=None, rel_files=()):
        events.append("prepare_home")
        rec.prepare_calls.append((host, target, rel_files))
        return rec.fake_home

    def fake_sync(fake_home, host, rel_files=()):
        events.append("sync")
        rec.sync_calls.append((fake_home, host, rel_files))

    def spy_agent(*args, **kwargs):
        events.append("ro_mounts")
        return real_agent(*args, **kwargs)

    def spy_additional(*args, **kwargs):
        events.append("optional_mounts")
        return real_additional(*args, **kwargs)

    monkeypatch.setattr(sb, "_prepare_isolated_home", fake_prepare)
    monkeypatch.setattr(sb, "_sync_credentials_back_to_host", fake_sync)
    monkeypatch.setattr(sb, "_agent_readonly_mount_args", spy_agent)
    monkeypatch.setattr(sb, "_additional_readonly_mount_args", spy_additional)
    return rec


def _wrap(world, backend, *, inner=("claude", "-p", "hi"), **kw):
    cls = _FAMILIES[backend][0]
    return cls().wrap(world.workspace, world.repo, list(inner), **kw)


def _env_values(cmd, flag):
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == flag]


# ── fake-home preparation: arguments + order ────────────────────────────────


@pytest.mark.parametrize("backend", _ALL)
def test_prepare_home_is_called_once_with_host_home_target_and_home_files(world, backend):
    files = (".claude.json", ".claude/settings.json")
    _wrap(world, backend, home_dir_name=".claude_home", home_files=files)
    ((host, target, rel_files),) = world.prepare_calls
    assert host == world.host_home
    assert target == world.workspace.parent / ".claude_home"
    assert rel_files is files


@pytest.mark.parametrize("backend", _ALL)
def test_default_home_dir_name_and_empty_home_files(world, backend):
    _wrap(world, backend)
    ((_host, target, rel_files),) = world.prepare_calls
    assert target == world.workspace.parent / ".agent_home"
    assert tuple(rel_files) == ()


@pytest.mark.parametrize("backend", _PODMAN_FAMILY)
def test_podman_family_prepares_the_home_before_building_any_mounts(world, backend):
    _wrap(world, backend)
    assert world.events == ["prepare_home", "ro_mounts", "optional_mounts"]


@pytest.mark.parametrize("backend", _APPTAINER_FAMILY)
def test_apptainer_family_builds_read_only_mounts_before_preparing_the_home(world, backend):
    _wrap(world, backend)
    assert world.events == ["ro_mounts", "prepare_home", "optional_mounts"]


@pytest.mark.parametrize("backend", _ALL)
def test_wrap_never_syncs_credentials_only_cleanup_does(world, backend):
    _cmd, cleanup = _wrap(world, backend, home_credential_files=(".claude.json",))
    assert world.sync_calls == []
    cleanup()
    assert len(world.sync_calls) == 1


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_invalid_home_dir_name_raises_before_preparing_the_home(world, backend):
    with pytest.raises(ValueError, match="invalid fake-home directory name"):
        _wrap(world, backend, home_dir_name="../escape")
    assert world.prepare_calls == []
    # The order difference shows through: podman fails before any mount builder runs,
    # apptainer has already built its read-only mounts.
    expected = [] if backend == "podman" else ["ro_mounts"]
    assert world.events == expected


# ── cleanup: sync timing ────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", _ALL)
def test_cleanup_syncs_fake_home_to_the_current_host_home_with_the_credential_files(world, backend):
    creds = (".claude.json", ".claude/.credentials.json")
    _cmd, cleanup = _wrap(world, backend, home_credential_files=creds)
    cleanup()
    ((fake_home, host, rel_files),) = world.sync_calls
    assert fake_home == world.fake_home and host == world.host_home
    assert rel_files is creds
    assert world.events[-1] == "sync"


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_cleanup_evaluates_path_home_at_cleanup_time_not_wrap_time(world, monkeypatch, backend):
    _cmd, cleanup = _wrap(world, backend)
    other_home = world.base / "other-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: other_home))
    cleanup()
    assert world.sync_calls[0][1] == other_home
    assert world.prepare_calls[0][0] == world.host_home  # wrap saw the original


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_each_cleanup_call_syncs_again_and_never_removes_the_fake_home(world, backend):
    _cmd, cleanup = _wrap(world, backend)
    world.fake_home.mkdir(parents=True)
    (world.fake_home / "state.db").write_text("kept")
    cleanup()
    cleanup()
    assert len(world.sync_calls) == 2
    assert (world.fake_home / "state.db").read_text() == "kept"


@pytest.mark.parametrize("backend", _ALL)
def test_default_credential_files_is_an_empty_tuple_passed_to_the_sync(world, backend):
    _cmd, cleanup = _wrap(world, backend)
    cleanup()
    assert tuple(world.sync_calls[0][2]) == ()


# ── image and env selection ─────────────────────────────────────────────────


@pytest.mark.parametrize("backend", _ALL)
def test_image_defaults_and_env_override_are_read_at_wrap_time(world, monkeypatch, backend):
    cmd, _ = _wrap(world, backend)
    assert cmd.count(_IMAGE) == 1
    monkeypatch.setenv("LHC_BENCH_IMAGE", "/imgs/a.sif")
    cmd2, _ = _wrap(world, backend)
    assert "/imgs/a.sif" in cmd2 and _IMAGE not in cmd2
    monkeypatch.setenv("LHC_BENCH_IMAGE", "")
    cmd3, _ = _wrap(world, backend)
    assert _IMAGE in cmd3  # an empty value falls back to the default


@pytest.mark.parametrize("backend", _ALL)
def test_image_is_resolved_before_the_fake_home_is_prepared_in_both_families(
    world, monkeypatch, backend
):
    monkeypatch.setenv("LHC_BENCH_IMAGE", "/imgs/early.sif")
    real_prepare = sb._prepare_isolated_home

    def prepare_then_change_env(*args, **kwargs):
        monkeypatch.setenv("LHC_BENCH_IMAGE", "/imgs/late.sif")
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(sb, "_prepare_isolated_home", prepare_then_change_env)
    cmd, _ = _wrap(world, backend)
    assert "/imgs/early.sif" in cmd and "/imgs/late.sif" not in cmd


@pytest.mark.parametrize("backend", _ALL)
def test_runner_command_follows_the_image_and_is_rewritten_for_system_clis(world, backend):
    cmd, _ = _wrap(world, backend, inner=("/usr/bin/claude", "-p", "x"))
    assert cmd[cmd.index(_IMAGE) + 1 :] == ["claude", "-p", "x"]


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_env_pairs_follow_allowlist_then_secret_order_with_explicit_env_first(
    world, monkeypatch, backend
):
    _cls, env_flag, _mount_flag = _FAMILIES[backend]
    monkeypatch.setenv("NO_COLOR", "host-nocolor")
    monkeypatch.setenv("CLAUDE_BIN", "host-bin")
    monkeypatch.setenv("SECRET_A", "host-secret-a")
    cmd, _ = _wrap(
        world,
        backend,
        container_env={"CLAUDE_BIN": "explicit-bin", "SECRET_B": "explicit-b", "CI": ""},
        secret_env_names=("SECRET_B", "SECRET_A"),
    )
    tail = _env_values(cmd, env_flag)
    expected_tail = [
        "NO_COLOR=host-nocolor",  # host fallback when the explicit env lacks it
        "CLAUDE_BIN=explicit-bin",  # explicit env wins over the host value
        "SECRET_B=explicit-b",
        "SECRET_A=host-secret-a",
    ]
    assert tail[-4:] == expected_tail
    assert not any(v.startswith("CI=") for v in tail)  # empty values are dropped


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_unlisted_variables_and_unset_secrets_never_appear(world, monkeypatch, backend):
    _cls, env_flag, _ = _FAMILIES[backend]
    monkeypatch.setenv("RANDOM_HOST_VAR", "x")
    cmd, _ = _wrap(
        world, backend, container_env={"ALSO_RANDOM": "y"}, secret_env_names=("UNSET_SECRET",)
    )
    joined = "\n".join(_env_values(cmd, env_flag))
    assert "RANDOM_HOST_VAR" not in joined and "ALSO_RANDOM" not in joined
    assert "UNSET_SECRET" not in joined


@pytest.mark.parametrize("backend", ["podman", "apptainer"])
def test_is_sandbox_flag_precedes_the_forwarded_env_pairs(world, backend):
    _cls, env_flag, _ = _FAMILIES[backend]
    cmd, _ = _wrap(world, backend, container_env={"NO_COLOR": "1"})
    values = _env_values(cmd, env_flag)
    assert values.index("IS_SANDBOX=1") < values.index("NO_COLOR=1")


@pytest.mark.parametrize("backend", _ALL)
def test_wrap_touches_no_filesystem_state_when_the_home_helper_is_faked(world, backend):
    before = sorted(p for p in world.base.rglob("*"))
    _wrap(world, backend, extra_ro_binds=[world.base / "missing"])
    assert sorted(p for p in world.base.rglob("*")) == before


# ── backend identity ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("backend", "engine"),
    [
        ("podman", "podman"),
        ("docker", "docker"),
        ("apptainer", "apptainer"),
        ("singularity", "singularity"),
    ],
)
def test_first_command_word_is_the_engine_and_subclasses_inherit_the_rest(world, backend, engine):
    cmd, _ = _wrap(world, backend)
    assert cmd[0] == engine
    assert cmd[1] == ("run" if backend in _PODMAN_FAMILY else "exec")
    assert _FAMILIES[backend][0].name == backend


def test_podman_hpc_is_the_engine_when_present(world, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: "/x/podman-hpc" if name == "podman-hpc" else None
    )
    cmd, _ = sb.PodmanSandbox().wrap(world.workspace, world.repo, ["claude"])
    assert cmd[0] == "podman-hpc"


# ── the "none" backend ──────────────────────────────────────────────────────


def test_none_backend_returns_the_inner_command_copy_and_a_noop_cleanup(world):
    inner = ["agent", "--x"]
    cmd, cleanup = sb.NoneSandbox().wrap(world.workspace, world.repo, inner, home_dir_name=".h")
    assert cmd == inner and cmd is not inner
    assert cleanup() is None
    assert world.prepare_calls == [] and world.sync_calls == [] and world.events == []


# ── sandbox_command shim ordering ───────────────────────────────────────────


def test_sandbox_command_materializes_papers_then_selects_the_backend_then_wraps(
    world, monkeypatch
):
    seen: list = []

    class FakeBackend:
        def wrap(self, *args):
            seen.append(("wrap", args))
            return ["cmd"], lambda: None

    monkeypatch.setattr(sb, "_materialize_papers_dir", lambda ws: seen.append(("papers", ws)))
    monkeypatch.setattr(
        sb, "get_sandbox", lambda name=None: seen.append(("get_sandbox", name)) or FakeBackend()
    )
    out = sb.sandbox_command(world.workspace, world.repo, ["x"], sandbox="docker")
    assert out[0] == ["cmd"]
    assert [s[0] for s in seen] == ["papers", "get_sandbox", "wrap"]
    assert seen[0][1] == world.workspace and seen[1][1] == "docker"


def test_sandbox_command_passes_wrap_arguments_positionally_with_defaults(world, monkeypatch):
    captured = {}

    class FakeBackend:
        def wrap(self, *args):
            captured["args"] = args
            return [], lambda: None

    monkeypatch.setattr(sb, "_materialize_papers_dir", lambda ws: None)
    monkeypatch.setattr(sb, "get_sandbox", lambda name=None: FakeBackend())
    sb.sandbox_command(world.workspace, world.repo, ["x"])
    defaults = (None, None, (), ".agent_home", (), ())
    assert captured["args"] == (world.workspace, world.repo, ["x"], *defaults)
    binds, env = [Path("/b")], {"K": "v"}
    sb.sandbox_command(
        world.workspace,
        world.repo,
        ["x"],
        extra_ro_binds=binds,
        container_env=env,
        secret_env_names=("S",),
        home_dir_name=".h",
        home_files=("f",),
        home_credential_files=("c",),
    )
    assert captured["args"] == (
        world.workspace,
        world.repo,
        ["x"],
        binds,
        env,
        ("S",),
        ".h",
        ("f",),
        ("c",),
    )
    assert captured["args"][3] is binds and captured["args"][4] is env


# ── real helpers against synthetic tmp trees (no host home, no credentials) ─


def test_prepare_isolated_home_rebuilds_the_target_and_copies_only_listed_existing_files(
    tmp_path,
):
    prepare = _REAL_PREPARE
    host = tmp_path / "host"
    (host / ".claude").mkdir(parents=True)
    (host / ".claude.json").write_text("top")
    (host / ".claude" / "settings.json").write_text("settings")
    (host / "unlisted.txt").write_text("no")
    target = tmp_path / "run" / ".home"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("stale")
    os.utime(host / ".claude.json", (1_000_000_000, 1_000_000_000))
    out = prepare(host, target, (".claude.json", ".claude/settings.json", "missing.json"))
    assert out == target
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        ".claude.json",
        ".claude/settings.json",
    ]
    assert int((target / ".claude.json").stat().st_mtime) == 1_000_000_000  # copy2 keeps mtime


def test_prepare_isolated_home_replaces_a_file_or_symlink_target_with_a_directory(tmp_path):
    prepare = _REAL_PREPARE
    host = tmp_path / "host"
    host.mkdir()
    for kind in ("file", "symlink"):
        target = tmp_path / f"t-{kind}"
        if kind == "file":
            target.write_text("not a dir")
        else:
            (tmp_path / "elsewhere").mkdir(exist_ok=True)
            target.symlink_to(tmp_path / "elsewhere")
        prepare(host, target, ())
        assert target.is_dir() and not target.is_symlink()
    assert (tmp_path / "elsewhere").is_dir()  # the symlink target itself is untouched


def test_prepare_isolated_home_without_a_target_makes_a_fresh_temp_dir(tmp_path):
    prepare = _REAL_PREPARE
    out = prepare(tmp_path, None, ())
    try:
        assert out.is_dir() and out.name.startswith("lhc-recast-home-")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_sync_copies_only_newer_fake_home_files_over_existing_host_files(tmp_path):
    sync = _REAL_SYNC
    fake, host = tmp_path / "fake", tmp_path / "host"
    (fake / ".claude").mkdir(parents=True)
    (host / ".claude").mkdir(parents=True)
    names = (".claude.json", ".claude/.credentials.json", ".claude/other.json", "absent.json")
    for rel, src_t, host_t in [
        (".claude.json", 2_000, 1_000),  # fake newer -> copied
        (".claude/.credentials.json", 1_000, 2_000),  # host newer -> kept
        (".claude/other.json", 1_500, 1_500),  # equal mtime -> kept
    ]:
        (fake / rel).write_text(f"fake:{rel}")
        (host / rel).write_text(f"host:{rel}")
        os.utime(fake / rel, (src_t, src_t))
        os.utime(host / rel, (host_t, host_t))
    (fake / "absent.json").write_text("only in fake")  # host copy missing -> skipped
    sync(fake, host, names)
    assert (host / ".claude.json").read_text() == "fake:.claude.json"
    assert (host / ".claude/.credentials.json").read_text() == "host:.claude/.credentials.json"
    assert (host / ".claude/other.json").read_text() == "host:.claude/other.json"
    assert not (host / "absent.json").exists()


def test_sync_swallows_copy_errors_and_validates_relative_paths(tmp_path, monkeypatch):
    fake, host = tmp_path / "fake", tmp_path / "host"
    fake.mkdir()
    host.mkdir()
    (fake / "a.json").write_text("new")
    (host / "a.json").write_text("old")
    os.utime(fake / "a.json", (5_000, 5_000))
    os.utime(host / "a.json", (1_000, 1_000))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", boom)
    _REAL_SYNC(fake, host, ("a.json",))  # must not raise
    assert (host / "a.json").read_text() == "old"
    with pytest.raises(ValueError, match="invalid fake-home file path"):
        _REAL_SYNC(fake, host, ("../escape",))


# ── Direct tests of the extracted helpers: _prepare_fake_home / _container_command_tail ──


def test_prepare_fake_home_returns_the_path_bind_spec_and_home_assignment(world):
    files = (".claude.json",)
    fake_home, bind, home_env = sb._prepare_fake_home(world.workspace, ".h", files)
    assert fake_home == world.fake_home
    assert bind == f"{world.fake_home}:{world.fake_home}"
    assert home_env == f"HOME={world.fake_home}"
    assert isinstance(bind, str) and isinstance(home_env, str)


def test_prepare_fake_home_calls_the_preparer_once_with_host_home_target_and_files(world):
    files = (".a", ".b")
    sb._prepare_fake_home(world.workspace, ".h", files)
    ((host, target, rel_files),) = world.prepare_calls
    assert host == world.host_home
    assert target == world.workspace.parent / ".h"
    assert rel_files is files  # passed through, not copied


def test_prepare_fake_home_validates_the_name_before_preparing_anything(world):
    with pytest.raises(ValueError, match="invalid fake-home directory name"):
        sb._prepare_fake_home(world.workspace, "a/b", ())
    assert world.prepare_calls == []


def test_prepare_fake_home_does_no_syncing_or_mount_building(world):
    sb._prepare_fake_home(world.workspace, ".h", ())
    assert world.events == ["prepare_home"]
    assert world.sync_calls == []


@pytest.mark.parametrize("flag", ["-e", "--env"])
def test_command_tail_is_marker_then_env_pairs_then_image_then_command(world, flag):
    tail = sb._container_command_tail(
        flag,
        {"NO_COLOR": "1", "SECRET_X": "s"},
        ("SECRET_X",),
        "IMG",
        ["claude", "-p", "hi"],
    )
    assert tail == [
        flag,
        "IS_SANDBOX=1",
        flag,
        "NO_COLOR=1",
        flag,
        "SECRET_X=s",
        "IMG",
        "claude",
        "-p",
        "hi",
    ]


def test_command_tail_without_env_is_marker_image_and_command_only(world):
    assert sb._container_command_tail("-e", None, (), "IMG", ["x"]) == [
        "-e",
        "IS_SANDBOX=1",
        "IMG",
        "x",
    ]
    assert sb._container_command_tail("-e", {}, (), "IMG", []) == ["-e", "IS_SANDBOX=1", "IMG"]


def test_command_tail_uses_the_same_env_rules_as_the_wrapper(world, monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "host-bin")
    monkeypatch.setenv("SECRET_A", "host-a")
    tail = sb._container_command_tail(
        "-e",
        {"CLAUDE_BIN": "explicit", "CI": "", "UNLISTED": "x"},
        ("SECRET_A", "UNSET"),
        "IMG",
        ["c"],
    )
    assert tail == [
        "-e",
        "IS_SANDBOX=1",
        "-e",
        "CLAUDE_BIN=explicit",  # explicit beats host
        "-e",
        "SECRET_A=host-a",  # host fallback for a secret
        "IMG",
        "c",
    ]


def test_command_tail_does_not_mutate_its_inputs_and_returns_a_new_list(world):
    env = {"NO_COLOR": "1"}
    secrets = ["S"]
    cmd = ["claude", "-p"]
    tail = sb._container_command_tail("-e", env, secrets, "IMG", cmd)
    assert env == {"NO_COLOR": "1"} and secrets == ["S"] and cmd == ["claude", "-p"]
    tail.append("mutated")
    assert cmd == ["claude", "-p"]


def test_command_tail_is_a_suffix_of_every_wrapped_command(world):
    for backend in _ALL:
        _cls, env_flag, _ = _FAMILIES[backend]
        cmd, _cleanup = _wrap(
            world, backend, container_env={"NO_COLOR": "1"}, inner=("/usr/bin/claude", "-x")
        )
        expected = sb._container_command_tail(
            env_flag, {"NO_COLOR": "1"}, (), _IMAGE, ["claude", "-x"]
        )
        assert cmd[-len(expected) :] == expected
