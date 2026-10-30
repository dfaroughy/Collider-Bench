"""Characterization of score_run's input resolution, loading and early errors.

Pins the *current* behavior of the part of ColliderBench.Evals.score.score_run
that runs before any metric: which files are loaded and in what order, how the
prediction directory can be overridden, the exact dicts (and key order) returned
for a missing prediction / missing reference series / missing prediction series,
which exceptions propagate unchanged, and that no alignment or metric function
runs after an early error, so the separation of input resolution/loading into
`_load_score_inputs` is shown to be behavior-preserving.

Everything is synthetic and offline: a tiny task tree under tmp_path replaces
`score._TASKS_ROOT` (no hidden reference is read), and thin recording wrappers
around the real `histograms.load_histogram/select_series/align` and the two
metric-block functions capture the operation order while preserving behavior.
Most tests drive the public `score_run(...)`; the last section tests
`_load_score_inputs` directly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from ColliderBench.Evals import histograms, score

_REF = [10, 20, 30, 40]


def _hist(values, name="Y"):
    return {
        "independent_variables": [
            {
                "header": {"name": "x", "units": ""},
                "values": [{"low": i, "high": i + 1} for i in range(len(values))],
            }
        ],
        "dependent_variables": [
            {"header": {"name": name}, "values": [{"value": v} for v in values]}
        ],
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A synthetic task + run with recorders around loading, alignment and metrics."""
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(score, "_TASKS_ROOT", tasks)
    task = tasks / "T"
    (task / "template").mkdir(parents=True)
    (tasks / "shared" / "P" / "reference").mkdir(parents=True)
    (task / "task.toml").write_text('[task]\npaper = "P"\n[metrics]\nmode = "shape_norm"\n')
    (task / "template" / "h.yaml").write_text(yaml.safe_dump(_hist([None] * 4)))
    ref = tasks / "shared" / "P" / "reference" / "h.yaml"
    ref.write_text(yaml.safe_dump(_hist(_REF)))
    run = tmp_path / "run"
    results = run / "workspace" / "results"
    results.mkdir(parents=True)
    (run / "run_info.json").write_text(json.dumps({"task_id": "T"}))
    pred = results / "h.yaml"
    pred.write_text(yaml.safe_dump(_hist(_REF)))

    events: list[tuple] = []
    real_load, real_select, real_align = (
        histograms.load_histogram,
        histograms.select_series,
        histograms.align,
    )
    real_shape, real_norm = score._shape_metrics, score._normalization_metrics

    def load(path):
        events.append(("load", path))
        return real_load(path)

    def select(hist, name):
        events.append(("select", hist.path.name, name))
        return real_select(hist, name)

    def align(ref_series, pred_series, bins):
        events.append(("align", ref_series.name))
        return real_align(ref_series, pred_series, bins)

    def shape(aligned, tolerance, n_toys):
        events.append(("shape", tolerance, n_toys))
        return real_shape(aligned, tolerance, n_toys)

    def norm(aligned):
        events.append(("norm",))
        return real_norm(aligned)

    monkeypatch.setattr(histograms, "load_histogram", load)
    monkeypatch.setattr(histograms, "select_series", select)
    monkeypatch.setattr(histograms, "align", align)
    monkeypatch.setattr(score, "_shape_metrics", shape)
    monkeypatch.setattr(score, "_normalization_metrics", norm)
    # Keep the Baker-Cousins toy run tiny wherever a shape block is computed.
    real_bc = score.baker_cousins_p_value
    monkeypatch.setattr(
        score, "baker_cousins_p_value", lambda *a, **k: real_bc(*a, **{**k, "n_toys": 20})
    )
    return SimpleNamespace(
        tasks=tasks,
        task=task,
        ref=ref,
        run=run,
        results=results,
        pred=pred,
        template=task / "template" / "h.yaml",
        events=events,
    )


def _kinds(events):
    return [e[0] for e in events]


# ── early errors: exact dicts + operation order ─────────────────────────────


