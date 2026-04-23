#!/usr/bin/env python3
"""LLM-based trajectory failure taxonomy — adaptation of Terminal-Bench's TAT.

Evaluates *how* an agent failed (process quality) independently of *what*
it produced (output correctness). Orthogonal to `llm_judge.py`: that tool
asks "is the answer right and not cheated?"; this one asks "did the agent
follow the spec, avoid step repetition, verify its work, …"

Nine failure modes in three classes; rubrics in [TAXONOMY.md](TAXONOMY.md).
Per Terminal-Bench 2.0 (arXiv:2601.11868), this achieves ~90% agreement
with human annotators when used with a frontier reasoning model.

Usage:
    python -m LHCRecastBench.evaluation.trajectory_judge <run_path>

Output (JSON written to <run_dir>/eval/trajectory_judge.json):
    {
      "modes":   {<mode>: {"matched": bool, "evidence": [str, ...], "class": str}},
      "classes": {<class>: {"prevalence": float, "modes": {...}}},
      "n_matched": int,
      "judge_model": str,
      "runtime_s": float
    }
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from ._resolve import resolve_run


_THIS_DIR = Path(__file__).resolve().parent


MODE_SPECS: dict[str, dict[str, str]] = {
    "disobey_spec": {
        "class": "execution",
        "rubric": (
            "Agent materially contradicts explicit task directives (hard or soft). "
            "Includes using the wrong source of truth, ignoring a required method, "
            "replacing a required implementation with a placeholder, fabricating data "
            "instead of recovering it from the specified source. Format-only violations "
            "are NOT a match. If the agent later fully corrected the violation, NO MATCH."
        ),
    },
    "step_repetition": {
        "class": "execution",
        "rubric": (
            "Agent re-executes the same phase (same sub-goal, tool, target, method) "
            "multiple times without a meaningful strategy change. Abort-loops count. "
            "Tool switches or genuinely different inputs do NOT count."
        ),
    },
    "unaware_termination": {
        "class": "execution",
        "rubric": (
            "Agent continues acting past a reasonable stopping point — after declared "
            "success, after established futility, or after completion — with no new "
            "justification tied to the task."
        ),
    },
    "reasoning_action_mismatch": {
        "class": "coherence",
        "rubric": (
            "Agent's stated claims (e.g. 'tests passed', 'requirements satisfied') are "
            "contradicted by visible actions, logs, or artifacts. Transient mid-debug "
            "claims later corrected do NOT match."
        ),
    },
    "context_loss": {
        "class": "coherence",
        "rubric": (
            "Agent forgets or contradicts relevant recent context — a file read, an "
            "error seen, a plan committed to — without explicit justification from new "
            "information."
        ),
    },
    "task_derailment": {
        "class": "coherence",
        "rubric": (
            "Agent spends a substantive span (~5%+ of the transcript) on something "
            "orthogonal to the task objective. Exploratory context-gathering that "
            "serves the objective does NOT match."
        ),
    },
    "premature_termination": {
        "class": "verification",
        "rubric": (
            "Agent declares the task complete before delivering required artifacts. "
            "For recast: any required HEPRecastData/*.yaml still has null values, or "
            "report.md is missing, at the moment of termination."
        ),
    },
    "no_incorrect_verification": {
        "class": "verification",
        "rubric": (
            "Agent bypasses a designated verifier (no bin/run-analysis, no comparison "
            "against the reference), OR runs verification that fails and still declares "
            "done."
        ),
    },
    "weak_verification": {
        "class": "verification",
        "rubric": (
            "Agent's verification fails to cover task-critical properties — e.g. checks "
            "'the YAML parses' but not 'the values match the reference within tolerance'; "
            "or fabricates data to satisfy a check."
        ),
    },
}


CLASSES = ("execution", "coherence", "verification")
MAX_TRANSCRIPT_CHARS = 80_000


# ── Transcript extraction ──────────────────────────────────────────────────


def _extract_transcript(session_log: Path, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Extract the agent's transcript in a prompt-friendly form.

    Reuses llm_judge's extractor: Claude stream-json becomes
    [THOUGHT]/[TOOL]/[RESULT]-tagged lines; other formats pass through.
    """
    from .llm_judge import extract_session_summary

    return extract_session_summary(session_log, max_chars=max_chars)


