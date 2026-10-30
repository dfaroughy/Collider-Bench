"""Characterization of the initial run-info metadata built by launch_single_run.

Pins the *current* behavior of the "named vs generated run" block of
agent_runtime/launch.py: which keys the metadata dict has and in what order, how
the model falls back to the runner name, how effort / max_thinking_tokens /
sandbox / tool_policy are added, how `generate_run_info` is called (keyword
arguments) and where it sits relative to `build_workspace`, and when
`write_run_info` runs and with what, so the extraction of this block into
`_build_run_info` is shown to be behavior-preserving. Most tests drive
`launch_single_run`; the last section tests `_build_run_info` directly.

Offline and synthetic: `build_workspace`, `_run_in_sandbox`, `_run_offline_evals`
and `generate_run_info` are stubbed in the `agent_runtime.launch` namespace, the
scorer is injected, and all files live under tmp_path. The real task.toml for the
shared `task_id` fixture is read (read-only) only for its paper reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime import launch
from agent_runtime.config import load_task_toml
from agent_runtime.run_info import write_run_info as real_write_run_info

_GOOD_SCORE = {"n_filled": 1, "n_bins": 1}


@pytest.fixture
def launched(tmp_path, monkeypatch, capsys, repo_root, task_id):
    """`go(*extra_argv, config_text=None)` -> a record of everything that happened."""
    paper = load_task_toml(repo_root, task_id)["task"]["paper"]
    events: list[str] = []
    ws_root = tmp_path / "ws_root"
    workspace = ws_root / "workspace"
    rec = SimpleNamespace(
        events=events,
        build_calls=[],
        write_calls=[],
        gen_calls=[],
        paper=paper,
        task_id=task_id,
        workspace=workspace,
        ws_root=ws_root,
        repo_root=repo_root,
    )

    def fake_generate_run_info(**kwargs):
        events.append("generate_run_info")
        rec.gen_calls.append(kwargs)
        return {
            "agent_id": "FakeId_deadbeef",
            "run_dir": "grp/generated_run",
            "task_id": kwargs["task_id"],
            "agent": kwargs["agent_name"],
            "runner": kwargs["runner_name"],
            "model": kwargs["model_name"],
            "paper_ref": kwargs["paper_ref"],
        }

    def fake_build_workspace(*args, **kwargs):
        events.append("build_workspace")
        rec.build_calls.append((args, kwargs))
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def recording_write_run_info(recast_dir, info):
        events.append("write_run_info")
        rec.write_calls.append((Path(recast_dir), dict(info), list(info)))
        return real_write_run_info(recast_dir, info)

    def fake_run_in_sandbox(*_args, **_kwargs):
        events.append("run")

    monkeypatch.setattr(launch, "generate_run_info", fake_generate_run_info)
    monkeypatch.setattr(launch, "build_workspace", fake_build_workspace)
    monkeypatch.setattr(launch, "write_run_info", recording_write_run_info)
    monkeypatch.setattr(launch, "_run_in_sandbox", fake_run_in_sandbox)
    monkeypatch.setattr(launch, "_run_offline_evals", lambda *_a, **_k: None)

    def go(*extra_argv, config_text=None, with_task=True):
        argv = ["--runner", "claude", "--auth", "oauth", *extra_argv]
        if with_task:
            argv = ["--task", task_id, *argv]
        if config_text is not None:
            cfg_path = tmp_path / "cfg.yaml"
            cfg_path.write_text(config_text)
            argv = ["--config", str(cfg_path), *argv]
        rec.code = launch.launch_single_run(
            "simple", lambda ref: "prompt", repo_root, argv=argv, score=lambda _w: _GOOD_SCORE
        )
        out = capsys.readouterr()
        rec.out, rec.err = out.out, out.err
        return rec

    go.rec = rec
    return go


def _tail(info: dict) -> dict:
    return {k: info[k] for k in ("effort", "max_thinking_tokens", "sandbox")}


# ── named runs (--run-name) ─────────────────────────────────────────────────


def test_named_run_metadata_exact_keys_order_and_defaults(launched):
    rec = launched("--run-name", "myrun")
    ((_path, info, key_order),) = rec.write_calls
    assert info == {
        "agent_id": "myrun",
        "run_dir": "myrun",
        "task_id": rec.task_id,
        "paper_ref": rec.paper,
        "agent": "simple",
        "runner": "claude",
        "model": "claude",  # --model absent -> falls back to the runner name
        "effort": "medium",
        "max_thinking_tokens": 8000,
        "sandbox": "auto",
    }
    assert key_order == [
        "agent_id",
        "run_dir",
        "task_id",
        "paper_ref",
        "agent",
        "runner",
        "model",
        "effort",
        "max_thinking_tokens",
        "sandbox",
    ]
    assert rec.gen_calls == []
    assert rec.code == 0


def test_named_run_uses_explicit_model_and_run_name_as_directory(launched):
    rec = launched("--run-name", "nr", "--model", "my-model")
    ((_path, info, _order),) = rec.write_calls
    assert info["model"] == "my-model"
    ((args, kwargs),) = rec.build_calls
    assert args == (rec.repo_root, "simple", rec.task_id, "nr")
    assert kwargs == {"tool_policy": None}


# ── generated runs ──────────────────────────────────────────────────────────


def test_generated_run_calls_generate_run_info_with_keyword_arguments(launched):
    rec = launched("--model", "m1")
    assert rec.gen_calls == [
        {
            "task_id": rec.task_id,
            "agent_name": "simple",
            "runner_name": "claude",
            "model_name": "m1",
            "paper_ref": rec.paper,
        }
    ]


def test_generated_run_model_falls_back_to_runner_in_the_generate_call(launched):
    rec = launched()
    assert rec.gen_calls[0]["model_name"] == "claude"
    assert rec.gen_calls[0]["runner_name"] == "claude"


def test_generated_run_info_keeps_generator_keys_then_appends_launch_keys(launched):
    rec = launched("--effort", "high", "--sandbox", "none")
    ((_path, info, key_order),) = rec.write_calls
    assert key_order == [
        "agent_id",
        "run_dir",
        "task_id",
        "agent",
        "runner",
        "model",
        "paper_ref",
        "effort",
        "max_thinking_tokens",
        "sandbox",
    ]
    assert info["agent_id"] == "FakeId_deadbeef"
    assert _tail(info) == {"effort": "high", "max_thinking_tokens": 31999, "sandbox": "none"}


def test_generated_run_dir_from_generator_is_passed_to_build_workspace(launched):
    rec = launched()
    ((args, _kwargs),) = rec.build_calls
    assert args == (rec.repo_root, "simple", rec.task_id, "grp/generated_run")
    assert rec.write_calls[0][1]["run_dir"] == "grp/generated_run"


# ── effort / sandbox ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("effort", "label", "tokens"),
    [
        ("low", "low", 2000),
        ("high", "high", 31999),
        ("max", "max", 31999),
        ("12000", "custom(12000)", 12000),
    ],
)
def test_effort_label_and_max_thinking_tokens_are_recorded(launched, effort, label, tokens):
    rec = launched("--run-name", "e", "--effort", effort)
    info = rec.write_calls[0][1]
    assert (info["effort"], info["max_thinking_tokens"]) == (label, tokens)


def test_sandbox_defaults_to_auto_and_records_the_requested_value(launched):
    assert launched("--run-name", "s1").write_calls[0][1]["sandbox"] == "auto"
    assert launched("--run-name", "s2", "--sandbox", "none").write_calls[1][1]["sandbox"] == "none"


# ── config interplay + tool_policy ──────────────────────────────────────────


def test_config_values_fill_in_and_cli_flags_override(launched):
    config = "runner: claude\nmodel: cfg-model\neffort: low\nsandbox: none\n"
    rec = launched("--run-name", "c1", config_text=config)
    info = rec.write_calls[0][1]
    assert info["model"] == "cfg-model"
    assert _tail(info) == {"effort": "low", "max_thinking_tokens": 2000, "sandbox": "none"}
    rec = launched(
        "--run-name", "c2", "--model", "cli-model", "--effort", "high", config_text=config
    )
    info = rec.write_calls[1][1]
    assert (info["model"], info["effort"]) == ("cli-model", "high")


def test_tool_policy_is_added_last_and_forwarded_to_build_workspace(launched):
    config = "tool_policy:\n  disabled: [prospino]\n"
    rec = launched("--run-name", "tp", config_text=config)
    ((_path, info, key_order),) = rec.write_calls
    assert info["tool_policy"] == {"disabled": ["prospino"]}
    assert key_order[-4:] == ["effort", "max_thinking_tokens", "sandbox", "tool_policy"]
    assert rec.build_calls[0][1] == {"tool_policy": {"disabled": ["prospino"]}}


def test_empty_tool_policy_adds_no_key_but_is_still_forwarded(launched):
    rec = launched("--run-name", "tp0", config_text="tool_policy: {}\n")
    ((_path, info, _order),) = rec.write_calls
    assert "tool_policy" not in info
    assert rec.build_calls[0][1] == {"tool_policy": {}}


def test_no_tool_policy_forwards_none_and_adds_no_key(launched):
    rec = launched("--run-name", "tpn")
    assert "tool_policy" not in rec.write_calls[0][1]
    assert rec.build_calls[0][1] == {"tool_policy": None}


# ── placement and timing ────────────────────────────────────────────────────


def test_event_order_generate_then_build_then_write_then_run(launched):
    rec = launched()
    assert rec.events == ["generate_run_info", "build_workspace", "write_run_info", "run"]


def test_named_run_skips_generation_but_keeps_the_rest_of_the_order(launched):
    rec = launched("--run-name", "n")
    assert rec.events == ["build_workspace", "write_run_info", "run"]


def test_write_run_info_targets_the_workspace_parent_and_adds_started_at_in_the_file(launched):
    rec = launched("--run-name", "w")
    ((path, info, _order),) = rec.write_calls
    assert path == rec.ws_root
    assert "started_at" not in info  # added by write_run_info itself
    saved = json.loads((rec.ws_root / "run_info.json").read_text())
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", saved["started_at"])
    assert {k: saved[k] for k in info} == info


def test_console_output_lines_precede_the_workspace_build_in_a_fixed_order(launched):
    rec = launched("--run-name", "out", "--effort", "low")
    lines = rec.out.splitlines()
    assert lines[0] == "Setting up workspace: out"
    assert re.fullmatch(
        r"Agent ID: out   \(effort=low, max_thinking_tokens=2000, walltime=\S+\)", lines[1]
    )
    assert lines[2] == f"Workspace: {rec.workspace}"
    assert lines[3] == "Running claude agent (sandbox=auto)..."


# ── failures before any metadata is built ──────────────────────────────────


def test_missing_task_exits_before_generation_build_or_write(launched):
    with pytest.raises(SystemExit) as exc:
        launched("--run-name", "x", with_task=False)  # no --task at all
    assert exc.value.code == 2
    assert launched.rec.events == []


# ── Direct tests of the extracted helper: _build_run_info ───────────────────


def _ns(**kw):
    import argparse

    base = {"run_name": "", "runner": "claude", "model": "", "sandbox": None}
    return argparse.Namespace(**{**base, **kw})


def test_helper_named_run_returns_info_and_run_name_without_generating(monkeypatch):
    def boom(**_kw):
        raise AssertionError("generate_run_info must not be called for a named run")

    monkeypatch.setattr(launch, "generate_run_info", boom)
    info, run_dir = launch._build_run_info(
        _ns(run_name="nm", model="mm", sandbox="podman"), {}, "simple", "T", "P", "high", 31999
    )
    assert run_dir == "nm"
    assert list(info) == [
        "agent_id",
        "run_dir",
        "task_id",
        "paper_ref",
        "agent",
        "runner",
        "model",
        "effort",
        "max_thinking_tokens",
        "sandbox",
    ]
    assert info["model"] == "mm" and info["sandbox"] == "podman"
    assert (info["effort"], info["max_thinking_tokens"]) == ("high", 31999)


def test_helper_generated_run_updates_the_generators_dict_in_place(monkeypatch):
    generated = {"agent_id": "A", "run_dir": "g/r", "task_id": "T"}
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return generated

    monkeypatch.setattr(launch, "generate_run_info", fake)
    info, run_dir = launch._build_run_info(_ns(), {}, "simple", "T", "P", "medium", 8000)
    assert info is generated and run_dir == "g/r"
    assert calls == [
        {
            "task_id": "T",
            "agent_name": "simple",
            "runner_name": "claude",
            "model_name": "claude",
            "paper_ref": "P",
        }
    ]
    assert info == {
        "agent_id": "A",
        "run_dir": "g/r",
        "task_id": "T",
        "effort": "medium",
        "max_thinking_tokens": 8000,
        "sandbox": "auto",
    }


def test_helper_tool_policy_is_added_only_when_truthy_and_cfg_is_not_mutated():
    cfg = {"tool_policy": {"disabled": ["delphes"]}}
    info, _ = launch._build_run_info(_ns(run_name="r"), cfg, "simple", "T", "P", "low", 2000)
    assert info["tool_policy"] == {"disabled": ["delphes"]}
    assert cfg == {"tool_policy": {"disabled": ["delphes"]}}
    for empty in ({}, {"tool_policy": {}}, {"tool_policy": None}):
        info, _ = launch._build_run_info(_ns(run_name="r"), empty, "simple", "T", "P", "low", 2000)
        assert "tool_policy" not in info


def test_helper_accepts_none_effort_values_as_recorded():
    info, _ = launch._build_run_info(_ns(run_name="r"), {}, "simple", "T", "P", None, None)
    assert (info["effort"], info["max_thinking_tokens"]) == (None, None)
