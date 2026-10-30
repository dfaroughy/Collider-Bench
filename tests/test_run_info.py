"""Characterization of agent_runtime.run_info: write_run_info / finalize_run_info.

Pins the *current* behavior of the run_info.json read/merge path (timestamps,
final_score shape, usage aggregation across session logs, cost_priced rules,
tolerance of a malformed prior file, and the best-effort exception path) so an
extraction of the usage aggregation can be shown to be behavior-preserving.

Most tests use the public functions; four exercise the extracted helper
directly. `parse_usage` (the per-log parser) is replaced via monkeypatch on
the `agent_runtime.run_info` module namespace, so no real session log,
network or pricing table is involved. Offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_runtime import run_info

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ZERO_USAGE = {
    "api_cost_usd": 0.0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "tokens_total_billed": 0,
    "n_turns": 0,
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _seed(tmp_path: Path, **info) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "run_info.json").write_text(json.dumps(info))
    return tmp_path


@pytest.fixture
def fake_usage(monkeypatch):
    """Install a fake parse_usage keyed by log file name; record every call."""
    table: dict[str, dict | None] = {}
    calls: list[tuple[str, str, Path]] = []

    def parse_usage(runner, model, path):
        calls.append((runner, model, path))
        return table.get(Path(path).name)

    monkeypatch.setattr(run_info, "parse_usage", parse_usage)
    parse_usage.table = table
    parse_usage.calls = calls
    return parse_usage


# ── write_run_info ──────────────────────────────────────────────────────────


def test_write_creates_dir_returns_path_and_adds_started_at(tmp_path):
    target = tmp_path / "a" / "b"
    out = run_info.write_run_info(target, {"task_id": "t"})
    assert out == target / "run_info.json"
    data = _read(out)
    assert data["task_id"] == "t"
    assert _TS.match(data["started_at"])


def test_write_preserves_explicit_started_at(tmp_path):
    out = run_info.write_run_info(tmp_path, {"started_at": "2020-01-02T03:04:05Z"})
    assert _read(out)["started_at"] == "2020-01-02T03:04:05Z"


@pytest.mark.parametrize("falsy", [None, ""])
def test_write_replaces_falsy_started_at(tmp_path, falsy):
    out = run_info.write_run_info(tmp_path, {"started_at": falsy})
    assert _TS.match(_read(out)["started_at"])


def test_write_does_not_mutate_input_dict(tmp_path):
    info = {"task_id": "t"}
    run_info.write_run_info(tmp_path, info)
    assert info == {"task_id": "t"}


# ── finalize_run_info: basics ───────────────────────────────────────────────


def test_finalize_without_prior_file_creates_minimal_record(tmp_path, fake_usage):
    out = run_info.finalize_run_info(tmp_path, exit_code=1)
    assert out == tmp_path / "run_info.json"
    data = _read(out)
    assert set(data) == {"ended_at", "exit_code"}
    assert data["exit_code"] == 1
    assert _TS.match(data["ended_at"])
    assert fake_usage.calls == []


def test_finalize_merges_into_prior_info_and_coerces_exit_code(tmp_path, fake_usage):
    _seed(tmp_path, task_id="t", started_at="2020-01-01T00:00:00Z")
    run_info.finalize_run_info(tmp_path, exit_code=True)
    data = _read(tmp_path / "run_info.json")
    assert data["task_id"] == "t"
    assert data["started_at"] == "2020-01-01T00:00:00Z"
    assert data["exit_code"] == 1 and isinstance(data["exit_code"], int)


def test_finalize_duration_present_only_with_started_at_and_rounded(
    tmp_path, monkeypatch, fake_usage
):
    monkeypatch.setattr(run_info.time, "time", lambda: 1012.5)
    run_info.finalize_run_info(tmp_path, exit_code=0, started_at=1000.0)
    assert _read(tmp_path / "run_info.json")["duration_wall_s"] == 12.5

    other = tmp_path / "other"
    other.mkdir()  # finalize_run_info does not create the directory
    run_info.finalize_run_info(other, exit_code=0)
    assert "duration_wall_s" not in _read(other / "run_info.json")


# ── finalize_run_info: final_score ──────────────────────────────────────────


def test_final_score_shape_maps_overall_keys(tmp_path, fake_usage):
    scores = {
        "overall_shape": 0.1,
        "overall_normalization": 0.2,
        "overall_combined": 0.3,
        "n_filled": 4,
        "n_bins": 5,
        "shape": {"ignored": True},
    }
    run_info.finalize_run_info(tmp_path, exit_code=0, scores=scores)
    assert _read(tmp_path / "run_info.json")["final_score"] == {
        "shape": 0.1,
        "normalization": 0.2,
        "combined": 0.3,
        "n_filled": 4,
        "n_bins": 5,
    }


def test_final_score_missing_keys_are_null_and_none_scores_omits_block(tmp_path, fake_usage):
    run_info.finalize_run_info(tmp_path, exit_code=0, scores={"n_bins": 7})
    assert _read(tmp_path / "run_info.json")["final_score"] == {
        "shape": None,
        "normalization": None,
        "combined": None,
        "n_filled": None,
        "n_bins": 7,
    }
    other = tmp_path / "other"
    other.mkdir()  # finalize_run_info does not create the directory
    run_info.finalize_run_info(other, exit_code=0, scores=None)
    assert "final_score" not in _read(other / "run_info.json")


# ── finalize_run_info: usage aggregation ────────────────────────────────────


def test_no_logs_means_no_usage_key(tmp_path, fake_usage):
    run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[])
    assert "usage" not in _read(tmp_path / "run_info.json")
    assert fake_usage.calls == []


def test_falsy_log_entries_are_skipped_and_empty_parse_is_ignored(tmp_path, fake_usage):
    fake_usage.table["empty.jsonl"] = {}
    run_info.finalize_run_info(
        tmp_path, exit_code=0, session_logs=[None, "", tmp_path / "empty.jsonl", tmp_path / "x"]
    )
    assert "usage" not in _read(tmp_path / "run_info.json")
    assert [c[2].name for c in fake_usage.calls] == ["empty.jsonl", "x"]


def test_parse_usage_receives_runner_model_from_prior_info_and_path(tmp_path, fake_usage):
    _seed(tmp_path, runner="claude", model="opus-x")
    log = tmp_path / "s.jsonl"
    run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[str(log)])
    assert fake_usage.calls == [("claude", "opus-x", log)]


def test_parse_usage_defaults_runner_and_model_to_empty_strings(tmp_path, fake_usage):
    run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[tmp_path / "s.jsonl"])
    assert [c[:2] for c in fake_usage.calls] == [("", "")]


def test_multiple_logs_are_summed_and_cost_rounded_to_six_places(tmp_path, fake_usage):
    _seed(tmp_path, runner="claude", model="m")
    fake_usage.table["a.jsonl"] = {
        "api_cost_usd": 0.1234564,
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 1,
        "cache_creation_tokens": 2,
        "tokens_total_billed": 18,
        "n_turns": 3,
    }
    fake_usage.table["b.jsonl"] = {
        "api_cost_usd": 0.2000003,
        "input_tokens": 7,
        "output_tokens": None,  # None counts as 0
        "n_turns": 1,  # other keys absent -> 0
    }
    run_info.finalize_run_info(
        tmp_path, exit_code=0, session_logs=[tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    )
    assert _read(tmp_path / "run_info.json")["usage"] == {
        "api_cost_usd": 0.323457,
        "input_tokens": 17,
        "output_tokens": 5,
        "cache_read_tokens": 1,
        "cache_creation_tokens": 2,
        "tokens_total_billed": 18,
        "n_turns": 4,
    }


def test_logs_with_no_parsed_usage_are_skipped_among_valid_ones(tmp_path, fake_usage):
    fake_usage.table["a.jsonl"] = {"input_tokens": 3}
    run_info.finalize_run_info(
        tmp_path, exit_code=0, session_logs=[tmp_path / "missing.jsonl", tmp_path / "a.jsonl"]
    )
    usage = _read(tmp_path / "run_info.json")["usage"]
    assert usage == {**_ZERO_USAGE, "input_tokens": 3}


def test_unknown_usage_keys_are_dropped(tmp_path, fake_usage):
    fake_usage.table["a.jsonl"] = {"input_tokens": 1, "model_extra": "x"}
    run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[tmp_path / "a.jsonl"])
    assert set(_read(tmp_path / "run_info.json")["usage"]) == set(_ZERO_USAGE)


# ── cost_priced ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("runner", ["codex", "gemini", "forge"])
def test_priced_runners_report_cost_priced_true_when_all_logs_priced(tmp_path, fake_usage, runner):
    _seed(tmp_path, runner=runner)
    fake_usage.table["a.jsonl"] = {"input_tokens": 1}  # key absent -> treated as priced
    fake_usage.table["b.jsonl"] = {"input_tokens": 1, "cost_priced": True}
    run_info.finalize_run_info(
        tmp_path, exit_code=0, session_logs=[tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    )
    assert _read(tmp_path / "run_info.json")["usage"]["cost_priced"] is True


@pytest.mark.parametrize("runner", ["codex", "gemini", "forge"])
def test_priced_runners_report_cost_priced_false_if_any_log_unpriced(tmp_path, fake_usage, runner):
    _seed(tmp_path, runner=runner)
    fake_usage.table["a.jsonl"] = {"input_tokens": 1, "cost_priced": True}
    fake_usage.table["b.jsonl"] = {"input_tokens": 1, "cost_priced": False}
    run_info.finalize_run_info(
        tmp_path, exit_code=0, session_logs=[tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    )
    assert _read(tmp_path / "run_info.json")["usage"]["cost_priced"] is False


@pytest.mark.parametrize("runner", ["claude", "", "aider", "deepseek"])
def test_other_runners_never_get_a_cost_priced_key(tmp_path, fake_usage, runner):
    _seed(tmp_path, runner=runner)
    fake_usage.table["a.jsonl"] = {"input_tokens": 1, "cost_priced": False}
    run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[tmp_path / "a.jsonl"])
    assert "cost_priced" not in _read(tmp_path / "run_info.json")["usage"]


# ── malformed prior file + best-effort exception path ───────────────────────


def test_malformed_prior_json_is_discarded_and_record_rebuilt(tmp_path, fake_usage):
    (tmp_path / "run_info.json").write_text("{not json")
    out = run_info.finalize_run_info(tmp_path, exit_code=1)
    assert set(_read(out)) == {"ended_at", "exit_code"}


def test_parse_usage_exception_is_swallowed_with_stderr_and_file_untouched(
    tmp_path, monkeypatch, capsys
):
    _seed(tmp_path, task_id="t")
    before = (tmp_path / "run_info.json").read_text()

    def boom(*_a, **_k):
        raise RuntimeError("bad log")

    monkeypatch.setattr(run_info, "parse_usage", boom)
    out = run_info.finalize_run_info(tmp_path, exit_code=0, session_logs=[tmp_path / "s.jsonl"])
    assert out == tmp_path / "run_info.json"
    assert (tmp_path / "run_info.json").read_text() == before
    assert capsys.readouterr().err == "finalize_run_info: bad log\n"


def test_valid_but_non_dict_prior_json_hits_best_effort_path(tmp_path, capsys, fake_usage):
    (tmp_path / "run_info.json").write_text("[]")
    out = run_info.finalize_run_info(tmp_path, exit_code=0)
    assert out == tmp_path / "run_info.json"
    assert (tmp_path / "run_info.json").read_text() == "[]"
    assert capsys.readouterr().err.startswith("finalize_run_info: ")


# ── Direct tests of the extracted helper: _aggregate_usage(session_logs, runner, model) ──


def test_helper_returns_none_without_logs_or_parsed_usage(fake_usage):
    assert run_info._aggregate_usage(None, "claude", "m") is None
    assert run_info._aggregate_usage([], "claude", "m") is None
    assert run_info._aggregate_usage([None, ""], "claude", "m") is None
    assert fake_usage.calls == []
    assert run_info._aggregate_usage([Path("missing.jsonl")], "claude", "m") is None
    assert fake_usage.calls == [("claude", "m", Path("missing.jsonl"))]


def test_helper_sums_rounds_and_orders_keys(fake_usage):
    fake_usage.table["a.jsonl"] = {"api_cost_usd": 0.1234564, "input_tokens": 10, "n_turns": 2}
    fake_usage.table["b.jsonl"] = {"api_cost_usd": 0.2000003, "output_tokens": None, "extra": 9}
    out = run_info._aggregate_usage([Path("a.jsonl"), Path("b.jsonl")], "claude", "m")
    assert out == {**_ZERO_USAGE, "api_cost_usd": 0.323457, "input_tokens": 10, "n_turns": 2}
    assert list(out) == list(_ZERO_USAGE)
    assert [c[2] for c in fake_usage.calls] == [Path("a.jsonl"), Path("b.jsonl")]


def test_helper_cost_priced_only_for_priced_runners(fake_usage):
    fake_usage.table["a.jsonl"] = {"input_tokens": 1}
    fake_usage.table["b.jsonl"] = {"input_tokens": 1, "cost_priced": False}
    logs = [Path("a.jsonl"), Path("b.jsonl")]
    assert run_info._aggregate_usage(logs, "codex", "m")["cost_priced"] is False
    assert run_info._aggregate_usage(logs[:1], "forge", "m")["cost_priced"] is True
    assert "cost_priced" not in run_info._aggregate_usage(logs, "claude", "m")


def test_helper_lets_parse_usage_exceptions_propagate(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bad log")

    monkeypatch.setattr(run_info, "parse_usage", boom)
    with pytest.raises(RuntimeError, match="bad log"):
        run_info._aggregate_usage([Path("s.jsonl")], "claude", "m")