# ── Prompt ─────────────────────────────────────────────────────────────────


def _build_prompt(transcript: str, report: str, task_md: str) -> str:
    """Single bundled prompt — one LLM call labels all 9 modes.

    Precision may be lower than one-prompt-per-mode (Terminal-Bench's
    approach), but costs are ~9x lower. Start here; split modes later if
    calibration is poor.
    """
    modes_section = "\n".join(
        f"- `{mode}` (class: {spec['class']}): {spec['rubric']}"
        for mode, spec in MODE_SPECS.items()
    )
    mode_names = list(MODE_SPECS)
    schema_example = json.dumps(
        {m: {"matched": False, "evidence": []} for m in mode_names},
        indent=2,
    )
    return f"""You are a careful auditor evaluating an AI agent's trajectory against a
fixed failure-mode taxonomy. Nine failure modes in three classes (Execution,
Coherence, Verification). Read the task, the agent's transcript, and its final
report, then label each mode independently.

## Failure modes

{modes_section}

## Decision rules

- For EACH mode, decide MATCH (matched=true) or NO MATCH (matched=false).
- Modes are NOT mutually exclusive — a run can match several.
- When in doubt, answer NO MATCH. False positives are more harmful than false
  negatives here.
- For every MATCH, cite 1–3 SHORT concrete quotes or "turn N: <detail>"
  references from the transcript. If you cannot point to concrete evidence,
  the answer must be NO MATCH.
- Do not paraphrase; quote directly or give a turn pointer.

## Output format

Reply with ONLY a single JSON object. No prose before or after, no markdown
fences. Schema:

{schema_example}

## Task (from TASK.md)

{task_md or "(no TASK.md found)"}

## Agent's final report (report.md)

{report or "(no report.md found)"}

## Agent's trajectory (truncated at {MAX_TRANSCRIPT_CHARS:,} chars)

{transcript}
"""


# ── Judge invocation ───────────────────────────────────────────────────────


_ARGV_UNSAFE = {chr(c) for c in range(32) if c not in (9, 10, 13)} | {"\x7f"}


def _sanitize_for_argv(s: str) -> str:
    """Strip characters that break subprocess argv (NUL) or corrupt terminal output."""
    if not any(c in _ARGV_UNSAFE or c == "\x00" for c in s):
        return s
    return "".join(c for c in s if c != "\x00" and c not in _ARGV_UNSAFE)


