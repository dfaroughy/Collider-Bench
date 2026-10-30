"""Characterization of how run_judge turns the judge CLI's stream into a score dict.

Pins the *current* response handling of ColliderBench.Evals.judge.run_judge:
which text is parsed (the final `result` event vs concatenated assistant text
blocks), how the first balanced `{...}` is located and decoded, which events
and lines are ignored, the exact error dicts (including the 2000-character
`raw` truncation and the 500-character stderr truncation), and the
`judge_model` stamp, so the extraction of the JSON parsing into
`_parse_judge_response` is shown to be behavior-preserving. Most tests drive
`run_judge`; the last section tests `_parse_judge_response` directly.

Offline and synthetic: `subprocess.Popen` is replaced by a fake that replays a
canned stream-json stdout, so `claude` is never invoked and nothing touches the
network. No `output_dir`/`rp` is passed, so only the parsing path runs and no
files are written.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from ColliderBench.Evals import judge

_MODEL = "test-model"


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}}) + "\n"


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _result(text) -> str:
    return json.dumps({"type": "result", "result": text}) + "\n"


class _FakeStderr:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text


class _FakeProc:
    def __init__(self, lines, rc=0, stderr=""):
        self.stdout = lines
        self.stderr = _FakeStderr(stderr)
        self._rc = rc
        self.killed = False

    def wait(self):
        return self._rc

    def kill(self):
        self.killed = True


@pytest.fixture
def judge_stream(tmp_path, monkeypatch, capsys):
    """`go(lines, rc=0, stderr="")` -> run_judge result for a canned CLI stream."""
    rubric = tmp_path / "rubric.md"
    rubric.write_text("R\n")
    monkeypatch.setattr(judge, "JUDGE_RUBRIC_PATH", rubric)
    procs: list[_FakeProc] = []

    def go(lines, rc=0, stderr=""):
        def fake_popen(argv, **kwargs):
            proc = _FakeProc(lines, rc=rc, stderr=stderr)
            procs.append(proc)
            return proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        result = judge.run_judge([], None, None, model=_MODEL)
        capsys.readouterr()
        return result

    go.procs = procs
    return go


def _parse_error(result: dict) -> str:
    assert set(result) == {"error", "raw"}
    prefix = "Could not parse judge response: "
    assert result["error"].startswith(prefix)
    return result["error"][len(prefix) :]


# ── successful parsing ──────────────────────────────────────────────────────


def test_final_result_json_is_parsed_and_stamped_with_model(judge_stream):
    assert judge_stream([_result('{"a": 1}')]) == {"a": 1, "judge_model": _MODEL}


def test_judge_model_stamp_overrides_any_value_in_the_response(judge_stream):
    out = judge_stream([_result('{"judge_model": "claimed", "a": 1}')])
    assert out == {"judge_model": _MODEL, "a": 1}


def test_prefixed_and_suffixed_prose_around_the_json_is_ignored(judge_stream):
    out = judge_stream([_result('Here is my verdict:\n```json\n{"a": 1}\n```\nHope that helps!')])
    assert out == {"a": 1, "judge_model": _MODEL}


def test_nested_objects_and_arrays_are_parsed_whole(judge_stream):
    body = '{"a": {"b": {"c": 1}}, "d": [{"e": 2}, {"f": {"g": 3}}]}'
    out = judge_stream([_result("x " + body + " y")])
    assert out == {**json.loads(body), "judge_model": _MODEL}


def test_only_the_first_balanced_object_is_used(judge_stream):
    out = judge_stream([_result('{"a": 1} then {"b": 2}')])
    assert out == {"a": 1, "judge_model": _MODEL}


def test_response_with_its_own_error_key_is_accepted_as_scores(judge_stream):
    # A valid judge object that happens to carry an "error" field is not a failure.
    out = judge_stream([_result('{"error": "none", "overall": 3}')])
    assert out == {"error": "none", "overall": 3, "judge_model": _MODEL}


# ── which text is parsed ────────────────────────────────────────────────────


def test_final_result_wins_over_assistant_text_blocks(judge_stream):
    out = judge_stream([_assistant(_text('{"from": "assistant"}')), _result('{"from": "result"}')])
    assert out == {"from": "result", "judge_model": _MODEL}


def test_assistant_blocks_are_concatenated_without_separator_when_no_result(judge_stream):
    out = judge_stream([_assistant(_text('{"a": ')), _assistant(_text("1, "), _text('"b": 2}'))])
    assert out == {"a": 1, "b": 2, "judge_model": _MODEL}


@pytest.mark.parametrize("final", ["", None])
def test_empty_or_null_result_falls_back_to_assistant_text(judge_stream, final):
    out = judge_stream([_assistant(_text('{"from": "assistant"}')), _result(final)])
    assert out == {"from": "assistant", "judge_model": _MODEL}


def test_the_last_result_event_wins(judge_stream):
    out = judge_stream([_result('{"n": 1}'), _result('{"n": 2}')])
    assert out == {"n": 2, "judge_model": _MODEL}


def test_non_text_blocks_and_non_assistant_events_do_not_contribute_text(judge_stream):
    lines = [
        json.dumps({"type": "system", "text": '{"ignored": 1}'}) + "\n",
        _assistant({"type": "tool_use", "name": "Bash"}, "not-a-dict", _text('{"a": 1}')),
        json.dumps({"type": "user", "message": {"content": [_text('{"ignored": 2}')]}}) + "\n",
    ]
    assert judge_stream(lines) == {"a": 1, "judge_model": _MODEL}


def test_blank_garbage_and_non_object_lines_are_skipped(judge_stream):
    lines = ["\n", "not json at all\n", "[1, 2]\n", "{bad\n", _result('{"a": 1}')]
    assert judge_stream(lines) == {"a": 1, "judge_model": _MODEL}


# ── error dicts ─────────────────────────────────────────────────────────────


def test_empty_stream_is_a_parse_error_with_empty_raw(judge_stream):
    out = judge_stream([])
    assert out == {"error": "Could not parse judge response: substring not found", "raw": ""}


def test_response_without_braces_reports_substring_not_found_and_keeps_raw(judge_stream):
    out = judge_stream([_result("I could not decide.")])
    assert _parse_error(out) == "substring not found"
    assert out["raw"] == "I could not decide."


def test_unbalanced_object_reports_an_empty_json_slice(judge_stream):
    # The scan never closes the object, so the slice is empty and json.loads("") fails.
    out = judge_stream([_result('{"a": 1')])
    assert _parse_error(out) == "Expecting value: line 1 column 1 (char 0)"
    assert out["raw"] == '{"a": 1'


def test_balanced_but_invalid_json_reports_the_json_error(judge_stream):
    out = judge_stream([_result("{not valid}")])
    expected = "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
    assert _parse_error(out) == expected
    assert out["raw"] == "{not valid}"


def test_a_closing_brace_inside_a_string_ends_the_scan_early(judge_stream):
    # The brace counter ignores JSON strings, so '"}"' closes the object prematurely.
    out = judge_stream([_result('{"a": "}"}')])
    assert _parse_error(out).startswith("Unterminated string")
    assert out["raw"] == '{"a": "}"}'


def test_raw_is_truncated_to_2000_characters(judge_stream):
    text = "no json here " * 300
    assert len(text) > 2000
    out = judge_stream([_result(text)])
    assert out["raw"] == text[:2000]
    assert len(out["raw"]) == 2000


def test_raw_on_a_json_error_is_also_truncated_to_2000_characters(judge_stream):
    text = "{" + "x" * 3000 + "}"
    out = judge_stream([_result(text)])
    assert out["raw"] == text[:2000]
    assert _parse_error(out).startswith("Expecting property name enclosed in double quotes")


# ── process-level outcomes around parsing ──────────────────────────────────


def test_nonzero_exit_returns_cli_error_with_stderr_cut_to_500_chars(judge_stream):
    out = judge_stream([_result('{"a": 1}')], rc=3, stderr="E" * 800)
    assert out == {"error": "Judge CLI failed (rc=3): " + "E" * 500}


def test_nonzero_exit_wins_over_a_valid_response(judge_stream):
    out = judge_stream([_result('{"a": 1}')], rc=1, stderr="")
    assert out == {"error": "Judge CLI failed (rc=1): "}


def test_keyboard_interrupt_kills_the_process_and_returns_interrupt_error(judge_stream):
    def interrupted():
        yield _result('{"a": 1}')
        raise KeyboardInterrupt

    out = judge_stream(interrupted())
    assert out == {"error": "Judge interrupted by user"}
    assert judge_stream.procs[-1].killed is True


# ── Direct tests of the extracted helper: _parse_judge_response(text) ──────


def test_parse_helper_returns_scores_and_no_error_without_model_stamp():
    scores, error = judge._parse_judge_response('prefix {"a": {"b": [1, {"c": 2}]}} suffix')
    assert error is None
    assert scores == {"a": {"b": [1, {"c": 2}]}}  # judge_model is stamped by run_judge


def test_parse_helper_uses_only_the_first_balanced_object():
    assert judge._parse_judge_response('{"a": 1} {"b": 2}') == ({"a": 1}, None)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "substring not found"),
        ("no braces", "substring not found"),
        ('{"a": 1', "Expecting value: line 1 column 1 (char 0)"),
        (
            "{not valid}",
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
        ),
    ],
)
def test_parse_helper_error_tuple_shape_and_messages(text, message):
    scores, error = judge._parse_judge_response(text)
    assert scores is None
    assert error == {"error": f"Could not parse judge response: {message}", "raw": text}


def test_parse_helper_truncates_raw_to_2000_characters():
    text = "word " * 1000
    scores, error = judge._parse_judge_response(text)
    assert scores is None and error["raw"] == text[:2000] and len(error["raw"]) == 2000
