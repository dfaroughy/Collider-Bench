> **Paper:** CMS-SUS-16-046
> **Energies**: 13 TeV
> **Luminosity**: 35.9 fb⁻¹
> **Task type:** validation
> **Data type:** observed
> **Target histogram:** `S_T^gamma`

### Instructions

Stream the datasets in the CMS open data portal to reproduce the event histogram `S_T^gamma` (figure 6) for the Observed data in the signal region.
- The observable `S_T^gamma` is defined by the scalar sum of missing transverse momentum `pT_miss` and all photon transverse momenta in the event.
- The observed data was gathered during 2016 using the single-photon HLT trigger from the run 2.
- The signal region is defined by preselections and the kinematic cuts: `pT_miss > 300 GeV`,  `MT(gamma, \vec pT_miss) > 100 GeV` and `S_T^gamma > 300 GeV`.

### Output requirements
| File | Purpose |
|---|---|
| `results/*.yaml` | filled signal histogram |
| `datasets.yaml` | All samples used: process name, role, recid, n_generated, file URLs, status if blocked. |
| `analysis/*.py` | Analysis code with Event selections and requirements |
| `report.md` | What you accomplished, what you couldn't, and why |
