#!/usr/bin/env python3
"""Render a human-readable summary.md from the JSONs in <run_dir>/eval/.

Reads whatever of score.json, score_corrected.json, and judge_scores.json is
present. Writes eval/summary.md. Safe to run on a partially-evaluated run;
missing sources are simply skipped.

Usage:
    python -m LHCRecastBench.evaluation.render_eval <run_dir_or_workspace>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"render_eval: could not read {path}: {exc}\n")
        return None


def _fmt_pct(x) -> str:
    try:
        return f"{float(x):.0%}"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(x, fmt: str = ".2f") -> str:
    try:
        return format(float(x), fmt)
    except (TypeError, ValueError):
        return "—"


def _fmt_int(x) -> str:
    try:
        return f"{int(x):,}"
    except (TypeError, ValueError):
        return "—"


def _provenance(judge: dict | None) -> dict:
    if not judge:
        return {}
    return judge.get("provenance_audit") or judge.get("provenance_verification") or {}


def _overrule(judge: dict | None) -> dict:
    pv = _provenance(judge)
    over = pv.get("overrule") or {}
    if isinstance(over, dict):
        return over
    return {}


def _invalidated_score(score: dict | None) -> dict | None:
    if score is None or "error" in score:
        return None
    out = dict(score)
    out["overall_shape"] = 0.0
    out["overall_normalization"] = 0.0
    out["overall_combined"] = 0.0
    out["audited_invalidated"] = True
    series = dict(out.get("series") or {})
    for key in ("shape", "normalization"):
        if isinstance(series.get(key), dict):
            block = dict(series[key])
            block["score"] = 0.0
            series[key] = block
    series["combined"] = 0.0
    series["diagnosis"] = "INVALIDATED_BY_JUDGE"
    out["series"] = series
    return out


def _audited_score(
    submitted: dict | None,
    corrected: dict | None,
    judge: dict | None,
) -> tuple[dict | None, str | None]:
    """Return the audited score and a short integrity-adjustment note."""
    if submitted is None or "error" in submitted:
        return None, None

    over = _overrule(judge)
    action = str(over.get("action") or "NONE").upper()
    reason = over.get("reason") or "judge provenance audit"
    evidence = over.get("evidence")

    if action == "RESCORE_CORRECTED":
        if corrected and "error" not in corrected:
            note = f"Audited score uses `score_corrected.json` because of `{reason}`."
            if evidence:
                note += f" Evidence: {evidence}"
            return corrected, note
        note = f"Judge requested corrected rescoring for `{reason}`, but `score_corrected.json` is unavailable."
        return submitted, note

    if action in {"INVALIDATE_SERIES", "INVALIDATE_RUN"}:
        audited = _invalidated_score(submitted)
        note = f"Judge overruled the submitted metrics with `{action}` because of `{reason}`."
        if evidence:
            note += f" Evidence: {evidence}"
        return audited, note

    # If the judge corrected values but did not emit an explicit overrule
    # action, still surface the corrected score as the audited column.
    if corrected and "error" not in corrected:
        return (
            corrected,
            "Audited score uses `score_corrected.json` from the judge provenance correction.",
        )

    # Once the LLM judge has run, show the audited column even when unchanged.
    if judge is not None:
        return submitted, None

    return None, None


# ── Section renderers ─────────────────────────────────────────────────────


def render_header(run_info: dict | None, run_dir: Path) -> list[str]:
    if run_info is None:
        return [f"# {run_dir.name}\n"]
    paper = run_info.get("paper_ref", "?")
    agent_id = run_info.get("agent_id", "?")
    agent = run_info.get("agent", "?")
    model = run_info.get("model", "?")
    started = run_info.get("started_at", "?")
    ended = run_info.get("ended_at")
    duration = run_info.get("duration_wall_s")
    final = run_info.get("final_score") or {}

    lines = [f"# {paper} — {agent_id}", ""]
    meta = f"**Agent:** {agent} · **Model:** {model} · **Started:** {started}"
    if ended:
        meta += f" · **Ended:** {ended}"
    if duration is not None:
        meta += f" · **Wall:** {int(duration) // 60} min {int(duration) % 60} s"
    lines.append(meta)
    if final:
        sh = final.get("shape")
        no = final.get("normalization")
        cb = final.get("combined")
        nf = final.get("n_filled")
        nb = final.get("n_bins")
        bits = []
        if sh is not None:
            bits.append(f"shape {_fmt_num(sh)}")
        if no is not None:
            bits.append(f"norm {_fmt_num(no)}")
        if cb is not None:
            bits.append(f"comb {_fmt_num(cb)}")
        if nf is not None and nb is not None:
            bits.append(f"filled {nf}/{nb}")
        if bits:
            lines.append("**Final:** " + " · ".join(bits))
    lines.append("")
    return lines


def render_score(
    score: dict | None,
    audited: dict | None = None,
    audit_note: str | None = None,
) -> list[str]:
    if score is None or "error" in score:
        return []
    out = ["## Score — `score.py`", ""]
    if audited is not None:
        out.append("| Metric | Submitted | Audited |")
        out.append("|---|---:|---:|")
        audited_bins = (
            "invalidated"
            if audited.get("audited_invalidated")
            else f"{_fmt_int(audited.get('n_filled'))} / {_fmt_int(audited.get('n_bins'))}"
        )
        out.append(
            f"| Bins filled | {_fmt_int(score.get('n_filled'))} / {_fmt_int(score.get('n_bins'))} "
            f"| {audited_bins} |"
        )
    else:
        out.append("| Metric | Value |")
        out.append("|---|---|")
        out.append(
            f"| Bins filled | {_fmt_int(score.get('n_filled'))} / {_fmt_int(score.get('n_bins'))} |"
        )
    if "overall_shape" in score:
        if audited is not None:
            out.append(
                f"| Shape score | {_fmt_num(score['overall_shape'])} "
                f"| {_fmt_num(audited.get('overall_shape'))} |"
            )
            out.append(
                f"| Normalization score | {_fmt_num(score['overall_normalization'])} "
                f"| {_fmt_num(audited.get('overall_normalization'))} |"
            )
            out.append(
                f"| Combined (geo mean) | {_fmt_num(score['overall_combined'])} "
                f"| {_fmt_num(audited.get('overall_combined'))} |"
            )
        else:
            out.append(f"| Shape score | {_fmt_num(score['overall_shape'])} |")
            out.append(f"| Normalization score | {_fmt_num(score['overall_normalization'])} |")
            out.append(f"| Combined (geo mean) | {_fmt_num(score['overall_combined'])} |")
    out.append("")
    if score.get("score_mode") == "shape":
        out.append(
            "_Shape-only task: normalization is reported as a diagnostic, not included in the audited objective._"
        )
        out.append("")
    elif score.get("score_mode") == "yield":
        out.append(
            "_Yield-only task: shape is reported as a diagnostic, not included in the audited objective._"
        )
        out.append("")
    if audit_note:
        out.append(f"**Integrity adjustment:** {audit_note}")
        out.append("")

    # Series-level Baker-Cousins breakdown. score.json now has a single
    # series (the task's one histogram); table-level wrapping is gone.
    s = score.get("series") or {}
    if "shape" in s:
        sh = s["shape"]
        nm = s["normalization"]
        ks = s.get("ks", {})
        out.append("<details><summary>Baker-Cousins decomposition</summary>\n")
        out.append("| Component | score | z | p | extras |")
        out.append("|---|---:|---:|---:|---|")
        out.append(
            f"| shape | {_fmt_num(sh['score'])} | {_fmt_num(sh.get('z'))} | "
            f"{_fmt_p(sh.get('p_value'))} | dof={_fmt_int(sh.get('dof'))} |"
        )
        out.append(
            f"| normalization | {_fmt_num(nm['score'])} | {_fmt_num(nm.get('z'))} | "
            f"{_fmt_p(nm.get('p_value'))} | ratio={_fmt_num(nm.get('ratio'))} |"
        )
        if ks:
            out.append(
                f"| KS | — | — | {_fmt_p(ks.get('p_value'))} | "
                f"D={_fmt_num(ks.get('statistic'))} |"
            )
        if "total" in s and isinstance(s.get("total"), dict):
            t = s["total"]
            out.append(
                f"| total | — | {_fmt_num(t.get('z'))} | {_fmt_p(t.get('p_value'))} | "
                f"λ={_fmt_num(t.get('bc_stat'))}, dof={_fmt_int(t.get('dof'))} |"
            )
        out.append(f"\n*Diagnosis:* {s.get('diagnosis', '—')}\n")
        out.append("</details>\n")
    return out


def _fmt_p(p) -> str:
    """Format a p-value: 4 significant figures for p ≥ 1e-4, else scientific."""
    if p is None:
        return "—"
    try:
        v = float(p)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "< 1e-300"
    if v >= 1e-4:
        return f"{v:.4f}"
    return f"{v:.2e}"


def render_corrected(corrected: dict | None, score: dict | None) -> list[str]:
    # Kept for callers that may import it directly. `render_summary` now folds
    # corrected scores into the main Submitted/Audited score table.
    if corrected is None or "error" in corrected:
        return []
    out = ["## Score (judge-corrected) — `score_corrected.json`", ""]
    out.append("After the judge rewrote problematic or NULL_BUT_COMPUTED series:")
    out.append("")
    out.append("| | Submitted | Corrected |")
    out.append("|---|---:|---:|")
    if "overall_shape" in corrected:
        out.append(
            f"| Shape | {_fmt_num(score.get('overall_shape') if score else None)} "
            f"| {_fmt_num(corrected['overall_shape'])} |"
        )
        out.append(
            f"| Normalization | {_fmt_num(score.get('overall_normalization') if score else None)} "
            f"| {_fmt_num(corrected['overall_normalization'])} |"
        )
        out.append(
            f"| Combined | {_fmt_num(score.get('overall_combined') if score else None)} "
            f"| {_fmt_num(corrected['overall_combined'])} |"
        )
    out.append("")
    return out


def render_run_meta(run_info: dict | None) -> list[str]:
    """Cost / wall-time / token usage block, sourced from run_info.json."""
    if not run_info:
        return []
    usage = run_info.get("usage") or {}
    wall_s = run_info.get("duration_wall_s")
    if not usage and wall_s is None:
        return []

    parts = []
    if usage.get("api_cost_usd") is not None:
        parts.append(f"**Cost:** ${_fmt_num(usage['api_cost_usd'])}")
    if wall_s is not None:
        parts.append(f"**Wall:** {int(wall_s) // 60} min {int(wall_s) % 60} s")
    if usage.get("n_turns") is not None:
        parts.append(f"**Turns:** {usage['n_turns']}")
    if usage.get("tokens_total_billed") is not None:
        parts.append(f"**Billed tokens:** {_fmt_int(usage['tokens_total_billed'])}")
    if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None:
        parts.append(
            f"(in {_fmt_int(usage['input_tokens'])} / out {_fmt_int(usage['output_tokens'])})"
        )
    return ["## Run", "", " · ".join(parts), ""] if parts else []


def render_judge(judge: dict | None) -> list[str]:
    if judge is None:
        return []
    out = ["## LLM judge — `judge_scores.json`", ""]

    # New schema: provenance audit + trajectory narrative.
    pv = judge.get("provenance_audit") or judge.get("provenance_verification") or {}
    if pv:
        out.append("**Provenance audit:**")
        out.append("")
        if pv.get("status"):
            out.append(f"- Status: `{pv['status']}`")
        if pv.get("summary"):
            out.append(f"- Summary: {pv['summary']}")
        over = pv.get("overrule") or {}
        if isinstance(over, dict) and str(over.get("action") or "NONE").upper() != "NONE":
            out.append(
                f"- Overrule: `{over.get('action')}`"
                + (f" — {over.get('reason')}" if over.get("reason") else "")
            )
        series = pv.get("series") or {}
        if series:
            buckets: dict[str, list[str]] = {}
            for sname, info in series.items():
                cls = info.get("classification", "UNKNOWN")
                buckets.setdefault(cls, []).append(sname)
            out.append(
                "- Series: "
                + ", ".join(f"{len(lst)} `{cls}`" for cls, lst in sorted(buckets.items()))
            )
            out.append("")
            out.append("<details><summary>Per-series provenance</summary>\n")
            out.append("| Series | Classification | Confidence |")
            out.append("|---|---|---|")
            for cls, lst in sorted(buckets.items()):
                for sname in sorted(lst):
                    info = series.get(sname) or {}
                    conf = info.get("confidence", "—")
                    out.append(f"| `{sname}` | {cls} | {conf} |")
            out.append("\n</details>\n")
        out.append("")

    traj = judge.get("trajectory") or {}
    if traj:
        out.append("**Trajectory:**")
        out.append("")
        for key in ("summary", "overall_assessment"):
            if traj.get(key):
                out.append(traj[key])
                out.append("")
        for label, key in (
            ("Strengths", "strengths"),
            ("Creative moves", "creative_moves"),
            ("Stuck points", "stuck_points"),
            ("Issues", "issues"),
        ):
            items = traj.get(key) or []
            if items:
                out.append(f"**{label}:**")
                for item in items:
                    if isinstance(item, dict):
                        desc = item.get("description") or item.get("label") or json.dumps(item)
                        meta = []
                        for subkey in ("impact", "severity", "avoidable"):
                            if item.get(subkey) is not None:
                                meta.append(f"{subkey}: {item[subkey]}")
                        suffix = f" ({'; '.join(meta)})" if meta else ""
                        out.append(f"- {desc}{suffix}")
                    else:
                        out.append(f"- {item}")
                out.append("")
        report_path = judge.get("trajectory_report_path")
        if report_path:
            rel = Path(report_path).name
            out.append(f"See [`{rel}`]({rel}) for the trajectory narrative.")
            out.append("")
        return out

    # Legacy schema: six dimensions + failure report.
    dims = [
        ("Diagnosis quality", "diagnosis_quality"),
        ("Creative problem-solving", "creative_problem_solving"),
        ("Scientific honesty", "scientific_honesty"),
        ("Hallucination (low = good)", "hallucination"),
        ("Tool use efficiency", "tool_use_efficiency"),
        ("Artifact completeness", "artifact_completeness"),
    ]
    out.append("| Dimension | Score |")
    out.append("|---|---:|")
    for label, key in dims:
        v = (judge.get(key) or {}).get("score")
        out.append(f"| {label} | {v if v is not None else '—'} / 5 |")
    if judge.get("overall_reasoning_score") is not None:
        out.append(f"| **Overall reasoning** | **{judge['overall_reasoning_score']} / 5** |")
    out.append("")

    # Strengths / weaknesses
    for label, key in (
        ("Strengths", "key_strengths"),
        ("Weaknesses", "key_weaknesses"),
        ("Missed opportunities", "missed_opportunities"),
    ):
        items = judge.get(key) or []
        if items:
            out.append(f"**{label}:**")
            for it in items:
                out.append(f"- {it}")
            out.append("")

    # Legacy provenance
    pv = judge.get("provenance_verification") or {}
    series = pv.get("series") or {}
    if series:
        buckets: dict[str, list[str]] = {}
        for sname, info in series.items():
            cls = info.get("classification", "UNKNOWN")
            buckets.setdefault(cls, []).append(sname)
        out.append(
            "**Provenance:** "
            + ", ".join(f"{len(lst)} {cls}" for cls, lst in sorted(buckets.items()))
        )
        out.append("")
        out.append("<details><summary>Per-series classifications</summary>\n")
        out.append("| Series | Classification |")
        out.append("|---|---|")
        for cls, lst in sorted(buckets.items()):
            for sname in sorted(lst):
                out.append(f"| `{sname}` | {cls} |")
        out.append("\n</details>\n")

    failure_path = judge.get("failure_report_path")
    if failure_path:
        # Show as relative link when report lives next to the JSON.
        rel = Path(failure_path).name
        out.append(f"See [`{rel}`]({rel}) for the narrative failure report.")
        out.append("")
    return out


# ── Driver ──────────────────────────────────────────────────────────────────


def render_summary(run_dir: Path) -> Path:
    """Write <run_dir>/eval/summary.md from whatever JSONs exist there."""
    eval_dir = run_dir / "eval"
    if not eval_dir.is_dir():
        raise FileNotFoundError(f"no eval/ under {run_dir}")

    run_info = _load(run_dir / "run_info.json")
    score = _load(eval_dir / "score.json")
    corrected = _load(eval_dir / "score_corrected.json")
    judge = _load(eval_dir / "judge_scores.json")
    audited, audit_note = _audited_score(score, corrected, judge)

    lines: list[str] = []
    lines += render_header(run_info, run_dir)
    lines += render_score(score, audited, audit_note)
    lines += render_run_meta(run_info)
    lines += render_judge(judge)

    if (eval_dir / "plots").is_dir():
        lines += [
            "## Plots",
            "",
            "CMS/recast histograms (yield + shape) in [`plots/`](plots/).",
            "",
        ]

    out = eval_dir / "summary.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a summary.md from the JSONs under <run_dir>/eval/."
    )
    parser.add_argument(
        "run_path",
        help="Run directory, workspace, iter dir, or results dir.",
    )
    args = parser.parse_args()

    from ._resolve import resolve_run

    try:
        rp = resolve_run(args.run_path)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        sys.exit(f"render_eval: {exc}")

    # render_summary consumes "the dir that contains eval/". For single-shot
    # that's rp.run_dir; for per-iter it's the iter dir itself (rp.eval_dir.parent).
    out = render_summary(rp.eval_dir.parent)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
