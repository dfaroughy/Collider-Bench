> **Paper:** CMS-SUS-16-034
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (yield-only)
> **Signal benchmark:** `TChiWZ_550_200`
> **Signal region:** electroweak WZ/ZZ region
> **Observable:** total signal-region yield

### Task

Implement the search analysis described in **CMS-SUS-16-034** and use it to predict the total number of signal events for the benchmark point `TChiWZ_550_200` in the analysis's **electroweak WZ/ZZ signal region**, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TChiWZ_550_200` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, baseline event-selection requirements, and the WZ/ZZ-region cuts that define this analysis. Apply them to your generated events.
3. Sum the surviving event yield over the WZ/ZZ signal region and fill the single `null` value already present in the `results/*.yaml` template.

### Definitions

- Total signal-region yield — the expected number of `TChiWZ_550_200` events passing the WZ/ZZ-region selection after all event weights and normalization factors have been applied.
- `TChiWZ_550_200` — associated production of a chargino and a neutralino via the simplified-model topology `pp → χ̃⁺₁ χ̃⁰₂` with `χ̃⁺₁ → W χ̃⁰₁`, `χ̃⁰₂ → Z χ̃⁰₁`, at `m(χ̃⁺₁) = m(χ̃⁰₂) = 550 GeV` and `m(χ̃⁰₁) = 200 GeV`.
- **Electroweak WZ/ZZ region** — the analysis's multilepton signal region(s) targeting `WZ` and `ZZ`-mediated electroweakino topologies (on-Z dilepton + extra lepton(s) + `E_T^miss`), as defined in the paper.
- `E_T^miss` — the distribution observable used in the corresponding binned simulation task; this yield-only task scores only the integrated WZ/ZZ-region yield.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the single `null` value with your predicted total signal-region yield. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the total signal-region yield from your own simulation and analysis pipeline. Do not extract or digitize the target yield from the paper's figures, tables, HEPData record, or elsewhere.
