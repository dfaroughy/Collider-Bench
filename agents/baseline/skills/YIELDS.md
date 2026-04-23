# Yields and Normalization

The bin values you write into `HEPRecastData/*.yaml` are **expected event counts at the paper's integrated luminosity** — not raw MC counts.

## Formula

For each Monte Carlo sample:

```
N_yield = sigma * L * (N_selected / N_generated)
```

- `sigma` — cross section in pb (apply the correct order: LO, NLO, or NLO+NLL per the paper).
- `L` — integrated luminosity (paper-specific; e.g. 35.9 fb⁻¹ for 2016, 137 fb⁻¹ for full Run 2). Convert units so `sigma * L` is dimensionless events.
- `N_selected` — events passing your full selection.
- `N_generated` — total events in the sample before any selection. Use the generator's effective count (sum of `genWeight` for NLO samples; raw count for LO).

For **data**, no rescaling: `N_yield = N_selected`.

## Where each number comes from

| Number | Source |
|---|---|
| σ for CMS Open Data MC | `bin/cms-opendata sample-info <nano-recid>` (follows to MiniAOD parent) |
| σ for simulated signals (LO) | MG5 banner / survey log in `sim/PROC_*/run_mg5_*/`, or `bin/prospino run --order LO ...` |
| σ at NLO for SUSY pair production | `bin/prospino run --process <gg\|sg\|ss\|nn\|ng\|tb\|bb\|ll\|ns\|sb> --slha <spectrum> --order NLO` — reads the SLHA spectrum you pass to MG5/Pythia, returns `xsec_pb.nlo` and `k_factor` as JSON. Cross-check against CMS SUSY Cross Section WG tables when available. |
| K-factors | Prospino output includes `xsec_pb.k_factor = NLO/LO`. Otherwise taken from the paper's signal-modeling section; apply as `sigma_NLO = K * sigma_LO`. |
| L | Stated in the paper (abstract or data/selection section) |
| N_generated | `sample-info` for Open Data MC; MG5 run summary for simulated signals |

## NLO samples and genWeight

For NLO MC (typically most backgrounds on Open Data and some signals), events carry signed generator weights. Use:

```python
N_generated_effective = ak.sum(genWeight)             # over ALL events, before selection
N_selected_effective  = ak.sum(genWeight[selection])  # over selected events
```

Never count raw events when the generator is NLO — you will under- or over-count by ~factor-of-2 due to cancelling negative weights.

## What to store

In `datasets.yaml` keep `cross_section_pb` and `events` (N_generated) per sample. In the final `analysis/*.py` (or a separate `yields.py`), compute `N_yield` per sample per bin and write those values into `HEPRecastData/*.yaml`.
