"""Characterization of the judge's provenance-correction step.

Pins the *current* behavior of ColliderBench.Evals.judge._apply_provenance_correction,
both called directly and through `run_judge`: the trigger rule (provenance status
`CORRECTED`, or an overrule action of `RESCORE_CORRECTED`, only when an output
directory, a results directory and the paths dict are all present), which score keys
are added and in what order, the corrected directory and `score_corrected.json` files,
the printed "Provenance correction applied" summary, the swallowed-error path that
records `corrected_score_error`, and which failures escape. It lets the extraction of
this step from `run_judge` be shown to be behavior-preserving. Most tests drive
`run_judge`; the last section tests `_apply_provenance_correction` directly.

Offline and synthetic: a tiny task tree under tmp_path replaces `score._TASKS_ROOT`
(no real task or reference is used), `subprocess.Popen` is replaced by a fake that
replays a canned verdict, and the Baker-Cousins p-value is a constant so the printed
summary is deterministic. `claude` is never invoked and nothing touches the network.
"""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from ColliderBench.Evals import judge, score

_MODEL = "test-model"
_REF = [10, 20, 30, 40]
_SUMMARY_HEADER = "\n  Provenance correction applied:\n"


def _doc(values, name="Y"):
    return {
        "independent_variables": [
            {
                "header": {"name": "x", "units": ""},
                "values": [{"low": i, "high": i + 1} for i in range(len(values))],
            }
        ],
        "dependent_variables": [
            {
                "header": {"name": name},
                "values": [{"value": v, "errors": [{"symerror": 0.1}]} for v in values],
            }
        ],
    }


def _write_results(path, values):
    with open(path, "w") as f:
        yaml.safe_dump_all([{"name": "h", "type": "histogram"}, _doc(values)], f, sort_keys=False)


class _FakeProc:
    def __init__(self, lines):
        self.stdout = lines
        self.stderr = None

    def wait(self):
        return 0

    def kill(self):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(score, "_TASKS_ROOT", tasks)
    task = tasks / "T"
    (task / "template").mkdir(parents=True)
    (tasks / "shared" / "P" / "reference").mkdir(parents=True)
    (task / "task.toml").write_text('[task]\npaper = "P"\n[metrics]\nmode = "shape_norm"\n')
    _write_results(task / "template" / "h.yaml", [None] * 4)
    ref = tasks / "shared" / "P" / "reference" / "h.yaml"
    _write_results(ref, _REF)
    run = tmp_path / "run"
    results = run / "workspace" / "results"
    results.mkdir(parents=True)
    (run / "run_info.json").write_text(json.dumps({"task_id": "T"}))
    _write_results(results / "h.yaml", [1, 1, 1, 1])  # agent output: wrong yields
    out = tmp_path / "out"
    out.mkdir()
    rubric = tmp_path / "rubric.md"
    rubric.write_text("R\n")
    monkeypatch.setattr(judge, "JUDGE_RUBRIC_PATH", rubric)
    monkeypatch.setattr(score, "baker_cousins_p_value", lambda *a, **k: 0.25)
    rec = type("Rec", (), {})()
    rec.run, rec.results, rec.out = run, results, out
    rec.rp = score.resolve_paths(run)
    rec.capsys = capsys

    def judged(verdict: dict, **kw):
        line = json.dumps({"type": "result", "result": json.dumps(verdict)}) + "\n"
        monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: _FakeProc([line]))
        kwargs = {"results_dir": results, "output_dir": out, "rp": rec.rp}
        kwargs.update(kw)
        result = judge.run_judge([], kwargs.pop("results_dir"), None, model=_MODEL, **kwargs)
        rec.printed = capsys.readouterr().out
        return result

    rec.judged = judged
    return rec


def _correcting(values=_REF, status="CORRECTED", **extra):
    audit = {
        "status": status,
        "series": {"h.yaml/Y": {"classification": "FABRICATED", "corrected_values": list(values)}},
    }
    audit.update(extra)
    return {"overall": 1, "provenance_audit": audit}


_EXPECTED_SUMMARY = (
    _SUMMARY_HEADER
    + "    Submitted: shape p=0.25  norm Δ%=96.00  (4/4 bins filled)\n"
    + "    Corrected: shape p=0.25  norm Δ%=0.00  (4/4 bins filled)\n"
)


