> **Paper:** CMS-SUS-16-047
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (shape-only)
> **Signal benchmark:** `T6gg_1750_1650`
> **Observable:** `p_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-047** and use it to predict the normalized event distribution (shape) of `p_T^miss` for the signal benchmark point `T6gg_1750_1650`, in the **high-H_T^gamma signal region**.

You should:

1. Generate `T6gg_1750_1650` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the high-H_T^gamma signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in `p_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `p_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all candidate objects in the event.
- `T6gg_1750_1650` — pair-produced squarks at `m(q̃) = 1750 GeV`, each decaying via the `T6gg` simplified-model topology to a neutralino at `1650 GeV` plus a quark jet, with two photons in the final state.
- High-H_T^gamma signal region — the higher-H_T^gamma branch of the analysis's two H_T^gamma-categorised signal regions, where `H_T^gamma` is the scalar sum of jet transverse momenta and the leading-photon `pT`.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted relative signal bin contents; the scorer ignores overall normalization. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |


### Important

> The goal of this task is to predict the signal shape from your own simulation and analysis pipeline. Do not extract or digitize the target bin values from the paper's figures, tables, or HEPData record.
