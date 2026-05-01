"""Symbolic reasoning-effort labels and their token budgets.

Different vendor CLIs accept different shapes:
  - Claude:  --max-thinking-tokens N
  - Codex:   -c model_reasoning_effort=<low|medium|high|xhigh>
  - Gemini:  no effort knob (server-side router)
  - Forge:   [reasoning].effort = "high" in ~/.forge/.forge.toml

This module is the single place where users' YAML `effort:` values
get translated into a (label, token_budget) pair. Vendor-specific
mapping (e.g. "max" → codex "xhigh") is in each runner's spec.
"""

from __future__ import annotations


# Map a symbolic effort level to max_thinking_tokens for the Claude CLI.
# "medium" mirrors the Claude CLI's own default; "high" matches what CC Code
# uses for heavy reasoning tasks. Users can also pass a raw integer.
EFFORT_THINKING_TOKENS: dict[str, int] = {
    "low": 2000,
    "medium": 8000,
    "high": 31999,
    # "max" = alias for "high". Anthropic's extended-thinking cap for a single
    # turn is ~32k tokens; exceeding it errors. To go higher, pass a raw int.
    "max": 31999,
    # "xhigh" = codex's extra-high reasoning effort (GPT-5 family). Token
    # budget here is only used by runners that honour max_thinking_tokens
    # (claude); CodexRunner maps this label to -c model_reasoning_effort=xhigh.
    "xhigh": 31999,
}


def resolve_effort(effort: str | int | None) -> tuple[str, int]:
    """Return (label, max_thinking_tokens) for a user-supplied effort value.

    Accepts: "low" | "medium" | "high" | integer string | int | None.
    None → "medium" (CLI default).
    """
    if effort is None or effort == "":
        effort = "medium"
    if isinstance(effort, int):
        return (f"custom({effort})", effort)
    s = str(effort).strip().lower()
    if s.isdigit():
        n = int(s)
        return (f"custom({n})", n)
    if s in EFFORT_THINKING_TOKENS:
        return (s, EFFORT_THINKING_TOKENS[s])
    # Unknown label → fall back to medium rather than erroring
    return ("medium", EFFORT_THINKING_TOKENS["medium"])