# ── trigger rule ────────────────────────────────────────────────────────────


def test_status_corrected_triggers_the_rescore_and_adds_keys_in_order(world):
    result = world.judged(_correcting())
    added = [k for k in result if k not in ("overall", "provenance_audit", "judge_model")]
    assert added == ["corrected_dir", "corrected_score", "submitted_score"]
    assert result["corrected_dir"] == str(world.out / "results_corrected_by_judge")


@pytest.mark.parametrize("action", ["RESCORE_CORRECTED", "rescore_corrected", "Rescore_Corrected"])
def test_overrule_action_triggers_case_insensitively_even_without_corrected_status(world, action):
    verdict = _correcting(status="OK", overrule={"action": action})
    assert "corrected_score" in world.judged(verdict)


@pytest.mark.parametrize(
    "audit",
    [
        {},
        {"status": "OK"},
        {"status": "corrected"},  # status match is case-sensitive
        {"overrule": {"action": "NONE"}},
        {"overrule": {"action": None}},
        {"overrule": "RESCORE_CORRECTED"},  # a non-dict overrule is ignored
        {"overrule": {"action": "KEEP"}},
    ],
)
def test_other_provenance_values_add_nothing(world, audit):
    audit = {**audit, "series": _correcting()["provenance_audit"]["series"]}
    result = world.judged({"provenance_audit": audit})
    assert not {"corrected_dir", "corrected_score", "submitted_score"} & set(result)
    assert not (world.out / "results_corrected_by_judge").exists()
    assert "Provenance" not in world.printed


@pytest.mark.parametrize("missing", ["output_dir", "results_dir", "rp"])
def test_missing_output_results_or_rp_skips_the_correction(world, missing):
    kwargs = {missing: None}
    result = world.judged(_correcting(), **kwargs)
    assert "corrected_dir" not in result and "corrected_score" not in result
    assert "Provenance" not in world.printed


def test_no_provenance_audit_key_at_all_is_a_noop(world):
    assert "corrected_dir" not in world.judged({"overall": 3})


# ── what a successful correction produces ───────────────────────────────────


def test_corrected_rescore_and_submitted_rescore_values_and_files(world):
    result = world.judged(_correcting())
    corrected, submitted = result["corrected_score"], result["submitted_score"]
    assert corrected["normalization"]["mean_abs_frac_error_pct"] == 0.0
    assert submitted["normalization"]["mean_abs_frac_error_pct"] == 96.0
    assert (corrected["n_filled"], submitted["n_filled"]) == (4, 4)
    saved = json.loads((world.out / "score_corrected.json").read_text())
    assert saved == corrected
    corrected_dir = world.out / "results_corrected_by_judge"
    docs = list(yaml.safe_load_all((corrected_dir / "h.yaml").read_text()))
    values = [e["value"] for e in docs[1]["dependent_variables"][0]["values"]]
    assert values == _REF
    # The agent's own results are untouched.
    original = list(yaml.safe_load_all((world.results / "h.yaml").read_text()))
    assert [e["value"] for e in original[1]["dependent_variables"][0]["values"]] == [1] * 4


def test_the_printed_summary_lists_submitted_then_corrected_with_fixed_formats(world):
    world.judged(_correcting())
    assert _EXPECTED_SUMMARY in world.printed


def test_summary_lines_are_skipped_for_scores_without_shape_p_or_norm_error(world, monkeypatch):
    def error_score(run_path, **kwargs):
        return {"task_id": "T", "error": "boom"}

    monkeypatch.setattr(score, "score_run", error_score)
    result = world.judged(_correcting())
    assert result["corrected_score"] == {"task_id": "T", "error": "boom"}
    assert world.printed.count("Provenance correction applied") == 1
    assert "Submitted:" not in world.printed and "Corrected:" not in world.printed


def test_judge_scores_json_is_written_before_the_correction_keys_are_added(world):
    result = world.judged(_correcting())
    saved = json.loads((world.out / "judge_scores.json").read_text())
    assert not {"corrected_dir", "corrected_score", "submitted_score"} & set(saved)
    assert saved["judge_model"] == _MODEL
    assert "corrected_score" in result


