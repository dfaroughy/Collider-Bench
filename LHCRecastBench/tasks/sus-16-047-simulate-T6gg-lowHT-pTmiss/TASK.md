> **Paper:** CMS-SUS-16-047
> **Energies**: 13 TeV
> **Luminosity**: 35.9 fb⁻¹
> **Task type:** simulation
> **Signal model:** gauge mediated SUSY
> **Signal process:** `T6gg_1750_1650`
> **Histogram observable:** `p_T^miss`

### Instructions

Use MC event generators & detector simulations to replicate the binned event distribution of `p_T^miss` in the SUSY search `CMS-SUS-16-047` (figure 4) for the signal process `T6gg_1750_1650` of the SMS models.
- The observable `pT_miss` is defined as the negative vector sum of the transverse momenta of all candidate objects in the event.
- The signal `T6gg_1750_1650` corresponds to squark pair production decaying into neutralinos at a mass point of `1750 GeV` for the squark and `1650 GeV` for the neutralino.
- The signal region is defined by low-H_T^gamma selection: `H_T^gamma < 2000 GeV`, `EE` and `|∆φ| > 0.3`, where `H_T^gamma` is the scalar sum of all jet momenta and the transverse momentum of the leading photon.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the missing values in the `p_T^miss` histogram with your estimated signal event yields.|
| `analysis/*.py` | Analysis code with event selection and requirements |
| `data/*.root`, `sims/*.dat` | Selected-event files and cards used in the simulations |
| `report.md` | What you accomplished, what you couldn't, and why |
