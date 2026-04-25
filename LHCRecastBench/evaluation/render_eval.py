#!/usr/bin/env python3
"""Render a human-readable summary.md from the JSONs in <run_dir>/eval/.

Reads whatever of score.json, score_corrected.json, judge_scores.json,
trajectory_judge.json is present. Writes eval/summary.md. Safe to run on
a partially-evaluated run; missing sources are simply skipped.

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


def render_score(score: dict | None) -> list[str]:
    if score is None or "error" in score:
        return []
    out = ["## Score — `score.py`", ""]
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(
        f"| Bins filled | {_fmt_int(score.get('n_filled'))} / {_fmt_int(score.get('n_bins'))} |"
    )
    if "overall_shape" in score:
        out.append(f"| Shape score | {_fmt_num(score['overall_shape'])} |")
        out.append(f"| Normalization score | {_fmt_num(score['overall_normalization'])} |")
        out.append(f"| Combined (geo mean) | {_fmt_num(score['overall_combined'])} |")
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
    if corrected is None or "error" in corrected:
        return []
    out = ["## Score (judge-corrected) — `score_corrected.json`", ""]
    out.append("After the judge rewrote COPIED / NULL_BUT_COMPUTED series:")
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

    # Provenance
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


def render_trajectory(traj: dict | None) -> list[str]:
    """Render the trajectory-judge failure-mode breakdown (TAT).

    Sourced from trajectory_judge.json. See TAXONOMY.md for rubrics.
    """
    if traj is None:
        return []
    if "error" in traj:
        return [
            "## Trajectory judge — `trajectory_judge.json`",
            "",
            f"_Judge errored: {traj['error']}_",
            "",
        ]
    out = ["## Trajectory failure modes — `trajectory_judge.json`", ""]
    out.append(
        f"_{traj.get('n_matched', 0)}/9 modes matched · judge: "
        f"{traj.get('judge_model', '?')} · {traj.get('runtime_s', '?')}s_"
    )
    out.append("")
    out.append("| Class | Modes matched | Which |")
    out.append("|---|---:|---|")
    for cls in ("execution", "coherence", "verification"):
        block = (traj.get("classes") or {}).get(cls) or {}
        matched = [m for m, v in (block.get("modes") or {}).items() if v.get("matched")]
        out.append(
            f"| {cls.capitalize()} | {block.get('n_matched', 0)} | "
            f"{' '.join(f'`{m}`' for m in matched) or '—'} |"
        )
    out.append("")

    # Evidence details for matched modes.
    matched_modes = [(m, v) for m, v in (traj.get("modes") or {}).items() if v.get("matched")]
    if matched_modes:
        out.append("<details><summary>Evidence per matched mode</summary>\n")
        for mode, v in matched_modes:
            out.append(f"**`{mode}`** ({v.get('class', '?')})")
            for ev in v.get("evidence", []):
                out.append(f"- {ev}")
            out.append("")
        out.append("</details>\n")
    return out


def render_summary(run_dir: Path) -> Path:
    """Write <run_dir>/eval/summary.md from whatever JSONs exist there."""
    eval_dir = run_dir / "eval"
    if not eval_dir.is_dir():
        raise FileNotFoundError(f"no eval/ under {run_dir}")

    run_info = _load(run_dir / "run_info.json")
    score = _load(eval_dir / "score.json")
    corrected = _load(eval_dir / "score_corrected.json")
    judge = _load(eval_dir / "judge_scores.json")
    trajectory = _load(eval_dir / "trajectory_judge.json")

    lines: list[str] = []
    lines += render_header(run_info, run_dir)
    lines += render_score(score)
    lines += render_corrected(corrected, score)
    lines += render_run_meta(run_info)
    lines += render_judge(judge)
    lines += render_trajectory(trajectory)

    if (eval_dir / "plots").is_dir():
        lines += [
            "## Plots",
            "",
            "Step histograms (CMS vs recast, yield + shape) in [`plots/`](plots/).",
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
        help="Run directory, workspace, iter dir, or HEPRecastData dir.",
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
