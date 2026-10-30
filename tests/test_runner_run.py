"""Characterization of DeclarativeRunner.run with a fake process and fake timers.

Pins the *current* behavior of the subprocess lifecycle in
agent_runtime/runner_spec.py: the exact `Popen` arguments, stdin handling for
flag vs stdin prompts, how stdout is decoded / stripped / parsed / written (with a
flush per line), when the post-result watchdog is armed and what it does, the
order of cleanup and the post-run hook, the exit-code rules and which exceptions
skip or keep the cleanup, so the extraction of output consumption into
`_consume_runner_output` is shown to be behavior-preserving. Most tests drive
`DeclarativeRunner.run`; the last section tests `_consume_runner_output` directly.

Entirely offline and deterministic: `subprocess.Popen`, `os.getpgid`,
`threading.Timer`, `_kill_process_group` and `_arm_walltime_watchdog` are replaced
by recorders, so no process is spawned, no timer runs, nothing sleeps and no
signal is sent. Output files live under tmp_path.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

# `agent_runtime.runners` must be imported before `agent_runtime.runner_spec` (circular import).
from agent_runtime.runners import LaunchPrep  # isort: skip  # noqa: F401
from agent_runtime import runner_spec  # isort: skip
from agent_runtime.runner_spec import DeclarativeRunner, RunnerSpec  # isort: skip

_PID = 4242
_PGID = 9090
_RESULT = '{"type":"result","result":"done"}'


class _FakeStdin:
    def __init__(self, events, exc=None):
        self.events = events
        self.exc = exc
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data):
        self.events.append("stdin_write")
        if self.exc:
            raise self.exc
        self.writes.append(data)

    def close(self):
        self.events.append("stdin_close")
        self.closed = True


class _FakeProc:
    def __init__(self, events, lines, rc=0, poll=None, stdin_exc=None):
        self.pid = _PID
        self.events = events
        self.stdout = lines
        self.stdin = _FakeStdin(events, stdin_exc)
        self._rc = rc
        self._poll = poll
        self.wait_calls = 0

    def poll(self):
        return self._poll

    def wait(self):
        self.events.append("wait")
        self.wait_calls += 1
        return self._rc


class _FakeTimer:
    instances: list["_FakeTimer"] = []
    fire_on_start = False
    events: list[str] = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True
        _FakeTimer.events.append("watchdog_start")
        if _FakeTimer.fire_on_start:
            self.function()

    def cancel(self):
        self.cancelled = True
        _FakeTimer.events.append("cancel_watchdog")


class _WalltimeTimer:
    def __init__(self, events):
        self.events = events
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self.events.append("cancel_walltime")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Install the fakes and return a `go(...)` driver plus a record of everything."""
    events: list[str] = []
    rec = type("Rec", (), {})()
    rec.events = events
    rec.popen_calls = []
    rec.kill_calls = []
    rec.arm_calls = []
    rec.walltime_timer = _WalltimeTimer(events)
    rec.arm_returns_timer = True
    rec.post_hook_calls = []
    rec.getpgid_exc = None
    rec.popen_exc = None
    rec.proc = None

    _FakeTimer.instances = []
    _FakeTimer.fire_on_start = False
    _FakeTimer.events = events

    def fake_popen(argv, **kwargs):
        events.append("popen")
        rec.popen_calls.append((argv, kwargs))
        if rec.popen_exc:
            raise rec.popen_exc
        return rec.proc

    def fake_getpgid(pid):
        events.append("getpgid")
        assert pid == _PID
        if rec.getpgid_exc:
            raise rec.getpgid_exc
        return _PGID

    def fake_kill(pgid, *a, **k):
        events.append("kill_group")
        rec.kill_calls.append(pgid)

    def fake_arm(pgid, walltime_s):
        events.append("arm_walltime")
        rec.arm_calls.append((pgid, walltime_s))
        return rec.walltime_timer if rec.arm_returns_timer else None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(os, "getpgid", fake_getpgid)
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(runner_spec, "_kill_process_group", fake_kill)
    monkeypatch.setattr(runner_spec, "_arm_walltime_watchdog", fake_arm)

    out = tmp_path / "session.jsonl"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    rec.out = out
    rec.sandbox = sandbox

    def go(
        *,
        lines=(),
        rc=0,
        poll=None,
        stdin_exc=None,
        parser=None,
        cmd=None,
        env=None,
        prompt="PROMPT",
        walltime_s=None,
        output_file=None,
        **spec_kw,
    ):
        spec = RunnerSpec(name="t", binary="tbin", **spec_kw)
        if parser is not None:
            monkeypatch.setitem(runner_spec.STREAM_PARSERS, spec.stream_format, parser)
        encoded = [x if isinstance(x, bytes) else x.encode() for x in lines]
        rec.proc = _FakeProc(events, encoded, rc=rc, poll=poll, stdin_exc=stdin_exc)
        rec.cmd = cmd if cmd is not None else ["wrapped", "cmd"]
        rec.env = env if env is not None else {"K": "v"}
        rec.result = DeclarativeRunner(spec).run(
            rec.cmd,
            prompt,
            rec.sandbox,
            rec.env,
            output_file if output_file is not None else out,
            walltime_s=walltime_s,
        )
        return rec

    go.rec = rec
    go.monkeypatch = monkeypatch
    return go


