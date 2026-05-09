> **Paper:** CMS-SUS-16-047
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** validation
> **Data source:** CMS Open Data (2016, single-photon HLT trigger, Run 2)
> **Observable:** `p_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-047** and use it to reproduce the binned distribution of `p_T^miss` in the **observed-data** sample, in the **high-H_T^gamma signal region**, integrated to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Identify and stream the appropriate 2016 single-photon CMS Open Data datasets covered by this analysis.
2. Read the paper to determine the object identification, event-selection requirements, and the high-H_T^gamma signal-region cuts that define this analysis. Apply them to the data sample.
3. Histogram the surviving events in `p_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `p_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all candidate objects in the event.
- High-H_T^gamma signal region — the higher-H_T^gamma branch of the analysis's two H_T^gamma-categorised signal regions, where `H_T^gamma` is the scalar sum of jet transverse momenta and the leading-photon `pT`.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with the observed event counts from your analysis. |
| `datasets.yaml` | All datasets used: process / role / Open Data record id / file URLs / status if any are blocked. |
| `analysis/*.py` | Event-selection code, runnable on the streamed data sample. |
| `data/*.root` | Selected-event files extracted from CMS Open Data. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *reproduce* the observed distribution from real data running through your own analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
