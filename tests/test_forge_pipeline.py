"""Forge: SQLite conversation DB → session.jsonl → usage dict.

Three independently-testable pieces:
  1. `_truncate_long_strings`  — recursive string-shortening for bash-output bloat.
  2. `_forge_post_run`         — reads .forge_home/.forge/.forge.db, writes session.jsonl.
  3. `parse_forge_usage`       — sums per-message DeepSeek `usage` blocks from the JSONL.

These run without forge installed: we synthesize the SQLite DB ourselves
since the schema is small (single `conversations` row with a JSON
`context` column) and the parser only cares about the `messages` array
shape, which we control.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_runtime.vendors import (
    _FORGE_TRUNC_HEAD,
    _FORGE_TRUNC_LEN,
    _FORGE_TRUNC_TAIL,
    _forge_post_run,
    _truncate_long_strings,
)
from agent_runtime.usage import parse_forge_usage


# ── Truncation ──────────────────────────────────────────────────────────────


def test_truncate_short_string_unchanged():
    s = "hello world"
    assert _truncate_long_strings(s) == s


def test_truncate_at_threshold_unchanged():
    s = "x" * _FORGE_TRUNC_LEN
    assert _truncate_long_strings(s) == s


def test_truncate_collapses_long_string_with_marker():
    s = "A" * 5000 + "B" * 5000  # 10_000 chars > 8192 threshold
    out = _truncate_long_strings(s)
    assert isinstance(out, str)
    assert len(out) < len(s)
    assert out.startswith("A" * _FORGE_TRUNC_HEAD)
    assert out.endswith("B" * _FORGE_TRUNC_TAIL)
    assert "[... truncated" in out
    cut = len(s) - _FORGE_TRUNC_HEAD - _FORGE_TRUNC_TAIL
    assert f"truncated {cut} chars" in out


def test_truncate_recurses_into_dicts():
    payload = {"role": "User", "content": "x" * 20_000, "model": "deepseek-v4-pro"}
    out = _truncate_long_strings(payload)
    assert out["role"] == "User"  # short field untouched
    assert out["model"] == "deepseek-v4-pro"
    assert "[... truncated" in out["content"]


def test_truncate_recurses_into_lists():
    payload = ["short", "x" * 20_000, {"nested": "y" * 20_000}]
    out = _truncate_long_strings(payload)
    assert out[0] == "short"
    assert "[... truncated" in out[1]
    assert "[... truncated" in out[2]["nested"]


def test_truncate_leaves_non_strings_alone():
    payload = {"prompt_tokens": 1234, "cached": True, "ratio": 0.5, "data": None}
    assert _truncate_long_strings(payload) == payload


# ── Helpers for post-run hook tests ─────────────────────────────────────────


def _build_forge_db(db_path: Path, ctx: dict) -> None:
    """Create a forge-shaped SQLite DB with one conversation row.

    Schema mirrors the real forge.db (we only need columns the post-run
    hook reads: `context`, `updated_at`).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY NOT NULL,
            title TEXT,
            workspace_id BIGINT NOT NULL,
            context TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            metrics TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO conversations (conversation_id, workspace_id, context, updated_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (ctx.get("conversation_id", "test-conv"), 1, json.dumps(ctx)),
    )
    conn.commit()
    conn.close()


