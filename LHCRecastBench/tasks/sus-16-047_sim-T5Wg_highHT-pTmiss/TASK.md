> **Paper:** CMS-SUS-16-047
> **Energies**: 13 TeV
> **Luminosity**: 35.9 fb⁻¹
> **Task type:** simulation
> **Signal model:** gauge mediated SUSY
> **Signal process:** `T5Wg_1600_100`
> **Histogram observable:** `p_T^miss`

### Instructions

Use MC event generators & detector simulations to replicate the binned event distribution of `p_T^miss` in the SUSY search `CMS-SUS-16-047` (figure 4) for the signal process `T5Wg_1600_100`.
- The observable `p_T^miss` is defined as the negative vector sum of the transverse
momenta of all candidate objects in the event.
- The signal `T5Wg_1600_100` corresponds to gluino pair production in the decaying into gauginos and jets at a mass point of `1600 GeV` for the gluino and mass-degenerate neutralino/chargino of `100 GeV`.
- The signal region is defined by the `high-H_T^gamma` selection: `H_T^gamma > 2000 GeV`, `EE` and `|∆φ| > 0.3`, where `H_T^gamma`is the scalar sum of all jet momenta and the transverse momentum of the leading photon.

### Output requirements

Produce the following artifcats during the run:
| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the missing values in the `p_T^miss` histogram with your estimated signal event yields.|
| `analysis/*.py` | Analysis code with event selection and requirements |
| `data/*.root`, `sims/*.dat` | Selected-event files and cards used in the simulations |
| `report.md` | What you accomplished, what you couldn't, and why |
