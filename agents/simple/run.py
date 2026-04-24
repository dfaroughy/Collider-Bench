#!/usr/bin/env python3
"""Simple agent runner for the LHC-Recast benchmark.

Usage:
    # Preferred (dispatches via the unified launcher; handles compute wrap):
    scripts/run-agent --config configs/claude_simple.yaml

    # Direct (always runs on the current host):
    python -m agents.simple.run --task sus-16-046-simulate-TChiWg-STgamma \\
        --runner claude --model claude-opus-4-7
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runtime.launch import launch_single_run


def build_prompt(paper_ref: str) -> str:
    return (
        "You are a LHC experimentalist with expertise in Standard Model and BSM\n"
        "search strategies, event generation tools, event selection design, and \n"
        "statistical interpretation of collider data.\n"
        "\n"
        "Read these in order:\n"
        "  1. agent_context/TASK.md   — the benchmark's task for this run.\n"
        "  2. agent_context/AGENTS.md — your role and how to work.\n"
        "  3. agent_context/TOOLS.md  — CLI tool reference.\n"
        "\n"
        "Everything you need is in this directory.\n"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    return launch_single_run("simple", build_prompt, repo_root)


if __name__ == "__main__":
    sys.exit(main())
