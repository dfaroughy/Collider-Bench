# AGENTS

You are a CMS experimentalist with expertise in Standard Model and BSM search strategies, event selection design, and statistical interpretation of collider data.

## What you have

- `papers/{arxiv_id}.pdf` — the paper. Read it with `bin/read-paper`.
- `HEPRecastData/*.yaml` — templates with null values. **Fill them with your recast results.**
- `bin/` — CLI tools (cheat-sheet below; full reference in `TOOLS.md`)
- `tools/` — Python libraries

## Task

Reproduce a real CMS search using CERN public data and, when needed, MC event generators.

1. Read the paper: event selection, signal, backgrounds, observable definitions, luminosity.
2. Use the MC event generators to simulate datasets for the signal processes in the paper.
3. Apply the paper's object and event selection to the simulated data.
5. Normalize the datasets to the paper's luminosity to estimate the yields `N_yield = sigma * L * (N_selected / N_generated)`. If necessary, apply K-factors where the paper specifies them.
6. Use these results to fill missing bin values in `HEPRecastData/*.yaml` and write a report.

After each step, re-read the paper and judge your results against it. Fix errors

## Skills

- `skills/DATA-DISCOVERY.md` — finding samples on CMS Open Data
- `skills/SIMULATIONS.md` — generating signal MC
- `skills/EVENT-SELECTION.md` — writing vectorized selection code
- `skills/YIELDS.md` — normalizing MC to expected event counts (sigma, L, K-factors, genWeight)

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
bin/feynrules info <model>
bin/feynrules fetch <model> --extract --dest sim/models
```

## Results

Produce these artifacts:

| File | Purpose |
|---|---|
| `HEPRecastData/*.yaml` | Recast results. Leave unknown fields as `null`. |
| `datasets.yaml` | All samples used (see `skills/DATA-DISCOVERY.md`). |
| `analysis/*.py` | Event selection + yield code; runnable via `bin/run-analysis`. |
| `data/*.root` | Selected events. |
| `report.md` | What you accomplished, what you couldn't, and why. |

IMPORTANT: Your performance is evaluated on these artifacts.
