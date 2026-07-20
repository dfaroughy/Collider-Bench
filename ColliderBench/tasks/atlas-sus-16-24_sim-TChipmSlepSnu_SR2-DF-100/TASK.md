> **Paper:** ATLAS-SUS-16-24
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `TChipmSlepSnu_300_150`
> **Signal region:** SR2-DF-100 (two-lepton, 0-jet, different-flavour channel)
> **Observable:** `m_T2`

### Task

Implement the search analysis described in **ATLAS-SUS-16-24** and use it to predict the binned differential signal yield in `m_T2` for the benchmark point `TChipmSlepSnu_300_150`, in **signal region SR2-DF-100**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TChipmSlepSnu_300_150` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the two-lepton, 0-jet, different-flavour baseline selection, and the cuts that define the **SR2-DF-100** signal region. Apply them to your generated events.
3. Histogram the surviving events in `m_T2` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `m_T2` — the stransverse mass of the two leptons and the missing transverse momentum (computed with a massless invisible-particle hypothesis).
- `TChipmSlepSnu_300_150` — pair-produced charginos, each decaying through an intermediate slepton or sneutrino to a lepton and the lightest neutralino (chi1+- -> l nu chi1_0); the benchmark point has masses (m(chargino), m(χ̃₁⁰)) = (300, 150) GeV. See the paper for the precise mass spectrum of the simplified-model grid.
- **SR2-DF-100** — a two-lepton, 0-jet, different-flavour signal region of the electroweak-SUSY search; see the paper's signal-region definition tables for the exact selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
