"""Per-vendor token-usage parsers + a single dispatcher.

Each runner produces a `session.jsonl` log with vendor-native event shapes;
this module extracts the usage block from each and computes a dollar cost
via `agent_runtime.pricing` (Claude reports its own `total_cost_usd` in the
stream, so we just lift it; the others get their tokens priced from a static
table).

The dispatcher `parse_usage(runner, model, session_log)` is what callers
should use; the per-vendor `parse_*_usage` functions are exposed for tests
and the `scripts/backfill_usage.py` rewrite path.
"""

from __future__ import annotations

import json
from pathlib import Path


def parse_session_log_usage(session_log: Path, model: str = "") -> dict:
    """Extract usage (cost, tokens) from a claude --output-format stream-json log.

    Returns {} if the log is missing or unreadable. Tolerant of truncation —
    returns whatever the last `result` event contained.

    Claude reports `total_cost_usd` directly in the stream — for native
    Anthropic Claude that's authoritative. When the Claude Code CLI is
    pointed at a third-party backend (e.g. DeepSeek's Anthropic-compatible
    endpoint via ANTHROPIC_BASE_URL), the CLI still emits `total_cost_usd`
    but it's computed from Anthropic's prices applied to the third-party's
    tokens — wrong by ~3-5×. We detect this case via the `model` arg and
    re-price via `agent_runtime.pricing` instead.
    """
    from agent_runtime import pricing

    session_log = Path(session_log) if session_log else None
    if session_log is None or not session_log.exists():
        return {}
    total_cost = 0.0
    input_tokens = output_tokens = 0
    cache_read = cache_creation = 0
    n_turns = 0
    try:
        with open(session_log) as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "result":
                    continue
                c = ev.get("total_cost_usd")
                if isinstance(c, (int, float)):
                    total_cost = float(c)
                u = ev.get("usage") or {}
                input_tokens = int(u.get("input_tokens", input_tokens) or input_tokens)
                output_tokens = int(u.get("output_tokens", output_tokens) or output_tokens)
                cache_read = int(u.get("cache_read_input_tokens", cache_read) or cache_read)
                cache_creation = int(
                    u.get("cache_creation_input_tokens", cache_creation) or cache_creation
                )
                n_turns = int(ev.get("num_turns", n_turns) or n_turns)
    except OSError:
        return {}
    if not (total_cost or input_tokens or output_tokens):
        return {}
    out = {
        "api_cost_usd": round(total_cost, 6),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "tokens_total_billed": input_tokens + output_tokens + cache_creation,
        "n_turns": n_turns,
    }
    # Override with table-priced cost when CC is routed to a non-Anthropic
    # backend (the stream's total_cost_usd is wrong in that case).
    if model and not model.startswith("claude-"):
        # Claude's input_tokens excludes cached tokens — pass the components
        # directly; pricing.compute_cost expects total prompt = input + cache.
        cost, priced = pricing.compute_cost(
            model,
            input_tokens=input_tokens + cache_read + cache_creation,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
        )
        out["api_cost_usd"] = cost
        out["cost_priced"] = priced
    return out


def parse_codex_usage(session_log: Path, model: str) -> dict:
    """Sum codex `turn.completed.usage` events and price them.

    Codex emits one `turn.completed` per `codex exec` invocation containing
    `{input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens}`.
    `input_tokens` is the OpenAI-convention total (cached portion included);
    `output_tokens` already includes reasoning tokens.
    """
    from agent_runtime import pricing

    session_log = Path(session_log) if session_log else None
    if session_log is None or not session_log.exists():
        return {}
    in_tok = out_tok = cache_read = n_turns = 0
    try:
        with open(session_log) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "turn.completed":
                    continue
                u = ev.get("usage") or {}
                in_tok += int(u.get("input_tokens") or 0)
                out_tok += int(u.get("output_tokens") or 0)
                cache_read += int(u.get("cached_input_tokens") or 0)
                n_turns += 1
    except OSError:
        return {}
    if not (in_tok or out_tok):
        return {}
    cost, priced = pricing.compute_cost(
        model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_input_tokens=cache_read,
    )
    return {
        "api_cost_usd": cost,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": 0,
        "tokens_total_billed": in_tok + out_tok,
        "n_turns": n_turns,
        "cost_priced": priced,
    }


