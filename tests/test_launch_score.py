"""Characterization of score reporting + exit-code demotion in launch_single_run.

Pins the *current* stdout/stderr text and exit codes of the post-agent phase of
agent_runtime/launch.py (print of the score summary, "agent exited cleanly but
produced no filled bins" demotion, prior-CLI-failure handling), so the
extraction of that logic into `_report_score_results` is shown to be
behavior-preserving.

Most tests drive the public entry point `launch_single_run(...)` with the
workspace build, the sandboxed agent run and the offline plots stubbed out, and
the scorer injected via `score=`; they do not depend on the helper. The last
section tests `_report_score_results` directly. Offline: no container, LLM,
network or real workspace.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_runtime import launch

_POPULATED = {
    "n_filled": 4,
    "n_bins": 4,
    "shape": {"p_value": 0.5, "mean_abs_frac_error_pct": 3.2, "jensen_shannon": 0.01},
    "normalization": {"mean_abs_frac_error_pct": 7.5},
}


@pytest.fixture
def run(monkeypatch, tmp_path, repo_root, task_id, capsys):
    """Return `go(score_dict, cli_error=None)` -> (exit_code, stdout, stderr, run_dir)."""

    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"

    def fake_build_workspace(*_args, **_kwargs):
        workspace.mkdir(parents=True)
        return workspace

    monkeypatch.setattr(launch, "build_workspace", fake_build_workspace)
    monkeypatch.setattr(launch, "_run_offline_evals", lambda *_a, **_k: None)

    def go(scores, cli_error: Exception | None = None):
        def fake_run_in_sandbox(*_args, **_kwargs):
            if cli_error is not None:
                raise cli_error

        monkeypatch.setattr(launch, "_run_in_sandbox", fake_run_in_sandbox)
        code = launch.launch_single_run(
            "simple",
            lambda paper_ref: "prompt",
            repo_root,
            argv=[
                "--task",
                task_id,
                "--runner",
                "claude",
                "--auth",
                "oauth",
                "--sandbox",
                "none",
                "--run-name",
                "golden",
            ],
            score=lambda _workspace: scores,
        )
        out = capsys.readouterr()
        return code, out.out, out.err, run_dir

    return go


def _run_info(run_dir) -> dict:
    return json.loads((run_dir / "run_info.json").read_text())


def test_score_error_prints_error_and_fails(run):
    code, out, err, run_dir = run({"error": "boom"})
    assert code == 1
    assert "  ERROR: boom" in out
    assert "Filled:" not in out
    assert "marking run as failed" not in err
    assert json.loads((run_dir / "eval" / "score.json").read_text()) == {"error": "boom"}
    assert _run_info(run_dir)["exit_code"] == 1


def test_populated_metrics_are_reported_and_exit_zero(run):
    code, out, err, run_dir = run(dict(_POPULATED))
    assert code == 0
    assert "  Filled: 4/4 bins" in out
    assert "  Shape: bin_err=3.2%  p=0.5  JSD=0.01" in out
    assert "  Norm: frac_err=7.5%" in out
    assert "marking run as failed" not in err
    assert "ERROR" not in out
    assert _run_info(run_dir)["exit_code"] == 0


def test_shape_line_requires_p_value_and_norm_line_requires_error(run):
    scores = {
        "n_filled": 2,
        "n_bins": 2,
        "shape": {"mean_abs_frac_error_pct": 1.0, "jensen_shannon": 0.2},
        "normalization": {"relative_l2": 0.1},
    }
    code, out, _err, _ = run(scores)
    assert code == 0
    assert "  Filled: 2/2 bins" in out
    assert "Shape:" not in out
    assert "Norm:" not in out


def test_zero_filled_with_bins_is_demoted_to_failure(run):
    code, out, err, run_dir = run({"n_filled": 0, "n_bins": 5})
    assert code == 1
    assert "  Filled: 0/5 bins" in out
    assert "  Agent exited cleanly but produced no filled bins — marking run as failed." in err
    assert _run_info(run_dir)["exit_code"] == 1


def test_missing_n_filled_key_counts_as_zero_and_is_demoted(run):
    code, out, err, _ = run({"n_bins": 3})
    assert code == 1
    assert "  Filled: 0/3 bins" in out
    assert "produced no filled bins" in err


def test_zero_total_bins_is_not_demoted(run):
    code, out, err, run_dir = run({"n_filled": 0, "n_bins": 0})
    assert code == 0
    assert "  Filled: 0/0 bins" in out
    assert "produced no filled bins" not in err
    assert _run_info(run_dir)["exit_code"] == 0


def test_partially_filled_is_not_demoted(run):
    code, out, err, _ = run({"n_filled": 1, "n_bins": 5})
    assert code == 0
    assert "  Filled: 1/5 bins" in out
    assert "marking run as failed" not in err


def test_prior_cli_failure_forces_failure_even_with_good_score(run):
    code, out, err, run_dir = run(dict(_POPULATED), cli_error=subprocess.CalledProcessError(2, "x"))
    assert code == 1
    assert "  Agent CLI exited with code 2 — marking run as failed." in err
    # Scoring still runs and is reported after a CLI failure.
    assert "Scoring results..." in out
    assert "  Filled: 4/4 bins" in out
    assert _run_info(run_dir)["exit_code"] == 1


def test_prior_cli_failure_with_zero_filled_has_no_extra_demotion_message(run):
    code, _out, err, _ = run(
        {"n_filled": 0, "n_bins": 5}, cli_error=subprocess.CalledProcessError(3, "x")
    )
    assert code == 1
    assert "  Agent CLI exited with code 3 — marking run as failed." in err
    assert "Agent exited cleanly but produced no filled bins" not in err


def test_prior_cli_failure_with_score_error_prints_error_and_stays_failed(run):
    code, out, err, _ = run({"error": "missing"}, cli_error=subprocess.CalledProcessError(1, "x"))
    assert code == 1
    assert "  ERROR: missing" in out
    assert "  Agent CLI exited with code 1 — marking run as failed." in err


# ── Direct tests of the extracted helper: _report_score_results(scores, exit_code) -> int ──


def _report(capsys, scores, exit_code=0):
    code = launch._report_score_results(scores, exit_code)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_helper_error_returns_one_from_zero_and_keeps_one(capsys):
    assert _report(capsys, {"error": "boom"}, 0)[0] == 1
    code, out, err = _report(capsys, {"error": "boom"}, 1)
    assert (code, out, err) == (1, "  ERROR: boom\n", "")


def test_helper_populated_metrics_return_input_exit_code_and_print_stdout_only(capsys):
    code, out, err = _report(capsys, dict(_POPULATED), 0)
    assert code == 0
    assert out == (
        "  Filled: 4/4 bins\n" "  Shape: bin_err=3.2%  p=0.5  JSD=0.01\n" "  Norm: frac_err=7.5%\n"
    )
    assert err == ""
    assert _report(capsys, dict(_POPULATED), 1)[0] == 1


def test_helper_demotes_clean_exit_with_no_filled_bins(capsys):
    code, out, err = _report(capsys, {"n_filled": 0, "n_bins": 5}, 0)
    assert code == 1
    assert out == "  Filled: 0/5 bins\n"
    assert err == "  Agent exited cleanly but produced no filled bins — marking run as failed.\n"


def test_helper_does_not_demote_when_already_failed_or_no_bins(capsys):
    code, _out, err = _report(capsys, {"n_filled": 0, "n_bins": 5}, 1)
    assert (code, err) == (1, "")
    code, out, err = _report(capsys, {"n_filled": 0, "n_bins": 0}, 0)
    assert (code, out, err) == (0, "  Filled: 0/0 bins\n", "")
