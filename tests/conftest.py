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
def task_id() -> str:
    # Canonical task for smoke tests — SUS-16-047 is the only one with real
    # reference values in the shared pool, so scoring tests don't no-op.
    return "sus-16-047_sim-T5Wg_highHT"


@pytest.fixture
def tmp_run_name(tmp_path_factory) -> str:
    # Name pattern is gitignored so the dir is never committed if a test
    # forgets to clean up. Prefix is cosmetic — tmp_path_factory already
    # places it outside the repo.
    return f"SMOKE_{os.getpid()}_{tmp_path_factory.mktemp('ws').name}"