def _call_claude(prompt: str, model: str) -> tuple[str, float]:
    """Call the claude CLI with stream-json and return (response_text, seconds)."""
    # argv cannot contain NUL bytes. Codex-style plain-text session logs may
    # carry raw NULs from binary tool outputs; strip them here (and other C0
    # controls except tab/newline/CR) before passing to the subprocess.
    prompt = _sanitize_for_argv(prompt)
    t0 = time.time()
    proc = subprocess.Popen(
        [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    text_parts: list[str] = []
    final_result_text: str | None = None
    last_heartbeat = t0
    turns = 0
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "assistant":
                turns += 1
                for block in ev.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
            elif t == "result":
                final_result_text = ev.get("result") or None
            now = time.time()
            if now - last_heartbeat >= 30:
                print(
                    f"  [trajectory_judge] {int(now - t0):4d}s  turns={turns}",
                    flush=True,
                )
                last_heartbeat = now
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        raise
    if rc != 0:
        err = (proc.stderr.read() if proc.stderr else "") or "(no stderr)"
        raise RuntimeError(f"claude CLI exit {rc}: {err[:500]}")

    response = final_result_text or "".join(text_parts)
    return response, time.time() - t0


def _parse_verdict(response: str) -> dict:
    """Parse the judge's JSON response.

    Tolerates ```json ...``` fences and leading prose by extracting the
    first balanced `{...}` block.
    """
    text = response.strip()
    # Strip markdown fences if the model ignored the no-fences instruction.
    if text.startswith("```"):
        text = text.split("```", 2)
        text = text[1] if len(text) > 1 else ""
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    # Find the first balanced JSON object.
    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON object in response: {response[:200]!r}")
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError(f"Unbalanced JSON in response: {response[:200]!r}")
    return json.loads(text[start:end])


# ── Aggregation ────────────────────────────────────────────────────────────


def _attach_classes(raw: dict) -> dict:
    """Normalize the judge's per-mode verdict and add class groupings."""
    modes: dict[str, dict] = {}
    for mode, spec in MODE_SPECS.items():
        v = raw.get(mode) or {}
        modes[mode] = {
            "matched": bool(v.get("matched", False)),
            "evidence": list(v.get("evidence", []))[:3],
            "class": spec["class"],
        }
    classes: dict[str, dict] = {cls: {"modes": {}} for cls in CLASSES}
    for mode, data in modes.items():
        classes[data["class"]]["modes"][mode] = {
            "matched": data["matched"],
            "evidence": data["evidence"],
        }
    n_matched = sum(1 for m in modes.values() if m["matched"])
    for block in classes.values():
        n_cls = sum(1 for m in block["modes"].values() if m["matched"])
        block["prevalence"] = round(n_cls / n_matched, 3) if n_matched else 0.0
        block["n_matched"] = n_cls
    return {"modes": modes, "classes": classes, "n_matched": n_matched}


# ── Public entry point ────────────────────────────────────────────────────


def judge_trajectory(
    session_log: Path,
    report_md: Path,
    task_md: Path,
    judge_model: str = "claude-opus-4-6",
) -> dict:
    transcript = _extract_transcript(session_log)
    report = report_md.read_text() if report_md.is_file() else ""
    task = task_md.read_text() if task_md.is_file() else ""

    prompt = _build_prompt(transcript, report, task)
    print(
        f"  [trajectory_judge] invoking {judge_model} " f"(prompt ~{len(prompt) // 1000}K chars)",
        flush=True,
    )
    response, runtime_s = _call_claude(prompt, judge_model)
    try:
        raw = _parse_verdict(response)
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "error": f"judge returned unparseable output: {exc}",
            "raw_response": response[:2000],
            "judge_model": judge_model,
            "runtime_s": round(runtime_s, 1),
        }

    result = _attach_classes(raw)
    result["judge_model"] = judge_model
    result["runtime_s"] = round(runtime_s, 1)
    return result


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trajectory_judge",
        description="LLM-based trajectory failure taxonomy (Terminal-Bench TAT).",
    )
    parser.add_argument(
        "run_path",
        help="Run dir, workspace, iter dir, or HEPRecastData dir.",
    )
    parser.add_argument(
        "--judge-model",
        default="claude-opus-4-6",
        help="Model to use as judge (default: claude-opus-4-6).",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        rp = resolve_run(args.run_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    session_logs = sorted(rp.artifact_dir.glob("session_log*.txt"))
    if not session_logs:
        print(f"ERROR: no session_log*.txt in {rp.artifact_dir}", file=sys.stderr)
        return 1
    session_log = session_logs[-1]  # Last one if multiple
    report_md = rp.artifact_dir / "report.md"
    task_md = rp.artifact_dir / "agent_context" / "TASK.md"
    if not task_md.is_file():
        # Single-shot layouts keep TASK.md at the workspace root.
        task_md = rp.artifact_dir / "TASK.md"

    rp.eval_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = judge_trajectory(session_log, report_md, task_md, args.judge_model)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out = rp.eval_dir / "trajectory_judge.json"
    out.write_text(json.dumps(result, indent=2))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  trajectory_judge ({result['judge_model']}, {result['runtime_s']}s)")
            print(f"  {result['n_matched']}/9 failure modes matched:")
            for cls in CLASSES:
                block = result["classes"][cls]
                matched = [m for m, v in block["modes"].items() if v["matched"]]
                print(f"    {cls}: {block['n_matched']}  {' '.join(matched) or '—'}")
        print(f"  Saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
