"""Benchmark path layout — one source of truth.

Consolidates the hardcoded "ColliderBench/..." strings that used to live
across workspace.py, sandbox.py, and naming.py so a benchmark-dir rename
is a one-file change.
"""

from __future__ import annotations

from pathlib import Path


BENCHMARK_DIRNAME = "ColliderBench"


def benchmark_dir(repo_root: Path) -> Path:
    return repo_root / BENCHMARK_DIRNAME


def tools_dir(repo_root: Path) -> Path:
    return benchmark_dir(repo_root) / "tools"


def sim_dir(repo_root: Path) -> Path:
    return tools_dir(repo_root) / "sim"


def tasks_root(repo_root: Path) -> Path:
    return benchmark_dir(repo_root) / "tasks"


def task_dir(repo_root: Path, task_id: str) -> Path:
    return tasks_root(repo_root) / task_id


def shared_root(repo_root: Path) -> Path:
    return tasks_root(repo_root) / "shared"


def shared_paper_dir(repo_root: Path, paper_ref: str) -> Path:
    return shared_root(repo_root) / paper_ref


def evaluation_dir(repo_root: Path) -> Path:
    return benchmark_dir(repo_root) / "evaluation"


def bin_dir(repo_root: Path) -> Path:
    return benchmark_dir(repo_root) / "bin"
