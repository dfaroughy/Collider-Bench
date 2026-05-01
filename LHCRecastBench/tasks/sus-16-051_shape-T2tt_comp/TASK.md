> **Paper:** CMS-SUS-16-051
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (shape-only)
> **Signal benchmark:** `T2tt_800_400`
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-051** and use it to predict the normalized event distribution (shape) of `E_T^miss` for the signal benchmark point `T2tt_800_400`, in the analysis's **compressed signal-region selection**.

You should:

1. Generate `T2tt_800_400` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the compressed-selection signal-region cuts that define this analysis (Section 5 / Table 3 of the paper). Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `E_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all reconstructed particle-flow candidates in the event.
- `T2tt_800_400` — pair-produced top squarks at `m(t̃) = 800 GeV`, each decaying via the `T2tt` simplified-model topology to a top quark and a neutralino at `m(χ̃⁰₁) = 400 GeV`. 
- Compressed signal-region selection — the search's secondary categorisation (Table 3 in the paper), designed to enhance sensitivity to compressed-spectrum stop decays via an ISR-tagged event topology.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted relative signal bin contents; the scorer ignores overall normalization. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |


### Important

> The goal of this task is to predict the signal shape from your own simulation and analysis pipeline. Do not extract or digitize the target bin values from the paper's figures, tables, or HEPData record.
