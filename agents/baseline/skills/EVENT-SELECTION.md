# Event Selection

Write `analysis.py` implementing the paper's object identification, cuts, and event selection. Save selected events to `data/*.root`. Use the usual stack: `uproot`, `awkward`, `numpy`, `hist`, `mplhep`, plus `from LHCRecastBench.tools.streaming import stream_files` for parallel I/O.

## Performance

Datasets can have 100M+ events across hundreds of files; the analysis must fit inside a 4-hour wall-clock budget.

- **Vectorize everything.** No Python `for` loops over events. Candidate pairing uses `ak.combinations` + `ak.argmin`, not loops.
- **Read only the branches you need** — do not materialize full NanoAOD events.
- **Stream from xrootd** (`root://` URIs in `datasets.yaml`); never download Open Data locally. `stream_files` handles worker tuning, progress reporting, and error recovery.

## Running

Run via `bin/run-analysis`. Apply the **same** selection to every sample in `datasets.yaml` (data, signal, background) and to any locally generated MC. Stream all files per sample — don't sub-sample.
