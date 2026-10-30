"""Characterization of score_run's shape / normalization blocks.

Pins the *current* output of ColliderBench.Evals.score.score_run for small
synthetic histograms: mode gating (shape / yield / shape_norm), the exact key
sets, undefined (null) metrics on zero yields, the dmax_dbar_ratio rules, how
`n_toys` and the task tolerance are forwarded to the Baker-Cousins p-value, and
the normalization values. It lets the extraction of the two blocks into
helpers be shown to be behavior-preserving.

Everything is synthetic and offline: a tiny task tree under tmp_path replaces
`score._TASKS_ROOT`, so no real task, reference or run directory is used; the
Baker-Cousins p-value is replaced by a recorder wherever its exact value or
call arguments matter, so no 1M-toy default run ever happens. Most tests
exercise the public `score_run(...)`; the last section tests the extracted
helpers `_shape_metrics` and `_normalization_metrics` directly.
"""

from __future__ import annotations

import json
import math

import pytest
import yaml

from ColliderBench.Evals import score

_BASE_KEYS = {"task_id", "paper", "header_name", "n_bins", "n_filled", "score_mode"}
_SHAPE_KEYS = [
    "mean_abs_frac_error_pct",
    "p_value",
    "jensen_shannon",
    "jensen_shannon_dist",
    "d_bar",
    "d_max",
    "dmax_dbar_ratio",
    "relative_l2",
]
_NORM_KEYS = ["mean_abs_frac_error_pct", "Delta", "rmsle", "relative_l2"]

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
def run(tmp_path, monkeypatch):
    """`go(ref, pred, mode=..., tolerance=..., **score_kwargs)` -> score_run output."""
    tasks = tmp_path / "tasks"
    monkeypatch.setattr(score, "_TASKS_ROOT", tasks)
    counter = {"n": 0}

    def go(ref, pred, *, mode="shape_norm", tolerance=0.0, metrics_block=True, **kwargs):
        counter["n"] += 1
        task_id = f"T{counter['n']}"
        task = tasks / task_id
        (task / "template").mkdir(parents=True)
        (tasks / "shared" / "P" / "reference").mkdir(parents=True, exist_ok=True)
        toml = '[task]\npaper = "P"\n'
        if metrics_block:
            toml += f'[metrics]\nmode = "{mode}"\ntolerance = {tolerance}\n'
        (task / "task.toml").write_text(toml)
        (task / "template" / "h.yaml").write_text(yaml.safe_dump(_hist([None] * len(ref))))
        (tasks / "shared" / "P" / "reference" / "h.yaml").write_text(yaml.safe_dump(_hist(ref)))
        run_dir = tmp_path / f"run{counter['n']}"
        results = run_dir / "workspace" / "results"
        results.mkdir(parents=True)
        (run_dir / "run_info.json").write_text(json.dumps({"task_id": task_id}))
        (results / "h.yaml").write_text(yaml.safe_dump(_hist(pred)))
        return score.score_run(run_dir, **kwargs)

    return go


@pytest.fixture
def bc_recorder(monkeypatch):
    """Replace baker_cousins_p_value with a recorder returning 0.5."""
    calls: list[tuple[tuple, dict]] = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return 0.5

    monkeypatch.setattr(score, "baker_cousins_p_value", fake)
    return calls


# ── mode gating + exact key sets ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "has_shape", "has_norm"),
    [("shape", True, False), ("yield", False, True), ("shape_norm", True, True)],
)
def test_mode_gates_blocks_and_exact_keys(run, bc_recorder, mode, has_shape, has_norm):
    out = run(_REF, [20, 20, 30, 40], mode=mode)
    expected = set(_BASE_KEYS)
    if has_shape:
        expected.add("shape")
    if has_norm:
        expected.add("normalization")
    assert set(out) == expected
    assert out["score_mode"] == mode
    if has_shape:
        assert list(out["shape"]) == _SHAPE_KEYS
    if has_norm:
        assert list(out["normalization"]) == _NORM_KEYS
    assert len(bc_recorder) == (1 if has_shape else 0)


def test_missing_metrics_block_defaults_to_shape_norm(run, bc_recorder):
    out = run(_REF, _REF, metrics_block=False)
    assert out["score_mode"] == "shape_norm"
    assert "shape" in out and "normalization" in out


def test_invalid_mode_raises_value_error(run):
    with pytest.raises(ValueError, match="must be shape/yield/shape_norm"):
        run(_REF, _REF, mode="bogus")


