"""Tests for the trajectory failure-mode judge.

The LLM invocation itself is network/cost-bound and not exercised here;
we cover the parsing, schema, aggregation, and prompt construction.
"""

from __future__ import annotations

import json

import pytest

from LHCRecastBench.evaluation import trajectory_judge as tj


# ── Taxonomy shape ─────────────────────────────────────────────────────────


def test_nine_modes_exactly():
    assert len(tj.MODE_SPECS) == 9


def test_every_mode_in_a_known_class():
    for mode, spec in tj.MODE_SPECS.items():
        assert spec["class"] in tj.CLASSES, f"{mode} has bad class {spec['class']!r}"
        assert spec["rubric"], f"{mode} has empty rubric"


def test_class_distribution_matches_terminal_bench():
    counts = dict.fromkeys(tj.CLASSES, 0)
    for spec in tj.MODE_SPECS.values():
        counts[spec["class"]] += 1
    # 3 + 3 + 3 per Terminal-Bench TAT
    assert counts == {"execution": 3, "coherence": 3, "verification": 3}


# ── Prompt construction ────────────────────────────────────────────────────


def test_prompt_includes_every_mode_name():
    prompt = tj._build_prompt("transcript", "report", "task")
    for mode in tj.MODE_SPECS:
        assert mode in prompt, f"prompt missing {mode}"


def test_prompt_includes_transcript_and_report():
    prompt = tj._build_prompt("TRANSCRIPT_TOKEN", "REPORT_TOKEN", "TASK_TOKEN")
    assert "TRANSCRIPT_TOKEN" in prompt
    assert "REPORT_TOKEN" in prompt
    assert "TASK_TOKEN" in prompt


def test_prompt_instructs_conservative_matching():
    prompt = tj._build_prompt("t", "r", "task")
    # "when in doubt, NO MATCH" is load-bearing per TB's calibration work.
    assert "NO MATCH" in prompt
    assert "false positives" in prompt.lower()


# ── Verdict parsing ────────────────────────────────────────────────────────


def test_parse_plain_json_object():
    resp = json.dumps({m: {"matched": False, "evidence": []} for m in tj.MODE_SPECS})
    parsed = tj._parse_verdict(resp)
    assert set(parsed) == set(tj.MODE_SPECS)


def test_parse_handles_leading_prose():
    payload = {"disobey_spec": {"matched": True, "evidence": ["turn 3: ..."]}}
    resp = f"Here is my assessment:\n\n{json.dumps(payload)}\n\nThat's all."
    parsed = tj._parse_verdict(resp)
    assert parsed == payload


def test_parse_handles_markdown_fences():
    payload = {"step_repetition": {"matched": False, "evidence": []}}
    resp = f"```json\n{json.dumps(payload)}\n```"
    parsed = tj._parse_verdict(resp)
    assert parsed == payload


def test_parse_rejects_no_json():
    with pytest.raises(ValueError, match="No JSON"):
        tj._parse_verdict("I cannot comply.")


def test_parse_rejects_unbalanced_braces():
    with pytest.raises(ValueError, match="Unbalanced"):
        tj._parse_verdict('{"disobey_spec": {"matched": true, "evidence": [')


# ── Aggregation ────────────────────────────────────────────────────────────


def test_attach_classes_fills_missing_modes_as_no_match():
    raw = {"disobey_spec": {"matched": True, "evidence": ["e1"]}}
    result = tj._attach_classes(raw)
    assert result["n_matched"] == 1
    for mode in tj.MODE_SPECS:
        assert mode in result["modes"]
        if mode == "disobey_spec":
            assert result["modes"][mode]["matched"] is True
        else:
            assert result["modes"][mode]["matched"] is False
            assert result["modes"][mode]["evidence"] == []


def test_attach_classes_computes_prevalence():
    # Match 1 execution, 2 coherence, 0 verification → 1/3, 2/3, 0 prevalence
    raw = {
        "disobey_spec": {"matched": True, "evidence": ["e"]},
        "reasoning_action_mismatch": {"matched": True, "evidence": ["e"]},
        "context_loss": {"matched": True, "evidence": ["e"]},
    }
    result = tj._attach_classes(raw)
    assert result["n_matched"] == 3
    assert result["classes"]["execution"]["prevalence"] == pytest.approx(1 / 3, abs=1e-3)
    assert result["classes"]["coherence"]["prevalence"] == pytest.approx(2 / 3, abs=1e-3)
    assert result["classes"]["verification"]["prevalence"] == 0.0


def test_attach_classes_caps_evidence_at_three():
    raw = {
        "disobey_spec": {
            "matched": True,
            "evidence": ["e1", "e2", "e3", "e4", "e5"],
        }
    }
    result = tj._attach_classes(raw)
    assert len(result["modes"]["disobey_spec"]["evidence"]) == 3


def test_attach_classes_with_zero_matches():
    raw = {m: {"matched": False, "evidence": []} for m in tj.MODE_SPECS}
    result = tj._attach_classes(raw)
    assert result["n_matched"] == 0
    for cls in tj.CLASSES:
        assert result["classes"][cls]["prevalence"] == 0.0


# ── CLI smoke ──────────────────────────────────────────────────────────────


def test_cli_help_does_not_crash():
    with pytest.raises(SystemExit) as exc:
        tj.main(["--help"])
    assert exc.value.code == 0


def test_cli_rejects_missing_run_path(capsys, tmp_path):
    rc = tj.main([str(tmp_path / "nonexistent")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
