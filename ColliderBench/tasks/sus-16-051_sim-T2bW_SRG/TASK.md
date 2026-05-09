> **Paper:** CMS-SUS-16-051
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 35.9 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T2bW_900_50`
> **Signal region:** SR-G (`tmod > 10`, `N_J ≥ 4`, `M_lb ≤ 175 GeV`)
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **CMS-SUS-16-051** and use it to predict the binned differential signal yield in `E_T^miss` for the benchmark point `T2bW_900_50`, in **signal region SR-G** of the analysis's standard (heavy-stop, mass-split) categorisation, normalized to 35.9 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T2bW_900_50` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, baseline event-selection requirements, and the SR-G cuts that define this analysis (Section 5 / Table 2 of the paper). Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- `E_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all reconstructed particle-flow candidates in the event.
- `T2bW_900_50` — pair-produced top squarks at `m(t̃) = 900 GeV`, each decaying via the `T2bW` simplified-model topology `t̃ → b χ̃⁺₁`, `χ̃⁺₁ → W χ̃⁰₁`, with `m(χ̃⁰₁) = 50 GeV` and the intermediate chargino mass fixed by the CMS convention `m(χ̃⁺₁) = (m(t̃) + m(χ̃⁰₁)) / 2`.
- **SR-G** — one of the standard (heavy-stop, mass-split) signal regions in Table 2 of the paper, defined on top of the baseline selection by `tmod > 10`, `N_J ≥ 4`, and `M_lb ≤ 175 GeV`. The standard categorisation is the search's primary one, as opposed to the compressed-spectrum selection.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
