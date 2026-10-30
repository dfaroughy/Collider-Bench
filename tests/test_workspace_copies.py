"""Characterization of build_workspace's copy phases on a synthetic repo.

Pins the *current* behavior of (a) the results/ template copy and (b) the
object_efficiencies/ shared-pool copy plus task-artifact overlay, together with
the resulting workspace layout and symlink types, so these copy steps can be
extracted without any layout change slipping through.

Everything runs against a tiny repo built under tmp_path (task, shared paper
with a stub PDF so nothing is fetched, benchmark bin/tools, agent runtime), so
the tests are offline, deterministic and independent of the real task corpus.
Only the public `build_workspace(...)` is exercised.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agent_runtime.workspace import build_workspace

TASK_ID = "synth_sim-A"
PAPER = "SYNTH-00-000"
RUN = "run1"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def make_repo(
    tmp_path: Path,
    *,
    template: dict[str, str] | None = None,
    shared_eff: dict[str, str] | None = None,
    artifacts: dict[str, str] | None = None,
    empty_eff_dir: bool = False,
    empty_artifacts_dir: bool = False,
) -> Path:
    """Build a minimal repo. `None` for template/shared_eff/artifacts => directory absent."""
    repo = tmp_path / "repo"
    bench = repo / "ColliderBench"
    task = bench / "tasks" / TASK_ID
    shared = bench / "tasks" / "shared" / PAPER

    _write(bench, {"tools/TOOLS.md": "# tools\n", "bin/simulate": "#!/bin/sh\n"})
    _write(task, {"task.toml": f'[task]\npaper = "{PAPER}"\n', "TASK.md": "# task {arxiv_id}\n"})
    _write(shared, {f"paper/{PAPER}.pdf": "%PDF-stub"})
    _write(
        repo,
        {
            "agents/simple/AGENTS.md": "# agent\n",
            "agents/simple/runtime/bin/run-analysis": "#!/bin/sh\n",
        },
    )

    if template is not None:
        (task / "template").mkdir(parents=True, exist_ok=True)
        _write(task / "template", template)
    if shared_eff is not None:
        _write(shared / "object_efficiencies", shared_eff)
    if empty_eff_dir:
        (shared / "object_efficiencies").mkdir(parents=True, exist_ok=True)
    if artifacts is not None:
        _write(task / "artifacts", artifacts)
    if empty_artifacts_dir:
        (task / "artifacts").mkdir(parents=True, exist_ok=True)
    return repo


def _tree(root: Path) -> dict[str, str]:
    """{relative posix path: content} for every file under root (dirs omitted)."""
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _build(repo: Path) -> Path:
    return build_workspace(repo, "simple", TASK_ID, RUN)


_TEMPLATE = {
    "histogram_a.yaml": "a: null\n",
    "sub/histogram_b.yaml": "b: null\n",
    "sub/deep/notes.txt": "deep\n",
}


# ── results/ template copy ─────────────────────────────────────────────────


def test_results_mirrors_nested_template_paths_and_content(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE))
    assert _tree(ws / "results") == _TEMPLATE
    assert (ws / "results").is_dir() and not (ws / "results").is_symlink()


def test_results_files_are_independent_regular_copies(tmp_path):
    repo = make_repo(tmp_path, template=_TEMPLATE)
    ws = _build(repo)
    for p in (ws / "results").rglob("*"):
        assert not p.is_symlink()
    (ws / "results" / "histogram_a.yaml").write_text("a: 1\n")
    src = repo / "ColliderBench" / "tasks" / TASK_ID / "template" / "histogram_a.yaml"
    assert src.read_text() == "a: null\n"


def test_results_copy_preserves_file_mode_and_mtime(tmp_path):
    repo = make_repo(tmp_path, template=_TEMPLATE)
    src = repo / "ColliderBench" / "tasks" / TASK_ID / "template" / "histogram_a.yaml"
    src.chmod(0o640)
    os.utime(src, (1_000_000_000, 1_000_000_000))
    ws = _build(repo)
    out = (ws / "results" / "histogram_a.yaml").stat()
    assert stat.S_IMODE(out.st_mode) == 0o640
    assert int(out.st_mtime) == 1_000_000_000


def test_results_drops_empty_template_dirs_and_follows_file_symlinks(tmp_path):
    repo = make_repo(tmp_path, template=_TEMPLATE)
    template = repo / "ColliderBench" / "tasks" / TASK_ID / "template"
    (template / "emptydir").mkdir()
    (template / "link.yaml").symlink_to(template / "histogram_a.yaml")
    ws = _build(repo)
    assert not (ws / "results" / "emptydir").exists()
    link = ws / "results" / "link.yaml"
    assert link.is_file() and not link.is_symlink()
    assert link.read_text() == "a: null\n"


def test_empty_template_dir_yields_empty_results_dir(tmp_path):
    ws = _build(make_repo(tmp_path, template={}))
    assert (ws / "results").is_dir()
    assert list((ws / "results").iterdir()) == []


# ── object_efficiencies/ shared pool + task artifacts ──────────────────────


def test_shared_efficiencies_are_copied_recursively(tmp_path):
    eff = {"eff_mu.json": "mu\n", "nested/eff_el.json": "el\n", "nested/x/y.txt": "y\n"}
    ws = _build(make_repo(tmp_path, template=_TEMPLATE, shared_eff=eff))
    assert _tree(ws / "object_efficiencies") == eff
    assert (ws / "object_efficiencies").is_dir()
    assert not (ws / "object_efficiencies").is_symlink()
    assert not any(p.is_symlink() for p in (ws / "object_efficiencies").rglob("*"))


def test_task_artifact_overrides_same_name_shared_file_and_adds_new_ones(tmp_path):
    ws = _build(
        make_repo(
            tmp_path,
            template=_TEMPLATE,
            shared_eff={"eff.json": "shared\n", "keep.json": "keep\n"},
            artifacts={"eff.json": "task\n", "extra.cxx": "extra\n"},
        )
    )
    assert _tree(ws / "object_efficiencies") == {
        "eff.json": "task\n",
        "keep.json": "keep\n",
        "extra.cxx": "extra\n",
    }


def test_artifacts_overlay_is_flat_nested_artifact_dirs_are_ignored(tmp_path):
    ws = _build(
        make_repo(
            tmp_path,
            template=_TEMPLATE,
            shared_eff={"sub/eff.json": "shared-nested\n"},
            artifacts={"eff.json": "task-top\n", "subdir/ignored.txt": "ignored\n"},
        )
    )
    # A top-level artifact does not replace a same-named file in a shared subdir,
    # and nested artifact directories are not copied at all.
    assert _tree(ws / "object_efficiencies") == {
        "sub/eff.json": "shared-nested\n",
        "eff.json": "task-top\n",
    }


def test_only_artifacts_creates_object_efficiencies_from_artifacts(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE, artifacts={"a.cxx": "a\n"}))
    assert _tree(ws / "object_efficiencies") == {"a.cxx": "a\n"}


# ── missing optional directories ───────────────────────────────────────────


def test_no_shared_efficiencies_and_no_artifacts_means_no_object_efficiencies(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE))
    assert not (ws / "object_efficiencies").exists()


def test_empty_shared_dir_without_artifacts_yields_empty_object_efficiencies(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE, empty_eff_dir=True))
    assert (ws / "object_efficiencies").is_dir()
    assert list((ws / "object_efficiencies").iterdir()) == []


def test_empty_artifacts_dir_alone_still_creates_object_efficiencies(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE, empty_artifacts_dir=True))
    assert (ws / "object_efficiencies").is_dir()
    assert list((ws / "object_efficiencies").iterdir()) == []


@pytest.mark.parametrize("missing", ["template", "task_md"])
def test_missing_required_inputs_raise_before_any_workspace_is_created(tmp_path, missing):
    repo = make_repo(tmp_path, template=_TEMPLATE)
    task = repo / "ColliderBench" / "tasks" / TASK_ID
    if missing == "template":
        for p in sorted((task / "template").rglob("*"), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        (task / "template").rmdir()
    else:
        (task / "TASK.md").unlink()
    with pytest.raises(FileNotFoundError):
        _build(repo)
    assert not (repo / "runs" / RUN / "workspace").exists()


def test_rebuilding_replaces_the_workspace_and_drops_stale_files(tmp_path):
    repo = make_repo(tmp_path, template=_TEMPLATE, shared_eff={"eff.json": "e\n"})
    ws = _build(repo)
    (ws / "results" / "stale.txt").write_text("old")
    (ws / "object_efficiencies" / "stale.txt").write_text("old")
    ws2 = _build(repo)
    assert ws2 == ws
    assert not (ws / "results" / "stale.txt").exists()
    assert not (ws / "object_efficiencies" / "stale.txt").exists()
    assert _tree(ws / "results") == _TEMPLATE


# ── resulting workspace layout + symlink types ─────────────────────────────


def test_workspace_layout_and_symlink_types(tmp_path):
    repo = make_repo(tmp_path, template=_TEMPLATE, shared_eff={"eff.json": "e\n"})
    ws = _build(repo)
    bench = repo / "ColliderBench"

    assert ws == repo / "runs" / RUN / "workspace"
    assert {p.name for p in ws.iterdir()} == {
        "tools",
        "bin",
        "agent_context",
        "results",
        "papers",
        "object_efficiencies",
    }

    # tools/ is the only top-level symlink; papers/ is a real in-workspace copy
    # of the shared paper dir (materialized during workspace build), as are the rest.
    assert (ws / "tools").is_symlink()
    assert (ws / "tools").readlink() == bench / "tools"
    assert (ws / "papers" / f"{PAPER}.pdf").read_text() == "%PDF-stub"
    assert not (ws / "papers" / f"{PAPER}.pdf").is_symlink()
    for name in ("bin", "papers", "agent_context", "results", "object_efficiencies"):
        assert (ws / name).is_dir() and not (ws / name).is_symlink(), name

    # bin/ holds one symlink per benchmark script and per agent runtime script.
    bin_links = {p.name: p.readlink() for p in (ws / "bin").iterdir() if p.is_symlink()}
    assert bin_links == {
        "simulate": bench / "bin" / "simulate",
        "run-analysis": repo / "agents" / "simple" / "runtime" / "bin" / "run-analysis",
    }
    assert {p.name for p in (ws / "bin").iterdir()} == set(bin_links)


def test_agent_context_gets_task_md_with_arxiv_id_substituted(tmp_path):
    ws = _build(make_repo(tmp_path, template=_TEMPLATE))
    assert (ws / "agent_context" / "TASK.md").read_text() == f"# task {PAPER}\n"
    assert (ws / "agent_context" / "TOOLS.md").read_text() == "# tools\n"
    assert (ws / "agent_context" / "AGENTS.md").read_text() == "# agent\n"


# ── Direct tests of the extracted helpers ───────────────────────────────────


def test_copy_result_templates_copies_nested_files_and_requires_fresh_results(tmp_path):
    from agent_runtime.workspace import _copy_result_templates

    src = tmp_path / "template"
    _write(src, _TEMPLATE)
    (src / "emptydir").mkdir()
    results = tmp_path / "results"
    _copy_result_templates(src, results)
    assert _tree(results) == _TEMPLATE
    assert not (results / "emptydir").exists()
    with pytest.raises(FileExistsError):
        _copy_result_templates(src, results)


def test_copy_object_efficiencies_creates_nothing_when_both_sources_absent(tmp_path):
    from agent_runtime.workspace import _copy_object_efficiencies

    obj = tmp_path / "object_efficiencies"
    _copy_object_efficiencies(tmp_path / "no-shared", tmp_path / "no-artifacts", obj)
    assert not obj.exists()


def test_copy_object_efficiencies_overlays_flat_artifacts_on_shared(tmp_path):
    from agent_runtime.workspace import _copy_object_efficiencies

    shared, art, obj = tmp_path / "shared", tmp_path / "art", tmp_path / "obj"
    _write(shared, {"eff.json": "shared\n", "sub/eff.json": "shared-nested\n", "keep": "k\n"})
    _write(art, {"eff.json": "task\n", "new.cxx": "n\n", "dir/ignored.txt": "x\n"})
    _copy_object_efficiencies(shared, art, obj)
    assert _tree(obj) == {
        "eff.json": "task\n",
        "sub/eff.json": "shared-nested\n",
        "keep": "k\n",
        "new.cxx": "n\n",
    }


@pytest.mark.parametrize("which", ["shared", "artifacts"])
def test_copy_object_efficiencies_empty_single_source_still_creates_dir(tmp_path, which):
    from agent_runtime.workspace import _copy_object_efficiencies

    shared, art, obj = tmp_path / "shared", tmp_path / "art", tmp_path / "obj"
    (shared if which == "shared" else art).mkdir()
    _copy_object_efficiencies(shared, art, obj)
    assert obj.is_dir() and list(obj.iterdir()) == []
