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

Each task is a self-contained unit covering one (paper, signal, histogram):

```
LHCRecastBench/tasks/
  <task-id>/                        e.g. sus-16-046-simulate-TChiWg-stgamma
    task.toml                       ← identity metadata (paper, type, signal, ...)
    TASK.md                         ← agent-facing instructions
    template/
      description.toml              ← per-histogram kinematics + benchmark text
      histogram_<sig>_<obs>.yml     ← null-filled skeleton the agent fills
    artifacts/                      ← optional task-specific auxiliary files
  shared/<paper>/                   shared across all tasks for that paper
    paper/<paper>.pdf
    histograms/                     ← reference ground-truth (HIDDEN from agent)
    object_efficiencies/            ← detector efficiency files
```

Task-id format: `<paper-slug>-<type>-<signal>-<histogram-slug>` with
type ∈ {`simulate`, `validate`} (`recast` to come).

## Agent workspace layout

Each run gets a fresh sandboxed workspace containing:

```
<run_dir>/workspace/
  agent_context/
    TASK.md                ← the task's instruction file
    AGENTS.md              ← agent role description
    TOOLS.md               ← canonical tool index
    SOUL.md, skills/*      ← optional per-agent guidance (baseline only)
  bin/                     ← all CLI wrappers
  tools/                   ← full tools/ tree (read-only)
  papers/                  ← symlinked paper PDF (read-only)
  object_efficiencies/     ← copy of shared detector files + task artifacts/
  results/                 ← copy of tasks/<task>/template/:
                             description.toml + null-filled histogram yaml.
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
      eval/                ← score.json, rubric_scorer.json, plots/, ...
```

## Scoring interface

Each task is scored against a **single** reference histogram:

- Agent output: `workspace/results/<histogram>.yml` (one series matching
  `header_name` from `description.toml`).
- Reference:    `LHCRecastBench/tasks/shared/<paper>/histograms/<histogram>.yaml`.

Metrics applied per task:

- Per-bin pulls (`pass` iff `|pull| < 2` or `rel_diff < 0.5`).
- Baker-Cousins likelihood-ratio decomposition (shape × norm).
- Kolmogorov–Smirnov on unit-area CDFs.

## Offline evaluation

All tools in [`evaluation/`](evaluation/) accept the same run-dir argument:

| Tool | What it measures |
|---|---|
| `score.py` | Per-bin pulls + Baker-Cousins shape/normalization + KS. |
| `rubric_scorer.py` | Weighted checkpoint scoring + cost + token efficiency. |
| `plot_recast.py` | Step-histogram PNG (reference vs agent, with ratio panel). |
| `llm_judge.py` | LLM-as-Judge reasoning evaluation + CORRECTED-provenance check. |
| `trajectory_judge.py` | 9-mode Terminal-Bench-style failure taxonomy. |

Drive all of them at once with `scripts/launch_eval.sh <run_dir>`; see
[`evaluation/EVAL.md`](evaluation/EVAL.md) and
[`evaluation/TAXONOMY.md`](evaluation/TAXONOMY.md) for methodology.