def _register_marker_hook(world, name="h"):
    """Register a post-run hook that records itself in the event log."""
    events = world.rec.events
    world.monkeypatch.setitem(
        runner_spec.POST_RUN_HOOKS, name, lambda sandbox: events.append("post_hook")
    )


def _passthrough_parser(log=None):
    def factory():
        def parse(line):
            if log is not None:
                log.append(line)
            return [line] if line else []

        return parse

    return factory


def _text(rec) -> str:
    return rec.out.read_text()


# ── Popen arguments ─────────────────────────────────────────────────────────


def test_flag_prompt_popen_gets_devnull_stdin_merged_stderr_and_a_new_session(world):
    rec = world()
    ((argv, kwargs),) = rec.popen_calls
    assert argv is rec.cmd
    assert kwargs == {
        "cwd": rec.sandbox,
        "env": {"K": "v"},
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "start_new_session": True,
        "stdin": subprocess.DEVNULL,
    }
    assert rec.proc.stdin.writes == [] and not rec.proc.stdin.closed


def test_env_is_copied_before_launch_and_the_callers_dict_is_untouched(world):
    env = {"K": "v"}
    rec = world(env=env)
    passed = rec.popen_calls[0][1]["env"]
    assert passed == env and passed is not env


def test_stdin_prompt_popen_gets_a_stdin_pipe(world):
    rec = world(prompt_via="stdin")
    assert rec.popen_calls[0][1]["stdin"] == subprocess.PIPE


# ── stdin prompt delivery ───────────────────────────────────────────────────


def test_stdin_prompt_is_utf8_encoded_written_once_and_closed_before_reading_output(world):
    rec = world(prompt_via="stdin", prompt="héllo ∑", lines=["x"])
    assert rec.proc.stdin.writes == ["héllo ∑".encode()]
    assert rec.proc.stdin.closed
    assert rec.events.index("stdin_close") < rec.events.index("wait")
    assert rec.events[:5] == ["popen", "getpgid", "arm_walltime", "stdin_write", "stdin_close"]


def test_flag_prompt_never_touches_stdin(world):
    rec = world(lines=["x"])
    assert "stdin_write" not in rec.events and "stdin_close" not in rec.events


# ── pgid + walltime arming ──────────────────────────────────────────────────


def test_pgid_comes_from_the_new_process_and_walltime_is_armed_right_after(world):
    rec = world(walltime_s=123.0)
    assert rec.events[:3] == ["popen", "getpgid", "arm_walltime"]
    assert rec.arm_calls == [(_PGID, 123.0)]


def test_walltime_none_is_passed_through_to_the_arming_helper(world):
    assert world().arm_calls == [(_PGID, None)]


# ── stdout decoding and writing ─────────────────────────────────────────────


def test_lines_are_decoded_rstripped_and_written_with_a_single_newline(world):
    seen: list[str] = []
    rec = world(
        lines=["  keep leading  \r\n", "plain\n", "tab\t \n", "last-no-newline"],
        parser=_passthrough_parser(seen),
    )
    assert seen == ["  keep leading", "plain", "tab", "last-no-newline"]
    assert _text(rec) == "  keep leading\nplain\ntab\nlast-no-newline\n"
    assert b"\r" not in rec.out.read_bytes()


def test_invalid_utf8_bytes_are_replaced_not_raised(world):
    rec = world(lines=[b"ok \xff\xfe bytes\n"], parser=_passthrough_parser())
    assert _text(rec) == "ok �� bytes\n"


