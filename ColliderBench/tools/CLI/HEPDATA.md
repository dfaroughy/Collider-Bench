# `hepdata` — query the HEPData repository

**Purpose.** Find a paper's published numerical results on HEPData and pull
individual tables as structured YAML/JSON.

**When to use.** After reading the paper, find the HEPData record and pull
the measured observable bins (observed counts, background predictions,
efficiencies, limits). These are the ground truth you are recasting
against — the agent should use them to validate its own yields.

## Invocation

```bash
bin/hepdata find <arxiv-id>                    # → INSPIRE + HEPData record IDs
bin/hepdata tables <inspire-id>                # → list of tables in the record
bin/hepdata get <inspire-id> "Table 1"         # → human-readable table print
bin/hepdata get <inspire-id> "Table 1" --json  # → machine-readable JSON
bin/hepdata download <inspire-id>              # → all YAMLs into ./HEPData/
bin/hepdata download <inspire-id> --output-dir <dir>
```

Every command takes `--json` for machine-readable output.

## Output

- `find`: arXiv ID → INSPIRE record ID + HEPData URL.
- `tables`: list of `{name, description, bins, n_bins}` per table.
- `get`: a single table as YAML or JSON (x bins + dependent variables).
- `download`: the full HEPData YAML tarball unpacked into a directory.

## Gotchas

- `inspire-id` is an integer, different from the arXiv ID. Always run
  `find` first to translate.
- Table names usually match the published paper (`"Table 1"`,
  `"Figure 4a"`) but are not always identical — list them first.
- The public API is rate-limited; don't loop over every table in a tight
  loop.

## Examples

```bash
# Typical flow for a SUSY paper
bin/hepdata find 1707.06193                   # → inspire_id
bin/hepdata tables 1610452                    # see what's available
bin/hepdata get 1610452 "Figure 5a" --json    # pull one distribution
bin/hepdata download 1610452                  # or grab everything as YAMLs
```
