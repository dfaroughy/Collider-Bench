"""Characterization of the trajectory Markdown report written by run_judge.

Pins the *current* judge_trajectory.md output: header, the fixed order of the
narrative sections and list sections, plain vs structured list items (field
order and the None/falsy rules for the impact/severity/avoidable/evidence
suffix), empty/missing trajectories, exact whitespace, the report path stamped
into the scores, and what is written alongside it. It lets the extraction of the
formatter into `_format_trajectory_report` be shown to be behavior-preserving.
Most tests drive `run_judge`; the last section tests the formatter directly.

Offline and synthetic: `subprocess.Popen` is replaced by a fake that replays a
canned stream-json result, so `claude` is never invoked and nothing touches the
network. Reports are written under tmp_path.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from ColliderBench.Evals import judge

_MODEL = "test-model"
_HEADER = "# Judge Trajectory Narrative\n\n**Judge model**: test-model\n"


class _FakeProc:
    def __init__(self, lines):
        self.stdout = lines
        self.stderr = None

    def wait(self):
        return 0

    def kill(self):
        pass


@pytest.fixture
def judged(tmp_path, monkeypatch, capsys):
    """`go(scores, output_dir=...)` -> (result, output_dir) for a canned judge verdict."""
    rubric = tmp_path / "rubric.md"
    rubric.write_text("R\n")
    monkeypatch.setattr(judge, "JUDGE_RUBRIC_PATH", rubric)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def go(scores, output_dir=out_dir, model=_MODEL):
        line = json.dumps({"type": "result", "result": json.dumps(scores)}) + "\n"
        monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: _FakeProc([line]))
        result = judge.run_judge([], None, None, model=model, output_dir=output_dir)
        capsys.readouterr()
        return result, output_dir

    go.out_dir = out_dir
    return go


def _report(out_dir):
    return (out_dir / "judge_trajectory.md").read_text()


# ── narrative sections ──────────────────────────────────────────────────────


def test_narrative_sections_follow_the_fixed_order_and_whitespace(judged):
    trajectory = {
        "overall_assessment": "OA",
        "tool_use": "TU",
        "summary": "S",
        "honesty_and_reporting": "HR",
        "planning": "P",
        "scientific_judgment": "SJ",
    }
    _result, out = judged({"trajectory": trajectory})
    assert _report(out) == (
        _HEADER + "\n"
        "\n## Summary\n\nS\n"
        "\n"
        "\n## Planning\n\nP\n"
        "\n"
        "\n## Tool Use\n\nTU\n"
        "\n"
        "\n## Scientific Judgment\n\nSJ\n"
        "\n"
        "\n## Honesty And Reporting\n\nHR\n"
        "\n"
        "\n## Overall Assessment\n\nOA\n"
    )


def test_empty_and_null_narrative_fields_and_unknown_keys_are_skipped(judged):
    trajectory = {"summary": "", "planning": None, "tool_use": "TU", "extra": "ignored"}
    _result, out = judged({"trajectory": trajectory})
    assert _report(out) == _HEADER + "\n\n## Tool Use\n\nTU\n"
    assert "ignored" not in _report(out)


def test_multiline_narrative_text_is_inserted_verbatim(judged):
    _result, out = judged({"trajectory": {"summary": "line1\nline2\n"}})
    assert _report(out) == _HEADER + "\n\n## Summary\n\nline1\nline2\n\n"


def test_judge_model_in_header_uses_the_model_argument(judged):
    _result, out = judged({"trajectory": {"summary": "S"}}, model="other-model")
    assert _report(out).startswith("# Judge Trajectory Narrative\n\n**Judge model**: other-model\n")


# ── list sections ───────────────────────────────────────────────────────────


def test_list_sections_follow_the_fixed_order_after_narrative(judged):
    trajectory = {
        "issues": ["i1"],
        "strengths": ["s1", "s2"],
        "stuck_points": ["sp"],
        "creative_moves": ["cm"],
        "summary": "S",
    }
    _result, out = judged({"trajectory": trajectory})
    assert _report(out) == (
        _HEADER + "\n"
        "\n## Summary\n\nS\n"
        "\n"
        "\n## Strengths\n"
        "\n- s1\n- s2\n"
        "\n"
        "\n## Creative Moves\n"
        "\n- cm\n"
        "\n"
        "\n## Stuck Points\n"
        "\n- sp\n"
        "\n"
        "\n## Issues\n"
        "\n- i1\n"
    )


def test_structured_items_use_description_then_label_then_json_and_fixed_suffix_order(judged):
    items = [
        {"description": "D", "evidence": "e", "avoidable": True, "severity": "high", "impact": "m"},
        {"label": "L", "impact": "low"},
        {"description": "", "label": "", "weird": 1},
        {"description": "bare"},
    ]
    _result, out = judged({"trajectory": {"issues": items}})
    assert _report(out) == (
        _HEADER + "\n"
        "\n## Issues\n"
        "\n- D (impact: m; severity: high; avoidable: True; evidence: e)"
        "\n- L (impact: low)"
        '\n- {"description": "", "label": "", "weird": 1}'
        "\n- bare\n"
    )


def test_none_suffix_fields_are_skipped_but_falsy_values_are_kept(judged):
    item = {"description": "D", "impact": None, "severity": 0, "avoidable": False, "evidence": ""}
    _result, out = judged({"trajectory": {"strengths": [item]}})
    assert _report(out).endswith("\n- D (severity: 0; avoidable: False; evidence: )\n")


def test_non_string_plain_items_are_formatted_with_str(judged):
    _result, out = judged({"trajectory": {"stuck_points": [3, None, ["a"]]}})
    assert _report(out).endswith("\n- 3\n- None\n- ['a']\n")


def test_json_fallback_description_includes_suffix_fields_too(judged):
    item = {"impact": "high"}  # no description/label -> whole item as JSON, suffix still added
    _result, out = judged({"trajectory": {"issues": [item]}})
    assert _report(out).endswith('\n- {"impact": "high"} (impact: high)\n')


@pytest.mark.parametrize("empty", [[], None])
def test_empty_or_null_lists_add_no_section(judged, empty):
    _result, out = judged({"trajectory": {"summary": "S", "strengths": empty}})
    assert "## Strengths" not in _report(out)


# ── empty / missing trajectory ──────────────────────────────────────────────


@pytest.mark.parametrize("trajectory", [None, {}, "<absent>"])
def test_missing_or_empty_trajectory_writes_no_report_and_no_path(judged, trajectory):
    scores: dict = {"overall": 1}
    if trajectory != "<absent>":
        scores["trajectory"] = trajectory
    result, out = judged(scores)
    assert not (out / "judge_trajectory.md").exists()
    assert "trajectory_report_path" not in result
    assert (out / "judge_scores.json").is_file()


def test_trajectory_with_only_empty_fields_still_writes_a_header_only_report(judged):
    result, out = judged({"trajectory": {"summary": "", "issues": []}})
    assert _report(out) == _HEADER
    assert result["trajectory_report_path"] == str(out / "judge_trajectory.md")


def test_no_output_dir_means_no_report_and_no_path(judged):
    result, _out = judged({"trajectory": {"summary": "S"}}, output_dir=None)
    assert "trajectory_report_path" not in result
    assert result["trajectory"] == {"summary": "S"}


def test_non_dict_trajectory_raises_attribute_error(judged):
    with pytest.raises(AttributeError):
        judged({"trajectory": "just a string"})


# ── report path + sibling artifacts ─────────────────────────────────────────


def test_report_path_is_stamped_into_scores_but_not_into_judge_scores_json(judged):
    result, out = judged({"trajectory": {"summary": "S"}, "overall": 2})
    path = out / "judge_trajectory.md"
    assert result["trajectory_report_path"] == str(path)
    saved = json.loads((out / "judge_scores.json").read_text())
    assert "trajectory_report_path" not in saved
    assert saved == {"trajectory": {"summary": "S"}, "overall": 2, "judge_model": _MODEL}
    assert result == {**saved, "trajectory_report_path": str(path)}


def test_an_existing_report_is_overwritten(judged):
    judged.out_dir.joinpath("judge_trajectory.md").write_text("STALE")
    _result, out = judged({"trajectory": {"summary": "S"}})
    assert "STALE" not in _report(out)


# ── Direct tests of the extracted formatter: _format_trajectory_report ─────


def test_formatter_returns_exactly_the_report_text_written_by_run_judge(judged):
    trajectory = {
        "summary": "S",
        "strengths": ["a", {"description": "D", "impact": "x"}],
        "issues": [{"label": "L"}],
    }
    _result, out = judged({"trajectory": trajectory})
    assert judge._format_trajectory_report(trajectory, _MODEL) == _report(out)


def test_formatter_header_only_for_an_empty_trajectory():
    assert judge._format_trajectory_report({}, "m") == (
        "# Judge Trajectory Narrative\n\n**Judge model**: m\n"
    )


def test_formatter_does_not_mutate_its_input():
    trajectory = {"summary": "S", "issues": [{"description": "D", "impact": "x"}]}
    before = json.dumps(trajectory, sort_keys=True)
    judge._format_trajectory_report(trajectory, "m")
    assert json.dumps(trajectory, sort_keys=True) == before