def test_full_file_replacement_via_corrected_results_is_used_instead_of_series_values(world):
    replacement = [{"name": "h"}, _doc([10, 20, 30, 40])]
    verdict = {
        "provenance_audit": {"status": "CORRECTED", "corrected_results": {"h.yaml": replacement}}
    }
    result = world.judged(verdict)
    assert result["corrected_score"]["normalization"]["mean_abs_frac_error_pct"] == 0.0


# ── swallowed errors vs escaping errors ─────────────────────────────────────


def test_score_failure_is_recorded_as_corrected_score_error_with_prior_keys_kept(
    world, monkeypatch
):
    def boom(run_path, **kwargs):
        raise RuntimeError("rescore failed")

    monkeypatch.setattr(score, "score_run", boom)
    result = world.judged(_correcting())
    assert result["corrected_score_error"] == "rescore failed"
    assert "corrected_dir" in result  # set before the rescore
    assert "corrected_score" not in result and "submitted_score" not in result
    assert not (world.out / "score_corrected.json").exists()
    assert "Provenance correction applied" not in world.printed


def test_a_failure_in_the_second_rescore_keeps_the_corrected_score_and_file(world, monkeypatch):
    real = score.score_run

    def second_fails(run_path, **kwargs):
        if "results_dir" not in kwargs:
            raise ValueError("submitted failed")
        return real(run_path, **kwargs)

    monkeypatch.setattr(score, "score_run", second_fails)
    result = world.judged(_correcting())
    assert result["corrected_score_error"] == "submitted failed"
    assert "corrected_score" in result and "submitted_score" not in result
    assert (world.out / "score_corrected.json").is_file()
    assert "Provenance correction applied" not in world.printed


def test_rp_without_run_dir_is_swallowed_as_a_key_error_message(world):
    result = world.judged(_correcting(), rp={})
    assert result["corrected_score_error"] == "'run_dir'"
    assert "corrected_dir" in result and "corrected_score" not in result


def test_a_missing_results_directory_escapes_as_file_not_found(world, tmp_path):
    with pytest.raises(FileNotFoundError):
        world.judged(_correcting(), results_dir=tmp_path / "does-not-exist")


# ── Direct tests of the extracted helper: _apply_provenance_correction ─────


def _direct(world, scores, **kw):
    args = {
        "results_dir": world.results,
        "output_dir": world.out,
        "rp": world.rp,
    }
    args.update(kw)
    result = judge._apply_provenance_correction(
        scores, args["results_dir"], args["output_dir"], args["rp"]
    )
    world.printed = world.capsys.readouterr().out
    return result


def test_helper_returns_none_and_mutates_scores_in_place(world):
    scores = _correcting()
    provenance_before = json.dumps(scores["provenance_audit"], sort_keys=True)
    assert _direct(world, scores) is None
    assert list(scores)[-3:] == ["corrected_dir", "corrected_score", "submitted_score"]
    assert json.dumps(scores["provenance_audit"], sort_keys=True) == provenance_before


def test_helper_is_a_noop_without_a_trigger_or_with_missing_inputs(world):
    scores = {"provenance_audit": {"status": "OK"}}
    _direct(world, scores)
    assert scores == {"provenance_audit": {"status": "OK"}}
    for missing in ("output_dir", "results_dir", "rp"):
        scores = _correcting()
        _direct(world, scores, **{missing: None})
        assert set(scores) == {"overall", "provenance_audit"}
    assert world.printed == ""


def test_helper_handles_scores_without_a_provenance_audit_or_with_a_null_one(world):
    for scores in ({}, {"provenance_audit": None}, {"provenance_audit": {"overrule": None}}):
        _direct(world, scores)
        assert "corrected_dir" not in scores


def test_helper_prints_the_summary_and_records_the_same_keys_as_the_public_path(world):
    scores = _correcting()
    _direct(world, scores)
    assert _EXPECTED_SUMMARY in world.printed
    assert scores["corrected_dir"] == str(world.out / "results_corrected_by_judge")
    assert scores["submitted_score"]["normalization"]["mean_abs_frac_error_pct"] == 96.0


def test_helper_records_the_error_text_and_does_not_raise_when_rescoring_fails(world, monkeypatch):
    monkeypatch.setattr(score, "score_run", lambda *a, **k: (_ for _ in ()).throw(OSError("io")))
    scores = _correcting()
    _direct(world, scores)
    assert scores["corrected_score_error"] == "io"
