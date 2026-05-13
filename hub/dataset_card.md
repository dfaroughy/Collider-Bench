---
license: mit
task_categories:
  - text-generation
  - question-answering
language:
  - en
tags:
  - benchmark
  - agentic
  - llm-agents
  - tool-use
  - simulation
  - physics
  - high-energy-physics
  - particle-physics
  - LHC
pretty_name: Collider-Bench
size_categories:
  - n<1K
---


![Collider-Bench diagram](https://huggingface.co/datasets/Dariusfar/ColliderBench/resolve/main/Collider-Bench_diagram.png)

**Collider-Bench** is an AI benchmark for evaluating whether LLM agents can reproduce experimental analyses from the **Large Hadron Collider** (LHC) at CERN using only public papers and open scientific software.

Each task requires multi-step scientific reasoning by an autonomous coding agent, from reading a published CMS or ATLAS search and identifying the relevant signal region, to generating and processing simulated signal events, implementing the event selection, and predicting the binned signal yields reported by the analysis

The benchmark tests long-horizon scientific reasoning under realistic conditions, including ambiguous or underspecified paper descriptions, underdocumented domain-specific tools and approximate public simulation pipelines.

This HuggingFace dataset hosts the **task corpus only** — the agent-facing instructions and artifacts. The **runtime harness, scorer, and hidden reference values** live in the companion GitHub repository:

🔗 **https://github.com/dfaroughy/Collider-Bench**

The reference yields used by the scorer are deliberately not published here to preserve the benchmark's blind-test property.

## Quick start

```python
from datasets import load_dataset

ds = load_dataset("Dariusfar/ColliderBench", split="train")
print(ds)                                  # 10 sim tasks
print(ds[0]["task_id"], ds[0]["paper_id"])
print(ds[0]["instructions_md"][:400])      # what the agent gets shown
print(ds[0]["template_yaml"][:400])        # the null-filled template
print(ds[0]["paper_pdf"][:8])              # PDF magic bytes (b'%PDF-1.5')
```

To actually run an agent against a task and score its submission, install the harness from the GitHub repo:

```bash
git clone https://github.com/dfaroughy/Collider-Bench.git
cd Collider-Bench
pip install -e ".[dev]"
podman pull ghcr.io/dfaroughy/lhc-bench:latest      # MadGraph + Pythia + Delphes + ROOT image
export ANTHROPIC_API_KEY=...                         # or OPENAI_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY
scripts/run-agent --config configs/anthropics/claude_sonnet.yaml --task sus-16-046_sim-T5Wg
```

## Schema (one row per task)

| Field | Type | Description |
|---|---|---|
| `task_id`            | string | Canonical task identifier, e.g. `sus-16-046_sim-T5Wg` |
| `paper_id`           | string | CMS analysis identifier, e.g. `CMS-SUS-16-046` |
| `analysis_target`    | string | Final state under study (`photons`, `single lepton`, `leptons + jets`) |
| `signal_model`       | string | SUSY simplified-model name + slice (`T5Wg, high-H_T`, `T2tt, compressed`, …) |
| `observable`         | string | Observable key as used by the scorer (`STgamma`, `MET`, …) |
| `observable_pretty`  | string | LaTeX-style label (`S_T^gamma`, `E_T^miss`, `p_T^miss`) |
| `plot_units`         | string | y-axis units of the histogram (`Events/bin`, `Events/GeV`) |
| `score_mode`         | string | Scoring mode used by the harness (`shape_norm` for sim tasks) |
| `tolerance`          | float64 | Per-bin tolerance band used in shape-pass-rate diagnostics |
| `walltime`           | string | Harness walltime budget (e.g. `2h30m`) |
| `difficulty`         | string | `easy` / `medium` / `hard` rough difficulty tag |
| `tags`               | seq<string> | Free-form metadata tags |
| `instructions_md`    | string | The full `TASK.md` text — the agent's primary instructions |
| `task_toml`          | string | Verbatim `task.toml` content (paper id, observable, walltime, tolerance, …) |
| `template_yaml`      | string | Null-filled HEPData-style YAML the agent fills with predicted bin values |
| `n_bins`             | int64  | Total bin count across all dependent variables in the template |
| `paper_pdf`          | binary | Bytes of the CMS analysis paper (publicly available on CDS/Inspire-HEP) |
| `paper_pdf_sha256`   | string | SHA-256 of `paper_pdf` |
| `paper_pdf_bytes`    | int64  | Length of `paper_pdf` |
| `object_efficiencies`| seq<{filename, data, sha256, size_bytes}> | CMS public detector-efficiency maps (ROOT files) the agent needs to apply during selection |

## Task corpus

| Task id | Analysis target | Signal | Observable | Paper |
|---|---|---|---|---|
| `sus-16-034_sim-TChiWZ`        | leptons + jets | `TChiWZ`              | $E_T^{\rm miss}$ | CMS-SUS-16-034 |
| `sus-16-046_sim-T5Wg`          | photons        | `T5Wg`                | $S_T^{\gamma}$   | CMS-SUS-16-046 |
| `sus-16-046_sim-TChiWg`        | photons        | `TChiWg`              | $S_T^{\gamma}$   | CMS-SUS-16-046 |
| `sus-16-047_sim-T5Wg_highHT`   | photons        | `T5Wg`, high-$H_T$    | $p_T^{\rm miss}$ | CMS-SUS-16-047 |
| `sus-16-047_sim-T5Wg_lowHT`    | photons        | `T5Wg`, low-$H_T$     | $p_T^{\rm miss}$ | CMS-SUS-16-047 |
| `sus-16-047_sim-T6gg_highHT`   | photons        | `T6gg`, high-$H_T$    | $p_T^{\rm miss}$ | CMS-SUS-16-047 |
| `sus-16-047_sim-T6gg_lowHT`    | photons        | `T6gg`, low-$H_T$     | $p_T^{\rm miss}$ | CMS-SUS-16-047 |
| `sus-16-051_sim-T2tt_SRG`      | single lepton  | `T2tt`                | $E_T^{\rm miss}$ | CMS-SUS-16-051 |
| `sus-16-051_sim-T2bW_SRG`      | single lepton  | `T2bW`                | $E_T^{\rm miss}$ | CMS-SUS-16-051 |
| `sus-16-051_sim-T2tt_comp`     | single lepton  | `T2tt`, compressed    | $E_T^{\rm miss}$ | CMS-SUS-16-051 |

## Scoring

Each `sim` task asks the agent to reproduce the published per-bin yield distribution. The primary metric used by the scorer is the relative L² distance

$$d(\hat y, y^\star) = \sqrt{\sum_k (\hat y_k - y_k^\star)^2 \big/ \sum_k (y_k^\star)^2}$$

between the agent's bin yields $\hat y$ and the published reference $y^\star$.

Scoring is offline and deterministic — it does **not** require an LLM. See [`ColliderBench/Evals/`](https://github.com/dfaroughy/Collider-Bench/tree/main/ColliderBench/Evals) in the harness repo.

## Citation

If you use Collider-Bench in your research, please cite:

```
@misc{colliderbench2026,
  title  = {Collider-Bench: A benchmark for LHC analysis recasting by LLM agents},
  author = {Faroughy, Darius A. and contributors},
  year   = {2026},
  url    = {https://huggingface.co/datasets/Dariusfar/ColliderBench},
}
```

## License

MIT (matches the GitHub repo). The CMS paper PDFs and detector efficiency maps are reproduced here as published by the CMS Collaboration under the terms of their respective public-data policies.
