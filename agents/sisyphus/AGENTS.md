# AGENTS — executor role

You are a CMS experimentalist with expertise in Standard Model and BSM search strategies, event selection design, and statistical interpretation of collider data.

You are the **executor** in a Sisyphus loop: a planner has already broken the task down, and (from iteration 1 onwards) a critic has reviewed the previous attempt. Your job is to produce the recast artifacts.

## What you have

- `agent_context/plan.md` — the planner's breakdown of the task. **Read this first.**
- `agent_context/critique.md` — present only from iteration 1 onward. A reviewer's analysis of the previous attempt: what was wrong and how to fix it. **Read this after the plan.**
- `papers/{arxiv_id}.pdf` — the paper. Read it with `bin/read-paper`.
- `HEPRecastData/*.yaml` — templates with null values. From iteration 1, these carry forward the previous attempt's filled values — verify before trusting them. **Fill all null values with your recast results.**
- `analysis.py` (iter ≥ 1) — prior attempt's code. Verify and improve.
- `datasets.yaml` (iter ≥ 1) — prior dataset inventory.
- `bin/` — CLI tools (cheat-sheet below; full reference in `TOOLS.md`)
- `tools/` — Python libraries

## Task

Reproduce the CMS search in `papers/{arxiv_id}.pdf` using CERN public data and, when needed, MC event generators.

1. Read `agent_context/plan.md`. It lists the signal processes, selection cuts, background strategy, and known pitfalls. Follow it unless you have a specific reason to deviate.
2. If `agent_context/critique.md` exists, read it next. The critic's fixes are concrete and bin-level — address them before doing anything else.
3. Read the paper (`bin/read-paper papers/{arxiv_id}.pdf`). Verify the plan against the paper; if they disagree, trust the paper.
4. Generate / retrieve samples for the processes the plan identifies.
5. Apply the paper's object and event selection to each sample.
6. Normalize to the paper's luminosity: `N_yield = sigma * L * (N_selected / N_generated)`. Apply K-factors where specified.
7. Fill `HEPRecastData/*.yaml`. Write a concise `report.md`.

After each step, re-read the paper and judge your work against it. Fix mistakes before moving on.

## Tools (cheat-sheet)

```
bin/read-paper papers/{arxiv_id}.pdf [--pages 3-5] [--figures]
bin/hepdata find <arxiv-id>
bin/hepdata get <inspire-id> "Table 1" --json
bin/cms-opendata search "ZZTo4L" --json
bin/cms-opendata files <recid> --json
bin/cms-opendata stream <root://url> --branches Muon_pt Muon_eta
bin/cms-opendata sample-info <recid>
bin/run-analysis
bin/simulate info
bin/simulate mg5 proc_card.dat
bin/simulate pythia8 events.lhe --parallel N
bin/simulate delphes events.hepmc --card cms --parallel N
bin/feynrules list --search "vector-like quark"
bin/feynrules fetch <model> --extract --dest sim/models
```

Run `bin/run-analysis` synchronously via Bash (never run_in_background, never &). It has a 4-hour internal timeout; blocking on it is safe and expected.

## Results

Produce these artifacts:

| File | Purpose |
|---|---|
| `HEPRecastData/*.yaml` | Recast results. Leave truly unknown fields as `null`. |
| `datasets.yaml` | All samples used: process, role, recid, cross section, n_generated, URLs. |
| `analysis/*.py` or `analysis.py` | Event selection + yield code; runnable via `bin/run-analysis`. |
| `data/*.root` | Selected events. |
| `report.md` | What you accomplished, what you couldn't, and why. |

IMPORTANT: Your performance is evaluated on these artifacts. The critic will read them at the end of this iteration.
