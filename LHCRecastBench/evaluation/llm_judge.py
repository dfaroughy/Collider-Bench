#!/usr/bin/env python3
"""LLM-as-a-Judge evaluation of agent reasoning quality.

Agent-agnostic: accepts any combination of session logs, filled HEPData,
and optional structured artifacts. The judge LLM reads whatever is provided
and evaluates reasoning quality.

Usage:
    # Minimal: just session logs
    python -m LHCRecastBench.evaluation.llm_judge \
        --session-logs agent_session.txt \
        --recast-dir HEPRecastData/ \
        --arxiv 1707.06193

    # With optional artifacts (our baseline produces these)
    python -m LHCRecastBench.evaluation.llm_judge \
        --session-logs session_log.txt \
        --recast-dir HEPRecastData/ \
        --arxiv 1707.06193 \
        --artifacts audit.json report.md

    # Multiple session logs (multi-agent runs)
    python -m LHCRecastBench.evaluation.llm_judge \
        --session-logs session_agent_000.txt session_agent_001.txt \
        --recast-dir HEPRecastData/ \
        --arxiv 1707.06193

    # Shortcut: pass agent dir (auto-discovers files, our baseline layout)
    python -m LHCRecastBench.evaluation.llm_judge \
        --agent-dir validation/agent_002 \
        --arxiv 1707.06193
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml


JUDGE_RUBRIC_PATH = Path(__file__).parent / "judge_rubric.md"


def _write_corrected_hepdata(
    provenance: dict,
    original_dir: Path,
    corrected_dir: Path,
) -> None:
    """Write corrected HEPRecastData using judge's provenance verification.

    Copies the original files, then overwrites values where the judge
    found COPIED series and provided corrected_values.
    """
    import shutil

    original_dir = Path(original_dir)
    corrected_dir = Path(corrected_dir)

    # Start with a copy of the original
    if corrected_dir.exists():
        shutil.rmtree(corrected_dir)
    shutil.copytree(original_dir, corrected_dir)

    # Check if judge provided corrected_hepdata (full YAML content)
    corrected_hepdata = provenance.get("corrected_hepdata", {})
    if corrected_hepdata:
        for filename, content in corrected_hepdata.items():
            if isinstance(content, dict):
                out_path = corrected_dir / filename
                yaml.dump(content, open(out_path, "w"), default_flow_style=False, sort_keys=False)
        return

    # Otherwise, apply per-series corrections from provenance_verification.series
    series_info = provenance.get("series", {})
    for series_key, info in series_info.items():
        if info.get("classification") not in ("COPIED", "NULL_BUT_COMPUTED"):
            continue
        corrected_values = info.get("corrected_values")
        if corrected_values is None:
            # No computed values available — set to null
            corrected_values = [None] * 10  # will be truncated to actual bin count

        # Parse series_key: "obs_low/BACKGROUND" -> file=obs_low.yaml, series=BACKGROUND
        parts = series_key.split("/")
        if len(parts) != 2:
            continue
        filename = parts[0] + ".yaml"
        series_name = parts[1]

        yaml_path = corrected_dir / filename
        if not yaml_path.exists():
            continue

        try:
            data = yaml.safe_load(yaml_path.read_text())
            for dep in data.get("dependent_variables", []):
                if dep.get("header", {}).get("name") == series_name:
                    values = dep.get("values", [])
                    for i, entry in enumerate(values):
                        if i < len(corrected_values):
                            entry["value"] = corrected_values[i]
                        else:
                            entry["value"] = None
                        # Clear errors for corrected values
                        for err in entry.get("errors", []):
                            if "symerror" in err:
                                err["symerror"] = None
                    break

            with open(yaml_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        except Exception:
            pass


JUDGE_PROMPT_TEMPLATE = """\
{rubric}

