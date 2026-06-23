> **Paper:** ATLAS-SUSY-2018-22
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 139.0 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `Sq_850_825`
> **Observable:** `$m_{\mathrm{eff}}$`
> **Signal region:** `MBC` (multi-bin search (Section 5.1))

### Task

Implement the search analysis described in **ATLAS-SUSY-2018-22** (arXiv:2010.14293) and use it to predict the binned differential signal yield in $m_{\mathrm{eff}}$ for the benchmark point `Sq_850_825`, in the analysis's **MBC** signal region, normalized to 139.0 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `Sq_850_825` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in $m_{\mathrm{eff}}$ using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- $m_{\mathrm{eff}}$ — the scalar sum of $E_{\mathrm{T}}^{\mathrm{miss}}$ and the $p_{\mathrm{T}}$ of all jets passing the analysis selection.
- `Sq_850_825` — SqSq direct (compressed mass spectrum), $\tilde{q} \to q\,\tilde{\chi}^{0}_{1}$, with $m(\tilde{q}) = 850$ GeV, $m(\tilde{\chi}^{0}_{1}) = 825$ GeV (compressed).

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