def _setup_run_dir(tmp_path: Path, ctx: dict | None) -> Path:
    """Lay out a fake `<recast>/` with .forge_home and workspace.

    Returns the workspace path the post-run hook expects.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if ctx is not None:
        db = tmp_path / ".forge_home" / ".forge" / ".forge.db"
        _build_forge_db(db, ctx)
    return workspace


def _assistant_msg(content: str, *, prompt: int, cached: int, completion: int) -> dict:
    """Build a forge-shaped Assistant message with usage."""
    return {
        "message": {
            "text": {
                "role": "Assistant",
                "content": content,
                "model": "deepseek-v4-pro",
            }
        },
        "usage": {
            "prompt_tokens": {"actual": prompt},
            "completion_tokens": {"actual": completion},
            "total_tokens": {"actual": prompt + completion},
            "cached_tokens": {"actual": cached},
        },
    }


# ── _forge_post_run ─────────────────────────────────────────────────────────


def test_forge_post_run_no_db_silent(tmp_path):
    """Missing DB must not raise — the run may have crashed before forge wrote."""
    ws = _setup_run_dir(tmp_path, ctx=None)
    _forge_post_run(ws)  # should not raise
    assert not (ws / "session.jsonl").exists()


def test_forge_post_run_writes_session_jsonl(tmp_path):
    ctx = {
        "conversation_id": "abc-123",
        "model": "deepseek-v4-pro",
        "max_tokens": 32768,
        "messages": [
            {"message": {"text": {"role": "System", "content": "system prompt"}}},
            {"message": {"text": {"role": "User", "content": "hello"}}},
            _assistant_msg("hi back", prompt=100, cached=80, completion=5),
        ],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    sj = ws / "session.jsonl"
    assert sj.is_file()
    lines = sj.read_text().splitlines()
    # 1 init event + 3 messages
    assert len(lines) == 4
    init = json.loads(lines[0])
    assert init == {
        "type": "init",
        "conversation_id": "abc-123",
        "model": "deepseek-v4-pro",
        "max_tokens": 32768,
        "n_messages": 3,
    }
    # Other lines are the messages verbatim.
    parsed_msgs = [json.loads(line) for line in lines[1:]]
    assert parsed_msgs[0]["message"]["text"]["role"] == "System"
    assert parsed_msgs[2]["usage"]["prompt_tokens"]["actual"] == 100


def test_forge_post_run_truncates_long_content(tmp_path):
    huge = "X" * 50_000  # massively over 8192 threshold
    ctx = {
        "conversation_id": "trunc-test",
        "model": "deepseek-v4-pro",
        "messages": [
            {"message": {"text": {"role": "User", "content": huge}}},
            _assistant_msg("ok", prompt=10, cached=0, completion=2),
        ],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    sj = ws / "session.jsonl"
    # Whole file must be far smaller than the raw 50KB content.
    assert sj.stat().st_size < 10_000
    user_line = json.loads(sj.read_text().splitlines()[1])
    content = user_line["message"]["text"]["content"]
    assert "[... truncated" in content
    # Head/tail preserved around the marker.
    assert content.startswith("X" * _FORGE_TRUNC_HEAD)
    assert content.endswith("X" * _FORGE_TRUNC_TAIL)


def test_forge_post_run_picks_newest_conversation(tmp_path):
    """If the DB somehow has multiple rows, the most recent one wins."""
    db_path = tmp_path / ".forge_home" / ".forge" / ".forge.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,
           title TEXT, workspace_id BIGINT NOT NULL, context TEXT,
           created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
           updated_at TIMESTAMP, metrics TEXT)"""
    )
    older = {"conversation_id": "old", "messages": [], "model": "old-model"}
    newer = {"conversation_id": "new", "messages": [], "model": "new-model"}
    conn.execute(
        "INSERT INTO conversations VALUES (?, NULL, 1, ?, datetime('2026-01-01'), datetime('2026-01-01'), NULL)",
        ("old", json.dumps(older)),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (?, NULL, 1, ?, datetime('2026-01-02'), datetime('2026-04-30'), NULL)",
        ("new", json.dumps(newer)),
    )
    conn.commit()
    conn.close()

    ws = tmp_path / "workspace"
    ws.mkdir()
    _forge_post_run(ws)

    init = json.loads((ws / "session.jsonl").read_text().splitlines()[0])
    assert init["conversation_id"] == "new"
    assert init["model"] == "new-model"


def test_forge_post_run_corrupt_db_silent(tmp_path):
    """Corrupt SQLite shouldn't crash the run finalizer."""
    db_path = tmp_path / ".forge_home" / ".forge" / ".forge.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a sqlite database")
    ws = tmp_path / "workspace"
    ws.mkdir()
    _forge_post_run(ws)  # must not raise
    assert not (ws / "session.jsonl").exists()