def test_missing_prediction_returns_error_dict_before_loading_reference_or_prediction(world):
    world.pred.unlink()
    out = score.score_run(world.run)
    assert out == {
        "task_id": "T",
        "paper": "P",
        "error": f"agent output missing: {world.results.resolve() / 'h.yaml'}",
    }
    assert list(out) == ["task_id", "paper", "error"]
    # Only the template is loaded (by resolve_paths); reference/prediction never are.
    assert world.events == [("load", world.template)]


def test_missing_reference_series_error_has_no_paper_key_and_follows_both_selects(world):
    world.ref.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    out = score.score_run(world.run)
    assert out == {"task_id": "T", "error": "series 'Y' not in reference"}
    assert list(out) == ["task_id", "error"]
    assert world.events == [
        ("load", world.template),
        ("load", world.ref),
        ("load", world.pred.resolve()),
        ("select", "h.yaml", "Y"),
        ("select", "h.yaml", "Y"),
    ]


def test_missing_prediction_series_error_and_operation_order(world):
    world.pred.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    out = score.score_run(world.run)
    assert out == {"task_id": "T", "error": "series 'Y' not in prediction"}
    assert list(out) == ["task_id", "error"]
    assert _kinds(world.events) == ["load", "load", "load", "select", "select"]


def test_reference_series_error_wins_when_both_series_are_missing(world):
    world.ref.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    world.pred.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    assert score.score_run(world.run) == {"task_id": "T", "error": "series 'Y' not in reference"}


@pytest.mark.parametrize("breakage", ["missing_prediction", "ref_series", "pred_series"])
def test_no_alignment_or_metric_function_runs_after_an_early_error(world, breakage):
    if breakage == "missing_prediction":
        world.pred.unlink()
    elif breakage == "ref_series":
        world.ref.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    else:
        world.pred.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    out = score.score_run(world.run)
    assert "error" in out
    assert not {"align", "shape", "norm"} & set(_kinds(world.events))


# ── successful path: operation order ────────────────────────────────────────


def test_success_runs_load_select_align_then_shape_then_norm_in_that_order(world):
    out = score.score_run(world.run, n_toys=7)
    assert "error" not in out
    assert world.events == [
        ("load", world.template),
        ("load", world.ref),
        ("load", world.pred.resolve()),
        ("select", "h.yaml", "Y"),
        ("select", "h.yaml", "Y"),
        ("align", "Y"),
        ("shape", 0.0, 7),
        ("norm",),
    ]


def test_n_toys_none_is_passed_through_to_the_shape_block(world):
    score.score_run(world.run)
    assert ("shape", 0.0, None) in world.events


# ── results_dir override ────────────────────────────────────────────────────


def test_results_dir_override_is_read_instead_of_the_workspace_results(world, tmp_path):
    other = tmp_path / "corrected"
    other.mkdir()
    (other / "h.yaml").write_text(yaml.safe_dump(_hist([20, 20, 30, 40])))
    out = score.score_run(world.run, results_dir=other)
    assert out["normalization"]["Delta"] == 0.1  # 110 vs 100, not the perfect run copy
    assert ("load", other / "h.yaml") in world.events
    assert ("load", world.pred.resolve()) not in world.events


def test_results_dir_may_be_a_string_path(world, tmp_path):
    other = tmp_path / "corrected"
    other.mkdir()
    (other / "h.yaml").write_text(yaml.safe_dump(_hist([20, 20, 30, 40])))
    assert score.score_run(world.run, results_dir=str(other))["normalization"]["Delta"] == 0.1


def test_missing_prediction_in_override_dir_reports_the_override_path(world, tmp_path):
    other = tmp_path / "empty-corrected"
    other.mkdir()
    out = score.score_run(world.run, results_dir=other)
    assert out["error"] == f"agent output missing: {other / 'h.yaml'}"
    assert list(out) == ["task_id", "paper", "error"]


