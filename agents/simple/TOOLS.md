# TOOLS

All tools are in `bin/`. Call them with relative paths. Do not write your own HTTP calls, curl commands, or Python data-fetching scripts.

## Paper

```bash
bin/read-paper papers/{arxiv_id}.pdf                  # full text
bin/read-paper papers/{arxiv_id}.pdf --pages 3-5      # specific pages
bin/read-paper papers/{arxiv_id}.pdf --figures         # render figure pages as PNG
bin/read-paper papers/{arxiv_id}.pdf --figures --pages 8-12
```

Figures are saved to `papers/figures/`. Read the PNGs to inspect plots.

## HEPData

```bash
bin/hepdata find <arxiv-id>
bin/hepdata tables <inspire-id>
bin/hepdata get <inspire-id> "Table 1" --json
```

## CMS Open Data

```bash
bin/cms-opendata search "ZZTo4L" --json
bin/cms-opendata files <recid> --json
bin/cms-opendata stream <root://url> --branches Muon_pt Muon_eta
bin/cms-opendata sample-info <nano-recid>          # follows NanoAOD → parent MiniAOD for cross section + generator cards
bin/cms-opendata sample-info <nano-recid> --json   # machine-readable
```

Use `root://` (xrootd) URLs, not `https://` — HTTPS fails with SSL errors on NERSC. For cross sections, pass the **NanoAOD** recid to `sample-info`; it finds the parent MiniAOD automatically.

## Simulation tools

When signal samples are not available in CMS Open Data, you can generate them. See `skills/SIMULATIONS.md` for the full guide.

```bash
bin/simulate info                                              # list models and detector cards
bin/simulate mg5 proc_card.dat                                 # parton-level generation
bin/simulate pythia8 events.lhe --parallel 32                  # showering
bin/simulate delphes events.hepmc --card cms --parallel 32     # detector simulation
```

## FeynRules model database

If `bin/simulate info` doesn't list a UFO model that fits the BSM scenario in the paper, query the FeynRules wiki database for additional models.

```bash
bin/feynrules categories                              # 7 top-level categories
bin/feynrules list --category SusyModels              # browse one category
bin/feynrules list --search "vector-like quark"       # substring search
bin/feynrules info MSSM                               # show all attachments for a model
bin/feynrules fetch MSSM --extract --dest sim/models  # download UFO tarball(s) and unpack
```

`fetch` defaults to UFO-format tarballs when the model page has them; use `--file <name>` for a specific attachment or `--all` for everything. The catalog is local — no network calls needed for browsing.

## Running the analysis

```bash
bin/run-analysis
```

Runs pre-flight checks, activates the conda env, executes `analysis.py`, prints a summary. Do NOT run `python3 analysis.py` directly — the env won't be active and imports will fail.

The following are importable:

- `from LHCRecastBench.tools.streaming import stream_files` — parallel file streaming (see `skills/EVENT-SELECTION.md`)
- Standard HEP stack: `uproot`, `awkward`, `numpy`, `hist`, `mplhep`, `yaml`
- Simulation: `pythia8`, `pyhepmc` (for custom showering scripts if needed)
