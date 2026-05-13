#!/usr/bin/env python3
"""Build + push the Collider-Bench task corpus to HuggingFace Datasets.

One row per sim task. Includes the agent-facing TASK.md, the null-filled
template, the task.toml metadata, the paper PDF, and the CMS object-efficiency
ROOT files. Excludes the hidden reference values (would leak the benchmark
answers into any LLM trained on HF data).

Usage:
    # Dry run — build the dataset in memory + print schema preview, do NOT push.
    scripts/build_hf_dataset.py --dry-run

    # Push to your HF dataset (defaults to Dariusfar/ColliderBench).
    huggingface-cli login                     # once per host
    scripts/build_hf_dataset.py --push

    # Push to a different repo, or as private:
    scripts/build_hf_dataset.py --push --repo Dariusfar/ColliderBench-preview --private

    # Upload the dataset_card.md / CITATION.cff alongside (after the dataset push):
    scripts/build_hf_dataset.py --push --upload-card

Requires:  pip install -e ".[hub]"   (datasets + huggingface_hub)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path

import yaml


# ── Static metadata per CMS analysis ───────────────────────────────────────
# Final state (analysis_target) isn't carried in task.toml today, and the
# signal-model "pretty" string for the table is also derived. Map it here
# from task_id so the row metadata matches the README task table 1:1.
# Keys are the task_id; values are dicts that get spread into the row.
_TASK_META: dict[str, dict[str, str]] = {
    "sus-16-034_sim-TChiWZ": {
        "analysis_target": "leptons + jets",
        "signal_model": "TChiWZ",
        "observable_pretty": "E_T^miss",
    },
    "sus-16-046_sim-T5Wg": {
        "analysis_target": "photons",
        "signal_model": "T5Wg",
        "observable_pretty": "S_T^gamma",
    },
    "sus-16-046_sim-TChiWg": {
        "analysis_target": "photons",
        "signal_model": "TChiWg",
        "observable_pretty": "S_T^gamma",
    },
    "sus-16-047_sim-T5Wg_highHT": {
        "analysis_target": "photons",
        "signal_model": "T5Wg, high-H_T",
        "observable_pretty": "p_T^miss",
    },
    "sus-16-047_sim-T5Wg_lowHT": {
        "analysis_target": "photons",
        "signal_model": "T5Wg, low-H_T",
        "observable_pretty": "p_T^miss",
    },
    "sus-16-047_sim-T6gg_highHT": {
        "analysis_target": "photons",
        "signal_model": "T6gg, high-H_T",
        "observable_pretty": "p_T^miss",
    },
    "sus-16-047_sim-T6gg_lowHT": {
        "analysis_target": "photons",
        "signal_model": "T6gg, low-H_T",
        "observable_pretty": "p_T^miss",
    },
    "sus-16-051_sim-T2tt_SRG": {
        "analysis_target": "single lepton",
        "signal_model": "T2tt",
        "observable_pretty": "E_T^miss",
    },
    "sus-16-051_sim-T2bW_SRG": {
        "analysis_target": "single lepton",
        "signal_model": "T2bW",
        "observable_pretty": "E_T^miss",
    },
    "sus-16-051_sim-T2tt_comp": {
        "analysis_target": "single lepton",
        "signal_model": "T2tt, compressed",
        "observable_pretty": "E_T^miss",
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _count_bins(template_yaml_text: str) -> int:
    """Sum of len(values) across all dependent_variables in the histogram doc."""
    try:
        docs = list(yaml.safe_load_all(template_yaml_text))
    except yaml.YAMLError:
        return 0
    for d in docs:
        if isinstance(d, dict) and "dependent_variables" in d:
            return sum(len((dv or {}).get("values") or []) for dv in d["dependent_variables"])
    return 0


def _load_task_row(task_dir: Path, repo_root: Path) -> dict:
    """Build one HF dataset row from a single task directory."""
    task_id = task_dir.name
    if task_id not in _TASK_META:
        raise SystemExit(f"task_id {task_id!r} not in _TASK_META — add an entry there first")

    toml_text = (task_dir / "task.toml").read_text()
    toml_data = tomllib.loads(toml_text)
    task_md_text = (task_dir / "TASK.md").read_text()

    template_files = sorted((task_dir / "template").glob("*.yaml"))
    if len(template_files) != 1:
        raise SystemExit(f"{task_id}: expected 1 template/*.yaml, got {len(template_files)}")
    template_text = template_files[0].read_text()

    paper_id = (toml_data.get("task") or {}).get("paper")
    if not paper_id:
        raise SystemExit(f"{task_id}: [task].paper missing in task.toml")

    paper_pdf = (
        repo_root / "ColliderBench" / "tasks" / "shared" / paper_id / "paper" / f"{paper_id}.pdf"
    )
    if not paper_pdf.is_file():
        raise SystemExit(f"{task_id}: paper PDF missing at {paper_pdf}")
    pdf_bytes = paper_pdf.read_bytes()

    eff_dir = repo_root / "ColliderBench" / "tasks" / "shared" / paper_id / "object_efficiencies"
    object_efficiencies = []
    if eff_dir.is_dir():
        for f in sorted(eff_dir.iterdir()):
            if not f.is_file():
                continue
            data = f.read_bytes()
            object_efficiencies.append(
                {
                    "filename": f.name,
                    "data": data,
                    "sha256": _sha256(data),
                    "size_bytes": len(data),
                }
            )

    metrics = toml_data.get("metrics") or {}
    metadata = toml_data.get("metadata") or {}
    extra = _TASK_META[task_id]

    return {
        "task_id": task_id,
        "paper_id": paper_id,
        "analysis_target": extra["analysis_target"],
        "signal_model": extra["signal_model"],
        "observable": (toml_data.get("task") or {}).get("observable") or "",
        "observable_pretty": extra["observable_pretty"],
        "plot_units": metrics.get("plot") or "",
        "score_mode": metrics.get("mode") or "",
        "tolerance": float(metrics.get("tolerance") or 0.0),
        "walltime": metadata.get("walltime") or "",
        "difficulty": metadata.get("difficulty") or "",
        "tags": [str(t) for t in (metadata.get("tags") or [])],
        "instructions_md": task_md_text,
        "task_toml": toml_text,
        "template_yaml": template_text,
        "n_bins": _count_bins(template_text),
        "paper_pdf": pdf_bytes,
        "paper_pdf_sha256": _sha256(pdf_bytes),
        "paper_pdf_bytes": len(pdf_bytes),
        "object_efficiencies": object_efficiencies,
    }


def _build_rows(repo_root: Path) -> list[dict]:
    tasks_root = repo_root / "ColliderBench" / "tasks"
    task_dirs = sorted(
        p for p in tasks_root.iterdir() if p.is_dir() and "_sim-" in p.name and p.name in _TASK_META
    )
    if not task_dirs:
        raise SystemExit(f"no sim tasks found under {tasks_root}")
    if len(task_dirs) != len(_TASK_META):
        missing = set(_TASK_META) - {p.name for p in task_dirs}
        if missing:
            print(
                f"  WARNING: tasks listed in _TASK_META but not on disk: {sorted(missing)}",
                file=sys.stderr,
            )
    return [_load_task_row(t, repo_root) for t in task_dirs]


def _print_preview(rows: list[dict]) -> None:
    print(f"  built {len(rows)} rows")
    for r in rows:
        eff_count = len(r["object_efficiencies"])
        eff_bytes = sum(e["size_bytes"] for e in r["object_efficiencies"])
        print(
            f"    {r['task_id']:<32}  paper={r['paper_id']:<18}  "
            f"bins={r['n_bins']:>3}  "
            f"pdf={r['paper_pdf_bytes']/1e6:>5.2f} MB  "
            f"eff_files={eff_count}  "
            f"eff_size={eff_bytes/1e6:>5.2f} MB"
        )
    total_pdf = sum(r["paper_pdf_bytes"] for r in rows)
    total_eff = sum(e["size_bytes"] for r in rows for e in r["object_efficiencies"])
    print(f"  total pdf+eff payload: {(total_pdf + total_eff) / 1e6:.1f} MB")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", default="Dariusfar/ColliderBench", help="HF dataset repo (user/name)")
    ap.add_argument(
        "--push", action="store_true", help="Upload to HF (default: dry run, no network)"
    )
    ap.add_argument("--private", action="store_true", help="Mark the HF dataset as private on push")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + preview only (also implied if neither --push nor --upload-card)",
    )
    ap.add_argument(
        "--upload-card",
        action="store_true",
        help="Also upload hub/dataset_card.md (as README.md) and hub/CITATION.cff",
    )
    ap.add_argument(
        "--include-references",
        action="store_true",
        help="DANGER: include hidden CMS reference values in the dataset rows. "
        "Doing this leaks the benchmark answers into any LLM trained on HF data. "
        "Do not use unless you understand and accept that the benchmark is no longer blind.",
    )
    args = ap.parse_args()

    if args.include_references:
        print(
            "  ⚠️  --include-references is ON. The dataset will contain the published\n"
            "    CMS reference yields. Once uploaded publicly, this leaks the\n"
            "    answer key to every future LLM that ingests HF datasets, and the\n"
            "    benchmark loses its blind-test property. Aborting unless you\n"
            "    re-confirm with --i-really-understand.",
            file=sys.stderr,
        )
        # Explicit second flag so it can't be enabled by accident.
        if "--i-really-understand" not in sys.argv:
            raise SystemExit(2)

    rows = _build_rows(repo_root)
    _print_preview(rows)

    if not (args.push or args.upload_card):
        print("\n  --dry-run (default): no upload. Use --push to upload to HF.")
        return

    # Lazy import so the dry-run path has zero extra deps.
    try:
        from datasets import Dataset, Features, Sequence, Value
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit(
            'install hub extras first:  pip install -e ".[hub]"\n'
            "(needs `datasets` + `huggingface_hub`)"
        ) from exc

    features = Features(
        {
            "task_id": Value("string"),
            "paper_id": Value("string"),
            "analysis_target": Value("string"),
            "signal_model": Value("string"),
            "observable": Value("string"),
            "observable_pretty": Value("string"),
            "plot_units": Value("string"),
            "score_mode": Value("string"),
            "tolerance": Value("float64"),
            "walltime": Value("string"),
            "difficulty": Value("string"),
            "tags": Sequence(Value("string")),
            "instructions_md": Value("string"),
            "task_toml": Value("string"),
            "template_yaml": Value("string"),
            "n_bins": Value("int64"),
            "paper_pdf": Value("binary"),
            "paper_pdf_sha256": Value("string"),
            "paper_pdf_bytes": Value("int64"),
            # NB: `[{...}]` here gives a list-of-structs column (one record per
            # efficiency file). `Sequence({...})` would transpose to a struct of
            # parallel arrays, which doesn't match our row shape and explodes
            # with "AttributeError: 'list' object has no attribute 'get'".
            "object_efficiencies": [
                {
                    "filename": Value("string"),
                    "data": Value("binary"),
                    "sha256": Value("string"),
                    "size_bytes": Value("int64"),
                }
            ],
        }
    )
    ds = Dataset.from_list(rows, features=features)
    print(f"\n  built {len(ds)}-row Dataset; features=\n{ds.features}")

    if args.push:
        print(
            f"\n  pushing to https://huggingface.co/datasets/{args.repo} (private={args.private})…"
        )
        ds.push_to_hub(args.repo, private=args.private)
        print("  pushed.")

    if args.upload_card:
        api = HfApi()
        card_md = repo_root / "hub" / "dataset_card.md"
        cite_cff = repo_root / "hub" / "CITATION.cff"
        if card_md.is_file():
            api.upload_file(
                path_or_fileobj=str(card_md),
                path_in_repo="README.md",
                repo_id=args.repo,
                repo_type="dataset",
                commit_message="update dataset card",
            )
            print(f"  uploaded {card_md.name} → {args.repo}/README.md")
        if cite_cff.is_file():
            api.upload_file(
                path_or_fileobj=str(cite_cff),
                path_in_repo="CITATION.cff",
                repo_id=args.repo,
                repo_type="dataset",
                commit_message="update citation file",
            )
            print(f"  uploaded {cite_cff.name} → {args.repo}/CITATION.cff")


if __name__ == "__main__":
    main()