def test_parser_output_decides_what_is_written_empty_means_skip_many_means_many(world):
    def factory():
        def parse(line):
            if line == "skip":
                return []
            if line == "many":
                return ["a", "b"]
            return [line.upper()]

        return parse

    rec = world(lines=["one\n", "skip\n", "many\n", "two\n"], parser=factory)
    assert _text(rec) == "ONE\na\nb\nTWO\n"


def test_output_is_flushed_after_every_line_even_when_the_parser_returns_nothing(world):
    snapshots: list[bytes] = []
    out = world.rec.out

    def factory():
        def parse(line):
            snapshots.append(out.read_bytes() if out.exists() else b"")
            return [] if line == "quiet" else [line]

        return parse

    world(lines=["first\n", "quiet\n", "third\n", "fourth\n"], parser=factory)
    # Each call sees everything written (and flushed) for the previous lines.
    assert snapshots == [b"", b"first\n", b"first\n", b"first\nthird\n"]


def test_parser_factory_is_called_once_per_run_for_fresh_state(world):
    calls = []

    def factory():
        calls.append("made")
        return lambda line: [line]

    world(lines=["a\n", "b\n"], parser=factory)
    world(lines=["c\n"], parser=factory)
    assert calls == ["made", "made"]


def test_output_file_is_truncated_and_created_and_may_be_a_str_path(world, tmp_path):
    out = tmp_path / "existing.jsonl"
    out.write_text("OLD CONTENT\n")
    world(lines=["new\n"], output_file=str(out), parser=_passthrough_parser())
    assert out.read_text() == "new\n"


def test_raw_stream_format_writes_non_empty_lines_verbatim_and_skips_blank_ones(world):
    rec = world(lines=["a\n", "\n", "  \n", "b c\n"], stream_format="raw")
    assert _text(rec) == "a\nb c\n"


def test_stream_format_selects_the_parser_from_the_registry(world):
    seen = []
    rec = world(lines=["x\n"], stream_format="aider_text", parser=_passthrough_parser(seen))
    assert seen == ["x"] and _text(rec) == "x\n"


# ── post-result watchdog ────────────────────────────────────────────────────


def test_watchdog_is_armed_once_at_the_first_result_line_with_the_grace_period(world):
    rec = world(
        lines=["noise\n", _RESULT + "\n", _RESULT + "\n", "after\n"],
        post_result_grace_s=15.0,
        parser=_passthrough_parser(),
    )
    (timer,) = _FakeTimer.instances
    assert timer.interval == 15.0 and timer.daemon is True and timer.started
    assert rec.events.index("watchdog_start") < rec.events.index("wait")
    assert timer.cancelled  # cancelled in the cleanup


def test_watchdog_is_armed_after_the_result_line_was_written_and_flushed(world):
    out = world.rec.out
    at_start: list[bytes] = []
    original_start = _FakeTimer.start

    def spying_start(self):
        at_start.append(out.read_bytes())
        original_start(self)

    world.monkeypatch.setattr(_FakeTimer, "start", spying_start)
    world(lines=[_RESULT + "\n"], post_result_grace_s=5.0, parser=_passthrough_parser())
    assert at_start == [(_RESULT + "\n").encode()]


@pytest.mark.parametrize("variant", ['{"type": "result"}', '{"type":"result"}'])
def test_both_result_event_spellings_arm_the_watchdog(world, variant):
    world(lines=[variant + "\n"], post_result_grace_s=3.0, parser=_passthrough_parser())
    assert len(_FakeTimer.instances) == 1


def test_result_detection_is_a_substring_match_on_the_stripped_line(world):
    # Any line merely containing the marker arms the watchdog, JSON or not.
    world(lines=['note: {"type":"result"} appears inside text  \n'], post_result_grace_s=2.0)
    assert len(_FakeTimer.instances) == 1


def test_no_watchdog_without_a_grace_period_or_without_a_result_line(world):
    world(lines=[_RESULT + "\n"], post_result_grace_s=0.0, parser=_passthrough_parser())
    assert _FakeTimer.instances == []
    world(lines=["not a result\n"], post_result_grace_s=9.0, parser=_passthrough_parser())
    assert _FakeTimer.instances == []


def test_a_stalled_session_is_terminated_and_counts_as_success(world, capsys):
    _FakeTimer.fire_on_start = True
    rec = world(
        lines=[_RESULT + "\n"],
        rc=-15,
        poll=None,
        post_result_grace_s=15.0,
        parser=_passthrough_parser(),
    )
    assert rec.result is None  # killed after the result: no CalledProcessError
    assert capsys.readouterr().out == (
        "\n[runner] t stalled 15s after result event; terminating session.\n"
    )
    assert rec.kill_calls == [_PGID, _PGID]  # once by the watchdog, once in the cleanup


