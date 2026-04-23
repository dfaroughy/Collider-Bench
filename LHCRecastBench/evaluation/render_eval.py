#!/usr/bin/env python3
"""Render a human-readable summary.md from the JSONs in <run_dir>/eval/.

Reads whatever of score.json, rubric_scorer.json, judge_scores.json,
score_corrected.json is present. Writes eval/summary.md. Safe to run on a
partially-evaluated run; missing sources are simply skipped.

Usage:
    python -m LHCRecastBench.evaluation.render_eval <run_dir_or_workspace>

Example:
    python -m LHCRecastBench.evaluation.render_eval \\
        recast_CMS-SUS-16-047_simple_claude-opus-4-7_ArcaneLagrange_51190fe0
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
        pass_s = "PASS" if final.get("overall_pass") else "FAIL"
        ovr = final.get("overall_score")
        n_pass = final.get("n_pass")
        n_filled = final.get("n_filled")
        lines.append(
            f"**Final:** {_fmt_pct(ovr)} ({_fmt_int(n_pass)}/{_fmt_int(n_filled)} bins) — {pass_s}"
        )
    lines.append("")
    return lines


def render_score(score: dict | None) -> list[str]:
    if score is None or "error" in score:
        return []
    out = ["## Accuracy — `score.py`", ""]
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(
        f"| Bins passing | {_fmt_int(score.get('n_pass'))} / {_fmt_int(score.get('n_filled'))} ({_fmt_pct(score.get('overall_score'))}) — {'**PASS**' if score.get('overall_pass') else '**FAIL**'} |"
    )
    if "overall_shape" in score:
        out.append(f"| Shape score | {_fmt_num(score['overall_shape'])} |")
        out.append(f"| Normalization score | {_fmt_num(score['overall_normalization'])} |")
        out.append(f"| Combined (geo mean) | {_fmt_num(score['overall_combined'])} |")
    out.append("")

    # Per-series Baker-Cousins breakdown. The "score" columns are the bounded
    # rubric scores (exp(−z/5)); p-values and z are reported alongside for
    # the statistical reading.
    rows: list[tuple[str, dict]] = []
    for t in score.get("tables", []):
        if "error" in t:
            continue
        for s in t.get("series", []):
            if "error" in s or "shape" not in s:
                continue
            rows.append((f"{t['table']}/{s['name']}", s))
    if rows:
        out.append("<details><summary>Per-series breakdown (Baker-Cousins)</summary>\n")
        out.append(
            "| Series | Bins pass | Shape score (z, p) | Norm score (z, p, ratio) | KS (p) | Combined | Diagnosis |"
        )
        out.append("|---|---|---:|---:|---:|---:|---|")
        for name, s in rows:
            bins = f"{_fmt_int(s['n_pass'])}/{_fmt_int(s['n_filled'])} ({_fmt_pct(s.get('score'))})"
            sh = s["shape"]
            nm = s["normalization"]
            ks = s.get("ks", {})
            shape_cell = (
                f"{_fmt_num(sh['score'])} "
                f"(z={_fmt_num(sh.get('z'))}, p={_fmt_p(sh.get('p_value'))})"
            )
            norm_cell = (
                f"{_fmt_num(nm['score'])} "
                f"(z={_fmt_num(nm.get('z'))}, p={_fmt_p(nm.get('p_value'))}, "
                f"ratio={_fmt_num(nm.get('ratio'))})"
            )
            ks_cell = _fmt_p(ks.get("p_value")) if ks else "—"
            out.append(
                f"| `{name}` | {bins} | {shape_cell} | {norm_cell} | {ks_cell} | "
                f"{_fmt_num(s['combined'])} | {s.get('diagnosis', '—')} |"
            )
        out.append("\n</details>\n")

        # Full-table goodness-of-fit, surfaced as its own block.
        total_rows = [
            (n, s["total"]) for n, s in rows if "total" in s and isinstance(s.get("total"), dict)
        ]
        if total_rows:
            out.append(
                "<details><summary>Full goodness-of-fit (λ_total = λ_shape + λ_norm)</summary>\n"
            )
            out.append("| Series | λ_total | dof | z | p |")
            out.append("|---|---:|---:|---:|---:|")
            for n, t in total_rows:
                out.append(
                    f"| `{n}` | {_fmt_num(t.get('bc_stat'))} | {_fmt_int(t.get('dof'))} | "
                    f"{_fmt_num(t.get('z'))} | {_fmt_p(t.get('p_value'))} |"
                )
            out.append("\n</details>\n")
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
    out = ["## Accuracy (judge-corrected) — `score_corrected.json`", ""]
    sub = score.get("overall_score") if score else None
    cor = corrected.get("overall_score")
    out.append("After the judge rewrote COPIED / NULL_BUT_COMPUTED series:")
    out.append("")
    out.append("| | Submitted | Corrected |")
    out.append("|---|---:|---:|")
    out.append(f"| Bins passing | {_fmt_pct(sub)} | {_fmt_pct(cor)} |")
    if "overall_shape" in corrected:
        out.append(
            f"| Shape | {_fmt_num(score.get('overall_shape') if score else None)} | {_fmt_num(corrected['overall_shape'])} |"
        )
        out.append(
            f"| Normalization | {_fmt_num(score.get('overall_normalization') if score else None)} | {_fmt_num(corrected['overall_normalization'])} |"
        )
    out.append("")
    return out


def render_rubric(rubric: dict | None, run_info: dict | None) -> list[str]:
    if rubric is None:
        return []
    r = rubric.get("rubric", {}) or {}
    checkpoints = r.get("checkpoints", []) or []
    total = r.get("rubric_score")

    out = ["## Rubric — `rubric_scorer.py`", ""]
    if checkpoints:
        out.append("| Checkpoint | Weight | Score | Detail |")
        out.append("|---|---:|---:|---|")
        for c in checkpoints:
            out.append(
                f"| {c.get('name', '?')} | "
                f"{_fmt_pct(c.get('weight'))} | "
                f"{_fmt_num(c.get('score'))} | "
                f"{c.get('detail', '—')} |"
            )
        if total is not None:
            out.append(f"| **Total (weighted)** | | **{_fmt_num(total)}** | |")
        out.append("")

    cost = rubric.get("cost") or {}
    tokens = rubric.get("tokens") or {}
    eff = rubric.get("efficiency") or {}
    usage = (run_info or {}).get("usage") or {}

    # Prefer run_info.json["usage"] (finalized) over rubric cost (heuristic).
    usd = usage.get("api_cost_usd") if usage else cost.get("usd")
    wall_s = (run_info or {}).get("duration_wall_s") or cost.get("duration_wall_s")
    tok_in = usage.get("input_tokens") if usage else tokens.get("input")
    tok_out = usage.get("output_tokens") if usage else tokens.get("output")
    tok_bil = usage.get("tokens_total_billed") if usage else tokens.get("total_billed")
    n_turns = usage.get("n_turns")

    has_any = any(v is not None for v in (usd, wall_s, tok_in, tok_out, tok_bil, n_turns))
    if has_any or eff:
        parts = []
        if usd is not None:
            parts.append(f"**Cost:** ${_fmt_num(usd)}")
        if wall_s is not None:
            parts.append(f"**Wall:** {int(wall_s) // 60} min {int(wall_s) % 60} s")
        if n_turns is not None:
            parts.append(f"**Turns:** {n_turns}")
        if tok_bil is not None:
            parts.append(f"**Billed tokens:** {_fmt_int(tok_bil)}")
        if tok_in is not None and tok_out is not None:
            parts.append(f"(in {_fmt_int(tok_in)} / out {_fmt_int(tok_out)})")
        if eff.get("tokens_per_point") is not None:
            parts.append(f"**Tokens/pt:** {_fmt_int(eff['tokens_per_point'])}")
        if eff.get("tools_per_turn") is not None:
            parts.append(f"**Tools/turn:** {_fmt_num(eff['tools_per_turn'])}")
        if eff.get("error_rate") is not None:
            parts.append(f"**Error rate:** {_fmt_pct(eff['error_rate'])}")
        out.append(" · ".join(parts))
        out.append("")
    return out


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
    rubric = _load(eval_dir / "rubric_scorer.json")
    judge = _load(eval_dir / "judge_scores.json")
    trajectory = _load(eval_dir / "trajectory_judge.json")

    lines: list[str] = []
    lines += render_header(run_info, run_dir)
    lines += render_score(score)
    lines += render_corrected(corrected, score)
    lines += render_rubric(rubric, run_info)
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
