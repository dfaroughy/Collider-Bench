> **Paper:** ATLAS-SUSY-2016-24
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `TSlepSlep_400_1`
> **Signal region:** SR2-SF-loose (two-lepton, 0-jet, same-flavour channel)
> **Observable:** `m_ll`

### Task

Implement the search analysis described in **ATLAS-SUSY-2016-24** and use it to predict the binned differential signal yield in `m_ll` for the benchmark point `TSlepSlep_400_1`, in **signal region SR2-SF-loose**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TSlepSlep_400_1` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the two-lepton, 0-jet, same-flavour baseline selection, and the cuts that define the **SR2-SF-loose** signal region. Apply them to your generated events.
3. Histogram the surviving events in `m_ll` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `m_ll` > 480 GeV in the rightmost (overflow) bin.

### Definitions

- `m_ll` — the invariant mass of the same-flavour opposite-sign lepton pair.
- `TSlepSlep_400_1` — pair-produced sleptons, each decaying directly to a lepton and the lightest neutralino (l~ -> l chi1_0); the benchmark point has masses (m(slepton), m(χ̃₁⁰)) = (400, 1) GeV. See the paper for the precise mass spectrum of the simplified-model grid.
- **SR2-SF-loose** — a two-lepton, 0-jet, same-flavour signal region of the electroweak-SUSY search; see the paper's signal-region definition tables for the exact selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
