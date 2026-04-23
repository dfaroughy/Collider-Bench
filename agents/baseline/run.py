#!/usr/bin/env python3
"""Baseline agent runner for the LHC-Recast benchmark.

Usage:
    # Preferred (dispatches via the unified launcher; handles compute wrap):
    scripts/run-agent --config configs/claude_baseline.yaml

    # Direct (always runs on the current host):
    python -m agents.baseline.run --paper-ref 1707.06193 \\
        --runner claude --model claude-opus-4-7
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runtime.launch import launch_single_run


def build_prompt(paper_ref: str) -> str:
    return (
        "You are a LHC experimentalist with expertise in Standard Model and BSM search strategies, event generation tools, event selection design, and statistical interpretation of collider data.\n"
        "\n"
        "Read these in order:\n"
        "  1. agent_context/TASK.md    — the benchmark's task for this run.\n"
        "  2. agent_context/AGENTS.md  — your role and how to work.\n"
        "  3. agent_context/SOUL.md    — scientific principles to follow.\n"
        "  4. agent_context/TOOLS.md   — CLI tool reference.\n"
        "  5. agent_context/skills/*   — detailed how-tos, consulted as needed.\n"
        "\n"
        "Everything you need is in this directory.\n"
        "\n"
        "Run bin/run-analysis synchronously via Bash (never run_in_background, never &).\n"
        "It has a 4-hour internal timeout; blocking on it is safe and expected.\n"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    return launch_single_run("baseline", build_prompt, repo_root)


if __name__ == "__main__":
    sys.exit(main())
