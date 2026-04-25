> **Paper:** CMS-SUS-16-047
> **Energies**: 13 TeV
> **Luminosity**: 35.9 fb⁻¹
> **Task type:** validation
> **Data type:** observed data
> **Histogram observable:** `p_T^miss`

### Instructions

Reproduce the observed-data binned event distribution of `p_T^miss` in the SUSY search `CMS-SUS-16-047` (figure 4) using CMS Open Data.
- The observable `pT_miss` is defined as the negative vector sum of the transverse momenta of all candidate objects in the event.
- The signal region is defined by low-H_T^gamma selection: `H_T^gamma < 2000 GeV`, `EE` and `|∆φ| > 0.3`, where `H_T^gamma` is the scalar sum of all jet momenta and the transverse momentum of the leading photon.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the missing values in the `p_T^miss` histogram with observed event counts.|
| `analysis/*.py` | Analysis code with event selection and requirements |
| `data/*.root` | Selected-event files from CMS Open Data |
| `report.md` | What you accomplished, what you couldn't, and why |
