> **Paper:** CMS-SUS-16-046
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (yield-only)
> **Signal benchmark:** `TChiWg_700`
> **Observable:** total signal-region yield

### Task

Implement the search analysis described in **CMS-SUS-16-046** and use it to predict the total number of signal events for the benchmark point `TChiWg_700` in the analysis's **signal region**, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `TChiWg_700` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Sum the surviving event yield over the full signal region and fill the single `null` value already present in the `results/*.yaml` template.

### Definitions

- Total signal-region yield — the expected number of `TChiWg_700` events passing the analysis signal-region selection after all event weights and normalization factors have been applied.
- `TChiWg_700` — the signal benchmark point specified in the task metadata.
- `S_T^gamma` — the distribution observable used in the corresponding binned simulation task; this yield-only task scores only the integrated signal-region yield.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the single `null` value with your predicted total signal-region yield. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the total signal-region yield from your own simulation and analysis pipeline. Do not extract or digitize the target yield from the paper's figures, tables, HEPData record, or elsewhere.
