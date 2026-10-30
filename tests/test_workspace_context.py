"""Characterization of build_workspace's agent_context/ block on a synthetic repo.

Pins the *current* behavior of how agent_context/ is populated: nested agent
docs mirrored, the runtime/ subtree and per-agent TOOLS.md excluded, the canonical
benchmark TOOLS.md copied with its file metadata (copy2) and without placeholder
substitution, `{arxiv_id}` substitution in agent docs and TASK.md, a missing
canonical TOOLS.md, the write order (task TASK.md last, so it wins), the exact
resulting layout, and that the blind-ablation scrub that follows acts on the
copies, so the extraction of this block into `_build_agent_context` is shown to be
behavior-preserving. Most tests drive `build_workspace`; the last section tests
`_build_agent_context` directly.

Everything runs against a tiny repo built under tmp_path (task, shared paper with a
stub PDF so nothing is fetched, benchmark bin/tools, agent runtime): offline and
deterministic.
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

_TASK_MD = "# task for {arxiv_id} / {arxiv_id}\n{other}\n"
_CANONICAL_TOOLS = "# canonical tools {arxiv_id}\n- MG5, Pythia8, Delphes, Prospino\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def make_repo(
    tmp_path: Path,
    *,
    agent_files: dict[str, str] | None = None,
    canonical_tools: str | None = _CANONICAL_TOOLS,
) -> Path:
    repo = tmp_path / "repo"
    bench = repo / "ColliderBench"
    task = bench / "tasks" / TASK_ID
    shared = bench / "tasks" / "shared" / PAPER

    _write(bench, {"bin/simulate": "#!/bin/sh\n"})
    if canonical_tools is not None:
        _write(bench, {"tools/TOOLS.md": canonical_tools})
    else:
        (bench / "tools").mkdir(parents=True)
    _write(task, {"task.toml": f'[task]\npaper = "{PAPER}"\n', "TASK.md": _TASK_MD})
    _write(task / "template", {"h.yaml": "x: null\n"})
    _write(shared, {f"paper/{PAPER}.pdf": "%PDF-stub"})
    _write(repo, {"agents/simple/runtime/bin/run-analysis": "#!/bin/sh\n"})
    _write(repo / "agents" / "simple", agent_files or {})
    return repo


def _build(repo: Path, tool_policy=None) -> Path:
    return build_workspace(repo, "simple", TASK_ID, RUN, tool_policy=tool_policy)


def _md_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── agent docs ──────────────────────────────────────────────────────────────


def test_nested_agent_docs_are_mirrored_at_the_same_relative_paths(tmp_path):
    docs = {
        "AGENTS.md": "# agents\n",
        "skills/a.md": "a\n",
        "skills/deep/b.md": "b\n",
        "notes/x.md": "x\n",
    }
    ws = _build(make_repo(tmp_path, agent_files=docs))
    ctx = _md_tree(ws / "agent_context")
    assert {k: v for k, v in ctx.items() if k not in ("TASK.md", "TOOLS.md")} == docs


def test_only_markdown_files_are_copied_from_the_agent_directory(tmp_path):
    files = {"AGENTS.md": "# a\n", "config.yaml": "k: v\n", "run.py": "print()\n", "n.txt": "t\n"}
    ws = _build(make_repo(tmp_path, agent_files=files))
    assert sorted(_md_tree(ws / "agent_context")) == ["AGENTS.md", "TASK.md", "TOOLS.md"]


def test_runtime_subtree_is_excluded_only_at_the_top_level(tmp_path):
    files = {
        "runtime/README.md": "secret runtime doc\n",
        "runtime/templates/workspace/report.md": "tmpl\n",
        "skills/runtime/ok.md": "kept\n",
    }
    ws = _build(make_repo(tmp_path, agent_files=files))
    ctx = _md_tree(ws / "agent_context")
    assert "runtime/README.md" not in ctx
    assert "runtime/templates/workspace/report.md" not in ctx
    assert ctx["skills/runtime/ok.md"] == "kept\n"


def test_per_agent_tools_md_is_excluded_at_any_depth_exact_name_only(tmp_path):
    files = {
        "TOOLS.md": "agent tools root\n",
        "skills/TOOLS.md": "agent tools nested\n",
        "other/tools.md": "lowercase kept\n",
        "skills/MY-TOOLS.md": "other name kept\n",
    }
    # "tools.md" lives in a different directory: on case-insensitive filesystems it
    # would otherwise be the same file as "TOOLS.md".
    ws = _build(make_repo(tmp_path, agent_files=files))
    ctx = _md_tree(ws / "agent_context")
    assert "skills/TOOLS.md" not in ctx
    assert ctx["TOOLS.md"] == _CANONICAL_TOOLS  # canonical, not the agent's copy
    assert ctx["other/tools.md"] == "lowercase kept\n"
    assert ctx["skills/MY-TOOLS.md"] == "other name kept\n"


# ── placeholder substitution ────────────────────────────────────────────────


def test_arxiv_id_is_substituted_in_agent_docs_and_task_md_but_not_canonical_tools(tmp_path):
    files = {"AGENTS.md": "paper {arxiv_id} and again {arxiv_id}; keep {other}\n", "n.md": "none\n"}
    ws = _build(make_repo(tmp_path, agent_files=files))
    ctx = _md_tree(ws / "agent_context")
    assert ctx["AGENTS.md"] == f"paper {PAPER} and again {PAPER}; keep {{other}}\n"
    assert ctx["n.md"] == "none\n"
    assert ctx["TASK.md"] == f"# task for {PAPER} / {PAPER}\n{{other}}\n"
    assert ctx["TOOLS.md"] == _CANONICAL_TOOLS  # still contains the raw placeholder
    assert "{arxiv_id}" in ctx["TOOLS.md"]


# ── canonical TOOLS.md ──────────────────────────────────────────────────────


def test_canonical_tools_md_is_copied_with_mode_and_mtime(tmp_path):
    repo = make_repo(tmp_path)
    src = repo / "ColliderBench" / "tools" / "TOOLS.md"
    src.chmod(0o640)
    os.utime(src, (1_000_000_000, 1_000_000_000))
    ws = _build(repo)
    dest = ws / "agent_context" / "TOOLS.md"
    assert dest.is_file() and not dest.is_symlink()
    st = dest.stat()
    assert stat.S_IMODE(st.st_mode) == 0o640
    assert int(st.st_mtime) == 1_000_000_000
    assert dest.read_text() == _CANONICAL_TOOLS


def test_missing_canonical_tools_md_means_no_tools_md_even_if_agent_has_one(tmp_path):
    repo = make_repo(tmp_path, agent_files={"TOOLS.md": "agent copy\n", "AGENTS.md": "a\n"})
    ws = _build(make_repo_without_tools(repo))
    assert sorted(_md_tree(ws / "agent_context")) == ["AGENTS.md", "TASK.md"]


def make_repo_without_tools(repo: Path) -> Path:
    (repo / "ColliderBench" / "tools" / "TOOLS.md").unlink()
    return repo


# ── TASK.md precedence + layout ─────────────────────────────────────────────


def test_task_md_from_the_task_directory_overwrites_an_agent_doc_with_the_same_name(tmp_path):
    ws = _build(make_repo(tmp_path, agent_files={"TASK.md": "agent's own task doc\n"}))
    assert (ws / "agent_context" / "TASK.md").read_text() == (
        f"# task for {PAPER} / {PAPER}\n{{other}}\n"
    )


def test_exact_agent_context_layout_and_file_types(tmp_path):
    files = {
        "AGENTS.md": "a\n",
        "skills/s.md": "s\n",
        "runtime/skip.md": "no\n",
        "TOOLS.md": "no\n",
    }
    ws = _build(make_repo(tmp_path, agent_files=files))
    ctx = ws / "agent_context"
    assert ctx.is_dir() and not ctx.is_symlink()
    assert sorted(_md_tree(ctx)) == ["AGENTS.md", "TASK.md", "TOOLS.md", "skills/s.md"]
    assert not any(p.is_symlink() for p in ctx.rglob("*"))


def test_agent_directory_without_docs_still_gets_task_and_tools_md(tmp_path):
    ws = _build(make_repo(tmp_path))
    assert sorted(_md_tree(ws / "agent_context")) == ["TASK.md", "TOOLS.md"]


def test_source_files_are_never_modified_by_the_copy_or_substitution(tmp_path):
    repo = make_repo(tmp_path, agent_files={"AGENTS.md": "x {arxiv_id}\n"})
    _build(repo)
    assert (repo / "agents" / "simple" / "AGENTS.md").read_text() == "x {arxiv_id}\n"
    task = repo / "ColliderBench" / "tasks" / TASK_ID / "TASK.md"
    assert task.read_text() == _TASK_MD
    tools = repo / "ColliderBench" / "tools" / "TOOLS.md"
    assert tools.read_text() == _CANONICAL_TOOLS


# ── interaction with the blind-ablation scrub that follows ─────────────────


def test_blind_prospino_scrub_acts_on_the_copies_not_on_the_sources(tmp_path):
    files = {"AGENTS.md": "keep this\nUse Prospino for xsec\nand this\n"}
    repo = make_repo(tmp_path, agent_files=files)
    ws = _build(repo, tool_policy={"disabled": ["prospino"]})
    ctx = _md_tree(ws / "agent_context")
    assert ctx["AGENTS.md"] == "keep this\nand this\n"
    assert "Prospino" not in ctx["TOOLS.md"] and "Pythia8" in ctx["TOOLS.md"]
    # Sources are untouched.
    assert "Prospino" in (repo / "agents" / "simple" / "AGENTS.md").read_text()
    assert "Prospino" in (repo / "ColliderBench" / "tools" / "TOOLS.md").read_text()


@pytest.mark.parametrize("policy", [None, {}, {"disabled": []}])
def test_no_effective_tool_policy_leaves_the_copies_verbatim(tmp_path, policy):
    files = {"AGENTS.md": "Use Prospino and Delphes\n"}
    ws = _build(make_repo(tmp_path, agent_files=files), tool_policy=policy)
    ctx = _md_tree(ws / "agent_context")
    assert ctx["AGENTS.md"] == "Use Prospino and Delphes\n"
    assert ctx["TOOLS.md"] == _CANONICAL_TOOLS


# ── Direct tests of the extracted helper: _build_agent_context ─────────────


def _direct_inputs(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    _write(agent_dir, {"AGENTS.md": "id={arxiv_id}\n", "skills/s.md": "s {arxiv_id}\n"})
    _write(agent_dir, {"runtime/skip.md": "skip\n", "TOOLS.md": "agent tools\n"})
    bench = tmp_path / "bench"
    _write(bench, {"tools/TOOLS.md": "canon {arxiv_id}\n"})
    task_md = tmp_path / "task" / "TASK.md"
    _write(task_md.parent, {"TASK.md": "task {arxiv_id}\n"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return agent_dir, bench, workspace, task_md


def test_helper_populates_agent_context_with_substitution(tmp_path):
    from agent_runtime.workspace import _build_agent_context

    agent_dir, bench, workspace, task_md = _direct_inputs(tmp_path)
    assert _build_agent_context(agent_dir, bench, workspace, task_md, "P-1") is None
    assert _md_tree(workspace / "agent_context") == {
        "AGENTS.md": "id=P-1\n",
        "skills/s.md": "s P-1\n",
        "TOOLS.md": "canon {arxiv_id}\n",
        "TASK.md": "task P-1\n",
    }


@pytest.mark.parametrize("paper_ref", [None, ""])
def test_helper_skips_substitution_when_paper_ref_is_falsy(tmp_path, paper_ref):
    from agent_runtime.workspace import _build_agent_context

    agent_dir, bench, workspace, task_md = _direct_inputs(tmp_path)
    _build_agent_context(agent_dir, bench, workspace, task_md, paper_ref)
    ctx = _md_tree(workspace / "agent_context")
    assert ctx["AGENTS.md"] == "id={arxiv_id}\n"
    assert ctx["TASK.md"] == "task {arxiv_id}\n"


def test_helper_requires_a_fresh_agent_context_directory(tmp_path):
    from agent_runtime.workspace import _build_agent_context

    agent_dir, bench, workspace, task_md = _direct_inputs(tmp_path)
    (workspace / "agent_context").mkdir()
    with pytest.raises(FileExistsError):
        _build_agent_context(agent_dir, bench, workspace, task_md, "P")


def test_helper_without_canonical_tools_md_writes_only_agent_docs_and_task(tmp_path):
    from agent_runtime.workspace import _build_agent_context

    agent_dir, bench, workspace, task_md = _direct_inputs(tmp_path)
    (bench / "tools" / "TOOLS.md").unlink()
    _build_agent_context(agent_dir, bench, workspace, task_md, "P")
    assert sorted(_md_tree(workspace / "agent_context")) == ["AGENTS.md", "TASK.md", "skills/s.md"]


def test_helper_task_md_overwrites_an_agent_doc_with_the_same_name(tmp_path):
    from agent_runtime.workspace import _build_agent_context

    agent_dir, bench, workspace, task_md = _direct_inputs(tmp_path)
    _write(agent_dir, {"TASK.md": "agent's own task doc {arxiv_id}\n"})
    _build_agent_context(agent_dir, bench, workspace, task_md, "P-9")
    assert (workspace / "agent_context" / "TASK.md").read_text() == "task P-9\n"
