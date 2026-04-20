# LHC-Recast Benchmark — Reference

This document describes the benchmark provided to all agent implementations. Tools and data are agent-agnostic.

## Overview

The benchmark provides:
- **CLI tools** for accessing physics data (papers, HEPData, CMS Open Data)
- **Simulation tools** for generating signal events (MadGraph5, Pythia8, Delphes)
- **A parallel streaming library** for processing large datasets
- **HEPData YAML templates** for each paper (agent fills null values with recast results)
- **Offline evaluation metrics** (shape/normalization decomposition, rubric scoring, LLM-as-Judge)

## CLI Tools

### 1. Paper reading

```bash
read-paper papers/{arxiv_id}.pdf                  # full text
read-paper papers/{arxiv_id}.pdf --pages 3-5      # specific pages
read-paper papers/{arxiv_id}.pdf --figures         # render figure pages as PNG
```

### 2. HEPData (published numerical results)

```bash
hepdata find <arxiv-id>
hepdata tables <inspire-id>
hepdata get <inspire-id> "Table 1" --json
```

### 3. CMS Open Data

```bash
cms-opendata search "ZZTo4L" --json
cms-opendata files <recid> --json
cms-opendata stream <root://url> --branches Muon_pt Muon_eta
cms-opendata sample-info <nano-recid>          # follows NanoAOD → parent MiniAOD → cross section
cms-opendata sample-info <nano-recid> --json   # machine-readable output
```

Note: use `root://` (xrootd) URIs, not `https://`. The `sample-info` command accepts a NanoAOD record ID and automatically follows the provenance chain to the parent MiniAOD record to extract cross sections and generator configurations.

### 4. Simulation

```bash
simulate info                                              # list available models and detector cards
simulate mg5 proc_card.dat                                 # MadGraph5 parton-level generation
simulate pythia8 events.lhe --parallel 32                  # Pythia8 showering (split-and-merge)
simulate delphes events.hepmc --card cms --parallel 32     # Delphes detector simulation
```

Available bundled MadGraph5 models: `sm`, `MSSM_SLHA2`, `SMEFTsim` (v3, general flavor, α-scheme). Anything else the analysis needs should be fetched with `bin/feynrules` (see below). Delphes card aliases: `cms`, `atlas`, `cms_pileup`.

### 5. FeynRules model database

For BSM scenarios where none of the bundled UFO models fit, query and download additional models from the FeynRules wiki (`feynrules.irmp.ucl.ac.be`).

```bash
feynrules categories                              # list 7 top-level categories
feynrules list --category SusyModels              # models in a category
feynrules list --search "vector-like quark"       # substring search across slug/title/description
feynrules info MSSM                               # show all attachments for a model
feynrules fetch MSSM --extract --dest sim/models  # download UFO tarball(s) and unpack
feynrules refresh-catalog                         # rescrape the wiki (already cached)
```

The local catalog at `LHCRecastBench/data/feynrules_catalog.json` is pre-built; `fetch` defaults to UFO-format tarballs when present. Use `--file <name>` for a specific attachment, `--all` for everything on the page.

## Python Libraries

Available in the benchmark environment:

- `from LHCRecastBench.tools.streaming import stream_files` — parallel file streaming with auto-tuning
- Standard HEP stack: `uproot`, `awkward`, `numpy`, `hist`, `mplhep`, `yaml`
- Simulation: `pythia8`, `pyhepmc`

## HEPData Templates

Each benchmark task provides HEPData YAML files in the agent's workspace:

```
workspace/
  HEPRecastData/          ← templates with null values (agent fills these)
    submission.yaml       ← paper metadata (read-only context)
    obs_low.yaml          ← bins defined, values = null
    obs_high.yaml         ← bins defined, values = null
  HEPData/                ← reference with published values (for self-diagnosis)
    submission.yaml
    obs_low.yaml
    obs_high.yaml
```

The agent reads the template structure to understand what bins/observables need filling, runs its analysis, and writes the recast values into the `HEPRecastData/` YAML files. The offline scorer compares filled templates against the reference.

## Scoring Interface

The agent's output is the set of filled `HEPRecastData/*.yaml` files. The offline evaluation compares each `value: X` in the filled template against the corresponding `value: Y` in the reference, computing per-bin pulls, shape scores, and normalization scores.

No custom JSON formats are needed — the agent writes standard HEPData YAML.

## Offline Evaluation

Four evaluation tools in `LHCRecastBench/evaluation/` (not agent-facing):

- `score.py` — per-bin pulls + shape/normalization decomposition (one pass, one JSON)
- `rubric_scorer.py` — weighted checkpoint scoring with cost/token metrics
- `plot_recast.py` — per-table step-histogram PNGs (CMS vs recast, with ratio panel)
- `llm_judge.py` — LLM-as-a-Judge reasoning evaluation (6 dimensions + failure report)
