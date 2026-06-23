> **Paper:** ATLAS-SUSY-2018-22
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 139.0 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `Gl_2400_400`
> **Observable:** `$E_{\mathrm{T}}^{\mathrm{miss}}/\sqrt{H_{\mathrm{T}}}$`
> **Signal region:** `MBGGd` (multi-bin search (Section 5.1))

### Task

Implement the search analysis described in **ATLAS-SUSY-2018-22** (arXiv:2010.14293) and use it to predict the binned differential signal yield in $E_{\mathrm{T}}^{\mathrm{miss}}/\sqrt{H_{\mathrm{T}}}$ for the benchmark point `Gl_2400_400`, in the analysis's **MBGGd** signal region, normalized to 139.0 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `Gl_2400_400` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in $E_{\mathrm{T}}^{\mathrm{miss}}/\sqrt{H_{\mathrm{T}}}$ using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- $E_{\mathrm{T}}^{\mathrm{miss}}/\sqrt{H_{\mathrm{T}}}$ — the missing-transverse-momentum significance defined as $E_{\mathrm{T}}^{\mathrm{miss}}$ divided by the square root of the scalar sum of jet transverse momenta.
- `Gl_2400_400` — GlGl direct, $\tilde{g} \to q\bar{q}\,\tilde{\chi}^{0}_{1}$, with $m(\tilde{g}) = 2400$ GeV, $m(\tilde{\chi}^{0}_{1}) = 400$ GeV.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
