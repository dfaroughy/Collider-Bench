# `simulate` — wrapper for MadGraph5 / Pythia8 / Delphes

**Purpose.** Generate signal events end-to-end (parton shower + detector
simulation) when no matching MC is available in CMS Open Data. The
wrapper archives inputs and outputs under `sim/<run_id>/` for provenance.

**When to use.** For BSM signals the paper defines but nobody has
produced for Open Data (SUSY benchmark points, EFT signals, etc.). For
SM backgrounds you usually want `cms-opendata` instead — save compute.

## Invocation

```bash
bin/simulate info                                              # list available models + cards
bin/simulate mg5 <proc_card.dat>                               # MG5 parton-level (parallel via nb_core in run_card)
bin/simulate pythia8 <events.lhe> [--parallel 32]              # Pythia8 showering (split-and-merge)
bin/simulate delphes <events.hepmc> [--card cms] [--parallel 32]
```

## Output

- `info`: bundled UFO models (`sm`, `MSSM_SLHA2`, `SMEFTsim`) and Delphes
  cards (`cms`, `atlas`, `cms_pileup`).
- `mg5`: events.lhe + MG5 banner + survey log in `sim/run_mg5_NNN/`.
- `pythia8`: showered events.hepmc in `sim/run_pythia8_NNN/`.
- `delphes`: detector ROOT file in `sim/run_delphes_NNN/`.

The archived `sim/run_*_NNN/` directories preserve proc_card.dat,
run_card.dat, param_card.dat, and stdout logs — use them to debug
generator issues or to cite exact settings in the final report.

## Gotchas

- `pythia8` and `delphes` parallelize via split-and-merge. Pick
  `--parallel N` to match available cores (e.g. 32 on a CPU node).
- `mg5` parallelism is set in the **run_card** (`nb_core = N`), not the
  CLI — edit the card before calling.
- `--card` for delphes accepts the aliases above; raw paths also work if
  you have a custom card.
- Delphes output ROOT schema is not NanoAOD — don't feed it into
  `cms-opendata stream`; use `uproot` directly.

## Examples

```bash
# What models and cards can I use?
bin/simulate info

# Generate SUSY signal at NLO
bin/simulate mg5 sim/T5Wg_1600_100/proc_card.dat

# Shower with Pythia on 32 cores
bin/simulate pythia8 sim/run_mg5_001/events.lhe --parallel 32

# Run Delphes with the CMS card
bin/simulate delphes sim/run_pythia8_001/events.hepmc --card cms --parallel 32
```

## Calling MG5 / Pythia / Delphes directly

The wrapper is thin — you can also call `tools/sim/MG5_aMC_v3_7_0/bin/mg5_aMC`,
`pythia8`, or `DelphesHepMC3` directly. Use the wrapper when you want the
provenance tracking.