def test_forge_post_run_invalid_context_json_silent(tmp_path):
    """A row with non-JSON context shouldn't crash the run finalizer."""
    db_path = tmp_path / ".forge_home" / ".forge" / ".forge.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,
           title TEXT, workspace_id BIGINT NOT NULL, context TEXT,
           created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
           updated_at TIMESTAMP, metrics TEXT)"""
    )
    conn.execute(
        "INSERT INTO conversations VALUES ('x', NULL, 1, ?, datetime('now'), datetime('now'), NULL)",
        ("not-valid-json",),
    )
    conn.commit()
    conn.close()
    ws = tmp_path / "workspace"
    ws.mkdir()
    _forge_post_run(ws)  # must not raise
    assert not (ws / "session.jsonl").exists()


# ── parse_forge_usage ───────────────────────────────────────────────────────


def test_parse_forge_usage_missing_file(tmp_path):
    assert parse_forge_usage(tmp_path / "nope.jsonl", "deepseek-v4-pro") == {}


def test_parse_forge_usage_empty_log(tmp_path):
    sj = tmp_path / "session.jsonl"
    sj.write_text("")
    assert parse_forge_usage(sj, "deepseek-v4-pro") == {}


def test_parse_forge_usage_no_assistant_messages(tmp_path):
    """init + System/User only → no usage to surface."""
    ctx = {
        "conversation_id": "x",
        "model": "deepseek-v4-pro",
        "messages": [
            {"message": {"text": {"role": "System", "content": "..."}}},
            {"message": {"text": {"role": "User", "content": "hi"}}},
        ],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)
    assert parse_forge_usage(ws / "session.jsonl", "deepseek-v4-pro") == {}


def test_parse_forge_usage_sums_across_assistant_turns(tmp_path):
    ctx = {
        "conversation_id": "multi-turn",
        "model": "deepseek-v4-pro",
        "messages": [
            {"message": {"text": {"role": "User", "content": "q1"}}},
            _assistant_msg("a1", prompt=1000, cached=800, completion=10),
            {"message": {"text": {"role": "User", "content": "q2"}}},
            _assistant_msg("a2", prompt=2000, cached=1700, completion=20),
            {"message": {"text": {"role": "User", "content": "q3"}}},
            _assistant_msg("a3", prompt=3000, cached=2500, completion=30),
        ],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    u = parse_forge_usage(ws / "session.jsonl", "deepseek-v4-pro")
    assert u["input_tokens"] == 6000  # 1000 + 2000 + 3000
    assert u["cache_read_tokens"] == 5000  # 800 + 1700 + 2500
    assert u["output_tokens"] == 60  # 10 + 20 + 30
    assert u["n_turns"] == 3
    assert u["conversation_id"] == "multi-turn"
    assert u["model_actual"] == "deepseek-v4-pro"
    assert u["cost_priced"] is True


def test_parse_forge_usage_cost_math(tmp_path):
    """Cost should match: non-cached_in × $1.74/M + cached × $0.0145/M + out × $3.48/M."""
    ctx = {
        "conversation_id": "cost-test",
        "model": "deepseek-v4-pro",
        "messages": [_assistant_msg("ok", prompt=1_000_000, cached=800_000, completion=10_000)],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    u = parse_forge_usage(ws / "session.jsonl", "deepseek-v4-pro")
    # 200_000 × 1.74e-6 + 800_000 × 1.45e-8 + 10_000 × 3.48e-6
    expected = 200_000 * 1.74e-6 + 800_000 * 1.45e-8 + 10_000 * 3.48e-6
    assert u["api_cost_usd"] == pytest.approx(expected, abs=1e-6)


def test_parse_forge_usage_unknown_model_flags_unpriced(tmp_path):
    """A model not in the pricing table → cost=0, cost_priced=false."""
    ctx = {
        "conversation_id": "unknown-model",
        "model": "made-up-model-v9",
        "messages": [
            {
                "message": {
                    "text": {
                        "role": "Assistant",
                        "content": "x",
                        "model": "made-up-model-v9",
                    }
                },
                "usage": {
                    "prompt_tokens": {"actual": 1000},
                    "completion_tokens": {"actual": 50},
                    "total_tokens": {"actual": 1050},
                    "cached_tokens": {"actual": 0},
                },
            }
        ],
    }
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    u = parse_forge_usage(ws / "session.jsonl", "made-up-model-v9")
    assert u["cost_priced"] is False
    assert u["api_cost_usd"] == 0.0
    assert u["input_tokens"] == 1000


def test_parse_forge_usage_tolerates_plain_int_usage(tmp_path):
    """Some forge versions might emit raw ints instead of {actual: N} dicts."""
    msg = {
        "message": {"text": {"role": "Assistant", "content": "y", "model": "deepseek-v4-pro"}},
        "usage": {
            "prompt_tokens": 500,  # bare int, not {"actual": ...}
            "completion_tokens": 5,
            "cached_tokens": 100,
        },
    }
    ctx = {"conversation_id": "raw-ints", "model": "deepseek-v4-pro", "messages": [msg]}
    ws = _setup_run_dir(tmp_path, ctx)
    _forge_post_run(ws)

    u = parse_forge_usage(ws / "session.jsonl", "deepseek-v4-pro")
    assert u["input_tokens"] == 500
    assert u["output_tokens"] == 5
    assert u["cache_read_tokens"] == 100
