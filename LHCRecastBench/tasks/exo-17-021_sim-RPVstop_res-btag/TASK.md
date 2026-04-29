> **Paper:** CMS-EXO-17-021
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `RPVstop_jj`
> **Observable:** efficiency vs. stop mass

### Task

Implement the search analysis described in **CMS-EXO-17-021** and use it to predict the signal selection efficiency for the `RPVstop_jj` benchmark as a function of the stop mass, in the analysis's **resolved four-jet signal region with the b-tagged selection**.

You should:

1. Generate `RPVstop_jj` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the resolved b-tagged signal-region cuts that define this analysis. Apply them to your generated events.
3. Compute the signal selection efficiency at each stop mass using the mass points already present in the `results/*.yaml` template (do not modify them).

### Definitions

- Signal selection efficiency — the fraction of generated `RPVstop_jj` events surviving the full reconstruction-and-selection chain in this signal region, expressed as a percentage.
- `RPVstop_jj` — pair-produced stops decaying through a baryonic RPV coupling to two quarks per stop; in this b-tagged variant, each resonance leg contains a bottom quark.
- Resolved b-tagged signal region — the four-jet resolved branch of the search with b-tag requirements applied to target bottom-quark final states.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` efficiency values for each mass point. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal efficiency from your own simulation and analysis pipeline. Do not extract efficiency values from the paper's figures, tables, HEPData record, or elsewhere.
