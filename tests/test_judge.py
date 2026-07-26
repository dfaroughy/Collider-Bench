"""Tests for ColliderBench.Evals.judge helpers.

The judge module mostly orchestrates an LLM call; we don't exercise that
in unit tests. What we DO test is the pure-Python provenance-driven
rewrite of results YAML — `_write_corrected_results` — which is the part
that silently corrupts every replicate when buggy.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ColliderBench.Evals.judge import (
    _find_result_file,
    _write_corrected_results,
    extract_session_summary,
    find_session_logs,
)


def _write_jsonl(path: Path, messages: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(message) for message in messages) + "\n")
    return path


def test_find_session_logs_prefers_nonempty_jsonl(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    text = tmp_path / "session_log.txt"
    jsonl.write_text('{"type":"thread.started"}\n')
    text.write_text("fallback")
    assert find_session_logs(tmp_path) == [jsonl]


def test_find_session_logs_falls_back_to_text(tmp_path):
    text = tmp_path / "session_log.txt"
    text.write_text("fallback")
    assert find_session_logs(tmp_path) == [text]


def test_find_session_logs_uses_text_when_jsonl_empty(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    text = tmp_path / "session_log.txt"
    jsonl.touch()
    text.write_text("fallback")
    assert find_session_logs(tmp_path) == [text]


def test_extract_session_summary_claude(tmp_path):
    session = _write_jsonl(
        tmp_path / "session.jsonl",
        [
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Claude plan"},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "run-analysis"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "ERROR: simulation failed",
                        }
                    ]
                },
            },
        ],
    )
    summary = extract_session_summary(session)
    assert "[THOUGHT] Claude plan" in summary
    assert "[TOOL] Bash: run-analysis" in summary
    assert "[ERROR] ERROR: simulation failed" in summary


def test_extract_session_summary_codex(tmp_path):
    session = _write_jsonl(
        tmp_path / "session.jsonl",
        [
            {"type": "thread.started", "thread_id": "test"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Codex plan"},
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "run-analysis",
                    "aggregated_output": "bad input",
                    "exit_code": 1,
                    "status": "failed",
                },
            },
        ],
    )
    summary = extract_session_summary(session)
    assert "[THOUGHT] Codex plan" in summary
    assert "[TOOL] Bash: run-analysis" in summary
    assert "[ERROR] exit_code=1: bad input" in summary


def test_extract_session_summary_forge(tmp_path):
    session = _write_jsonl(
        tmp_path / "session.jsonl",
        [
            {"type": "init"},
            {
                "message": {
                    "text": {
                        "role": "Assistant",
                        "content": "Forge plan",
                        "tool_calls": [
                            {
                                "name": "shell",
                                "arguments": {"command": "run-analysis"},
                            }
                        ],
                    }
                }
            },
            {
                "message": {
                    "tool": {
                        "name": "shell",
                        "output": {"is_error": True, "values": ["bad input"]},
                    }
                }
            },
        ],
    )
    summary = extract_session_summary(session)
    assert "[THOUGHT] Forge plan" in summary
    assert "[TOOL] Bash: run-analysis" in summary
    assert "[ERROR]" in summary
    assert "bad input" in summary


def test_extract_session_summary_plain_text_fallback(tmp_path):
    session = tmp_path / "session_log.txt"
    session.write_text("plain text trajectory")
    assert extract_session_summary(session) == "plain text trajectory"


def test_extract_session_summary_respects_shared_character_limit(tmp_path):
    session = _write_jsonl(
        tmp_path / "session.jsonl",
        [
            {"type": "thread.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "x" * 100},
            },
        ],
    )
    summary = extract_session_summary(session, max_chars=25)
    assert summary.startswith("[THOUGHT]")
    assert len(summary) <= 25 + len("[... truncated ...]\n")


def _make_template_results_dir(root: Path) -> Path:
    """Create a minimal HEPData-style results dir with one histogram file.

    Returns the inner ``results/`` directory — that's what production
    code passes as ``original_dir`` to ``_write_corrected_results``.
    """
    root.mkdir(parents=True, exist_ok=True)
    results = root / "results"
    results.mkdir()
    metadata = {
        "name": "histogram_test",
        "type": "histogram",
        "category": "shape",
    }
    histogram = {
        "independent_variables": [
            {
                "header": {"name": "ETmiss [GeV]"},
                "values": [{"low": 0, "high": 100}, {"low": 100, "high": 200}],
            },
        ],
        "dependent_variables": [
            {
                "header": {"name": "agent_yield"},
                "values": [
                    {"value": 5.0, "errors": [{"symerror": 0.5}]},
                    {"value": 7.0, "errors": [{"symerror": 0.7}]},
                ],
            }
        ],
    }
    yaml_path = results / "histogram_test.yaml"
    with yaml_path.open("w") as fh:
        yaml.safe_dump_all([metadata, histogram], fh, sort_keys=False)
    return results


def _read_dep_values(yaml_path: Path) -> list:
    docs = list(yaml.safe_load_all(yaml_path.read_text()))
    # Histogram doc is the one with dependent_variables.
    hist = next(d for d in docs if isinstance(d, dict) and "dependent_variables" in d)
    return hist["dependent_variables"][0]["values"]


# ── _find_result_file ──────────────────────────────────────────────────────


def test_find_result_file_exact_match(tmp_path):
    results = _make_template_results_dir(tmp_path)
    found = _find_result_file(results, "histogram_test.yaml")
    assert found == results / "histogram_test.yaml"


def test_find_result_file_stem_only(tmp_path):
    results = _make_template_results_dir(tmp_path)
    found = _find_result_file(results, "histogram_test")
    assert found == results / "histogram_test.yaml"


def test_find_result_file_missing_returns_none(tmp_path):
    results = _make_template_results_dir(tmp_path)
    assert _find_result_file(results, "nonexistent") is None


# ── _write_corrected_results: corrected_results path (full overwrite) ──────


def test_write_corrected_results_full_overwrite(tmp_path):
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    new_payload = [
        {"name": "histogram_test", "type": "histogram", "category": "shape"},
        {
            "independent_variables": [{"header": {"name": "ETmiss [GeV]"}, "values": []}],
            "dependent_variables": [
                {
                    "header": {"name": "agent_yield"},
                    "values": [{"value": 99.0, "errors": []}],
                }
            ],
        },
    ]
    provenance = {"corrected_results": {"histogram_test.yaml": new_payload}}
    _write_corrected_results(provenance, original, corrected)
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    assert vals == [{"value": 99.0, "errors": []}]


# ── _write_corrected_results: per-series correction path ───────────────────


def test_write_corrected_results_per_series_overwrites_values(tmp_path):
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    provenance = {
        "series": {
            "histogram_test/agent_yield": {
                "classification": "COPIED",
                "corrected_values": [10.0, 20.0],
            }
        }
    }
    _write_corrected_results(provenance, original, corrected)
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    # Values overwritten; symerrors cleared to None.
    assert [v["value"] for v in vals] == [10.0, 20.0]
    for v in vals:
        for err in v["errors"]:
            assert err["symerror"] is None


def test_write_corrected_results_skips_unrelated_classifications(tmp_path):
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    provenance = {
        "series": {
            "histogram_test/agent_yield": {
                "classification": "VERIFIED",  # not in the rewrite-trigger list
                "corrected_values": [99.0, 99.0],
            }
        }
    }
    _write_corrected_results(provenance, original, corrected)
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    # Untouched: original 5.0 / 7.0 still in place.
    assert [v["value"] for v in vals] == [5.0, 7.0]


def test_write_corrected_results_partial_traceable_without_corrected_values_skipped(tmp_path):
    """PARTIALLY_TRACEABLE entries without corrected_values must not nuke the series."""
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    provenance = {
        "series": {
            "histogram_test/agent_yield": {
                "classification": "PARTIALLY_TRACEABLE",
                # NB: no corrected_values key
            }
        }
    }
    _write_corrected_results(provenance, original, corrected)
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    assert [v["value"] for v in vals] == [5.0, 7.0]


def test_write_corrected_results_null_but_computed_zeros_values_when_corrected_none(tmp_path):
    """classification=NULL_BUT_COMPUTED with corrected_values=None → values become None."""
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    provenance = {
        "series": {
            "histogram_test/agent_yield": {
                "classification": "NULL_BUT_COMPUTED",
                "corrected_values": None,
            }
        }
    }
    _write_corrected_results(provenance, original, corrected)
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    assert [v["value"] for v in vals] == [None, None]


def test_write_corrected_results_malformed_series_key_skipped(tmp_path):
    """series_key without exactly one '/' must be skipped, not crash."""
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    provenance = {
        "series": {
            "no_slash_here": {
                "classification": "FABRICATED",
                "corrected_values": [1.0, 2.0],
            },
            "too/many/slashes": {
                "classification": "FABRICATED",
                "corrected_values": [1.0, 2.0],
            },
        }
    }
    _write_corrected_results(provenance, original, corrected)
    # Original values intact, no exception raised.
    vals = _read_dep_values(corrected / "histogram_test.yaml")
    assert [v["value"] for v in vals] == [5.0, 7.0]


def test_write_corrected_results_overwrites_existing_corrected_dir(tmp_path):
    """A second invocation must replace the prior corrected_dir, not merge into it."""
    original = _make_template_results_dir(tmp_path / "orig")
    corrected = tmp_path / "corrected"
    # Pre-populate corrected/ with a stale file that should disappear.
    corrected.mkdir()
    (corrected / "stale.txt").write_text("leftover")

    _write_corrected_results({}, original, corrected)
    assert not (corrected / "stale.txt").exists()
    # Original results were copied through.
    assert (corrected / "histogram_test.yaml").is_file()
