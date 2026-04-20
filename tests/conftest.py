"""Shared fixtures. Keep the suite offline — no network, no SLURM, no claude/codex."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Guarantee the repo is importable even when pytest is invoked from elsewhere
# and before the package is pip-installed.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def paper_ref() -> str:
    # The benchmark ships this paper — all smoke tests use it.
    return "CMS-SUS-16-047"


@pytest.fixture
def tmp_run_name(tmp_path_factory) -> str:
    # Ensure test workspaces land under a name matching recast_*/ .gitignore
    # so they're never committed even if a test forgets to clean up.
    return f"recast_SMOKE_{os.getpid()}_{tmp_path_factory.mktemp('ws').name}"
