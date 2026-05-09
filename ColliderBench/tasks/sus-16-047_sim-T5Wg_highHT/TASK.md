> **Paper:** CMS-SUS-16-047
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T5Wg_1600_100`
> **Observable:** `p_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-047** and use it to predict the binned differential signal yield in `p_T^miss` for the benchmark point `T5Wg_1600_100`, in the **high-H_T^gamma signal region**, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T5Wg_1600_100` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the high-H_T^gamma signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in `p_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `p_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all candidate objects in the event.
- `T5Wg_1600_100` — pair-produced gluinos at `m(g̃) = 1600 GeV`, each decaying via the `T5Wg` simplified-model topology to a mass-degenerate neutralino/chargino at `100 GeV` plus light-quark jets (and on average one photon per gluino-pair event).
- High-H_T^gamma signal region — the higher-H_T^gamma branch of the analysis's two H_T^gamma-categorised signal regions, where `H_T^gamma` is the scalar sum of jet transverse momenta and the leading-photon `pT`.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
