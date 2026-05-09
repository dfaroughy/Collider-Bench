> **Paper:** CMS-SUS-16-046
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T5Wg_1750_1700`
> **Observable:** `S_T^gamma`

### Task

Implement the search analysis described in **CMS-SUS-16-046** and use it to predict the binned differential signal yield in `S_T^gamma` for the benchmark point `T5Wg_1750_1700`, in the analysis's **signal region**, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T5Wg_1750_1700` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in `S_T^gamma` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `S_T^gamma` — the scalar sum of `pT_miss` and the transverse momenta of all photons in the event.
- `T5Wg_1750_1700` — pair-produced gluinos at `m(g̃) = 1750 GeV`, each decaying via the `T5Wg` simplified-model topology to a mass-degenerate wino NLSP at `m(W̃) = 1700 GeV` and a massless LSP.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
