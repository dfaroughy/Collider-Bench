"""Prompt builders for the three Sisyphus roles.

Each role sees a different workspace layout. The prompts here assume the layout
that sisyphus_loop._setup_*_workspace creates.
"""

from __future__ import annotations


def build_planner_prompt(paper_ref: str) -> str:
    return (
        f"You are the planner for a recast of {paper_ref}.\n"
        "\n"
        "Read PLANNER.md for your role card. Then read papers/"
        f"{paper_ref}.pdf and the null-valued HEPRecastData_templates/*.yaml "
        "to understand the shape of what the executor must produce.\n"
        "\n"
        "Write a compact plan.md following the structure in PLANNER.md. Output ONLY plan.md.\n"
    )


def build_executor_prompt(paper_ref: str, iter_index: int, has_prior: bool) -> str:
    parts = [
        f"You are the executor (iteration #{iter_index}) in a Sisyphus recast "
        f"of {paper_ref}.",
        "",
        "Read agent_context/AGENTS.md for your role. Before doing anything else:",
        "  1. Read agent_context/plan.md — the planner's breakdown of the task.",
    ]
    if has_prior:
        parts += [
            "  2. Read agent_context/critique.md — the critic's review of the "
            "previous attempt. The fixes it lists are concrete and bin-level; "
            "address them.",
            "  3. Then read agent_context/TOOLS.md for tool details.",
            "",
            "The workspace carries forward from the previous iteration:",
            "  analysis.py (or analysis/*.py) — prior code; verify and improve",
            "  datasets.yaml                  — prior dataset inventory",
            "  HEPRecastData/*.yaml           — partially filled by the prior run",
            "  status.md                      — prior report (unverified notes)",
            "  previous_score.json            — per-bin score from the prior run",
            "",
            "Treat inherited files as UNTRUSTED. Verify against the paper before "
            "relying on them.",
        ]
    else:
        parts += [
            "  2. Then read agent_context/TOOLS.md for tool details.",
        ]
    parts += [
        "",
        "Fill the null values in HEPRecastData/*.yaml with your recast results. "
        "Consult HEPData tables via bin/hepdata when useful.",
        "",
        "Everything you need is in this directory. Do not look outside it.",
        "",
        "Run bin/run-analysis synchronously via Bash (never run_in_background, "
        "never &). It has a 4-hour internal timeout; blocking on it is safe and "
        "expected.",
        "",
        "Write report.md describing what you accomplished and what you could not. "
        "A critic will read your artifacts at the end of this iteration.",
    ]
    return "\n".join(parts) + "\n"


def build_critic_prompt(paper_ref: str, iter_index: int) -> str:
    return (
        f"You are the critic reviewing iteration {iter_index:03d} of a Sisyphus "
        f"recast of {paper_ref}.\n"
        "\n"
        "Read CRITIC.md for your role card. Then inspect the executor's work:\n"
        "  artifacts/report.md              — the executor's self-report\n"
        "  artifacts/score.json             — per-bin pulls and scores\n"
        "  artifacts/HEPRecastData/*.yaml   — what the executor produced\n"
        "  artifacts/analysis.py            — the executor's code\n"
        "  artifacts/datasets.yaml          — samples used\n"
        "  reference/HEPRecastData_reference/*.yaml — the paper's truth values\n"
        "  plan.md                          — the planner's original breakdown\n"
        f"  paper.pdf                        — {paper_ref}\n"
        "\n"
        "Write critique.md following the exact schema in CRITIC.md. Output ONLY "
        "critique.md.\n"
    )
