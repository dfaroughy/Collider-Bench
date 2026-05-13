#!/usr/bin/env python3
"""Build + push the Collider-Bench task corpus to HuggingFace Datasets.

One row per sim task. Includes the agent-facing TASK.md, the null-filled
template, the task.toml metadata, the paper PDF, and the CMS object-efficiency
ROOT files. Excludes the hidden reference values (would leak the benchmark
answers into any LLM trained on HF data).

Usage:
    # Dry run — build the dataset in memory + print schema preview, do NOT push.
    hub/build_hf_dataset.py --dry-run

    # Push to your HF dataset (defaults to Dariusfar/ColliderBench).
    huggingface-cli login                     # once per host
    hub/build_hf_dataset.py --push

    # Push to a different repo, or as private:
    hub/build_hf_dataset.py --push --repo Dariusfar/ColliderBench-preview --private

    # Upload the dataset_card.md / CITATION.cff alongside (after the dataset push):
    hub/build_hf_dataset.py --push --upload-card

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


def _import_build_prompt(repo_root: Path):
    """Import agents.simple.run.build_prompt without forcing the repo on sys.path
    at module-level (keeps the dry-run dependency surface tiny)."""
    import sys

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from agents.simple.run import build_prompt

    return build_prompt


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

    # The initial system prompt the harness's `simple` agent sends. Rendered
    # per-task (currently `build_prompt` doesn't actually template paper_id
    # in, but we pass it so the column reflects what the harness would emit
    # if/when that changes).
    build_prompt = _import_build_prompt(repo_root)
    initial_prompt = build_prompt(paper_id)

    return {
        "task_id": task_id,
        "paper_id": paper_id,
        "task_type": (toml_data.get("task") or {}).get("type") or "",
        "analysis_target": extra["analysis_target"],
        "signal_model": extra["signal_model"],
        "observable": (toml_data.get("task") or {}).get("observable") or "",
        "observable_pretty": extra["observable_pretty"],
        "plot_units": metrics.get("plot") or "",
        "score_mode": metrics.get("mode") or "",
        "walltime": metadata.get("walltime") or "",
        "difficulty": metadata.get("difficulty") or "",
        "tags": [str(t) for t in (metadata.get("tags") or [])],
        "initial_prompt": initial_prompt,
        "task_md": task_md_text,
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
            "task_type": Value("string"),
            "analysis_target": Value("string"),
            "signal_model": Value("string"),
            "observable": Value("string"),
            "observable_pretty": Value("string"),
            "plot_units": Value("string"),
            "score_mode": Value("string"),
            "walltime": Value("string"),
            "difficulty": Value("string"),
            "tags": Sequence(Value("string")),
            "initial_prompt": Value("string"),
            "task_md": Value("string"),
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
            merged = _merge_card_with_remote_dataset_info(card_md, args.repo, api, ds=ds)
            api.upload_file(
                path_or_fileobj=merged.encode("utf-8"),
                path_in_repo="README.md",
                repo_id=args.repo,
                repo_type="dataset",
                commit_message="update dataset card",
            )
            print(f"  uploaded {card_md.name} → {args.repo}/README.md (dataset_info merged in)")
        if cite_cff.is_file():
            api.upload_file(
                path_or_fileobj=str(cite_cff),
                path_in_repo="CITATION.cff",
                repo_id=args.repo,
                repo_type="dataset",
                commit_message="update citation file",
            )
            print(f"  uploaded {cite_cff.name} → {args.repo}/CITATION.cff")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (parsed front-matter dict, body string) from a Markdown doc.

    A doc with no `---\\n…\\n---` opener returns ({}, text).
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    front = text[4:end]
    body = text[end + 5 :]
    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError:
        data = {}
    return data, body


def _join_frontmatter(front: dict, body: str) -> str:
    """Render a Markdown doc with a YAML front-matter header."""
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm}\n---\n\n{body.lstrip()}"


def _merge_card_with_remote_dataset_info(local_card_path: Path, repo: str, api, *, ds) -> str:
    """Compose a README that satisfies the HF Dataset Viewer.

    Layout of a viewer-compatible README:
      - YAML front matter MUST carry a `dataset_info` block whose
        `features` list matches the Parquet schema exactly. If it
        doesn't, the viewer fails with `CastError: column names don't
        match`.
      - The body is freely user-authored.

    Strategy: build `dataset_info.features` from the *local* Dataset object
    via `ds.features._to_yaml_list()` (canonical source of truth — survives
    any schema drift). For the other dataset_info sub-keys (splits,
    download_size, dataset_size) reuse whatever is on the Hub if present;
    they're approximate sizes and rarely fatal if slightly stale. Same for
    `configs` (data-files routing).
    """
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    local_text = local_card_path.read_text()
    local_front, local_body = _split_frontmatter(local_text)

    # Pull anything reusable from the remote (sizes, configs, splits).
    try:
        remote_path = hf_hub_download(repo_id=repo, filename="README.md", repo_type="dataset")
        remote_text = Path(remote_path).read_text()
        remote_front, _ = _split_frontmatter(remote_text)
    except (EntryNotFoundError, FileNotFoundError):
        remote_front = {}

    remote_info = remote_front.get("dataset_info") or {}
    local_features_yaml = ds.features._to_yaml_list()
    # If the remote splits/sizes look reasonable, keep them as a best-effort
    # estimate. Either way, ALWAYS overwrite features from our local schema.
    dataset_info = {
        "features": local_features_yaml,
        "splits": remote_info.get("splits") or [{"name": "train", "num_examples": len(ds)}],
    }
    if "download_size" in remote_info:
        dataset_info["download_size"] = remote_info["download_size"]
    if "dataset_size" in remote_info:
        dataset_info["dataset_size"] = remote_info["dataset_size"]

    local_front["dataset_info"] = dataset_info
    if "configs" in remote_front:
        local_front["configs"] = remote_front["configs"]
    else:
        # Fallback `configs` — tells HF where to find the Parquet shards.
        local_front.setdefault(
            "configs",
            [
                {
                    "config_name": "default",
                    "data_files": [{"split": "train", "path": "data/train-*"}],
                }
            ],
        )

    return _join_frontmatter(local_front, local_body)


if __name__ == "__main__":
    main()
