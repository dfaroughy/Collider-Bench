# AGENTS

You are analyzing CMS paper `{arxiv_id}`."


## What you have

- `papers/{arxiv_id}.pdf` — the paper. Read it with `bin/read-paper`.
- `results/*.yml` or `results/*.yaml` — histogram template with null values. The file is **two YAML documents** separated by `---`: a metadata block (`instructions`, `description`, `target`, `cm_energy_gev`, `luminosity_fb`) followed by the HEPData-style histogram (`dependent_variables`, `independent_variables`). **Fill the nulls in `dependent_variables[0].values` in place; do not modify bin edges or metadata.**
- `object_efficiencies/` — detector efficiency files (if provided for this task).
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
bin/simulate info             # list installed models, cards, env vars
bin/simulate --doc            # full sim guide (mg5_aMC / pythia / Delphes patterns, incl. multi-CPU pythia)
mg5_aMC <proc_card>.dat       # parton-level (call directly, no wrapper)
DelphesHepMC3 "$DELPHES_DIR/cards/delphes_card_CMS.tcl" out.root events.hepmc
# Pythia: write a small Python driver (`import pythia8`) — see `bin/simulate --doc`
bin/prospino list-processes
bin/prospino help-process <proc>
bin/prospino run --process <proc> --sqrts 13000 --order <fixed-order> --slha <file>.slha
```

## Results

Produce these artifacts:

| File | Purpose |
|---|---|
| `results/*.yml` | Recast histogram — fill null values in place. Leave truly unknown fields as `null`. |
| `datasets.yaml` | All samples used |
| `analysis/*.py` | Event selection + yield code; runnable via `bin/run-analysis`. |
| `data/*.root` | Selected events. |
| `report.md` | What you accomplished, what you couldn't, and why. |

IMPORTANT: Your performance is evaluated on these artifacts.
