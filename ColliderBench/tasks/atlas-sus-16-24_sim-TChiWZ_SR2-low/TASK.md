> **Paper:** ATLAS-SUS-16-24
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `TChiWZ_150_50`
> **Signal region:** SR2-low (two-lepton + jets channel)
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **ATLAS-SUS-16-24** and use it to predict the binned differential signal yield in `E_T^miss` for the benchmark point `TChiWZ_150_50`, in **signal region SR2-low**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TChiWZ_150_50` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the two-lepton + jets baseline selection, and the cuts that define the **SR2-low** signal region. Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `E_T^miss` > 225 GeV in the rightmost (overflow) bin.

### Definitions

- `E_T^miss` — the magnitude of the missing transverse momentum.
- `TChiWZ_150_50` — associated chargino-neutralino production (chi1+- chi2_0) with gauge-boson-mediated decays chi2_0 -> Z chi1_0 and chi1+- -> W chi1_0; the benchmark point has masses (m(chargino/neutralino-2), m(χ̃₁⁰)) = (150, 50) GeV. See the paper for the precise mass spectrum of the simplified-model grid.
- **SR2-low** — a two-lepton + jets signal region of the electroweak-SUSY search; see the paper's signal-region definition tables for the exact selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
