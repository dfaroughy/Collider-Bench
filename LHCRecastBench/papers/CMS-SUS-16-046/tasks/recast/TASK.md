# RECAST

You are a CMS experimentalist with expertise in Standard Model and BSM search strategies, event selection design, and statistical interpretation of collider data.

## Task

Reproduce the `CMS SUS-16-046` search end-to-end. Both **validate** against CMS Open Data collision events *and* **simulate** the BSM signal processes, then fill `HEPRecastData/` with both the data yields and the signal yields.

## What you must do

1. Read the paper (`bin/read-paper papers/CMS-SUS-16-046.pdf`): event selection, observable binning, luminosity, signal benchmarks.
2. **Data side.** Locate the CMS Open Data trigger sample(s) matching the paper's signal region (photon + MET, 35.9 fb⁻¹ at 13 TeV). Apply the paper's object and event selection. Fill the data column(s) if the template requires it.
3. **Signal side.** For each signal benchmark listed in the template (`T5Wg_*`, `TChiWg_*`):
   a. Get a UFO model implementing the relevant BSM scenario (SMS/MSSM). Use `bin/feynrules` if necessary.
   b. Generate parton-level events with MadGraph5_aMC@NLO (`bin/simulate mg5`).
   c. Shower with Pythia8 (`bin/simulate pythia8`).
   d. Detector-simulate with Delphes using the CMS card (`bin/simulate delphes`).
   e. Apply the same paper selection as in (2) to the reconstructed events.
4. Normalize to the paper's luminosity: `N = σ · L · (N_selected / N_generated)`. Apply K-factors the paper specifies.
5. Fill every null value in `HEPRecastData/*.yaml`. Document provenance and caveats in `report.md`.

## What to produce

| File | Purpose |
|---|---|
| `HEPRecastData/*.yaml` | All columns filled (data, backgrounds where present, signals). |
| `datasets.yaml` | Every sample used (Open Data or locally generated): name, role, recid/path, cross section, n_generated. |
| `analysis.py` | The selection code; runnable via `bin/run-analysis`. |
| `data/*.root` | Selected events. |
| `report.md` | End-to-end write-up, including known caveats. |

## Constraints

- No peeking at `HEPRecastData_reference/*.yaml` or any file under `artifacts/` (not present in your sandbox).
- Run `bin/run-analysis` synchronously via Bash.
- Every number in `HEPRecastData/*.yaml` must trace to code you ran, not to values copied from the paper text or HEPData.
