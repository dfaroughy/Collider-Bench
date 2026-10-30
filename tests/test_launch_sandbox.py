"""Characterization of launch._run_in_sandbox: env assembly, call wiring, cleanup.

Pins the *current* behavior of the function that turns a built workspace into one
sandboxed agent run: the arguments given to `runner.build_command`, how the
process env is built (host env, then PATH/PYTHONPATH/REPO_ROOT, then the
disabled-tool redirects, then the runner's own env), what `sandbox_command` and
`runner.run` receive (including which objects are shared), the order of the
collaborator calls, and when `cleanup()` runs (success, exceptions, and not at all
when nothing was wrapped), so the extraction of the assembly step into
`_assemble_sandbox_run` is shown to be behavior-preserving. Most tests drive
`_run_in_sandbox`; the last sections test `_assemble_sandbox_run` directly and that
`_run_in_sandbox` consumes its tuple unchanged.

Offline and synthetic: the runner and `sandbox_command` are fakes that record
their calls, and the host environment is set explicitly with monkeypatch. No
container, agent CLI, process or network is ever used.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_runtime import launch
from agent_runtime.runners import LaunchPrep

_WS = Path("/ws/run/workspace")
_REPO = Path("/repo")
_CMD = ["sandbox", "wrapped", "cmd"]


class _FakeRunner:
    def __init__(self, events, prep: LaunchPrep, run_exc=None, build_exc=None, prep_exc=None):
        self.events = events
        self.prep = prep
        self.run_exc = run_exc
        self.build_exc = build_exc
        self.prep_exc = prep_exc
        self.build_calls: list[tuple[tuple, dict]] = []
        self.prep_calls: list[tuple[tuple, dict]] = []
        self.run_calls: list[tuple[tuple, dict]] = []
        self.inner_cmd = ["inner", "agent", "cmd"]

    def build_command(self, *args, **kwargs):
        self.events.append("build_command")
        self.build_calls.append((args, kwargs))
        if self.build_exc:
            raise self.build_exc
        return self.inner_cmd

    def prepare_launch(self, *args, **kwargs):
        self.events.append("prepare_launch")
        self.prep_calls.append((args, kwargs))
        if self.prep_exc:
            raise self.prep_exc
        return self.prep

    def run(self, *args, **kwargs):
        self.events.append("run")
        self.run_calls.append((args, kwargs))
        if self.run_exc:
            raise self.run_exc


@pytest.fixture
def harness(monkeypatch):
    """`go(...)` runs `_run_in_sandbox` against fakes and returns a record."""
    events: list[str] = []
    rec = type("Rec", (), {})()
    rec.events = events
    rec.sandbox_calls = []
    rec.cleanups = 0
    rec.disabled_calls = []
    rec.disabled_env = {}
    rec.get_runner_calls = []
    rec.sandbox_exc = None
    rec.cleanup_exc = None

    def fake_disabled_tool_env(workspace, tool_policy):
        events.append("disabled_tool_env")
        rec.disabled_calls.append((workspace, tool_policy))
        return dict(rec.disabled_env)

    def fake_sandbox_command(*args, **kwargs):
        events.append("sandbox_command")
        rec.sandbox_calls.append((args, kwargs))
        if rec.sandbox_exc:
            raise rec.sandbox_exc

        def cleanup():
            events.append("cleanup")
            rec.cleanups += 1
            if rec.cleanup_exc:
                raise rec.cleanup_exc

        return _CMD, cleanup

    monkeypatch.setattr(launch, "disabled_tool_env", fake_disabled_tool_env)
    monkeypatch.setattr("agent_runtime.sandbox.sandbox_command", fake_sandbox_command)

    def go(
        *,
        prep: LaunchPrep | None = None,
        runner_kw: dict | None = None,
        **call_kw,
    ):
        runner = _FakeRunner(events, prep or LaunchPrep(), **(runner_kw or {}))
        rec.runner = runner

        def fake_get_runner(name):
            events.append("get_runner")
            rec.get_runner_calls.append(name)
            return runner

        monkeypatch.setattr(launch, "get_runner", fake_get_runner)
        kwargs = {
            "workspace": _WS,
            "repo_root": _REPO,
            "prompt": "PROMPT",
            "runner_name": "claude",
            "model": "M",
            "max_thinking_tokens": 8000,
            "sandbox": "podman",
            "extra_ro_binds": [Path("/runtime"), Path("/shared")],
        }
        kwargs.update(call_kw)
        rec.result = launch._run_in_sandbox(**kwargs)
        return rec

    go.rec = rec
    return go


@pytest.fixture(autouse=True)
def _host_env(monkeypatch):
    monkeypatch.setenv("PATH", "/host/bin:/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/host/py")
    monkeypatch.setenv("HOST_ONLY", "yes")
    monkeypatch.delenv("REPO_ROOT", raising=False)


def _env(rec) -> dict:
    return rec.sandbox_calls[0][1]["container_env"]


# ── build_command wiring ────────────────────────────────────────────────────


def test_build_command_receives_prompt_workspace_model_and_keyword_options(harness):
    rec = harness(model="mdl", max_thinking_tokens=31999, effort_label="high")
    ((args, kwargs),) = rec.runner.build_calls
    assert args == ("PROMPT", _WS, "mdl")
    assert kwargs == {"allowlist": None, "max_thinking_tokens": 31999, "effort_label": "high"}
    assert rec.get_runner_calls == ["claude"]


def test_model_none_and_default_effort_label_are_passed_through(harness):
    rec = harness(model=None, max_thinking_tokens=None)
    ((args, kwargs),) = rec.runner.build_calls
    assert args[2] is None
    assert kwargs == {"allowlist": None, "max_thinking_tokens": None, "effort_label": None}


# ── env assembly ────────────────────────────────────────────────────────────


def test_path_pythonpath_and_repo_root_are_prepended_to_the_host_env(harness):
    env = _env(harness())
    assert env["PATH"] == "/ws/run/workspace/bin:/host/bin:/usr/bin"
    assert env["PYTHONPATH"] == "/repo:/host/py"
    assert env["REPO_ROOT"] == "/repo"
    assert env["HOST_ONLY"] == "yes"


def test_missing_host_path_and_pythonpath_leave_a_trailing_colon(harness, monkeypatch):
    monkeypatch.delenv("PATH")
    monkeypatch.delenv("PYTHONPATH")
    env = _env(harness())
    assert env["PATH"] == "/ws/run/workspace/bin:"
    assert env["PYTHONPATH"] == "/repo:"


def test_host_environment_is_not_mutated_by_the_call(harness):
    before = dict(os.environ)
    harness(prep=LaunchPrep(env={"FROM_PREP": "1"}))
    assert dict(os.environ) == before
    assert "FROM_PREP" not in os.environ and "REPO_ROOT" not in os.environ


def test_env_precedence_host_then_base_overrides_then_disabled_then_prep(harness):
    harness.rec.disabled_env = {"X": "disabled", "Y": "disabled", "REPO_ROOT": "disabled-root"}
    prep = LaunchPrep(env={"Y": "prep", "PATH": "prep-path"})
    env = _env(harness(prep=prep))
    assert env["X"] == "disabled"  # disabled-tool redirect beats the base env
    assert env["Y"] == "prep"  # runner env beats the disabled-tool redirect
    assert env["PATH"] == "prep-path"  # runner env may even replace PATH
    assert env["REPO_ROOT"] == "disabled-root"  # redirects come after REPO_ROOT is set
    assert env["PYTHONPATH"] == "/repo:/host/py"


def test_disabled_tool_env_receives_the_workspace_and_the_configured_policy(harness):
    policy = {"disabled": ["prospino"]}
    rec = harness(config={"tool_policy": policy})
    assert rec.disabled_calls == [(_WS, policy)]
    assert rec.disabled_calls[0][1] is policy


@pytest.mark.parametrize("config", [None, {}, {"tool_policy": None}, {"tool_policy": {}}])
def test_missing_or_empty_tool_policy_is_passed_as_an_empty_dict(harness, config):
    rec = harness(config=config)
    assert rec.disabled_calls == [(_WS, {})]


def test_real_disabled_tool_env_adds_nothing_for_the_current_blind_tools(monkeypatch):
    from agent_runtime.workspace import disabled_tool_env

    assert disabled_tool_env(_WS, {"disabled": ["delphes", "prospino"]}) == {}


# ── prepare_launch + sandbox_command wiring ─────────────────────────────────


def test_prepare_launch_gets_the_workspace_and_the_same_config_object(harness):
    config = {"auth": "api", "tool_policy": None}
    rec = harness(config=config)
    ((args, kwargs),) = rec.runner.prep_calls
    assert args == (_WS,)
    assert kwargs["config"] is config and set(kwargs) == {"config"}


def test_sandbox_command_gets_the_workspace_repo_inner_command_and_keyword_options(harness):
    prep = LaunchPrep(
        env={"E": "1"},
        extra_ro_binds=[Path("/prep/bind")],
        secret_env_names=("SECRET_A", "SECRET_B"),
        home_dir_name=".h",
        home_files=("hf",),
        home_credential_files=("hc",),
    )
    rec = harness(prep=prep, sandbox="apptainer")
    ((args, kwargs),) = rec.sandbox_calls
    assert args == (_WS, _REPO, rec.runner.inner_cmd)
    assert args[2] is rec.runner.inner_cmd
    assert set(kwargs) == {
        "extra_ro_binds",
        "container_env",
        "secret_env_names",
        "home_dir_name",
        "home_files",
        "home_credential_files",
        "sandbox",
    }
    assert kwargs["secret_env_names"] == ("SECRET_A", "SECRET_B")
    assert kwargs["home_dir_name"] == ".h"
    assert kwargs["home_files"] == ("hf",)
    assert kwargs["home_credential_files"] == ("hc",)
    assert kwargs["sandbox"] == "apptainer"
    assert kwargs["container_env"]["E"] == "1"


def test_extra_ro_binds_are_the_callers_then_the_runners_in_a_new_list(harness):
    caller = [Path("/runtime"), Path("/shared")]
    prep = LaunchPrep(extra_ro_binds=[Path("/prep/a"), Path("/prep/b")])
    rec = harness(prep=prep, extra_ro_binds=caller)
    binds = rec.sandbox_calls[0][1]["extra_ro_binds"]
    assert binds == [Path("/runtime"), Path("/shared"), Path("/prep/a"), Path("/prep/b")]
    assert binds is not caller and caller == [Path("/runtime"), Path("/shared")]


def test_sandbox_none_is_forwarded_as_none(harness):
    assert harness(sandbox=None).sandbox_calls[0][1]["sandbox"] is None


# ── runner.run wiring ───────────────────────────────────────────────────────


def test_run_receives_the_wrapped_command_and_the_same_env_object_as_the_sandbox(harness):
    rec = harness(walltime_s=3600.0)
    ((args, kwargs),) = rec.runner.run_calls
    assert args[0] is _CMD
    assert args[1:3] == ("PROMPT", _WS)
    assert args[3] is _env(rec)  # one env dict shared by sandbox_command and run
    assert args[4] == _WS / "session.jsonl"
    assert len(args) == 5
    assert kwargs == {"walltime_s": 3600.0}


def test_walltime_defaults_to_none_and_the_function_returns_none(harness):
    rec = harness()
    assert rec.runner.run_calls[0][1] == {"walltime_s": None}
    assert rec.result is None


# ── call order + cleanup ────────────────────────────────────────────────────


def test_collaborators_are_called_in_a_fixed_order_with_cleanup_last(harness):
    rec = harness()
    assert rec.events == [
        "get_runner",
        "build_command",
        "disabled_tool_env",
        "prepare_launch",
        "sandbox_command",
        "run",
        "cleanup",
    ]
    assert rec.cleanups == 1


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(2, "agent"),
        RuntimeError("boom"),
        KeyboardInterrupt(),
    ],
)
def test_cleanup_runs_once_when_run_raises_and_the_exception_propagates(harness, exc):
    with pytest.raises(type(exc)) as info:
        harness(runner_kw={"run_exc": exc})
    assert info.value is exc
    assert harness.rec.cleanups == 1
    assert harness.rec.events[-2:] == ["run", "cleanup"]


def test_a_failing_cleanup_after_success_propagates(harness):
    harness.rec.cleanup_exc = OSError("cleanup failed")
    with pytest.raises(OSError, match="cleanup failed"):
        harness()


def test_a_failing_cleanup_masks_the_run_error_but_keeps_it_as_context(harness):
    harness.rec.cleanup_exc = OSError("cleanup failed")
    run_exc = RuntimeError("run failed")
    with pytest.raises(OSError) as info:
        harness(runner_kw={"run_exc": run_exc})
    assert info.value.__context__ is run_exc


def test_no_cleanup_and_no_run_when_sandbox_command_raises(harness):
    harness.rec.sandbox_exc = RuntimeError("no sandbox")
    with pytest.raises(RuntimeError, match="no sandbox"):
        harness()
    assert harness.rec.cleanups == 0
    assert "run" not in harness.rec.events


def test_build_command_failure_stops_before_env_prep_and_sandbox(harness):
    with pytest.raises(ValueError, match="bad build"):
        harness(runner_kw={"build_exc": ValueError("bad build")})
    assert harness.rec.events == ["get_runner", "build_command"]


def test_prepare_launch_failure_stops_before_the_sandbox_is_wrapped(harness):
    with pytest.raises(KeyError):
        harness(runner_kw={"prep_exc": KeyError("hook")})
    assert harness.rec.events == [
        "get_runner",
        "build_command",
        "disabled_tool_env",
        "prepare_launch",
    ]
    assert harness.rec.cleanups == 0


# ── Direct tests of the extracted helper: _assemble_sandbox_run ─────────────


@pytest.fixture
def assembled(harness, monkeypatch):
    """`go(...)` calls `_assemble_sandbox_run` directly against the same fakes."""
    rec = harness.rec

    def go(*, prep: LaunchPrep | None = None, runner_kw: dict | None = None, **call_kw):
        runner = _FakeRunner(rec.events, prep or LaunchPrep(), **(runner_kw or {}))
        rec.runner = runner

        def fake_get_runner(name):
            rec.events.append("get_runner")
            rec.get_runner_calls.append(name)
            return runner

        monkeypatch.setattr(launch, "get_runner", fake_get_runner)
        kwargs = {
            "workspace": _WS,
            "repo_root": _REPO,
            "prompt": "PROMPT",
            "runner_name": "claude",
            "model": "M",
            "max_thinking_tokens": 8000,
        }
        kwargs.update(call_kw)
        rec.assembled = launch._assemble_sandbox_run(**kwargs)
        return rec

    return go


def test_helper_signature_excludes_sandbox_binds_and_walltime():
    import inspect

    params = inspect.signature(launch._assemble_sandbox_run).parameters
    assert list(params) == [
        "workspace",
        "repo_root",
        "prompt",
        "runner_name",
        "model",
        "max_thinking_tokens",
        "effort_label",
        "config",
    ]
    assert params["effort_label"].default is None and params["config"].default is None


def test_helper_returns_runner_inner_command_env_and_the_runners_prep(assembled):
    prep = LaunchPrep(env={"E": "1"}, secret_env_names=("S",))
    rec = assembled(prep=prep)
    runner, inner_cmd, env, got_prep = rec.assembled
    assert runner is rec.runner
    assert inner_cmd is rec.runner.inner_cmd
    assert got_prep is prep  # the runner's own LaunchPrep object, not a copy
    assert isinstance(env, dict) and env["E"] == "1"


def test_helper_env_is_a_fresh_dict_and_neither_host_env_nor_prep_env_is_mutated(assembled):
    prep = LaunchPrep(env={"E": "1"})
    before = dict(os.environ)
    rec = assembled(prep=prep)
    _runner, _cmd, env, _prep = rec.assembled
    assert env is not os.environ and env is not prep.env
    assert dict(os.environ) == before
    assert prep.env == {"E": "1"}
    env["MUTATED"] = "x"
    assert "MUTATED" not in os.environ and "MUTATED" not in prep.env


def test_helper_env_assembly_matches_the_documented_precedence(assembled):
    rec = assembled(prep=LaunchPrep(env={"Y": "prep"}))
    env = rec.assembled[2]
    assert env["PATH"] == "/ws/run/workspace/bin:/host/bin:/usr/bin"
    assert env["PYTHONPATH"] == "/repo:/host/py"
    assert env["REPO_ROOT"] == "/repo"
    assert env["HOST_ONLY"] == "yes" and env["Y"] == "prep"


def test_helper_forwards_the_same_calls_as_the_public_path(assembled):
    config = {"auth": "api", "tool_policy": {"disabled": ["prospino"]}}
    rec = assembled(model="mdl", max_thinking_tokens=5, effort_label="low", config=config)
    ((args, kwargs),) = rec.runner.build_calls
    assert args == ("PROMPT", _WS, "mdl")
    assert kwargs == {"allowlist": None, "max_thinking_tokens": 5, "effort_label": "low"}
    ((pargs, pkwargs),) = rec.runner.prep_calls
    assert pargs == (_WS,) and pkwargs["config"] is config
    assert rec.disabled_calls == [(_WS, config["tool_policy"])]


def test_helper_has_no_sandbox_run_or_cleanup_side_effects(assembled):
    rec = assembled()
    assert rec.sandbox_calls == []
    assert rec.runner.run_calls == []
    assert rec.cleanups == 0
    assert rec.events == ["get_runner", "build_command", "disabled_tool_env", "prepare_launch"]


@pytest.mark.parametrize("which", ["build_exc", "prep_exc"])
def test_helper_lets_runner_errors_propagate(assembled, which):
    exc = RuntimeError(which)
    with pytest.raises(RuntimeError, match=which):
        assembled(runner_kw={which: exc})


# ── _run_in_sandbox consumes the helper's tuple as-is ───────────────────────


def test_run_in_sandbox_passes_the_helpers_objects_through_unchanged(harness, monkeypatch):
    rec = harness.rec
    marker_env = {"MARK": "env"}
    marker_prep = LaunchPrep(extra_ro_binds=[Path("/p")], secret_env_names=("S",))
    runner = _FakeRunner(rec.events, marker_prep)
    inner = ["inner", "marker"]
    seen = []

    def fake_assemble(*args, **kwargs):
        seen.append((args, kwargs))
        return runner, inner, marker_env, marker_prep

    monkeypatch.setattr(launch, "_assemble_sandbox_run", fake_assemble)
    launch._run_in_sandbox(
        _WS,
        _REPO,
        "PROMPT",
        "claude",
        "M",
        8000,
        "podman",
        [Path("/runtime")],
        effort_label="high",
        walltime_s=9.0,
        config={"k": "v"},
    )
    expected_kwargs = {"effort_label": "high", "config": {"k": "v"}}
    assert seen == [((_WS, _REPO, "PROMPT", "claude", "M", 8000), expected_kwargs)]
    ((args, kwargs),) = rec.sandbox_calls
    assert args[2] is inner and kwargs["container_env"] is marker_env
    assert kwargs["extra_ro_binds"] == [Path("/runtime"), Path("/p")]
    assert kwargs["secret_env_names"] == ("S",)
    ((rargs, rkwargs),) = runner.run_calls
    assert rargs[0] is _CMD and rargs[3] is marker_env
    assert rkwargs == {"walltime_s": 9.0}
    assert rec.cleanups == 1
