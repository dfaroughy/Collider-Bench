> **Paper:** ATLAS-SUSY-2018-22
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 139.0 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `Gl1step_2200_1300_400`
> **Observable:** `BDT-GGo1 score`
> **Signal region:** `BDTGGo1` (BDT search (Section 5.2))

### Task

Implement the search analysis described in **ATLAS-SUSY-2018-22** (arXiv:2010.14293) and use it to predict the binned differential signal yield in BDT-GGo1 score for the benchmark point `Gl1step_2200_1300_400`, in the analysis's **BDTGGo1** signal region, normalized to 139.0 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `Gl1step_2200_1300_400` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, event-selection requirements, and the signal-region cuts that define this analysis. Apply them to your generated events.
3. Histogram the surviving events in BDT-GGo1 score using the bin edges already present in the `results/*.yaml` template (do not modify them).

### Definitions

- BDT-GGo1 score — the value of the boosted-decision-tree classifier trained on the BDT-GGo1 region's discriminating variables (see paper Section 5.2; the BDT-GGo1 pre-selection cuts and target Δm(g̃, χ̃⁰₁) range are listed in Table 7).
- `Gl1step_2200_1300_400` — GlGl one-step via intermediate chargino, $\tilde{g} \to q\bar{q}'\,\tilde{\chi}^{\pm}_{1}$ with $\tilde{\chi}^{\pm}_{1} \to W^{\pm}\,\tilde{\chi}^{0}_{1}$, with $m(\tilde{g}) = 2200$ GeV, $m(\tilde{\chi}^{\pm}_{1}) = 1300$ GeV, $m(\tilde{\chi}^{0}_{1}) = 400$ GeV.

### Provided reinterpretation material

> ATLAS shipped a complete truth-level analysis kit on HepData. Use it as your authoritative source for the SR selection and BDT evaluation: see `../shared/ATLAS-SUSY-2018-22/object_efficiencies/` — in particular `ZeroLeptonBDT2018.cxx` (cuts + BDT input variables) and `ZeroLepton2018-SRBDT-weight.tar.gz` (TMVA XML weights for the trained BDTs).

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
