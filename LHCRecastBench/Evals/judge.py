#!/usr/bin/env python3
"""LLM-as-a-Judge evaluation of agent reasoning quality.

The numeric benchmark score is computed by score.py. This judge inspects the
agent transcript, filled results/*.yaml, report, and optional artifacts to
evaluate reasoning quality, scientific honesty, provenance, and auditability.
The published reference is supplied to the judge as hidden evaluator-only
context; it was not available to the agent during the run and the judge must
not penalize the agent for failing to compare against it.

Usage:
    python -m LHCRecastBench.evaluation.llm_judge <run_dir_or_workspace>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml


JUDGE_RUBRIC_PATH = Path(__file__).parent / "judge_rubric.md"


def _hist_yaml_files(directory: Path) -> list[Path]:
    """Return canonical histogram YAML files in a results/reference directory."""
    if not directory or not directory.exists():
        return []
    files = sorted(directory.glob("*.yaml"))
    return [p for p in files if p.name != "submission.yaml"]


def _load_yaml_docs(path: Path) -> list:
    with open(path) as f:
        return list(yaml.safe_load_all(f))


def _hist_doc_index(docs: list) -> int | None:
    for i, doc in enumerate(docs):
        if isinstance(doc, dict) and "dependent_variables" in doc:
            return i
    return None


def _dump_yaml_docs(path: Path, docs: list) -> None:
    with open(path, "w") as f:
        yaml.safe_dump_all(docs, f, default_flow_style=False, sort_keys=False)


def _find_result_file(directory: Path, filename_or_stem: str) -> Path | None:
    """Find an existing result file by exact name or .yaml stem."""
    raw = Path(filename_or_stem)
    candidates = [directory / raw.name]
    stem = raw.stem if raw.suffix else raw.name
    candidates.append(directory / f"{stem}.yaml")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _write_corrected_results(
    provenance: dict,
    original_dir: Path,
    corrected_dir: Path,
) -> None:
    """Write corrected results/*.yaml using judge provenance verification.

    Copies the original results dir, then overwrites values where the judge
    supplied corrected values for problematic or NULL_BUT_COMPUTED series.
    Current task templates are two YAML documents: metadata, then histogram.
    """
    import shutil

    original_dir = Path(original_dir)
    corrected_dir = Path(corrected_dir)

    # Start with a copy of the original
    if corrected_dir.exists():
        shutil.rmtree(corrected_dir)
    shutil.copytree(original_dir, corrected_dir)

    corrected_results = provenance.get("corrected_results") or {}
    if corrected_results:
        for filename, content in corrected_results.items():
            out_path = _find_result_file(corrected_dir, filename) or (corrected_dir / filename)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                out_path.write_text(content)
            elif isinstance(content, list):
                _dump_yaml_docs(out_path, content)
            elif isinstance(content, dict):
                _dump_yaml_docs(out_path, [content])
        return

    # Otherwise, apply per-series corrections from provenance series.
    series_info = provenance.get("series", {})
    for series_key, info in series_info.items():
        if info.get("classification") not in (
            "COPIED",
            "COPIED_OR_LEAKED",
            "FABRICATED",
            "PARTIALLY_TRACEABLE",
            "NULL_BUT_COMPUTED",
        ):
            continue
        if info.get("classification") == "PARTIALLY_TRACEABLE" and "corrected_values" not in info:
            continue
        corrected_values = info.get("corrected_values")
        # Parse series_key: "histogram_name/series" or
        # "histogram_name.yaml/series".
        parts = series_key.split("/")
        if len(parts) != 2:
            continue
        filename = parts[0]
        series_name = parts[1]

        yaml_path = _find_result_file(corrected_dir, filename)
        if yaml_path is None:
            continue

        try:
            docs = _load_yaml_docs(yaml_path)
            idx = _hist_doc_index(docs)
            if idx is None:
                continue
            hist = docs[idx]
            for dep in hist.get("dependent_variables", []):
                if dep.get("header", {}).get("name") == series_name:
                    values = dep.get("values", [])
                    if corrected_values is None:
                        corrected_values = [None] * len(values)
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

            _dump_yaml_docs(yaml_path, docs)
        except Exception:
            pass


JUDGE_PROMPT_TEMPLATE = """\
{rubric}

--- FILLED RESULTS (agent's results/*.yaml) ---
{results_yaml}

--- HIDDEN REFERENCE (published CMS values; not visible to the agent) ---
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


# ── Load result/reference YAML for context ──────────────────────────────────


def _load_results_summary(directory: Path, max_chars: int = 5000) -> str:
    """Load result YAML files as text for the judge."""
    if not directory or not directory.exists():
        return "(not available)"

    parts = []
    for yf in _hist_yaml_files(directory):
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
        rp: paths-dict from `score.resolve_paths`, used when provenance correction
            re-scores the corrected dir.
    """
    # Load session logs
    session_parts = []
    for log_path in session_logs:
        if log_path.exists():
            summary = extract_session_summary(log_path)
            session_parts.append(f"--- {log_path.name} ---\n{summary}")
    session_summary = "\n\n".join(session_parts) if session_parts else "(no session logs provided)"

    # Load agent results and hidden reference data.
    results_yaml = _load_results_summary(results_dir) if results_dir else "(not provided)"
    reference_yaml = "(not available)"
    if reference_file and reference_file.is_file():
        # _load_results_summary takes a dir; wrap via a tempdir-like single-file pass.
        import tempfile
        import shutil as _sh

        with tempfile.TemporaryDirectory() as td:
            _sh.copy2(reference_file, Path(td) / reference_file.name)
            reference_yaml = _load_results_summary(Path(td))

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
        results_yaml=results_yaml,
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

    # Save full judge output + extracted trajectory narrative
    if output_dir:
        (output_dir / "judge_scores.json").write_text(json.dumps(scores, indent=2))
    trajectory = scores.get("trajectory") or {}
    if trajectory and output_dir:
        report_path = output_dir / "judge_trajectory.md"
        lines = [f"# Judge Trajectory Narrative\n\n**Judge model**: {model}\n"]
        for key, title in [
            ("summary", "Summary"),
            ("planning", "Planning"),
            ("tool_use", "Tool Use"),
            ("scientific_judgment", "Scientific Judgment"),
            ("honesty_and_reporting", "Honesty And Reporting"),
            ("overall_assessment", "Overall Assessment"),
        ]:
            value = trajectory.get(key)
            if value:
                lines.append(f"\n## {title}\n\n{value}\n")
        for key, title in [
            ("strengths", "Strengths"),
            ("creative_moves", "Creative Moves"),
            ("stuck_points", "Stuck Points"),
            ("issues", "Issues"),
        ]:
            items = trajectory.get(key) or []
            if items:
                lines.append(f"\n## {title}\n")
                for item in items:
                    if isinstance(item, dict):
                        desc = item.get("description") or item.get("label") or json.dumps(item)
                        extra = []
                        for subkey in ("impact", "severity", "avoidable", "evidence"):
                            if item.get(subkey) is not None:
                                extra.append(f"{subkey}: {item[subkey]}")
                        suffix = f" ({'; '.join(extra)})" if extra else ""
                        lines.append(f"- {desc}{suffix}")
                    else:
                        lines.append(f"- {item}")
                lines.append("")
        report_path.write_text("\n".join(lines))
        scores["trajectory_report_path"] = str(report_path)

    # Handle provenance audit and correction.
    provenance = scores.get("provenance_audit") or {}
    overrule = provenance.get("overrule") or {}
    overrule_action = (
        str(overrule.get("action") or "NONE").upper() if isinstance(overrule, dict) else "NONE"
    )
    needs_corrected_rescore = (
        provenance.get("status") == "CORRECTED" or overrule_action == "RESCORE_CORRECTED"
    )
    if needs_corrected_rescore and output_dir and results_dir and rp is not None:
        corrected_dir = output_dir / "results_corrected_by_judge"
        _write_corrected_results(provenance, results_dir, corrected_dir)
        scores["corrected_dir"] = str(corrected_dir)

        # Re-score on corrected data using the new score_run API.
        try:
            from .score import score_run

            run_path = rp["run_dir"]
            corrected_scores = score_run(run_path, results_dir=corrected_dir)
            scores["corrected_score"] = corrected_scores
            (output_dir / "score_corrected.json").write_text(json.dumps(corrected_scores, indent=2))
            submitted_scores = score_run(run_path)
            scores["submitted_score"] = submitted_scores

            print("\n  Provenance correction applied:")
            for label, sc in [("Submitted", submitted_scores), ("Corrected", corrected_scores)]:
                sh = (sc.get("shape") or {}).get("p_value")
                no = (sc.get("normalization") or {}).get("mean_abs_frac_error_pct")
                if sh is not None or no is not None:
                    print(
                        f"    {label}: shape p={sh if sh is None else f'{sh:.3g}'}  "
                        f"norm Δ%={no if no is None else f'{no:.2f}'}  "
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

    provenance = scores.get("provenance_audit") or {}
    if provenance:
        print("\n  Provenance Audit")
        print(f"    Status: {provenance.get('status', '?')}")
        summary = provenance.get("summary")
        if summary:
            print(f"    {summary}")
        overrule = provenance.get("overrule") or {}
        if isinstance(overrule, dict) and str(overrule.get("action") or "NONE").upper() != "NONE":
            print(
                f"    Overrule: {overrule.get('action')} ({overrule.get('reason', 'no reason provided')})"
            )
        series = provenance.get("series") or {}
        if series:
            buckets: dict[str, int] = {}
            for info in series.values():
                cls = info.get("classification", "UNKNOWN") if isinstance(info, dict) else "UNKNOWN"
                buckets[cls] = buckets.get(cls, 0) + 1
            print("    " + ", ".join(f"{n} {cls}" for cls, n in sorted(buckets.items())))

    trajectory = scores.get("trajectory") or {}
    if trajectory:
        print("\n  Trajectory")
        for key in ("summary", "overall_assessment"):
            if trajectory.get(key):
                print(f"    {trajectory[key]}")
        for label, key in [
            ("Strengths", "strengths"),
            ("Creative moves", "creative_moves"),
            ("Stuck points", "stuck_points"),
            ("Issues", "issues"),
        ]:
            items = trajectory.get(key) or []
            if items:
                print(f"\n  {label}:")
                for item in items[:5]:
                    if isinstance(item, dict):
                        text = item.get("description") or item.get("label") or json.dumps(item)
                    else:
                        text = str(item)
                    print(f"    - {text}")
                if len(items) > 5:
                    print(f"    ... ({len(items) - 5} more)")
        path = scores.get("trajectory_report_path")
        if path:
            print(f"\n  Full trajectory report: {path}")
        print()
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="LLM-as-a-Judge evaluation of agent reasoning quality. "
        "task identity is read from run_info.json; session logs and artifacts "
        "are auto-discovered from the artifact dir.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "run_path",
        help="Run directory, workspace, iter dir, or results dir.",
    )
    parser.add_argument("--model", default="claude-opus-4-6", help="Judge model")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract session summary (does not call the judge)",
    )
    args = parser.parse_args()

    from .score import resolve_paths

    try:
        rp = resolve_paths(args.run_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    workspace = rp["workspace"]
    session_logs = sorted(workspace.glob("session.jsonl"))
    artifacts = [
        p
        for p in (workspace / f for f in ("report.md", "datasets.yaml", "results.json"))
        if p.exists()
    ]
    eval_dir = rp["eval_dir"]
    eval_dir.mkdir(parents=True, exist_ok=True)

    if args.extract_only:
        for log_path in session_logs:
            print(f"--- {log_path.name} ---")
            print(extract_session_summary(log_path))
        return

    if not session_logs:
        print(f"ERROR: no session.jsonl in {workspace}", file=sys.stderr)
        sys.exit(1)

    scores = run_judge(
        session_logs=session_logs,
        results_dir=rp["results_dir"],
        reference_file=rp["reference_file"],
        artifacts=artifacts,
        model=args.model,
        output_dir=eval_dir,
        rp=rp,
    )

    # Always persist the judge's output (or the error payload). run_judge
    # already writes judge_scores.json on success; on failure it returns an
    # {"error": ...} dict, which we also want on disk for post-hoc inspection.
    (eval_dir / "judge_scores.json").write_text(json.dumps(scores, indent=2))

    if args.json:
        print(json.dumps(scores, indent=2))
    else:
        print_scores(scores)


if __name__ == "__main__":
    main()
