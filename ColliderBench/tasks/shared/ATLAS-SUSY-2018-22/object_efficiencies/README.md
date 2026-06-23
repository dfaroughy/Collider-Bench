# ATLAS-SUSY-2018-22 reinterpretation material

Files used by the BDT signal-region tasks (`atlas-susy-2018-22_sim-BDTGGd1_bdt`
and `atlas-susy-2018-22_sim-BDTGGo1_bdt`).

| File | Description |
|---|---|
| `ZeroLeptonBDT2018.cxx` | Truth-level analysis code defining the BDT input variables. The variable *names* appear in the XML weight files (`Aplanarity`, `MeffNJ`, `dPhiMin3`, …); their *definitions* live here as the `setVarValue("…", …)` calls. |
| `ZeroLepton2018-SRBDT-weight.tar.gz` | TMVA XML weight files, one per BDT signal region (GGd1–4, GGo1–4 × 2 trainings). Extract to `BDTxml/` and load with `TMVA::Reader` or any TMVA-compatible reader. |

## How to use

1. Extract the weight tarball:
   ```
   tar xzf ZeroLepton2018-SRBDT-weight.tar.gz
   ```
2. For each event surviving your BDT signal-region pre-selection, compute the BDT input variables using the definitions in `ZeroLeptonBDT2018.cxx`.
3. Evaluate the appropriate `BDTxml/ZeroLepton2018-SRBDT-{GGd1,GGo1,…}_weight{1,2}.xml` against those inputs to get the BDT score.
4. Histogram the score into the bins specified by the task template.
