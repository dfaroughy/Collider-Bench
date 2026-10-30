"""Characterization of the prompt assembled by ColliderBench.Evals.judge.run_judge.

Pins the *current* prompt text sent to the judge CLI: section order and markers,
log and artifact ordering, the "(…)" placeholders for missing inputs, text
truncation limits, the way the hidden reference file is wrapped through
`_load_results_summary`, and the control-character stripping applied before the
prompt reaches argv, so the extraction of prompt assembly into `_build_judge_prompt`
is shown to be behavior-preserving. Most tests drive `run_judge`; the last section
tests `_build_judge_prompt` directly.

Offline and synthetic: `subprocess.Popen` is replaced by a recorder that emits no
output (so `claude` is never invoked and nothing touches the network), the rubric
path is patched to a tiny file, and every results/reference/artifact/session file
is created under tmp_path. The judge's return value is irrelevant here (empty
output yields a parse-error dict); only the captured prompt is inspected.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from ColliderBench.Evals import judge

_RUBRIC = "RUBRIC-TEXT\n"
_TRUNC = "\n... (truncated)"

_MARKERS = [
    "--- FILLED RESULTS (agent's results/*.yaml) ---\n",
    "--- HIDDEN REFERENCE (published CMS values; not visible to the agent) ---\n",
    "--- ADDITIONAL ARTIFACTS ---\n",
    "--- SESSION LOGS ---\n",
]


class _FakeProc:
    stdout = iter(())
    stderr = None

    def wait(self):
        return 0

    def kill(self):
        pass


@pytest.fixture
def judged(tmp_path, monkeypatch, capsys):
    """`go(**run_judge_kwargs)` -> (prompt, argv). Invokes run_judge with a fake CLI."""
    rubric = tmp_path / "rubric.md"
    rubric.write_text(_RUBRIC)
    monkeypatch.setattr(judge, "JUDGE_RUBRIC_PATH", rubric)
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    def go(**kwargs):
        kwargs.setdefault("session_logs", [])
        kwargs.setdefault("results_dir", None)
        kwargs.setdefault("reference_file", None)
        result = judge.run_judge(**kwargs)
        capsys.readouterr()
        assert "error" in result  # empty CLI output -> parse error; no network/LLM
        return seen["argv"][2], seen["argv"], seen["kwargs"]

    return go


def _sections(prompt: str) -> dict[str, str]:
    """Split a prompt into {rubric, results, reference, artifacts, session} bodies.

    Template shape: "{rubric}\\n\\n<marker>\\n{body}\\n\\n<marker>\\n...{body}\\n".
    """
    positions = [prompt.index(m) for m in _MARKERS]
    assert positions == sorted(positions)
    assert prompt[: positions[0]] == _RUBRIC + "\n\n"
    starts = [pos + len(m) for pos, m in zip(positions, _MARKERS, strict=False)]
    ends = [*(pos - 2 for pos in positions[1:]), len(prompt) - 1]
    for pos in positions[1:]:
        assert prompt[pos - 2 : pos] == "\n\n"
    assert prompt.endswith("\n")
    return {
        "rubric": prompt[: len(_RUBRIC)],
        "results": prompt[starts[0] : ends[0]],
        "reference": prompt[starts[1] : ends[1]],
        "artifacts": prompt[starts[2] : ends[2]],
        "session": prompt[starts[3] : ends[3]],
    }


# ── exact assembly + order ──────────────────────────────────────────────────


def test_full_prompt_is_assembled_exactly(judged, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "b.yaml").write_text("b: 2\n")
    (results / "a.yaml").write_text("a: 1\n")
    (results / "submission.yaml").write_text("excluded: true\n")
    (results / "notes.txt").write_text("not yaml\n")
    ref = tmp_path / "ref.yaml"
    ref.write_text("ref: 9\n")
    z, a = tmp_path / "z.txt", tmp_path / "a.txt"
    z.write_text("zzz")
    a.write_text("aaa")
    log2, log1 = tmp_path / "second.log", tmp_path / "first.log"
    log2.write_text("two\n")
    log1.write_text("one\n")

    prompt, _argv, _kw = judged(
        session_logs=[log2, log1], results_dir=results, reference_file=ref, artifacts=[z, a]
    )

    assert prompt == (
        "RUBRIC-TEXT\n\n\n"
        "--- FILLED RESULTS (agent's results/*.yaml) ---\n"
        "--- a.yaml ---\na: 1\n\n"
        "\n"
        "--- b.yaml ---\nb: 2\n\n"
        "\n\n"
        "--- HIDDEN REFERENCE (published CMS values; not visible to the agent) ---\n"
        "--- ref.yaml ---\nref: 9\n\n"
        "\n\n"
        "--- ADDITIONAL ARTIFACTS ---\n"
        "--- z.txt ---\nzzz\n\n"
        "--- a.txt ---\naaa\n\n"
        "--- SESSION LOGS ---\n"
        "--- second.log ---\ntwo\n\n"
        "\n"
        "--- first.log ---\none\n\n"
    )


def test_results_are_sorted_but_artifacts_and_logs_keep_caller_order(judged, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    for name in ("c.yaml", "a.yaml", "b.yaml"):
        (results / name).write_text(f"{name}\n")
    files = []
    for name in ("z.txt", "m.txt", "a.txt"):
        p = tmp_path / name
        p.write_text(name)
        files.append(p)
    prompt, _a, _k = judged(results_dir=results, artifacts=files, session_logs=files)
    s = _sections(prompt)
    assert [s["results"].index(f"--- {n}.yaml ---") for n in "abc"] == sorted(
        s["results"].index(f"--- {n}.yaml ---") for n in "abc"
    )
    assert [s["artifacts"].index(f"--- {n}.txt ---") for n in ("z", "m", "a")] == sorted(
        s["artifacts"].index(f"--- {n}.txt ---") for n in ("z", "m", "a")
    )
    assert s["session"].index("--- z.txt ---") < s["session"].index("--- m.txt ---")
    assert s["session"].index("--- m.txt ---") < s["session"].index("--- a.txt ---")


def test_cli_argv_shape_and_default_model(judged):
    _prompt, argv, kwargs = judged()
    assert argv[:2] == ["claude", "-p"]
    assert argv[3:] == ["--model", "claude-opus-4-6", "--output-format", "stream-json", "--verbose"]
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE
    assert kwargs["text"] is True


def test_explicit_model_is_passed_through(judged):
    _prompt, argv, _kw = judged(model="m-x")
    assert argv[argv.index("--model") + 1] == "m-x"


# ── missing-input markers ───────────────────────────────────────────────────


def test_all_inputs_missing_use_placeholder_markers(judged):
    prompt, _a, _k = judged()
    assert prompt == (
        "RUBRIC-TEXT\n\n\n"
        "--- FILLED RESULTS (agent's results/*.yaml) ---\n(not provided)\n\n"
        "--- HIDDEN REFERENCE (published CMS values; not visible to the agent) ---\n"
        "(not available)\n\n"
        "--- ADDITIONAL ARTIFACTS ---\n(none provided)\n\n"
        "--- SESSION LOGS ---\n(no session logs provided)\n"
    )


def test_nonexistent_inputs_are_treated_as_missing(judged, tmp_path):
    prompt, _a, _k = judged(
        results_dir=tmp_path / "nope",
        reference_file=tmp_path / "no-ref.yaml",
        artifacts=[tmp_path / "no-art.txt"],
        session_logs=[tmp_path / "no.log"],
    )
    s = _sections(prompt)
    assert s["results"].strip() == "(not available)"
    assert s["reference"].strip() == "(not available)"
    assert s["artifacts"].strip() == "(none provided)"
    assert s["session"].strip() == "(no session logs provided)"


def test_missing_artifacts_and_logs_are_skipped_among_existing_ones(judged, tmp_path):
    art, log = tmp_path / "real.txt", tmp_path / "real.log"
    art.write_text("A")
    log.write_text("L\n")
    prompt, _a, _k = judged(
        artifacts=[tmp_path / "gone.txt", art], session_logs=[tmp_path / "gone.log", log]
    )
    s = _sections(prompt)
    assert s["artifacts"] == "--- real.txt ---\nA"
    assert s["session"] == "--- real.log ---\nL\n"


def test_empty_results_dir_gives_empty_section_not_a_placeholder(judged, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    prompt, _a, _k = judged(results_dir=empty)
    assert _sections(prompt)["results"] == ""


# ── reference wrapping ──────────────────────────────────────────────────────


def test_reference_is_wrapped_with_its_own_filename_header(judged, tmp_path):
    ref = tmp_path / "hist_reference.yaml"
    ref.write_text("v: 1\n")
    prompt, _a, _k = judged(reference_file=ref)
    assert _sections(prompt)["reference"] == "--- hist_reference.yaml ---\nv: 1\n\n"


def test_reference_named_submission_yaml_is_excluded_and_yields_empty_text(judged, tmp_path):
    ref = tmp_path / "submission.yaml"
    ref.write_text("secret: 1\n")
    prompt, _a, _k = judged(reference_file=ref)
    assert "secret" not in prompt
    assert _sections(prompt)["reference"] == ""


def test_non_yaml_reference_file_yields_empty_text(judged, tmp_path):
    ref = tmp_path / "ref.json"
    ref.write_text("{}")
    prompt, _a, _k = judged(reference_file=ref)
    assert _sections(prompt)["reference"] == ""


# ── truncation ──────────────────────────────────────────────────────────────


def test_results_and_reference_text_truncate_at_5000_chars(judged, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "a.yaml").write_text("x" * 6000)
    ref = tmp_path / "ref.yaml"
    ref.write_text("y" * 6000)
    prompt, _a, _k = judged(results_dir=results, reference_file=ref)
    s = _sections(prompt)
    expected_head = "--- a.yaml ---\n" + "x" * (5000 - len("--- a.yaml ---\n"))
    assert s["results"] == expected_head + _TRUNC
    expected_ref = "--- ref.yaml ---\n" + "y" * (5000 - len("--- ref.yaml ---\n"))
    assert s["reference"] == expected_ref + _TRUNC


def test_results_summary_helper_truncates_and_marks_unavailable_dirs(tmp_path):
    (tmp_path / "a.yaml").write_text("x" * 100)
    out = judge._load_results_summary(tmp_path, max_chars=30)
    assert out == ("--- a.yaml ---\n" + "x" * 15) + _TRUNC
    assert judge._load_results_summary(tmp_path / "missing") == "(not available)"
    assert judge._load_results_summary(None) == "(not available)"  # type: ignore[arg-type]


def test_artifact_text_truncates_at_10000_chars(judged, tmp_path):
    art = tmp_path / "big.txt"
    art.write_text("z" * 12000)
    prompt, _a, _k = judged(artifacts=[art])
    assert _sections(prompt)["artifacts"] == "--- big.txt ---\n" + "z" * 10000 + _TRUNC


def test_raw_text_session_log_truncates_at_50000_chars(judged, tmp_path):
    log = tmp_path / "raw.log"
    log.write_text("r" * 50001)
    prompt, _a, _k = judged(session_logs=[log])
    assert _sections(prompt)["session"] == "--- raw.log ---\n" + "r" * 50000 + _TRUNC


# ── session log formats ─────────────────────────────────────────────────────


def _stream_line(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def test_stream_json_session_is_summarized_into_tagged_moments(judged, tmp_path):
    log = tmp_path / "s.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": " plan it "}]}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/c.py"}},
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo"}},
                    {"type": "tool_use", "name": "WebFetch", "input": {}},
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "all fine"},
                    {"type": "tool_result", "content": "Error: boom"},
                ]
            },
        },
    ]
    log.write_text("".join(_stream_line(x) for x in lines))
    prompt, _a, _k = judged(session_logs=[log])
    assert _sections(prompt)["session"] == (
        "--- s.jsonl ---\n"
        "[THOUGHT] plan it\n"
        "[TOOL] Bash: ls -la\n"
        "[TOOL] Read: c.py\n"
        "[TOOL] Grep: foo\n"
        "[TOOL] WebFetch\n"
        "[ERROR] Error: boom\n"
    )


def test_missing_session_path_helper_returns_marker(tmp_path):
    assert judge.extract_session_summary(tmp_path / "nope") == "(no session log)"


# ── control-character stripping before argv ────────────────────────────────


def test_control_characters_are_stripped_but_tab_and_newline_are_kept(judged, tmp_path):
    art = tmp_path / "ctl.txt"
    # "\r" is turned into "\n" by universal-newline reading before stripping runs.
    art.write_text("a\x00b\x07c\td\ne\rf\x7fg\x1bh")
    prompt, argv, _k = judged(artifacts=[art])
    assert _sections(prompt)["artifacts"] == "--- ctl.txt ---\nabc\td\ne\nfgh"
    assert not any(c in argv[2] for c in ("\x00", "\x07", "\x7f", "\x1b"))


# ── Direct tests of the extracted helper: _build_judge_prompt ──────────────


def test_prompt_helper_matches_prompt_sent_to_cli(judged, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "a.yaml").write_text("a: 1\n")
    art, log = tmp_path / "art.txt", tmp_path / "s.log"
    art.write_text("A")
    log.write_text("L\n")
    kwargs = {"session_logs": [log], "results_dir": results, "reference_file": None}
    sent, _argv, _kw = judged(artifacts=[art], **kwargs)
    assert judge._build_judge_prompt([log], results, None, [art]) == sent


def test_prompt_helper_defaults_artifacts_and_marks_missing_inputs(judged):
    judged()  # installs the patched rubric path
    prompt = judge._build_judge_prompt([], None, None)
    assert prompt == judge._build_judge_prompt([], None, None, None)
    assert "(not provided)" in prompt and "(none provided)" in prompt
    assert prompt.endswith("(no session logs provided)\n")


def test_prompt_helper_keeps_control_characters_stripping_happens_in_run_judge(judged, tmp_path):
    art = tmp_path / "ctl.txt"
    art.write_text("a\x00b\x07c")
    judged()  # installs the patched rubric path
    assert "a\x00b\x07c" in judge._build_judge_prompt([], None, None, [art])
    sent, _argv, _kw = judged(artifacts=[art])
    assert "abc" in sent and "\x00" not in sent
