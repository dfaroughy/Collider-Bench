> **Paper:** CMS-SUS-16-051
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation (yield-only)
> **Signal benchmark:** `T2tt_1000_50`
> **Signal region:** SR-G (`tmod > 10`, `N_J ≥ 4`, `M_lb ≤ 175 GeV`)
> **Observable:** total signal-region yield

### Task

Implement the search analysis described in **CMS-SUS-16-051** and use it to predict the total number of signal events for the benchmark point `T2tt_1000_50` in **signal region SR-G** of the analysis's standard (heavy-stop, mass-split) categorisation, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T2tt_1000_50` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, baseline event-selection requirements, and the SR-G cuts that define this analysis (Section 5 / Table 2 of the paper). Apply them to your generated events.
3. Sum the surviving event yield over SR-G and fill the single `null` value already present in the `results/*.yaml` template.

### Definitions

- Total signal-region yield — the expected number of `T2tt_1000_50` events passing the SR-G selection after all event weights and normalization factors have been applied.
- `T2tt_1000_50` — the signal benchmark point specified in the task metadata.
- **SR-G** — one of the standard (heavy-stop, mass-split) signal regions in Table 2 of the paper, defined on top of the baseline selection by `tmod > 10`, `N_J ≥ 4`, and `M_lb ≤ 175 GeV`. The standard categorisation is the search's primary one, as opposed to the compressed-spectrum selection.
- `E_T^miss` — the distribution observable used in the corresponding binned simulation task; this yield-only task scores only the integrated SR-G yield.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the single `null` value with your predicted total signal-region yield. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the total signal-region yield from your own simulation and analysis pipeline. Do not extract or digitize the target yield from the paper's figures, tables, HEPData record, or elsewhere.