def test_watchdog_does_nothing_when_the_process_already_exited(world, capsys):
    _FakeTimer.fire_on_start = True
    with pytest.raises(subprocess.CalledProcessError):
        world(
            lines=[_RESULT + "\n"],
            rc=3,
            poll=0,
            post_result_grace_s=15.0,
            parser=_passthrough_parser(),
        )
    assert capsys.readouterr().out == ""
    assert world.rec.kill_calls == [_PGID]  # only the unconditional cleanup kill


# ── cleanup ordering + post-run hook ────────────────────────────────────────


def test_cleanup_order_cancel_timers_then_kill_group_then_post_hook(world):
    hook_calls = []

    def hook(sandbox):
        world.rec.events.append("post_hook")
        hook_calls.append(sandbox)

    world.monkeypatch.setitem(runner_spec.POST_RUN_HOOKS, "h", hook)
    rec = world(
        lines=[_RESULT + "\n"],
        post_result_grace_s=15.0,
        post_run_hook="h",
        parser=_passthrough_parser(),
    )
    tail = rec.events[rec.events.index("wait") :]
    assert tail == ["wait", "cancel_walltime", "cancel_watchdog", "kill_group", "post_hook"]
    assert hook_calls == [Path(rec.sandbox)]
    assert rec.walltime_timer.cancelled


def test_cleanup_skips_missing_timers_and_unregistered_hook_names(world):
    world.rec.arm_returns_timer = False
    rec = world(post_run_hook="never-registered")
    assert rec.events[rec.events.index("wait") :] == ["wait", "kill_group"]


def test_post_hook_receives_a_path_even_for_a_str_sandbox(world):
    received = []
    world.monkeypatch.setitem(runner_spec.POST_RUN_HOOKS, "h", received.append)
    spec = RunnerSpec(name="t", binary="tbin", post_run_hook="h")
    world.rec.proc = _FakeProc(world.rec.events, [])
    DeclarativeRunner(spec).run(["c"], "p", str(world.rec.sandbox), {}, world.rec.out)
    assert received == [Path(world.rec.sandbox)] and isinstance(received[0], Path)


# ── exit code handling ──────────────────────────────────────────────────────


def test_zero_exit_returns_none_without_raising(world):
    assert world(rc=0).result is None


@pytest.mark.parametrize("rc", [1, 2, 127, -9])
def test_nonzero_exit_raises_called_process_error_with_rc_and_the_command(world, rc):
    with pytest.raises(subprocess.CalledProcessError) as info:
        world(rc=rc)
    assert info.value.returncode == rc
    assert info.value.cmd is world.rec.cmd


def test_nonzero_exit_still_runs_the_full_cleanup_before_raising(world):
    _register_marker_hook(world)
    with pytest.raises(subprocess.CalledProcessError):
        world(rc=1, post_run_hook="h")
    assert world.rec.events[-2:] == ["kill_group", "post_hook"]


# ── exceptions ──────────────────────────────────────────────────────────────


def test_popen_failure_propagates_before_any_cleanup(world):
    world.rec.popen_exc = FileNotFoundError("no such cli")
    with pytest.raises(FileNotFoundError):
        world()
    assert world.rec.events == ["popen"]
    assert world.rec.kill_calls == []


def test_getpgid_failure_propagates_before_any_cleanup(world):
    world.rec.getpgid_exc = ProcessLookupError()
    with pytest.raises(ProcessLookupError):
        world()
    assert world.rec.events == ["popen", "getpgid"]
    assert world.rec.kill_calls == []


@pytest.mark.parametrize("exc", [RuntimeError("parser boom"), KeyboardInterrupt()])
def test_errors_while_streaming_propagate_and_still_clean_up(world, exc):
    def factory():
        def parse(line):
            raise exc

        return parse

    _register_marker_hook(world)
    with pytest.raises(type(exc)) as info:
        world(lines=["x\n"], parser=factory, post_run_hook="h")
    assert info.value is exc
    assert "wait" not in world.rec.events  # rc is never read
    assert world.rec.events[-3:] == ["cancel_walltime", "kill_group", "post_hook"]


