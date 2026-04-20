# Dataset Discovery

Find CMS Open Data samples for every process the paper uses and record them in `datasets.yaml`.

## What to find

- **Data**: real events matching the triggers described in the paper.
- **Signal**: every signal process in the paper. If a signal isn't in CMS Open Data, generate it (see `SIMULATIONS.md`). Only signals get simulated — never backgrounds.

## How to search

```
cms-opendata search "<process>"        # find candidate datasets
cms-opendata files <recid> --json      # get xrootd URLs
cms-opendata stream <url> --branches <b1> <b2>   # verify branches exist
```

Try multiple search terms — naming varies. Watch out for inclusive vs exclusive samples (e.g., EWK+2jet vs inclusive EW).

## datasets.yaml format

```yaml
# Found on Open Data
- process: "PROC"
  role: data | signal | background
  recid: 12345
  title: "Full_dataset_title"
  cross_section_pb: 1.234
  events: 100000
  files: 10
  paper_reference: "Section X, Table Y"
  file_urls:
    - root://eospublic.cern.ch//eos/opendata/cms/mc/.../.root

# Not on Open Data — blocked
- process: "PROC_missing"
  role: background
  recid: null
  paper_reference: "Section X"
  status: BLOCKED_BY_MISSING_SAMPLE
  notes: "Not found in CMS Open Data"

# Simulated locally (signals only)
- process: "PROC_simulated"
  role: signal
  recid: null
  MC_tools: [mg5, pythia8, delphes]   # what YOU used
  title: "Full_dataset_title"
  cross_section_pb: 1.234
  events: 10000
  files: 10
  paper_reference: "Section X, Table Y"
  file_dirs:
    - sim/PROC_<name>
  status: FIXED_GENERATED
  notes: "Signal absent from Open Data — generated locally"
```

Statuses used: `FIXED_GENERATED`, `BLOCKED_BY_MISSING_SAMPLE`, `BLOCKED_BY_NANOAOD`. Omit `status` when the sample is fine.
