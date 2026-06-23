> **Paper:** ATLAS-SUSY-2017-03
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `C1N2_225_0`
> **Observable:** `$E_{\mathrm{T}}^{\mathrm{miss}}$`
> **Signal region:** `SR1LbbMedium` (1 lepton + 2 b-tags channel)

### Task

Implement the search analysis described in **ATLAS-SUSY-2017-03** (arXiv:1812.09432) and use it to predict the binned differential signal yield in $E_{\mathrm{T}}^{\mathrm{miss}}$ for the benchmark point `C1N2_225_0`, in the analysis's **SR1LbbMedium** signal region, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `C1N2_225_0` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in $E_{\mathrm{T}}^{\mathrm{miss}}$ using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- $E_{\mathrm{T}}^{\mathrm{miss}}$ — the magnitude of the missing transverse momentum vector.
- `C1N2_225_0` — electroweak pair production of chargino + neutralino-2, $pp \to \tilde{\chi}^{\pm}_{1} \tilde{\chi}^{0}_{2} \to W^{\pm}\tilde{\chi}^{0}_{1}\, h\,\tilde{\chi}^{0}_{1}$, with $m(\tilde{\chi}^{\pm}_{1}/\tilde{\chi}^{0}_{2}) = 225$ GeV, $m(\tilde{\chi}^{0}_{1}) = 0$ GeV. $\tilde{\chi}^{\pm}_{1}$ and $\tilde{\chi}^{0}_{2}$ are taken mass-degenerate.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
