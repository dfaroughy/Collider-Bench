> **Paper:** ATLAS-SUSY-2016-07
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T2qq_1500_0`
> **Signal region:** SR2j-2800 (two-jet, large effective mass)
> **Observable:** `m_eff(incl.)`

### Task

Implement the search analysis described in **ATLAS-SUSY-2016-07** and use it to predict the binned differential signal yield in `m_eff(incl.)` for the benchmark point `T2qq_1500_0`, in **signal region SR2j-2800**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T2qq_1500_0` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the 0-lepton baseline selection, and the cuts that define the **SR2j-2800** signal region. Apply them to your generated events.
3. Histogram the surviving events in `m_eff(incl.)` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `m_eff(incl.)` > 4000 GeV in the rightmost (overflow) bin.

### Definitions

- `m_eff(incl.)` — the inclusive effective mass: the scalar sum of `E_T^miss` and the transverse momenta of all jets with `pT > 50 GeV` in the event.
- `T2qq_1500_0` — pair-produced light-flavour squarks at `m(q̃) = 1500 GeV`, each decaying directly to a quark and the neutralino LSP (`q̃ → q χ̃₁⁰`) with `m(χ̃₁⁰) = 0 GeV`. The large squark–LSP mass splitting gives two hard jets recoiling against large `E_T^miss`, motivating the two-jet region.
- **SR2j-2800** — a two-jet, 0-lepton signal region of the squark/gluino jets + `E_T^miss` search; see the paper's signal-region definition tables for the exact selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