def test_override_still_requires_the_runs_own_results_directory(world, tmp_path):
    other = tmp_path / "corrected"
    other.mkdir()
    (other / "h.yaml").write_text(yaml.safe_dump(_hist(_REF)))
    (world.pred).unlink()
    world.results.rmdir()
    with pytest.raises(FileNotFoundError, match="Missing results dir"):
        score.score_run(world.run, results_dir=other)


# ── exceptions propagate unchanged ──────────────────────────────────────────


def test_malformed_prediction_yaml_propagates_the_yaml_error(world):
    world.pred.write_text("a: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        score.score_run(world.run)
    assert "align" not in _kinds(world.events)


def test_prediction_without_dependent_variables_raises_value_error(world):
    world.pred.write_text(yaml.safe_dump({"independent_variables": []}))
    with pytest.raises(ValueError, match="no YAML doc with `dependent_variables`"):
        score.score_run(world.run)


def test_missing_reference_file_raises_file_not_found_before_any_prediction_load(world):
    world.ref.unlink()
    with pytest.raises(FileNotFoundError, match="Missing reference"):
        score.score_run(world.run)
    assert world.events == [("load", world.template)]  # only the template, from resolve_paths


def test_missing_run_info_raises_file_not_found(world):
    (world.run / "run_info.json").unlink()
    with pytest.raises(FileNotFoundError, match="Missing run_info.json"):
        score.score_run(world.run)
    assert world.events == []


def test_run_info_without_task_id_raises_value_error(world):
    (world.run / "run_info.json").write_text(json.dumps({"agent_id": "x"}))
    with pytest.raises(ValueError, match="task_id missing"):
        score.score_run(world.run)


def test_alignment_exception_propagates_and_skips_the_metric_blocks(world, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("align failed")

    monkeypatch.setattr(histograms, "align", boom)
    with pytest.raises(RuntimeError, match="align failed"):
        score.score_run(world.run)
    assert not {"shape", "norm"} & set(_kinds(world.events))


def test_run_path_may_be_a_string_and_is_resolved(world):
    out = score.score_run(str(world.run))
    assert out["task_id"] == "T" and out["n_filled"] == 4


# ── Direct tests of the extracted helper: _load_score_inputs ────────────────


def test_helper_success_returns_paths_aligned_series_and_no_error(world):
    rp, aligned, error = score._load_score_inputs(world.run, None)
    assert error is None
    assert isinstance(aligned, histograms.Aligned)
    assert (aligned.n_bins, aligned.n_filled) == (4, 4)
    assert list(aligned.reference) == [10.0, 20.0, 30.0, 40.0]
    assert rp["task_id"] == "T" and rp["header_name"] == "Y"
    assert rp["results_dir"] == world.results.resolve()
    assert rp["data_filename"] == "h.yaml"
    assert world.events[-1] == ("align", "Y")
    assert not {"shape", "norm"} & set(_kinds(world.events))  # metrics are not its job


def test_helper_early_errors_return_paths_none_and_the_exact_error_dict(world):
    world.ref.write_text(yaml.safe_dump(_hist(_REF, name="Other")))
    rp, aligned, error = score._load_score_inputs(world.run, None)
    assert aligned is None and rp["task_id"] == "T"
    assert error == {"task_id": "T", "error": "series 'Y' not in reference"}
    world.ref.write_text(yaml.safe_dump(_hist(_REF)))
    world.pred.unlink()
    rp, aligned, error = score._load_score_inputs(world.run, None)
    assert aligned is None
    assert list(error) == ["task_id", "paper", "error"]
    assert error["error"].startswith("agent output missing: ")


def test_helper_honors_the_results_dir_override(world, tmp_path):
    other = tmp_path / "corrected"
    other.mkdir()
    (other / "h.yaml").write_text(yaml.safe_dump(_hist([20, 20, 30, 40])))
    _rp, aligned, error = score._load_score_inputs(world.run, other)
    assert error is None and list(aligned.prediction) == [20.0, 20.0, 30.0, 40.0]


def test_helper_lets_load_errors_propagate(world):
    world.pred.write_text("a: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        score._load_score_inputs(world.run, None)
