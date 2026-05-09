> **Paper:** CMS-SUS-16-034
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (shape-only)
> **Signal benchmark:** `TChiWZ_550_200`
> **Signal region:** electroweak WZ/ZZ region
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-034** and use it to predict the normalized event distribution (shape) of `E_T^miss` for the signal benchmark point `TChiWZ_550_200`, in the analysis's **electroweak WZ/ZZ signal region**.

You should:

1. Generate `TChiWZ_550_200` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, baseline event-selection requirements, and the WZ/ZZ-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `E_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all reconstructed particle-flow candidates in the event.
- `TChiWZ_550_200` — associated production of a chargino and a neutralino via the simplified-model topology `pp → χ̃⁺₁ χ̃⁰₂` with `χ̃⁺₁ → W χ̃⁰₁`, `χ̃⁰₂ → Z χ̃⁰₁`, at `m(χ̃⁺₁) = m(χ̃⁰₂) = 550 GeV` and `m(χ̃⁰₁) = 200 GeV`. The lightest neutralino is the LSP and escapes the detector, contributing to `E_T^miss`.
- **Electroweak WZ/ZZ region** — the analysis's multilepton signal region(s) targeting `WZ` and `ZZ`-mediated electroweakino topologies (on-Z dilepton + extra lepton(s) + `E_T^miss`), as defined in the paper.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted relative signal bin contents; the scorer ignores overall normalization. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |


### Important

> The goal of this task is to predict the signal shape from your own simulation and analysis pipeline. Do not extract or digitize the target bin values from the paper's figures, tables, or HEPData record.