def test_stdin_write_failure_propagates_and_cleans_up_without_reading_output(world):
    with pytest.raises(BrokenPipeError):
        world(prompt_via="stdin", stdin_exc=BrokenPipeError(), lines=["x\n"])
    assert "wait" not in world.rec.events
    assert world.rec.events[-2:] == ["cancel_walltime", "kill_group"]
    assert not world.rec.out.exists()  # the output file is opened only after stdin delivery


def test_unwritable_output_path_propagates_after_the_prompt_was_delivered(world, tmp_path):
    bad = tmp_path / "missing-dir" / "session.jsonl"
    with pytest.raises(FileNotFoundError):
        world(prompt_via="stdin", output_file=bad, lines=["x\n"])
    assert world.rec.proc.stdin.closed
    assert world.rec.events[-2:] == ["cancel_walltime", "kill_group"]


# ── ordering constraints: parser factory before the try, masking in teardown ─


def test_parser_factory_failure_skips_all_cleanup_and_prompt_delivery(world):
    def failing_factory():
        raise RuntimeError("factory boom")

    _register_marker_hook(world)
    with pytest.raises(RuntimeError, match="factory boom"):
        world(
            prompt_via="stdin",
            lines=["x\n"],
            parser=failing_factory,
            walltime_s=60.0,
            post_run_hook="h",
        )
    # Popen and the walltime timer already exist, but the factory runs before the
    # try/finally: no cancel, no group kill, no post-run hook, no stdin, no output file.
    assert world.rec.events == ["popen", "getpgid", "arm_walltime"]
    assert world.rec.kill_calls == []
    assert not world.rec.walltime_timer.cancelled
    assert world.rec.proc.stdin.writes == [] and not world.rec.proc.stdin.closed
    assert not world.rec.out.exists()


def test_a_failing_post_hook_masks_the_streaming_error_but_keeps_it_as_context(world):
    stream_exc = RuntimeError("parser boom")

    def factory():
        def parse(line):
            raise stream_exc

        return parse

    def bad_hook(sandbox):
        raise OSError("hook boom")

    world.monkeypatch.setitem(runner_spec.POST_RUN_HOOKS, "h", bad_hook)
    with pytest.raises(OSError, match="hook boom") as info:
        world(lines=["x\n"], parser=factory, post_run_hook="h")
    assert info.value.__context__ is stream_exc
    assert world.rec.kill_calls == [_PGID]  # the kill happened before the hook raised


def test_a_failing_timer_cancel_skips_the_kill_and_the_post_hook(world):
    def bad_cancel():
        raise RuntimeError("cancel boom")

    world.rec.walltime_timer.cancel = bad_cancel
    _register_marker_hook(world)
    with pytest.raises(RuntimeError, match="cancel boom"):
        world(post_run_hook="h", walltime_s=5.0)
    assert world.rec.kill_calls == []
    assert "post_hook" not in world.rec.events


def test_a_failing_group_kill_masks_the_exit_error_and_skips_the_post_hook(world):
    def bad_kill(pgid, *a, **k):
        raise PermissionError("kill boom")

    world.monkeypatch.setattr(runner_spec, "_kill_process_group", bad_kill)
    _register_marker_hook(world)
    with pytest.raises(PermissionError, match="kill boom"):
        world(rc=1, post_run_hook="h")  # would otherwise raise CalledProcessError
    assert "post_hook" not in world.rec.events


def test_stream_steps_happen_in_the_documented_order(world):
    rec = world(
        prompt_via="stdin",
        lines=[_RESULT + "\n"],
        post_result_grace_s=7.0,
        walltime_s=30.0,
        parser=_passthrough_parser(),
    )
    assert rec.events == [
        "popen",
        "getpgid",
        "arm_walltime",
        "stdin_write",
        "stdin_close",
        "watchdog_start",  # armed after the line was written and flushed
        "wait",
        "cancel_walltime",
        "cancel_watchdog",
        "kill_group",
    ]


# ── Direct tests of the extracted helper: _consume_runner_output ────────────


class _StdoutOnly:
    """A process stand-in exposing only stdout; any other attribute use fails."""

    def __init__(self, lines):
        self.stdout = iter(lines)


def _consume(lines, parser, out, on_line=lambda line: None):
    runner_spec._consume_runner_output(_StdoutOnly(lines), parser, out, on_line)


def test_consume_decodes_utf8_with_replacement_and_rstrips_each_raw_line(tmp_path):
    seen = []
    out = tmp_path / "o.jsonl"
    _consume(
        [b"  lead kept  \r\n", b"ok \xff bytes\n", b"\t\n", "é\n".encode()],
        lambda line: seen.append(line) or [line],
        out,
    )
    assert seen == ["  lead kept", "ok � bytes", "", "é"]
    assert out.read_bytes() == "  lead kept\nok � bytes\n\né\n".encode()


