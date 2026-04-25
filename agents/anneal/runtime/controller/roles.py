"""Prompt builders for the three Anneal roles.

Planner, executor, and examiner all run in the *same* workspace shape
(the one build_workspace produces — agent_context/, results/, papers/,
bin/, tools/). They differ only in prompt and which file they're
expected to write back. Crucially, the examiner does NOT see
eval/score.json; they work from the same evidence the executor saw plus
the executor's output. The examiner alone gets the running
proposals_log.md (seeded by the controller) and updates it in place.
"""

from __future__ import annotations


def _temperature_blurb(t: float) -> str:
    """One-liner about how the role should treat the current temperature.

    Temperature comes from the controller's annealing schedule (see
    anneal_loop.temperature). High T → exploration; low T → refinement.
    """
    if t >= 0.66:
        mode = "high — feel free to overhaul methodology, swap selections, re-derive cross-sections"
    elif t >= 0.33:
        mode = "medium — adjust major components if warranted; preserve what's working"
    elif t > 0:
        mode = "low — micro-refinements only; do not rewrite analysis or change selection"
    else:
        mode = "zero — do not change methodology; only fill gaps and fix typos"
    return f"Temperature: T={t:.2f} ({mode})."


def build_planner_prompt(paper_ref: str, task_id: str) -> str:
    return (
        f"You are the PLANNER in an Anneal recast loop for {paper_ref}.\n"
        f"Task id: {task_id}\n"
        f"Paper PDF: `papers/{paper_ref}.pdf` (this exact path — the file uses the\n"
        "  CMS/ATLAS publication code as its name, NOT the arXiv ID. The arXiv\n"
        "  identifier you may see inside the paper is irrelevant for file paths).\n"
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


def build_executor_prompt(
    paper_ref: str,
    task_id: str,
    iter_index: int,
    has_prior: bool,
    temperature: float = 0.0,
) -> str:
    parts = [
        f"You are the EXECUTOR (iteration #{iter_index}) in an Anneal recast loop "
        f"for {paper_ref}.",
        f"Task id: {task_id}",
        f"Paper PDF: `papers/{paper_ref}.pdf` (this exact path — uses the "
        "CMS/ATLAS publication code, NOT the arXiv ID. Ignore any arXiv "
        "identifiers you see inside the paper for file-path purposes).",
        _temperature_blurb(temperature),
        "",
        "Read in order:",
        "  1. agent_context/TASK.md   — the task spec",
        "  2. agent_context/AGENTS.md — your role and tools",
        "  3. agent_context/plan.md   — the planner's breakdown of the task "
        "(updated each iteration by the examiner)",
        "  4. agent_context/TOOLS.md  — CLI tool reference",
    ]
    if has_prior:
        parts += [
            "",
            "This is iteration > 1. The workspace carries forward from the previous iteration:",
            "  results/*.yml         — partially-filled histogram from the prior run",
            "  analysis.py / analysis/*.py — prior code; verify and improve",
            "  datasets.yaml         — prior dataset inventory",
            "  report.md             — prior self-report",
            "",
            "Treat inherited files as UNTRUSTED but useful. The plan.md you just read "
            "has been updated by the examiner with concrete fixes for this iteration — "
            "address them. The temperature blurb above tells you how aggressively to "
            "rewrite versus refine.",
        ]
    fill_line = (
        "Fill the null values in results/*.yml with your recast results. "
        "Replace any prior values where you have stronger evidence."
        if has_prior
        else "Fill the null values in results/*.yml with your recast results."
    )
    parts += [
        "",
        fill_line,
        "",
        "Everything you need is in this workspace. Do not look outside it.",
        "",
        "Write report.md describing what you accomplished and what you could not. "
        "An examiner will read your code, report, and the paper to update plan.md "
        "for the next iteration.",
    ]
    return "\n".join(parts) + "\n"


def build_examiner_prompt(
    paper_ref: str,
    task_id: str,
    iter_index: int,
    temperature: float = 0.0,
) -> str:
    return (
        f"You are the EXAMINER reviewing iteration {iter_index:03d} of an Anneal "
        f"recast loop for {paper_ref}.\n"
        f"Task id: {task_id}\n"
        f"Paper PDF: `papers/{paper_ref}.pdf` (this exact path — uses the "
        "CMS/ATLAS publication code, NOT the arXiv ID).\n"
        f"{_temperature_blurb(temperature)}\n"
        "\n"
        "Read agent_context/EXAMINER.md for your role card. You see exactly what "
        "the executor saw plus the artifacts the executor produced. You do NOT "
        "see the reference values nor any score numbers — your job is to spot "
        "methodology errors from the work itself.\n"
        "\n"
        "Read in this order:\n"
        "  agent_context/TASK.md           — what was supposed to happen\n"
        "  agent_context/plan.md           — the current plan (you will rewrite this)\n"
        "  agent_context/proposals_log.md  — your running record of fixes proposed "
        "in prior iterations and whether each one improved the fit. UPDATE this in "
        "place: fill in outcomes for the previous iter's proposals, then append rows "
        "for this iter's new proposals.\n"
        f"  papers/{paper_ref}.pdf          — the paper, for cross-checking the physics\n"
        "  report.md                       — the executor's self-report\n"
        "  analysis.py and analysis/       — the executor's code (read all of it)\n"
        "  datasets.yaml                   — samples / cross sections used\n"
        "  results/*.yml                   — what the executor produced\n"
        "\n"
        "Identify what's wrong with the methodology, code, or physics — missing "
        "branching ratios, wrong cuts, normalization errors, dataset selection "
        "mistakes, etc. — based ONLY on cross-checking the work against the paper. "
        "You will NOT be given the reference numbers; do not speculate about them. "
        "Use proposals_log.md to track which past fixes worked: judge 'improved' / "
        "'no change' / 'regressed' qualitatively by comparing this iter's results/ "
        "against the prior iter's results/ that carried forward, and by reading the "
        "paper figures.\n"
        "\n"
        "Then OVERWRITE agent_context/plan.md with a revised plan that:\n"
        "  • keeps what was correct\n"
        "  • adds concrete, actionable fixes for the issues you identified\n"
        "  • stays compact (≤ ~60 lines)\n"
        "\n"
        "Output ONLY the rewritten agent_context/plan.md and the updated "
        "agent_context/proposals_log.md. Do not modify anything under results/ or "
        "any other workspace file.\n"
    )
