> **Paper:** ATLAS-SUS-16-24
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `TChiWZ_450_150`
> **Signal region:** SR3-WZ-1Jb (three-lepton channel)
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **ATLAS-SUS-16-24** and use it to predict the binned differential signal yield in `E_T^miss` for the benchmark point `TChiWZ_450_150`, in **signal region SR3-WZ-1Jb**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TChiWZ_450_150` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the three-lepton baseline selection, and the cuts that define the **SR3-WZ-1Jb** signal region. Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `E_T^miss` > 280 GeV in the rightmost (overflow) bin.

### Definitions

- `E_T^miss` — the magnitude of the missing transverse momentum.
- `TChiWZ_450_150` — associated chargino-neutralino production (chi1+- chi2_0) with gauge-boson-mediated decays chi2_0 -> Z chi1_0 and chi1+- -> W chi1_0; the benchmark point has masses (m(chargino/neutralino-2), m(χ̃₁⁰)) = (450, 150) GeV. See the paper for the precise mass spectrum of the simplified-model grid.
- **SR3-WZ-1Jb** — a three-lepton signal region of the electroweak-SUSY search; see the paper's signal-region definition tables for the exact selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
