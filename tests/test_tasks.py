"""End-to-end validation of every task under LHCRecastBench/tasks/.

Every test is parametrized over the full set of tasks discovered on disk,
so adding a new task automatically extends the suite. The goal is to catch
the kinds of bugs that silently break scoring without crashing the run:

  - task.toml id mismatched with directory name (copy-paste regressions)
  - template histogram filename pointing at the wrong reference
  - template header.name not present in the reference series
  - template bin count not matching the reference
  - reference values accidentally leaked into the null-filled template
  - missing paper PDF, missing required metadata fields, etc.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from LHCRecastBench.evaluation._resolve import (
    TASKS_ROOT,
    _load_task_identity,
    _reference_file,
)


REQUIRED_TASK_TOML_FIELDS = ("id", "paper", "type", "target", "observable")
REQUIRED_METADATA_FIELDS = (
    "instructions",
    "description",
    "target",
    "cm_energy_gev",
    "luminosity_fb",
)
ALLOWED_TYPES = {"simulation", "validation"}
ALLOWED_SCORE_MODES = {"shape_norm", "shape"}
ALLOWED_PLOT_MODES = {"Events/bin", "Events/GeV"}


def _all_tasks() -> list[str]:
    """Discover every task dir under tasks/ (excluding the shared/ pool)."""
    if not TASKS_ROOT.is_dir():
        return []
    return sorted(p.name for p in TASKS_ROOT.iterdir() if p.is_dir() and p.name != "shared")


TASK_IDS = _all_tasks()


@pytest.fixture(params=TASK_IDS, ids=lambda tid: tid)
def task_id_param(request) -> str:
    return request.param


def _task_toml(task_id: str) -> dict:
    return tomllib.loads((TASKS_ROOT / task_id / "task.toml").read_text())


def _template_docs(task_id: str) -> tuple[Path, list]:
    """Return (template_path, [yaml docs]) for a task."""
    template_dir = TASKS_ROOT / task_id / "template"
    files = sorted(list(template_dir.glob("*.yaml")) + list(template_dir.glob("*.yml")))
    assert files, f"No histogram .yml/.yaml in {template_dir}"
    return files[0], list(yaml.safe_load_all(files[0].read_text()))


def _hist_doc(docs: list) -> dict:
    return next(
        (d for d in docs if isinstance(d, dict) and "dependent_variables" in d),
        None,
    )


def _meta_doc(docs: list) -> dict:
    return next(
        (d for d in docs if isinstance(d, dict) and "instructions" in d),
        None,
    )


# ── Discovery ────────────────────────────────────────────────────────────


def test_at_least_one_task_exists():
    assert TASK_IDS, "No tasks under LHCRecastBench/tasks/"


# ── task.toml ─────────────────────────────────────────────────────────────


def test_task_toml_parses(task_id_param):
    path = TASKS_ROOT / task_id_param / "task.toml"
    assert path.is_file(), f"Missing {path}"
    tomllib.loads(path.read_text())


def test_task_toml_required_fields(task_id_param):
    toml = _task_toml(task_id_param)
    block = toml.get("task")
    assert block, f"{task_id_param}: missing [task] block"
    for field in REQUIRED_TASK_TOML_FIELDS:
        assert block.get(field), f"{task_id_param}: [task].{field} missing or empty"


def test_task_toml_id_matches_dirname(task_id_param):
    """Catches copy-paste bugs where a new task forgot to update [task].id."""
    toml = _task_toml(task_id_param)
    assert (
        toml["task"]["id"] == task_id_param
    ), f"{task_id_param}: [task].id={toml['task']['id']!r} differs from dir name"


def test_task_type_is_known(task_id_param):
    toml = _task_toml(task_id_param)
    assert toml["task"]["type"] in ALLOWED_TYPES


def test_metrics_tolerance_set_and_valid(task_id_param):
    """Each task must declare a per-bin systematic tolerance under
    [metrics].tolerance — the scoring knob that broadens the toy null in
    score.py. Non-negative float; missing means stats-only scoring,
    which is allowed by code but discouraged for benchmark tasks."""
    toml = _task_toml(task_id_param)
    metrics = toml.get("metrics") or {}
    assert "tolerance" in metrics, f"{task_id_param}: [metrics].tolerance missing from task.toml"
    tol = metrics["tolerance"]
    assert isinstance(tol, (int, float)) and not isinstance(
        tol, bool
    ), f"{task_id_param}: [metrics].tolerance must be a number, got {type(tol).__name__}"
    assert tol >= 0, f"{task_id_param}: [metrics].tolerance must be non-negative"


def test_metrics_score_mode_valid(task_id_param):
    toml = _task_toml(task_id_param)
    metrics = toml.get("metrics") or {}
    mode = metrics.get("score", "shape_norm")
    assert mode in ALLOWED_SCORE_MODES, (
        f"{task_id_param}: [metrics].score must be one of {sorted(ALLOWED_SCORE_MODES)}, "
        f"got {mode!r}"
    )


def test_metrics_plot_mode_set_and_valid(task_id_param):
    toml = _task_toml(task_id_param)
    metrics = toml.get("metrics") or {}
    assert "plot" in metrics, f"{task_id_param}: [metrics].plot missing from task.toml"
    mode = metrics["plot"]
    assert mode in ALLOWED_PLOT_MODES, (
        f"{task_id_param}: [metrics].plot must be one of {sorted(ALLOWED_PLOT_MODES)}, "
        f"got {mode!r}"
    )


# ── TASK.md and template/ structure ──────────────────────────────────────


def test_task_md_present(task_id_param):
    path = TASKS_ROOT / task_id_param / "TASK.md"
    assert path.is_file(), f"Missing {path}"
    assert path.read_text().strip(), f"{path} is empty"


def test_template_has_exactly_one_histogram_file(task_id_param):
    """Resolver expects a single .yml/.yaml under template/."""
    template_dir = TASKS_ROOT / task_id_param / "template"
    assert template_dir.is_dir(), f"Missing {template_dir}"
    files = list(template_dir.glob("*.yml")) + list(template_dir.glob("*.yaml"))
    assert len(files) == 1, (
        f"{task_id_param}: expected 1 histogram in template/, got " f"{[f.name for f in files]}"
    )


def test_template_no_legacy_description_toml(task_id_param):
    """description.toml has been retired — metadata lives inside the .yaml."""
    desc = TASKS_ROOT / task_id_param / "template" / "description.toml"
    assert (
        not desc.exists()
    ), f"{desc} should not exist; metadata is now embedded in the histogram .yaml"


def test_template_is_two_yaml_docs(task_id_param):
    path, docs = _template_docs(task_id_param)
    assert (
        _meta_doc(docs) is not None
    ), f"{path}: no metadata document (expected one with `instructions`)"
    assert (
        _hist_doc(docs) is not None
    ), f"{path}: no histogram document (expected one with `dependent_variables`)"


def test_metadata_required_fields(task_id_param):
    _, docs = _template_docs(task_id_param)
    meta = _meta_doc(docs)
    for field in REQUIRED_METADATA_FIELDS:
        assert field in meta, f"{task_id_param}: metadata missing {field!r}"


def test_metadata_luminosity_is_positive_number(task_id_param):
    _, docs = _template_docs(task_id_param)
    meta = _meta_doc(docs)
    lumi = meta.get("luminosity_fb")
    assert (
        isinstance(lumi, (int, float)) and lumi > 0
    ), f"{task_id_param}: luminosity_fb={lumi!r} must be a positive number"


# ── Skeleton invariants ──────────────────────────────────────────────────


def test_template_values_are_null(task_id_param):
    """Templates must be skeletons — no leaked reference values."""
    _, docs = _template_docs(task_id_param)
    deps = (_hist_doc(docs) or {}).get("dependent_variables") or []
    for dep in deps:
        for entry in dep.get("values", []):
            v = entry.get("value") if isinstance(entry, dict) else None
            assert v is None, (
                f"{task_id_param}: template has non-null value {v!r}; "
                "templates must be null-filled"
            )


def test_template_bin_count_internal_consistency(task_id_param):
    """dep_variables[0].values length must match indep_variables[0].values length."""
    _, docs = _template_docs(task_id_param)
    hist = _hist_doc(docs) or {}
    deps = hist.get("dependent_variables") or []
    indeps = hist.get("independent_variables") or []
    if not (deps and indeps):
        pytest.skip("dep or indep variables missing/empty")
    n_dep = len(deps[0].get("values", []))
    n_indep = len(indeps[0].get("values", []))
    if n_dep == 0 or n_indep == 0:
        pytest.skip("no values yet to compare")
    assert n_dep == n_indep, f"{task_id_param}: {n_dep} dep values vs {n_indep} indep bin edges"


# ── Paper / shared-pool wiring ──────────────────────────────────────────


def test_shared_paper_dir_exists(task_id_param):
    paper = _task_toml(task_id_param)["task"]["paper"]
    shared = TASKS_ROOT / "shared" / paper
    assert shared.is_dir(), f"{task_id_param}: missing {shared}"


def test_paper_pdf_present(task_id_param):
    paper = _task_toml(task_id_param)["task"]["paper"]
    pdf = TASKS_ROOT / "shared" / paper / "paper" / f"{paper}.pdf"
    assert pdf.is_file(), f"{task_id_param}: missing {pdf}"


# ── Eval-pipeline resolution (full _resolve.py path) ────────────────────


def test_load_task_identity_succeeds(task_id_param):
    paper, fname, header, _sys_pct, _score_mode, _plot_mode = _load_task_identity(task_id_param)
    assert paper
    assert fname.endswith((".yml", ".yaml"))
    assert header


def test_reference_file_exists(task_id_param):
    paper, fname, _, _sys_pct, _score_mode, _plot_mode = _load_task_identity(task_id_param)
    ref = _reference_file(paper, fname)
    assert ref.is_file(), f"{task_id_param}: reference {ref} missing"


def test_template_header_present_in_reference(task_id_param):
    """The series the agent fills must exist in the reference under that name.

    Catches `Obs` vs `Nobs`, casing typos, and any other header drift.
    """
    paper, fname, header, _sys_pct, _score_mode, _plot_mode = _load_task_identity(task_id_param)
    ref = _reference_file(paper, fname)
    ref_docs = list(yaml.safe_load_all(ref.read_text()))
    ref_hist = _hist_doc(ref_docs)
    assert ref_hist, f"{ref}: no histogram document"
    ref_headers = [
        (d.get("header") or {}).get("name")
        for d in ref_hist.get("dependent_variables", [])
        if isinstance(d, dict)
    ]
    assert header in ref_headers, (
        f"{task_id_param}: header {header!r} not in reference {ref.name} "
        f"(reference has {ref_headers})"
    )


def test_template_and_reference_bin_count_match(task_id_param):
    """Template and reference must have the same number of bins, otherwise
    BC scoring silently misaligns."""
    paper, fname, _, _sys_pct, _score_mode, _plot_mode = _load_task_identity(task_id_param)
    ref = _reference_file(paper, fname)

    _, t_docs = _template_docs(task_id_param)
    t_indep = (_hist_doc(t_docs) or {}).get("independent_variables") or []
    r_indep = (_hist_doc(list(yaml.safe_load_all(ref.read_text()))) or {}).get(
        "independent_variables"
    ) or []
    if not (t_indep and r_indep):
        pytest.skip("indep vars missing on one side")
    t_bins = len(t_indep[0].get("values", []))
    r_bins = len(r_indep[0].get("values", []))
    if t_bins == 0 or r_bins == 0:
        pytest.skip("one side has no bin edges yet")
    assert t_bins == r_bins, f"{task_id_param}: template has {t_bins} bins, reference has {r_bins}"


def test_template_and_reference_bin_edges_agree(task_id_param):
    """Bin edges (low/high) must match between template and reference."""
    paper, fname, _, _sys_pct, _score_mode, _plot_mode = _load_task_identity(task_id_param)
    ref = _reference_file(paper, fname)

    _, t_docs = _template_docs(task_id_param)
    t_edges = ((_hist_doc(t_docs) or {}).get("independent_variables") or [{}])[0].get("values", [])
    r_edges = (_hist_doc(list(yaml.safe_load_all(ref.read_text()))) or {}).get(
        "independent_variables"
    ) or [{}]
    r_edges = r_edges[0].get("values", []) if r_edges else []
    if not (t_edges and r_edges):
        pytest.skip("no bin edges to compare")
    if len(t_edges) != len(r_edges):
        pytest.skip("bin counts differ — covered by a separate test")
    for i, (t, r) in enumerate(zip(t_edges, r_edges, strict=False)):
        assert t.get("low") == r.get("low") and t.get("high") == r.get("high"), (
            f"{task_id_param}: bin {i} edges differ — " f"template={t}  reference={r}"
        )