def test_consume_writes_each_parser_item_with_a_newline_and_skips_empty_results(tmp_path):
    out = tmp_path / "o.jsonl"
    _consume(
        [b"a\n", b"skip\n", b"many\n"],
        lambda line: {"skip": [], "many": ["x", "y"]}.get(line, [line.upper()]),
        out,
    )
    assert out.read_bytes() == b"A\nx\ny\n"


def test_consume_flushes_after_every_raw_line_and_notifies_after_the_flush(tmp_path):
    out = tmp_path / "o.jsonl"
    snapshots: list[tuple[str, bytes]] = []
    _consume(
        [b"first\n", b"quiet\n", b"third\n"],
        lambda line: [] if line == "quiet" else [line],
        out,
        on_line=lambda line: snapshots.append((line, out.read_bytes())),
    )
    # The callback sees the current line's output already on disk, including when the
    # parser wrote nothing, and receives the stripped raw line (not the parser output).
    assert snapshots == [
        ("first", b"first\n"),
        ("quiet", b"first\n"),
        ("third", b"first\nthird\n"),
    ]


def test_consume_per_line_order_is_parser_then_write_then_callback(tmp_path):
    out = tmp_path / "o.jsonl"
    order: list[str] = []

    def parser(line):
        order.append(f"parse:{line}")
        return [line]

    _consume([b"a\n", b"b\n"], parser, out, on_line=lambda line: order.append(f"cb:{line}"))
    assert order == ["parse:a", "cb:a", "parse:b", "cb:b"]


def test_consume_notifies_for_blank_lines_too_and_returns_none(tmp_path):
    out = tmp_path / "o.jsonl"
    called: list[str] = []
    result = runner_spec._consume_runner_output(
        _StdoutOnly([b"\n", b"x\n"]), lambda line: [line] if line else [], out, called.append
    )
    assert result is None and called == ["", "x"]
    assert out.read_bytes() == b"x\n"


def test_consume_truncates_existing_output_creates_missing_and_accepts_str_paths(tmp_path):
    old = tmp_path / "old.jsonl"
    old.write_text("OLD\n")
    _consume([b"new\n"], lambda line: [line], str(old))
    assert old.read_text() == "new\n"
    empty = tmp_path / "empty.jsonl"
    _consume([], lambda line: [line], empty)
    assert empty.exists() and empty.read_bytes() == b""


def test_parser_error_propagates_keeps_prior_output_and_skips_the_callback(tmp_path):
    out = tmp_path / "o.jsonl"
    called: list[str] = []

    def parser(line):
        if line == "bad":
            raise RuntimeError("parser boom")
        return [line]

    with pytest.raises(RuntimeError, match="parser boom"):
        _consume([b"ok\n", b"bad\n", b"never\n"], parser, out, called.append)
    assert called == ["ok"]
    assert out.read_bytes() == b"ok\n"  # earlier lines were flushed; the file is closed


def test_callback_error_propagates_after_its_line_was_written_and_stops_consumption(tmp_path):
    out = tmp_path / "o.jsonl"
    seen: list[str] = []

    def on_line(line):
        if line == "two":
            raise ValueError("callback boom")

    def parser(line):
        seen.append(line)
        return [line]

    with pytest.raises(ValueError, match="callback boom"):
        _consume([b"one\n", b"two\n", b"three\n"], parser, out, on_line)
    assert seen == ["one", "two"]  # "three" is never parsed
    assert out.read_bytes() == b"one\ntwo\n"


def test_unopenable_output_path_fails_before_stdout_is_consumed(tmp_path):
    proc = _StdoutOnly([b"x\n"])
    with pytest.raises(FileNotFoundError):
        runner_spec._consume_runner_output(
            proc, lambda line: [line], tmp_path / "no-dir" / "o.jsonl", lambda line: None
        )
    assert list(proc.stdout) == [b"x\n"]  # untouched


def test_missing_stdout_creates_the_file_then_fails_an_assertion(tmp_path):
    out = tmp_path / "o.jsonl"
    proc = type("P", (), {"stdout": None})()
    with pytest.raises(AssertionError):
        runner_spec._consume_runner_output(proc, lambda line: [line], out, lambda line: None)
    assert out.exists() and out.read_bytes() == b""