def parse_gemini_usage(session_log: Path, model: str) -> dict:
    """Pull token counts from the last gemini `result.stats` event and price them.

    Gemini stats: `{input_tokens, output_tokens, cached, input, models:{...}}`
    where `input_tokens == cached + input`. The `models` map gives a
    per-routed-model split (e.g. gemini-3-pro-preview vs gemini-3-flash);
    we price each segment with its own rate when multiple models appear.
    """
    from agent_runtime import pricing

    session_log = Path(session_log) if session_log else None
    if session_log is None or not session_log.exists():
        return {}
    stats: dict = {}
    try:
        with open(session_log) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "result" and "stats" in ev:
                    stats = ev["stats"] or {}
    except OSError:
        return {}
    if not stats:
        return {}
    in_tok = int(stats.get("input_tokens") or 0)
    out_tok = int(stats.get("output_tokens") or 0)
    cache_read = int(stats.get("cached") or 0)
    if not (in_tok or out_tok):
        return {}
    # Per-model breakdown if available; else single-model with the configured id.
    per_model = stats.get("models") or {}
    total_cost = 0.0
    priced_all = True
    if per_model:
        for sub_model, sub in per_model.items():
            c, p = pricing.compute_cost(
                sub_model,
                input_tokens=int(sub.get("input_tokens") or 0),
                output_tokens=int(sub.get("output_tokens") or 0),
                cached_input_tokens=int(sub.get("cached") or 0),
            )
            total_cost += c
            priced_all = priced_all and p
    else:
        total_cost, priced_all = pricing.compute_cost(
            model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cache_read,
        )
    return {
        "api_cost_usd": round(total_cost, 6),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": 0,
        "tokens_total_billed": in_tok + out_tok,
        "n_turns": int(stats.get("tool_calls") or 0),
        "cost_priced": priced_all,
    }


def _forge_usage_field(ev: dict, field: str) -> int:
    """Pull a usage field from a forge message event.

    Forge wraps each token count as `{"actual": N}`; tolerate plain ints
    too in case forge upstream changes the shape.
    """
    u = ev.get("usage") or {}
    x = u.get(field)
    if isinstance(x, dict):
        x = x.get("actual")
    return int(x or 0)


def parse_forge_usage(session_log: Path, model: str) -> dict:
    """Sum per-message usage from forge's session.jsonl.

    The forge post-run hook writes one JSON object per line to
    session.jsonl, derived from the SQLite conversation DB. Each
    Assistant message carries a verbatim DeepSeek `usage` block:

        {"message": {"text": {"role": "Assistant", "content": "...", "model": "..."}},
         "usage": {"prompt_tokens":   {"actual": N},
                   "completion_tokens":{"actual": M},
                   "total_tokens":     {"actual": N+M},
                   "cached_tokens":    {"actual": K}}}

    `prompt_tokens` is OpenAI-convention total (cache hits included);
    `cached_tokens` is the cache-hit subset. The first line of the file
    is a synthetic `init` event with conversation-level metadata.
    """
    from agent_runtime import pricing

    session_log = Path(session_log) if session_log else None
    if session_log is None or not session_log.is_file():
        return {}
    in_tok = out_tok = cache_read = n_assistant = 0
    actual_model = ""
    conversation_id = ""
    try:
        with open(session_log) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "init":
                    conversation_id = ev.get("conversation_id", "") or conversation_id
                    actual_model = ev.get("model", "") or actual_model
                    continue
                msg = ev.get("message") or {}
                text = msg.get("text") or {}
                if text.get("role") != "Assistant":
                    continue
                n_assistant += 1
                actual_model = text.get("model") or actual_model
                in_tok += _forge_usage_field(ev, "prompt_tokens")
                out_tok += _forge_usage_field(ev, "completion_tokens")
                cache_read += _forge_usage_field(ev, "cached_tokens")
    except OSError:
        return {}
    if not (in_tok or out_tok):
        return {}
    cost, priced = pricing.compute_cost(
        actual_model or model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_input_tokens=cache_read,
    )
    return {
        "api_cost_usd": cost,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": 0,
        "tokens_total_billed": in_tok + out_tok,
        "n_turns": n_assistant,
        "conversation_id": conversation_id,
        "model_actual": actual_model,
        "cost_priced": priced,
    }


def parse_usage(runner: str, model: str, session_log: Path) -> dict:
    """Dispatch to the right per-vendor parser. Returns {} if no usage found.

    All four supported vendors read from a single per-run `session.jsonl` —
    claude/codex/gemini stream it directly, forge builds it post-run from
    its SQLite DB.
    """
    if runner == "claude":
        return parse_session_log_usage(session_log, model)
    if runner == "codex":
        return parse_codex_usage(session_log, model)
    if runner == "gemini":
        return parse_gemini_usage(session_log, model)
    if runner == "forge":
        return parse_forge_usage(session_log, model)
    # aider: no usage info in the stream (yet).
    return {}
