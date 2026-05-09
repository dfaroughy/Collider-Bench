"""Tests for ColliderBench.Evals.judge helpers.

The judge module mostly orchestrates an LLM call; we don't exercise that
in unit tests. What we DO test is the pure-Python provenance-driven
rewrite of results YAML — `_write_corrected_results` — which is the part
that silently corrupts every replicate when buggy.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ColliderBench.Evals.judge import _find_result_file, _write_corrected_results


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
