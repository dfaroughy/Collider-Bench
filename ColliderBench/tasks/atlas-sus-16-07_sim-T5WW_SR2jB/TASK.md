> **Paper:** ATLAS-SUS-16-07
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T5WW_1600_1590_60`
> **Signal region:** SR2jB-2400 (two-jet, large effective mass)
> **Observable:** `m_eff(incl.)`

### Task

Implement the search analysis described in **ATLAS-SUS-16-07** and use it to predict the binned differential signal yield in `m_eff(incl.)` for the benchmark point `T5WW_1600_1590_60`, in **signal region SR2jB-2400**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T5WW_1600_1590_60` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the 0-lepton baseline selection, and the cuts that define the **SR2jB-2400** signal region (jet multiplicity, jet pT thresholds, `Δφ(jet, p_T^miss)`, the `E_T^miss`-based requirement, and the `m_eff(incl.) > 2400 GeV` cut — see the paper's signal-region definition tables). Apply them to your generated events.
3. Histogram the surviving events in `m_eff(incl.)` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `m_eff(incl.)` > 4000 GeV in the rightmost (overflow) bin.

### Definitions

- `m_eff(incl.)` — the inclusive effective mass: the scalar sum of `E_T^miss` and the transverse momenta of all jets with `pT > 50 GeV` in the event.
- `T5WW_1600_1590_60` — pair-produced gluinos at `m(g̃) = 1600 GeV`, each decaying via the one-step topology `g̃ → q q̄′ χ̃₁±` with `χ̃₁± → W± χ̃₁⁰`, with chargino mass `m(χ̃₁±) = 1590 GeV` (nearly degenerate with the gluino, so the quarks from the gluino decay are soft) and LSP `m(χ̃₁⁰) = 60 GeV` (so the W bosons are energetic). This compressed gluino–chargino spectrum favours the low-jet-multiplicity region.
- **SR2jB-2400** — a two-jet (`2jB`), 0-lepton signal region defined on top of the baseline selection by `m_eff(incl.) > 2400 GeV` and the associated jet-pT, `Δφ`, and `E_T^miss/m_eff` requirements given in the paper's signal-region tables.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
