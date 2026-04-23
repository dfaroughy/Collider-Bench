# `run-analysis` — execute `analysis.py` under the benchmark's env

**Purpose.** Activate the `cms_analysis` conda env, run `analysis.py` from
the workspace root, and print a summary. Handles pre-flight checks and
guarantees only one analysis runs at a time in the workspace.

**When to use.** Every time you want to run the final (or intermediate)
analysis pipeline that produces the HEPRecastData yields. **Do NOT**
invoke `python3 analysis.py` directly — the env won't be active and
imports will fail.

## Invocation

```bash
bin/run-analysis
```

There are no flags. The tool:

1. Acquires a file lock on `.analysis.lock` (fails fast if a prior run
   is still going).
2. Sources `cms_analysis` conda env.
3. Runs pre-flight checks (workspace layout, required files).
4. Executes `python3 analysis.py`.
5. Prints a summary of what `analysis.py` produced.

## Output

Whatever `analysis.py` writes to stdout plus a trailer with the run
summary. The script has a 4-hour internal timeout; block on it
synchronously.

## Gotchas

- **Run synchronously via `Bash`**, never `run_in_background` and never
  with `&`. The internal 4-hour timeout is sufficient; blocking is safe
  and expected.
- Only one `bin/run-analysis` can execute in a workspace at a time —
  the lock prevents double-runs.
- `analysis.py` must exist in the workspace root. If it doesn't, the
  pre-flight check errors with a clear message.

## Importable Python modules

From within `analysis.py` these are available:

- `from LHCRecastBench.tools.streaming import stream_files` — parallel
  file streaming (see `skills/EVENT-SELECTION.md` if the baseline
  agent's skills are seeded).
- Standard HEP stack: `uproot`, `awkward`, `numpy`, `hist`, `mplhep`,
  `yaml`.
- Simulation: `pythia8`, `pyhepmc` (for custom showering scripts).
