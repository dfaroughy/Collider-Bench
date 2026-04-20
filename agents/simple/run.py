#!/usr/bin/env python3
"""Simple agent runner for the LHC-Recast benchmark.

Usage:
    # Preferred (dispatches via the unified launcher; handles compute wrap):
    scripts/run-agent --config configs/claude_simple.yaml

    # Direct (always runs on the current host):
    python -m agents.simple.run --paper-ref 1707.06193 \\
        --runner claude --model claude-opus-4-7
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runtime.launch import launch_single_run


def build_prompt(paper_ref: str) -> str:
    return (
        f"You are recasting CMS paper {paper_ref} using public CMS Open Data.\n"
        "\n"
        "Read agent_context/AGENTS.md first, then agent_context/TOOLS.md for CLI\n"
        "tool details. Follow AGENTS.md step by step.\n"
        "\n"
        "Fill the null values in HEPRecastData/*.yaml with your recast results.\n"
        "If you want to consult published HEPData tables, query them via bin/hepdata.\n"
        "\n"
        "Everything you need is in this directory. Do not look outside it.\n"
        "\n"
        "Run bin/run-analysis synchronously via Bash (never run_in_background, never &).\n"
        "It has a 4-hour internal timeout; blocking on it is safe and expected.\n"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    return launch_single_run("simple", build_prompt, repo_root)


if __name__ == "__main__":
    sys.exit(main())