--- FILLED HEPDATA (agent's recast results) ---
{recast_yaml}

--- REFERENCE HEPDATA (published CMS values) ---
{reference_yaml}

--- ADDITIONAL ARTIFACTS ---
{artifacts_text}

--- SESSION LOGS ---
{session_summary}
"""


# ── Session log extraction ──────────────────────────────────────────────────


def extract_session_summary(session_path: Path, max_chars: int = 50000) -> str:
    """Extract key reasoning moments from a session log.

    Supports Claude stream-json format natively. For other formats,
    returns the raw text (the judge LLM can read any format).
    """
    if not session_path.exists():
        return "(no session log)"

    # Try Claude stream-json format first
    first_line = ""
    with open(session_path) as f:
        first_line = f.readline().strip()

    if first_line.startswith("{") and '"type"' in first_line:
        return _extract_claude_stream_json(session_path, max_chars)
    else:
        # Raw text log — truncate and return as-is
        text = session_path.read_text()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text


def _extract_claude_stream_json(session_path: Path, max_chars: int) -> str:
    """Extract from Claude CLI stream-json format."""
    moments = []
    total_chars = 0

    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")

            if msg_type == "assistant":
                content = msg.get("message", {}).get("content", [])
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = block["text"].strip()
                        if text:
                            entry = f"[THOUGHT] {text}\n"
                            moments.append(entry)
                            total_chars += len(entry)
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        inp = block.get("input", {})
                        if name == "Bash":
                            cmd = inp.get("command", "")[:200]
                            entry = f"[TOOL] Bash: {cmd}\n"
                        elif name in ("Read", "Write"):
                            path = inp.get("file_path", "")
                            short = path.split("/")[-1] if "/" in path else path
                            entry = f"[TOOL] {name}: {short}\n"
                        elif name == "Edit":
                            path = inp.get("file_path", "")
                            short = path.split("/")[-1] if "/" in path else path
                            entry = f"[TOOL] Edit: {short}\n"
                        elif name == "Grep":
                            pattern = inp.get("pattern", "")
                            entry = f"[TOOL] Grep: {pattern}\n"
                        else:
                            entry = f"[TOOL] {name}\n"
                        moments.append(entry)
                        total_chars += len(entry)

            elif msg_type == "user":
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            rc = block.get("content", "")
                            if not isinstance(rc, str):
                                continue
                            is_error = (
                                rc.startswith("Error")
                                or rc.startswith("ERROR")
                                or "Traceback (most recent" in rc
                                or "exit code 1" in rc.lower()
                                or "command not found" in rc.lower()
                            )
                            if is_error:
                                entry = f"[ERROR] {rc[:300]}\n"
                                moments.append(entry)
                                total_chars += len(entry)

            if total_chars > max_chars:
                moments.append("[... truncated ...]\n")
                break

    return "".join(moments)


# ── Load HEPData for context ────────────────────────────────────────────────


def _load_hepdata_summary(directory: Path, max_chars: int = 5000) -> str:
    """Load HEPData YAML files as text for the judge."""
    if not directory or not directory.exists():
        return "(not available)"

    parts = []
    for yf in sorted(directory.glob("*.yaml")):
        if yf.name == "submission.yaml":
            continue
        text = yf.read_text()
        parts.append(f"--- {yf.name} ---\n{text}\n")

    result = "\n".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"
    return result


# ── Run the judge ───────────────────────────────────────────────────────────


def run_judge(
    session_logs: list[Path],
    results_dir: Path | None,
    reference_file: Path | None,
    artifacts: list[Path] | None = None,
    model: str = "claude-opus-4-6",
    output_dir: Path | None = None,
    rp=None,
) -> dict:
    """Run the LLM judge.

    Args:
        session_logs: list of session log files (any readable format)
        results_dir: path to the agent's filled results/ dir (optional)
        reference_file: path to the ground-truth histogram yaml (optional)
        artifacts: optional list of additional files (audit.json, report.md, etc.)
        model: judge LLM model
        output_dir: where to save the failure report (optional)
        rp: RunPaths, used when provenance correction re-scores the corrected dir
    """
    # Load session logs
    session_parts = []
    for log_path in session_logs:
        if log_path.exists():
            summary = extract_session_summary(log_path)
            session_parts.append(f"--- {log_path.name} ---\n{summary}")
    session_summary = "\n\n".join(session_parts) if session_parts else "(no session logs provided)"

    # Load recast and reference HEPData
    recast_yaml = _load_hepdata_summary(results_dir) if results_dir else "(not provided)"
    reference_yaml = "(not available)"
    if reference_file and reference_file.is_file():
        # _load_hepdata_summary takes a dir; wrap via a tempdir-like single-file pass.
        import tempfile
        import shutil as _sh

        with tempfile.TemporaryDirectory() as td:
            _sh.copy2(reference_file, Path(td) / reference_file.name)
            reference_yaml = _load_hepdata_summary(Path(td))

    # Load additional artifacts
    artifacts_parts = []
    for art_path in artifacts or []:
        if art_path.exists():
            text = art_path.read_text()
            if len(text) > 10000:
                text = text[:10000] + "\n... (truncated)"
            artifacts_parts.append(f"--- {art_path.name} ---\n{text}")
    artifacts_text = "\n\n".join(artifacts_parts) if artifacts_parts else "(none provided)"

    # Build prompt
    rubric = JUDGE_RUBRIC_PATH.read_text()
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        rubric=rubric,
        recast_yaml=recast_yaml,
        reference_yaml=reference_yaml,
        artifacts_text=artifacts_text,
        session_summary=session_summary,
    )

    # Call Claude via CLI using stream-json so we can show a live heartbeat
    # instead of blocking silently for up to 30 minutes. Each line of stdout
    # is one JSON event (system/assistant/user/result). We accumulate the
    # assistant text blocks and parse the JSON verdict from them.
    print(
        f"  [llm_judge] invoking {model} (prompt ~{len(prompt) // 1000}K chars) "
        f"— streaming progress below",
        flush=True,
    )
    import time as _time

    # argv cannot contain NUL bytes. Codex plain-text session logs may carry
    # raw NULs from binary tool outputs; strip them (and C0 control chars
    # except tab/newline/CR) before handing the prompt to subprocess.
    _unsafe = {chr(c) for c in range(32) if c not in (9, 10, 13)} | {"\x00", "\x7f"}
    if any(c in _unsafe for c in prompt):
        prompt = "".join(c for c in prompt if c not in _unsafe)

    t0 = _time.time()
    proc = subprocess.Popen(
        ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    text_parts: list[str] = []
    final_result_text: str | None = None
    n_turns = 0
    n_tool_calls = 0
    last_heartbeat = t0
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
                n_turns += 1
                for block in ev.get("message", {}).get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        n_tool_calls += 1
            elif t == "result":
                final_result_text = ev.get("result") or None

            now = _time.time()
            if now - last_heartbeat >= 30:
                print(
                    f"  [llm_judge] {int(now - t0):4d}s  "
                    f"turns={n_turns}  tool_calls={n_tool_calls}",
                    flush=True,
                )
                last_heartbeat = now
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        return {"error": "Judge interrupted by user"}

    if rc != 0:
        stderr = (proc.stderr.read() if proc.stderr else "")[:500]
        return {"error": f"Judge CLI failed (rc={rc}): {stderr}"}

    elapsed = int(_time.time() - t0)
    print(
        f"  [llm_judge] completed in {elapsed}s ({n_turns} turns, {n_tool_calls} tool calls)",
        flush=True,
    )

    # The judge produced one final JSON blob in its text response. The stream
    # may split it across multiple text blocks; prefer the last-message text
    # if the CLI provided it, else concatenate everything we saw.
    text = final_result_text if final_result_text is not None else "".join(text_parts)

    # Extract JSON from judge response
    try:
        start = text.index("{")
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        scores = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError) as e:
        return {"error": f"Could not parse judge response: {e}", "raw": text[:2000]}

    scores["judge_model"] = model

    # Save full judge output + extracted failure report
    if output_dir:
        (output_dir / "judge_scores.json").write_text(json.dumps(scores, indent=2))
    failure_report = scores.get("reasoning_failure_report", "")
    if failure_report and output_dir:
        report_path = output_dir / "judge_failure_report.md"
        header = f"# Reasoning Failure Report\n\n**Judge model**: {model}\n\n"
        report_path.write_text(header + failure_report)
        scores["failure_report_path"] = str(report_path)

    # Handle provenance verification and correction
    provenance = scores.get("provenance_verification", {})
    if provenance.get("status") == "CORRECTED" and output_dir and results_dir and rp is not None:
        corrected_dir = output_dir / "results_corrected_by_judge"
        _write_corrected_hepdata(provenance, results_dir, corrected_dir)
        scores["corrected_dir"] = str(corrected_dir)

        # Re-score on corrected data: clone rp pointing at corrected_dir.
        try:
            from dataclasses import replace as _replace

            from LHCRecastBench.evaluation.score import score_run

            rp_corrected = _replace(rp, results_dir=corrected_dir)
            corrected_scores = score_run(rp_corrected)
            scores["corrected_score"] = corrected_scores
            (output_dir / "score_corrected.json").write_text(json.dumps(corrected_scores, indent=2))
            submitted_scores = score_run(rp)
            scores["submitted_score"] = submitted_scores

            print("\n  Provenance correction applied:")
            for label, sc in [("Submitted", submitted_scores), ("Corrected", corrected_scores)]:
                sh = sc.get("overall_shape")
                no = sc.get("overall_normalization")
                cb = sc.get("overall_combined")
                if sh is not None and no is not None:
                    print(
                        f"    {label}: shape={sh:.2f}  norm={no:.2f}  combined={cb:.2f}  "
                        f"({sc.get('n_filled', 0)}/{sc.get('n_bins', 0)} bins filled)"
                    )
        except Exception as e:
            scores["corrected_score_error"] = str(e)

    return scores


# ── Display ─────────────────────────────────────────────────────────────────


def print_scores(scores: dict) -> None:
    if "error" in scores:
        print(f"  ERROR: {scores['error']}")
        if "raw" in scores:
            print(f"  Raw: {scores['raw'][:500]}")
        return

    print("\n  LLM Judge Evaluation")
    print(f"  Judge model: {scores.get('judge_model', '?')}")
    print(f"  {'=' * 60}")

    dimensions = [
        ("Diagnosis Quality", "diagnosis_quality"),
        ("Creative Problem-Solving", "creative_problem_solving"),
        ("Scientific Honesty", "scientific_honesty"),
        ("Hallucination", "hallucination"),
        ("Tool Use Efficiency", "tool_use_efficiency"),
        ("Artifact Completeness", "artifact_completeness"),
    ]

    for label, key in dimensions:
        dim = scores.get(key, {})
        score = dim.get("score", "?")
        reasoning = dim.get("reasoning", "")
        bar = "#" * (score if isinstance(score, int) else 0)
        print(f"  {label:<28s} {score}/5 {bar}")
        if reasoning:
            words = reasoning.split()
            line = "    "
            for w in words:
                if len(line) + len(w) > 75:
                    print(line)
                    line = "    "
                line += w + " "
            if line.strip():
                print(line)

    overall = scores.get("overall_reasoning_score", "?")
    print(f"\n  {'─' * 60}")
    print(f"  Overall Reasoning Score: {overall}/5")

    for label, key in [
        ("Strengths", "key_strengths"),
        ("Weaknesses", "key_weaknesses"),
        ("Missed opportunities", "missed_opportunities"),
    ]:
        items = scores.get(key, [])
        if items:
            prefix = {"Strengths": "+", "Weaknesses": "-", "Missed opportunities": "*"}[label]
            print(f"\n  {label}:")
            for item in items:
                print(f"    {prefix} {item}")

    failure_report = scores.get("reasoning_failure_report", "")
    if failure_report:
        print(f"\n  {'─' * 60}")
        lines = failure_report.strip().split("\n")
        n_failures = len([ln for ln in lines if ln.startswith("### F")])
        print(f"  Failure Report ({n_failures} failures):")
        for line in lines[:20]:
            print(f"    {line}")
        if len(lines) > 20:
            print(f"    ... ({len(lines) - 20} more lines)")
        path = scores.get("failure_report_path")
        if path:
            print(f"\n  Full report: {path}")

    print()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="LLM-as-a-Judge evaluation of agent reasoning quality. "
        "arxiv and task are read from run_info.json; session logs and artifacts "
        "are auto-discovered from the artifact dir.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "run_path",
        help="Run directory, workspace, iter dir, or HEPRecastData dir.",
    )
    parser.add_argument("--model", default="claude-opus-4-6", help="Judge model")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract session summary (does not call the judge)",
    )
    args = parser.parse_args()

    from ._resolve import resolve_run

    try:
        rp = resolve_run(args.run_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    agent_dir = rp.artifact_dir
    session_logs = sorted(agent_dir.glob("session_log*.txt")) or sorted(
        agent_dir.glob("session*.txt")
    )
    artifacts = [
        p
        for p in (agent_dir / f for f in ("report.md", "datasets.yaml", "results.json"))
        if p.exists()
    ]
    rp.eval_dir.mkdir(parents=True, exist_ok=True)

    if args.extract_only:
        for log_path in session_logs:
            print(f"--- {log_path.name} ---")
            print(extract_session_summary(log_path))
        return

    if not session_logs:
        print(f"ERROR: no session_log*.txt found in {agent_dir}", file=sys.stderr)
        sys.exit(1)

    scores = run_judge(
        session_logs=session_logs,
        results_dir=rp.results_dir,
        reference_file=rp.reference_file,
        artifacts=artifacts,
        model=args.model,
        output_dir=rp.eval_dir,
        rp=rp,
    )

    # Always persist the judge's output (or the error payload). run_judge
    # already writes judge_scores.json on success; on failure it returns an
    # {"error": ...} dict, which we also want on disk for post-hoc inspection.
    (rp.eval_dir / "judge_scores.json").write_text(json.dumps(scores, indent=2))

    if args.json:
        print(json.dumps(scores, indent=2))
    else:
        print_scores(scores)


if __name__ == "__main__":
    main()
