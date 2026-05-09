"""End-to-end scorer tests using a real task fixture.

Picks an existing task that has both a reference YAML and a template, fakes
a `run_dir` with a copy of the reference as the agent's "filled" output
(perfect prediction), and runs `score_run`. Then checks that the JSON has
the expected schema and the metrics agree with hand-computed expectations
for the perfect-match case.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ColliderBench.Evals import score


_BENCH_ROOT = Path(__file__).resolve().parent.parent / "ColliderBench"
_TASKS_ROOT = _BENCH_ROOT / "tasks"


def _pick_task_with_reference() -> tuple[str, str, Path]:
    """Find any task that has a single template + a reference file in shared/."""
    import tomllib

    for task_dir in sorted(_TASKS_ROOT.iterdir()):
        if not task_dir.is_dir() or task_dir.name == "shared":
            continue
        toml_path = task_dir / "task.toml"
        template_dir = task_dir / "template"
        if not toml_path.is_file() or not template_dir.is_dir():
            continue
        templates = sorted(template_dir.glob("*.yaml"))
        if len(templates) != 1:
            continue
        toml = tomllib.loads(toml_path.read_text())
        paper = (toml.get("task") or {}).get("paper")
        if not paper:
            continue
        ref_path = _TASKS_ROOT / "shared" / paper / "reference" / templates[0].name
        if ref_path.is_file():
            return task_dir.name, paper, ref_path
    return "", "", Path()


def _make_perfect_run(tmp_path: Path) -> tuple[Path, str, Path]:
    """Lay out a fake <run_dir> whose results/<file>.yaml IS the reference.
    Returns (run_dir, task_id, reference_file)."""
    task_id, _paper, ref_path = _pick_task_with_reference()
    if not task_id:
        pytest.skip("no task with both template + shared reference found")

    run_dir = tmp_path / "perfect_run"
    workspace = run_dir / "workspace"
    results = workspace / "results"
    results.mkdir(parents=True)
    shutil.copy2(ref_path, results / ref_path.name)

    (run_dir / "run_info.json").write_text(
        json.dumps({"task_id": task_id, "agent_id": "test", "runner": "claude", "model": "test"})
    )
    return run_dir, task_id, ref_path


def test_score_run_perfect_match_schema(tmp_path):
    """Perfect prediction (results == reference): schema fields + metric edges.

    Whether `shape` and/or `normalization` blocks appear depends on the task's
    `score_mode`, which is real and set by the picked task.toml. We assert
    only the blocks that should be present given the picked mode.
    """
    run_dir, task_id, _ref = _make_perfect_run(tmp_path)
    result = score.score_run(run_dir, n_toys=2_000)
    assert result["task_id"] == task_id
    mode = result["score_mode"]

    if mode in ("shape", "shape_norm"):
        assert "shape" in result
        s = result["shape"]
        assert {
            "mean_abs_frac_error_pct",
            "p_value",
            "jensen_shannon",
            "jensen_shannon_dist",
            "d_bar",
            "d_max",
            "dmax_dbar_ratio",
        } <= set(s)
        # Perfect match → d_bar = 0 → ratio is undefined (null).
        assert s["dmax_dbar_ratio"] is None
        if s["mean_abs_frac_error_pct"] is not None:
            assert s["mean_abs_frac_error_pct"] == pytest.approx(0.0, abs=1e-6)
        if s["jensen_shannon"] is not None:
            assert s["jensen_shannon"] == pytest.approx(0.0, abs=1e-6)
            assert s["jensen_shannon_dist"] == pytest.approx(0.0, abs=1e-6)
    else:
        assert "shape" not in result, f"shape block should be absent for mode={mode!r}"

    if mode in ("yield", "shape_norm"):
        assert "normalization" in result
        n = result["normalization"]
        assert "mean_abs_frac_error_pct" in n
        if n["mean_abs_frac_error_pct"] is not None:
            assert n["mean_abs_frac_error_pct"] == pytest.approx(0.0, abs=1e-6)
    else:
        assert (
            "normalization" not in result
        ), f"normalization block should be absent for mode={mode!r}"


def test_score_run_writes_score_json(tmp_path):
    """The CLI emit path writes <run>/eval/score.json (round-trips faithfully)."""
    run_dir, _task_id, _ref = _make_perfect_run(tmp_path)
    result = score.score_run(run_dir, n_toys=2_000)
    out = score._emit(run_dir, result, with_plots=False)
    assert out.is_file()
    loaded = json.loads(out.read_text())
    assert loaded == result


def test_score_run_dmax_dbar_ratio_in_expected_bounds(tmp_path):
    """For a non-perfect prediction, ratio must lie in [1/K, 1]."""
    # Build a perfect run, then perturb the prediction to break perfection.
    run_dir, _task_id, _ref = _make_perfect_run(tmp_path)
    pred_files = list((run_dir / "workspace" / "results").glob("*.yaml"))
    assert len(pred_files) == 1
    # Tweak one bin's value to 1.5x to introduce disagreement.
    txt = pred_files[0].read_text()
    # Cheap perturbation: find first numeric "value: <num>" and double it.
    import re

    new_txt, n_subs = re.subn(
        r"(value:\s*)(\d+(?:\.\d+)?)",
        lambda m: f"{m.group(1)}{float(m.group(2)) * 2.0}",
        txt,
        count=1,
    )
    assert n_subs == 1
    pred_files[0].write_text(new_txt)

    result = score.score_run(run_dir, n_toys=2_000)
    s = result["shape"]
    if s.get("d_bar") is None or s.get("d_max") is None:
        pytest.skip("d_bar/d_max undefined for this fixture (zero-total)")
    K = result["n_bins"]
    ratio = s["dmax_dbar_ratio"]
    assert ratio is not None
    assert 1.0 / K - 1e-9 <= ratio <= 1.0 + 1e-9


@pytest.mark.parametrize(
    "mode,expect_shape,expect_norm",
    [
        ("shape", True, False),
        ("yield", False, True),
        ("shape_norm", True, True),
    ],
)
def test_score_run_omits_block_per_mode(tmp_path, monkeypatch, mode, expect_shape, expect_norm):
    """Force each score_mode in turn and verify the right blocks are present/absent."""
    run_dir, _task_id, _ref = _make_perfect_run(tmp_path)
    real_load = score._load_task

    def _patched_load_task(task_id):
        task = real_load(task_id)
        return {**task, "mode": mode}

    monkeypatch.setattr(score, "_load_task", _patched_load_task)
    result = score.score_run(run_dir, n_toys=2_000)
    assert result["score_mode"] == mode
    assert ("shape" in result) == expect_shape
    assert ("normalization" in result) == expect_norm


def test_resolve_paths_returns_expected_keys(tmp_path):
    run_dir, _task_id, _ref = _make_perfect_run(tmp_path)
    paths = score.resolve_paths(run_dir)
    expected_keys = {
        "task_id",
        "paper",
        "run_dir",
        "workspace",
        "results_dir",
        "eval_dir",
        "data_filename",
        "header_name",
        "reference_file",
        "task",
    }
    assert expected_keys <= set(paths)
    assert paths["reference_file"].is_file()
    assert paths["results_dir"].is_dir()


def test_score_run_missing_run_info_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="run_info.json"):
        score.score_run(tmp_path)


@pytest.mark.parametrize(
    "mode,expected_plots",
    [
        ("shape", ["_shape.png"]),
        ("yield", ["_yield.png"]),
        ("shape_norm", ["_shape.png", "_yield.png"]),
    ],
)
def test_emit_plots_match_mode(tmp_path, monkeypatch, mode, expected_plots):
    """Plot files mirror the JSON: shape-mode → no _yield.png, etc."""
    run_dir, _task_id, _ref = _make_perfect_run(tmp_path)
    real_load = score._load_task

    def _patched_load_task(task_id):
        task = real_load(task_id)
        return {**task, "mode": mode}

    monkeypatch.setattr(score, "_load_task", _patched_load_task)
    result = score.score_run(run_dir, n_toys=2_000)
    score._emit(run_dir, result, with_plots=True)

    plots_dir = run_dir / "eval" / "plots"
    if expected_plots:
        assert plots_dir.is_dir()
        names = sorted(p.name for p in plots_dir.glob("*.png"))
        suffixes = sorted({n[n.rfind("_") :] for n in names})
        assert suffixes == sorted(
            expected_plots
        ), f"mode={mode!r}: expected suffixes {sorted(expected_plots)}, got {suffixes} ({names})"
