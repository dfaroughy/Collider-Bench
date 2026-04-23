# AGENTS

You are analyzing CMS paper `{arxiv_id}`."


## What you have

- `papers/{arxiv_id}.pdf` — the paper. Read it with `bin/read-paper`.
- `HEPRecastData/*.yaml` — templates with null values. **Replace them with your recast results.**
- `bin/` — CLI tools (cheat-sheet below; full reference in `TOOLS.md`)
- `skills/` — Collider Physics skill-set

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
bin/feynrules list --search "vector-like quark"
bin/feynrules info <model>
bin/feynrules fetch <model> --extract --dest sim/models
bin/simulate info
bin/simulate mg5 <proc_card>.dat
bin/simulate pythia8 <events>.lhe --parallel N
bin/simulate delphes <events>.hepmc --card cms --parallel N
bin/prospino list-processes
bin/prospino help-process <proc>
bin/prospino run --process <proc> --sqrts 13000 --order <fixed-order> --slha <file>.slha
```

## Results

Produce these artifacts:

| File | Purpose |
|---|---|
| `HEPRecastData/*.yaml` | Recast results. Leave unknown fields as `null`. |
| `datasets.yaml` | All samples used |
| `analysis/*.py` | Event selection + yield code; runnable via `bin/run-analysis`. |
| `data/*.root` | Selected events. |
| `report.md` | What you accomplished, what you couldn't, and why. |

IMPORTANT: Your performance is evaluated on these artifacts.
