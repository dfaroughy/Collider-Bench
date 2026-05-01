# LHC-Recast Benchmark — what it provides

Researcher-facing overview: what an agent receives in its workspace, what
it's expected to produce, and how the output is scored. For tool-by-tool
CLI reference, see [`tools/TOOLS.md`](tools/TOOLS.md) and run
`bin/<tool> --doc` for individual docs.

## What the benchmark provides

- **CLI tools** for reading papers, pulling HEPData tables, browsing
  CMS Open Data, running simulation chains (MG5 → Pythia8 → Delphes),
  fetching FeynRules UFO models, and computing NLO SUSY cross sections
  (Prospino). Full index in [`tools/TOOLS.md`](tools/TOOLS.md).
- **Simulation binaries** (MG5 v3.7.0, Pythia 8.313, Delphes, Prospino
  2.1) vendored under [`tools/sim/`](tools/sim/), usable both via the
  `bin/simulate` wrapper and directly.
- **A parallel streaming library** —
  `from LHCRecastBench.tools.streaming import stream_files`.
- **HEPData YAML templates** per task — null-filled skeleton the
  agent fills in place.
- **Offline evaluation metrics** — see below.

## Task-centric layout

Each task is a self-contained unit covering one benchmark target:

```
LHCRecastBench/tasks/
  <task-id>/                        e.g. sus-16-046_sim-TChiWg
    task.toml                       ← identity metadata (paper, type, signal, ...)
    TASK.md                         ← agent-facing instructions
    template/
      histogram_<target>.yaml       ← null-filled skeleton the agent fills
    artifacts/                      ← optional task-specific auxiliary files
  shared/<paper>/                   shared across all tasks for that paper
    paper/<paper>.pdf
    reference/                      ← reference ground-truth (HIDDEN from agent)
    object_efficiencies/            ← detector efficiency files
```

Task-id format: `<paper-slug>_<type>-<benchmark>[_<region-or-variant>]` with
type ∈ {`sim`, `val`, `shape`, `yield`} (`recast` to come). Observable names live in
`task.toml` and the HEPData metadata, not in the task id or histogram filename.
Shape-only simulation tasks use `shape` in the task id and set
`[metrics].mode = "shape"`; they score only the unit-normalized distribution
shape while still reporting normalization as a diagnostic.
Yield-only tasks use `yield` in the task id and set
`[metrics].mode = "yield"`; they score only the integrated signal-region yield
while still reporting the trivial one-bin shape diagnostic.

## Agent workspace layout

Each run gets a fresh sandboxed workspace containing:

```
<run_dir>/workspace/
  agent_context/
    TASK.md                ← the task's instruction file
    AGENTS.md              ← agent role description
    TOOLS.md               ← canonical tool index
    SOUL.md, skills/*      ← optional per-agent guidance (when shipped by the agent)
  bin/                     ← all CLI wrappers
  tools/                   ← full tools/ tree (read-only)
  papers/                  ← symlinked paper PDF (read-only)
  object_efficiencies/     ← copy of shared detector files + task artifacts/
  results/                 ← copy of tasks/<task>/template/:
                             null-filled histogram/efficiency yaml.
                             Agent fills the nulls IN PLACE.
```

Agent also writes `analysis.py`, `report.md`, optional `sim/`, `data/`
subdirs. Task.toml is harness metadata and is NOT exposed to the agent.

## Run directory layout

Runs are grouped by `(runner, model)`:

```
runs/
  <runner>_<model>/
    <task-id>_<Adj><Physicist><hex8>/
      run_info.json        ← task_id, paper_ref, agent, runner, model, ...
      workspace/
      eval/                ← score.json, judge_scores.json, plots/, ...
```

## Scoring interface

Each task is scored against a **single** reference histogram:

- Agent output: `workspace/results/<file>.yaml` (one series matching
  `header_name` extracted from the template's first HEPData document).
- Reference:    `LHCRecastBench/tasks/shared/<paper>/reference/<file>.yaml`.

Metrics applied per task:

- Baker-Cousins likelihood-ratio decomposition (shape × norm).
- Kolmogorov–Smirnov on unit-area CDFs.
- Fill completeness (`n_filled` / `n_bins`) as a sanity flag.

For shape-only tasks, the final score is the Baker-Cousins shape score; the
normalization score is shown but not included in `overall_combined`. For
yield-only tasks, the final score is the normalization score.

## Offline evaluation

All tools in [`evaluation/`](evaluation/) accept the same run-dir argument:

| Tool | What it measures |
|---|---|
| `score.py` | Baker-Cousins shape/normalization + KS, plus fill completeness. |
| `plot_recast.py` | Step-histogram PNG (reference vs agent, with ratio panel). |
| `llm_judge.py` | Provenance audit + qualitative trajectory narrative. |

Drive all of them at once with `scripts/launch_eval.sh <run_dir>`; see
[`evaluation/EVAL.md`](evaluation/EVAL.md) for methodology.
