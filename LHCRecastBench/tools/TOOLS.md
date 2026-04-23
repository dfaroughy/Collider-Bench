# TOOLS

All tools live in `bin/`. Call them with relative paths. Use the
wrappers below, and fall back to the Python APIs listed at the bottom.

Each tool has a detailed doc you can pull with `bin/<tool> --doc`. This
file is the **index** — what each tool does and when to use it. See the
"When to pull `--doc`" section below for when to fetch the deep reference.

| Tool | Purpose | Deep doc |
|---|---|---|
| `read-paper` | Extract paper text / render figures as PNGs | `bin/read-paper --doc` |
| `hepdata` | Query the HEPData repository for published tables | `bin/hepdata --doc` |
| `cms-opendata` | Browse + stream CMS Open Data (data, MC, σ, cards) | `bin/cms-opendata --doc` |
| `simulate` | MG5 / Pythia8 / Delphes wrapper with provenance | `bin/simulate --doc` |
| `feynrules` | Browse + fetch UFO models from FeynRules wiki | `bin/feynrules --doc` |
| `prospino` | NLO σ for SUSY pair production | `bin/prospino --doc` |
| `run-analysis` | Execute `analysis.py` under the benchmark env | `bin/run-analysis --doc` |

## When to pull `--doc`

You don't need to read every doc upfront — the one-line purposes above are
usually enough to pick the right tool. Pull `bin/<tool> --doc` when you're
actually ready to invoke one and need:

- a specific CLI flag or option the cheat-sheet didn't mention
- the exact output schema (for parsing the JSON)
- a gotcha — something isn't behaving the way you expected

Think of the per-tool docs as man pages you can call on, not required
pre-reading.

## Calling the tools

**Never** run `python3 analysis.py` directly — use `bin/run-analysis` so the
conda env is active.

**Never** background `bin/run-analysis` (no `run_in_background`, no `&`).
It has a 4-hour internal timeout; blocking is safe.

**Always** use `root://` (xrootd) URLs with `cms-opendata stream`, not
`https://`.

## Python modules

- `from LHCRecastBench.tools.streaming import stream_files` — parallel file streaming.
- HEP stack: `uproot`, `awkward`, `numpy`, `hist`, `mplhep`, `yaml`.
