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


## Task schema

| Field | Type | Description |
|---|---|---|
| `task_id`            | string | Canonical task identifier, e.g. `sus-16-046_sim-T5Wg` |
| `paper_id`           | string | CMS analysis identifier, e.g. `CMS-SUS-16-046` |
| `task_type`          | string | Task family (`simulation` for the sim corpus) |
| `analysis_target`    | string | Final state under study (`photons`, `single lepton`, `leptons + jets`) |
| `signal_model`       | string | SUSY simplified-model name + slice (`T5Wg, high-H_T`, `T2tt, compressed`, …) |
| `observable`         | string | Observable key as used by the scorer (`STgamma`, `MET`, …) |
| `observable_pretty`  | string | LaTeX-style label (`S_T^gamma`, `E_T^miss`, `p_T^miss`) |
| `plot_units`         | string | y-axis units of the histogram (`Events/bin`, `Events/GeV`) |
| `score_mode`         | string | Scoring mode used by the harness (`shape_norm` for sim tasks) |
| `walltime`           | string | Harness walltime budget (e.g. `2h30m`) |
| `difficulty`         | string | `easy` / `medium` / `hard` rough difficulty tag |
| `tags`               | seq<string> | Free-form metadata tags |
| `initial_prompt`     | string | The system prompt the reference `simple` agent receives (from `agents/simple/run.py:build_prompt`); use as a starting point or replace with your own |
| `task_md`            | string | The full `TASK.md` text — the agent's primary instructions |
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


## Citation

If you use Collider-Bench in your research, please cite:

```
@misc{colliderbench2026,
  title  = {Collider-Bench: A benchmark for LHC analysis recasting by LLM agents},
  author = {Faroughy, Darius A. and Palacios Schweitzer, Sofia and Pang, Ian and Mishra-Sharma, Siddharth and Shih, David},
  year   = {2026},
  url    = {https://huggingface.co/datasets/Dariusfar/ColliderBench},
}
```

## License

MIT (matches the GitHub repo). The CMS paper PDFs and detector efficiency maps are reproduced here as published by the CMS Collaboration under the terms of their respective public-data policies.