def test_base_fields(run, bc_recorder):
    out = run(_REF, [10, None, 30, 40])
    assert out["task_id"].startswith("T")
    assert (out["paper"], out["header_name"]) == ("P", "Y")
    assert (out["n_bins"], out["n_filled"]) == (4, 3)


# ── perfect match ───────────────────────────────────────────────────────────


def test_perfect_match_values_and_null_ratio(run):
    out = run(_REF, _REF, n_toys=50)
    shape = out["shape"]
    assert shape["mean_abs_frac_error_pct"] == 0.0
    assert shape["p_value"] == 1.0
    assert shape["jensen_shannon"] == 0.0
    assert shape["jensen_shannon_dist"] == 0.0
    assert shape["d_bar"] == 0.0 and shape["d_max"] == 0.0
    assert shape["dmax_dbar_ratio"] is None  # d_bar == 0 -> 0/0 -> null
    assert shape["relative_l2"] == 0.0
    assert out["normalization"] == {
        "mean_abs_frac_error_pct": 0.0,
        "Delta": 0.0,
        "rmsle": 0.0,
        "relative_l2": 0.0,
    }


# ── non-trivial shape / normalization values ───────────────────────────────


def test_single_bin_excess_values(run, bc_recorder):
    # pred = [20,20,30,40] (total 110 vs 100): norm off by 10%, one bin carries
    # the whole shape disagreement so dmax/dbar == 1.
    out = run(_REF, [20, 20, 30, 40])
    s, n = out["shape"], out["normalization"]
    assert s["mean_abs_frac_error_pct"] == pytest.approx(27.273, abs=1e-6)
    assert s["d_bar"] == pytest.approx(0.081818, abs=1e-6)
    assert s["d_max"] == pytest.approx(0.081818, abs=1e-6)
    assert s["dmax_dbar_ratio"] == 1.0
    assert s["relative_l2"] == pytest.approx(0.174078, abs=1e-6)
    assert s["jensen_shannon"] == pytest.approx(0.010098, abs=1e-6)
    assert s["jensen_shannon_dist"] == pytest.approx(math.sqrt(s["jensen_shannon"]), abs=1e-4)
    assert n["mean_abs_frac_error_pct"] == 10.0
    assert n["Delta"] == 0.1
    assert n["rmsle"] == pytest.approx(0.323314, abs=1e-6)
    assert n["relative_l2"] == pytest.approx(0.182574, abs=1e-6)


def test_spread_disagreement_gives_fractional_ratio_and_zero_norm_error(run, bc_recorder):
    # Same total (100) but reshuffled: pure shape disagreement, normalization exact.
    out = run(_REF, [15, 15, 35, 35])
    s, n = out["shape"], out["normalization"]
    assert s["d_bar"] == pytest.approx(0.1, abs=1e-9)
    assert s["d_max"] == pytest.approx(0.05, abs=1e-9)
    assert s["dmax_dbar_ratio"] == 0.5
    assert s["mean_abs_frac_error_pct"] == pytest.approx(26.042, abs=1e-6)
    assert s["relative_l2"] == pytest.approx(0.182574, abs=1e-6)
    assert n["mean_abs_frac_error_pct"] == 0.0 and n["Delta"] == 0.0
    assert n["rmsle"] == pytest.approx(0.251802, abs=1e-6)


def test_unfilled_bins_are_dropped_from_both_blocks(run, bc_recorder):
    out = run(_REF, [10, None, 30, 40])
    assert out["n_bins"] == 4 and out["n_filled"] == 3
    assert out["shape"]["relative_l2"] == 0.0
    assert out["normalization"]["Delta"] == 0.0
    # The shape block saw only the three bins filled on both sides.
    ((pred, ref), _kwargs) = bc_recorder[0]
    assert list(pred) == [10.0, 30.0, 40.0] and list(ref) == [10.0, 30.0, 40.0]


# ── zero-yield / undefined metrics ──────────────────────────────────────────


def test_zero_prediction_yield_nulls_shape_but_keeps_normalization(run):
    out = run(_REF, [0, 0, 0, 0], n_toys=50)
    assert out["n_filled"] == 4
    assert out["shape"] == {key: None for key in _SHAPE_KEYS}
    assert out["normalization"]["mean_abs_frac_error_pct"] == 100.0
    assert out["normalization"]["Delta"] == 1.0
    assert out["normalization"]["relative_l2"] == 1.0
    assert out["normalization"]["rmsle"] == pytest.approx(3.185981, abs=1e-6)


