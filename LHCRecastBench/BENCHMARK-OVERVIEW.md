# LHC-Recast Benchmark — what it provides

Researcher-facing overview of the benchmark: what an agent receives in
its workspace, what it's expected to produce, and how the output is
scored. For tool-by-tool CLI reference, see
[`tools/TOOLS.md`](tools/TOOLS.md) and run `bin/<tool> --doc` for
individual docs.

## What the benchmark provides

- **CLI tools** for reading papers, pulling HEPData tables, browsing
  CMS Open Data, running simulation chains (MG5 → Pythia8 → Delphes),
  fetching FeynRules UFO models, and computing NLO SUSY cross sections
  (Prospino). Full index in [`tools/TOOLS.md`](tools/TOOLS.md).
- **Simulation binaries** (MG5 v3.7.0, Pythia 8.313, Delphes, Prospino
  2.1) vendored under [`tools/sim/`](tools/sim/) and usable both via the
  `bin/simulate` wrapper and directly.
- **A parallel streaming library** —
  `from LHCRecastBench.tools.streaming import stream_files`.
- **HEPData YAML templates** per paper + task — `HEPRecastData/`
  with `null` values where the agent writes its recast results.
- **Offline evaluation metrics** — see below.

## Agent workspace layout

Each run gets a fresh sandboxed workspace containing:

```
<run_dir>/workspace/
  agent_context/
    TASK.md              ← the benchmark's task spec for this run (validate|simulate|recast)
    AGENTS.md            ← the agent's role description (per-agent)
    TOOLS.md             ← tool index (seeded from tools/TOOLS.md)
    SOUL.md, skills/*    ← optional per-agent guidance (baseline only)
  bin/                   ← all CLI wrappers (symlinked from LHCRecastBench/bin/)
  papers/                ← paper PDF (symlinked, read-only)
  tools/                 ← full tools/ tree (read-only)
  HEPRecastData/         ← task-specific templates, null values to fill in
  <task-specific-shared> ← object_efficiencies/, datasets.yaml stubs, etc.
```

The agent writes `analysis.py` + `report.md` in the workspace root and
fills the `HEPRecastData/*.yaml` values. Everything else is read-only
or seeded per-run.

## Tasks

Each paper ships one or more tasks under `papers/<arxiv>/tasks/`:

| Task | Expected depth |
|---|---|
| `validate` | Reproduce a known sanity check — minimal MC, one observable. |
| `simulate` | Generate the signal MC and fill a subset of the HEPData tables. |
| `recast`   | Full recast: all signal benchmarks, all observables. |

The same benchmark harness drives all three; the `task:` field in the
launch config (or `--task` CLI flag) picks which `TASK.md` + HEPRecastData
templates + reference get seeded.

## Scoring interface

The agent's output is the set of filled `HEPRecastData/*.yaml` files.
Scoring compares each `value: X` in the filled YAML against the
corresponding `value: Y` in the hidden reference under
`papers/<arxiv>/tasks/<task>/reference/HEPRecastData/`. No custom JSON
formats — the agent writes standard HEPData YAML.

## Offline evaluation

Five tools in [`evaluation/`](evaluation/), each accepting the same
run-dir argument (see [`evaluation/EVAL.md`](evaluation/EVAL.md) for the
full schema):

| Tool | What it measures |
|---|---|
| `score.py` | Per-bin pulls + Baker-Cousins shape/normalization decomposition (goodness-of-fit + p-values). |
| `rubric_scorer.py` | Weighted checkpoint scoring + cost + token efficiency. |
| `plot_recast.py` | Per-table step-histogram PNGs (CMS vs recast, with ratio panel). |
| `llm_judge.py` | LLM-as-Judge reasoning evaluation (6 dimensions + CORRECTED-provenance check). |
| `trajectory_judge.py` | 9-mode Terminal-Bench-style failure taxonomy (execution / coherence / verification). |

Drive all of them at once with `scripts/launch_eval.sh <run_dir>`; see
[`evaluation/EVAL.md`](evaluation/EVAL.md) and
[`evaluation/TAXONOMY.md`](evaluation/TAXONOMY.md) for methodology.
