"""Prompt builders for the three Sisyphus roles.

Planner, executor, and critic all run in the *same* workspace shape (the
one build_workspace produces — agent_context/, results/, papers/, bin/,
tools/). They differ only in prompt and which file they're expected to
write back. Crucially, the critic does NOT see eval/score.json; they
work from the same evidence the executor saw plus the executor's output.
"""

from __future__ import annotations


def build_planner_prompt(paper_ref: str, task_id: str) -> str:
    return (
        f"You are the PLANNER in a Sisyphus recast loop for {paper_ref}.\n"
        f"Task id: {task_id}\n"
        "\n"
        "Read agent_context/PLANNER.md for your role card. Then read:\n"
        f"  papers/{paper_ref}.pdf            — the paper\n"
        "  agent_context/TASK.md            — what the executor must produce\n"
        "  agent_context/AGENTS.md          — the executor's role card\n"
        "  agent_context/TOOLS.md           — available CLI tools\n"
        "  results/description.toml         — per-histogram metadata + task instructions\n"
        "  results/*.yml                    — null-filled histogram skeleton\n"
        "\n"
        "Write a compact plan.md to agent_context/plan.md following the structure\n"
        "in PLANNER.md. The plan will be read by the executor at the start of every\n"
        "iteration. Output the file at agent_context/plan.md and stop.\n"
    )


def build_executor_prompt(paper_ref: str, task_id: str, iter_index: int, has_prior: bool) -> str:
    parts = [
        f"You are the EXECUTOR (iteration #{iter_index}) in a Sisyphus recast loop "
        f"for {paper_ref}.",
        f"Task id: {task_id}",
        "",
        "Read in order:",
        "  1. agent_context/TASK.md   — the task spec",
        "  2. agent_context/AGENTS.md — your role and tools",
        "  3. agent_context/plan.md   — the planner's breakdown of the task "
        "(updated each iteration by the critic)",
        "  4. agent_context/TOOLS.md  — CLI tool reference",
    ]
    if has_prior:
        parts += [
            "",
            "This is iteration > 1. The workspace carries forward from the previous " "iteration:",
            "  results/*.yml         — partially-filled histogram from the prior run",
            "  analysis.py / analysis/*.py — prior code; verify and improve",
            "  datasets.yaml         — prior dataset inventory",
            "  report.md             — prior self-report",
            "",
            "Treat inherited files as UNTRUSTED but useful. The plan.md you just read "
            "has been updated by the critic with concrete fixes for this iteration — "
            "address them.",
        ]
    parts += [
        "",
        "Fill the null values in results/*.yml with your recast results. "
        "Replace any prior values where you have stronger evidence.",
        "",
        "Everything you need is in this workspace. Do not look outside it.",
        "",
        "Write report.md describing what you accomplished and what you could not. "
        "A critic will read your code, report, and the paper to update plan.md "
        "for the next iteration.",
    ]
    return "\n".join(parts) + "\n"


def build_critic_prompt(paper_ref: str, task_id: str, iter_index: int) -> str:
    return (
        f"You are the CRITIC reviewing iteration {iter_index:03d} of a Sisyphus "
        f"recast loop for {paper_ref}.\n"
        f"Task id: {task_id}\n"
        "\n"
        "Read agent_context/CRITIC.md for your role card. You see exactly what "
        "the executor saw plus the artifacts the executor produced. You do NOT "
        "see the reference values nor any score numbers — your job is to spot "
        "methodology errors from the work itself.\n"
        "\n"
        "Read in this order:\n"
        "  agent_context/TASK.md      — what was supposed to happen\n"
        "  agent_context/plan.md      — the current plan (you will rewrite this)\n"
        f"  papers/{paper_ref}.pdf     — the paper, for cross-checking the physics\n"
        "  report.md                  — the executor's self-report\n"
        "  analysis.py and analysis/  — the executor's code (read all of it)\n"
        "  datasets.yaml              — samples / cross sections used\n"
        "  results/*.yml              — what the executor produced\n"
        "\n"
        "Identify what's wrong with the methodology, code, or physics — missing "
        "branching ratios, wrong cuts, normalization errors, dataset selection "
        "mistakes, etc. — based ONLY on cross-checking the work against the paper. "
        "You will NOT be given the reference numbers; do not speculate about them.\n"
        "\n"
        "Then OVERWRITE agent_context/plan.md with a revised plan that:\n"
        "  • keeps what was correct\n"
        "  • adds concrete, actionable fixes for the issues you identified\n"
        "  • stays compact (≤ ~60 lines)\n"
        "\n"
        "Output ONLY the rewritten agent_context/plan.md. Do not modify anything "
        "under results/ or any other workspace file.\n"
    )
