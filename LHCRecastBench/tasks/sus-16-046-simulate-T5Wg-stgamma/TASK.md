> **Paper:** CMS-SUS-16-046
> **Energies**: 13 TeV
> **Luminosity**: 35.9 fb⁻¹
> **Task type:** simulation
> **Signal model:** gauge mediated SUSY
> **Signal process:** `T5Wg_1750_1700`
> **Histogram observable:** `S_T^gamma`

### Instructions

Use MC event generators & detector simulations to replicate the binned event distribution of `S_T^gamma` in the SUSY search `CMS-SUS-16-046` (figure 6) for the signal process `T5Wg_1750_1700` of the SMS models.
- The observable `S_T^gamma` is defined by the scalar sum of missing transverse momentum `pT_miss` and all photon transverse momenta in the event.
- The signal `T5Wg_1750_1700` corresponds to pair produced gluinos with mass `1750 GeV`, decaying into mass-degenerate winos (NLSP) with a mass of `1700 GeV`.
- The signal region is defined by preselections and the kinematic cuts: `pT_miss > 300 GeV`,  `MT(gamma, \vec pT_miss) > 100 GeV` and `S_T^gamma > 300 GeV`.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the missing values in the `S_T^gamma` histogram with your estimated signal event yields.|
| `analysis/*.py` | Analysis code with event selection and requirements |
| `data/*.root`, `sims/*.dat` | Selected-event files and cards used in the simulations |
| `report.md` | What you accomplished, what you couldn't, and why |