def test_zero_reference_yield_nulls_shape_and_ratio_metrics(run):
    out = run([0, 0, 0, 0], [1, 2, 3, 4], n_toys=50)
    assert out["shape"] == {key: None for key in _SHAPE_KEYS}
    n = out["normalization"]
    assert n["mean_abs_frac_error_pct"] is None
    assert n["Delta"] is None
    assert n["relative_l2"] is None
    assert n["rmsle"] == pytest.approx(1.24494, abs=1e-6)


# ── Baker-Cousins forwarding ────────────────────────────────────────────────


def test_n_toys_default_is_not_forwarded_and_tolerance_is(run, bc_recorder):
    out = run(_REF, [20, 20, 30, 40], tolerance=0.05)
    assert out["shape"]["p_value"] == 0.5
    ((pred, ref), kwargs) = bc_recorder[0]
    assert kwargs == {"kind": "shape", "systematic_frac": 0.05}
    assert list(pred) == [20.0, 20.0, 30.0, 40.0] and list(ref) == [10.0, 20.0, 30.0, 40.0]


def test_explicit_n_toys_is_forwarded(run, bc_recorder):
    run(_REF, _REF, n_toys=77)
    assert bc_recorder[0][1] == {"kind": "shape", "systematic_frac": 0.0, "n_toys": 77}


def test_tolerance_defaults_to_zero_when_metrics_block_absent(run, bc_recorder):
    run(_REF, _REF, metrics_block=False)
    assert bc_recorder[0][1]["systematic_frac"] == 0.0
    assert "n_toys" not in bc_recorder[0][1]


def test_p_value_is_called_once_even_for_perfect_and_zero_yield_inputs(run, bc_recorder):
    run(_REF, [0, 0, 0, 0])
    assert len(bc_recorder) == 1  # called before/independent of the zero-yield guards


# ── Direct tests of the extracted helpers ───────────────────────────────────


def _aligned(ref, pred):
    import numpy as np

    from ColliderBench.Evals import histograms

    ref_a, pred_a = np.asarray(ref, dtype=float), np.asarray(pred, dtype=float)
    return histograms.Aligned(
        name="Y", bins=[], reference=ref_a, prediction=pred_a, n_bins=len(ref), n_filled=len(pred)
    )


def test_shape_metrics_helper_matches_score_run_shape_block(run, bc_recorder):
    from_run = run(_REF, [15, 15, 35, 35], tolerance=0.1, n_toys=9)["shape"]
    direct = score._shape_metrics(_aligned(_REF, [15, 15, 35, 35]), 0.1, 9)
    assert direct == from_run
    assert list(direct) == _SHAPE_KEYS
    assert bc_recorder[1][1] == {"kind": "shape", "systematic_frac": 0.1, "n_toys": 9}


def test_shape_metrics_helper_forwards_tolerance_and_omits_n_toys_when_none(bc_recorder):
    score._shape_metrics(_aligned(_REF, _REF), 0.25, None)
    assert bc_recorder[0][1] == {"kind": "shape", "systematic_frac": 0.25}


def test_shape_metrics_helper_perfect_and_zero_yield_cases(bc_recorder):
    perfect = score._shape_metrics(_aligned(_REF, _REF), 0.0, None)
    assert perfect["dmax_dbar_ratio"] is None and perfect["d_bar"] == 0.0
    zero = score._shape_metrics(_aligned(_REF, [0, 0, 0, 0]), 0.0, None)
    assert zero == {**{key: None for key in _SHAPE_KEYS}, "p_value": 0.5}


def test_normalization_metrics_helper_values_and_keys():
    out = score._normalization_metrics(_aligned(_REF, [20, 20, 30, 40]))
    assert list(out) == _NORM_KEYS
    assert out["mean_abs_frac_error_pct"] == 10.0 and out["Delta"] == 0.1
    assert out["rmsle"] == pytest.approx(0.323314, abs=1e-6)
    assert out["relative_l2"] == pytest.approx(0.182574, abs=1e-6)
    zero_ref = score._normalization_metrics(_aligned([0, 0, 0, 0], [1, 2, 3, 4]))
    assert zero_ref["Delta"] is None and zero_ref["relative_l2"] is None
