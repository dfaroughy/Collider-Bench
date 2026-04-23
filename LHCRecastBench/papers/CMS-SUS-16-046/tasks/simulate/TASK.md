# SIMULATE

You are a CMS experimentalist with expertise in Standard Model and BSM search strategies, event selection design, and statistical interpretation of collider data

## Task

**Your goal is to generate from scratch events from the BSM signal processes of `CMS-SUS-16-046` and apply the paper's event selection to the simulated data.**

Follow these instructions:

1. Read the paper `CMS-SUS-16-046.pdf` and identify the signal models, production processes, decay chains, event selection, luminosity.
2. For each signal benchmark `T5Wg` and `TChiWg`:
   a. Identify the UFO model
   b. Generate parton-level events with `MadGraph5_aMC@NLO`. Write the proc card explicitly
   c. Shower the events with `Pythia8`
   d. Apply detector effects to the events with `Delphes` after modifying accordingly the CMS card
3. Apply the paper's object + event selection on the output events and save to disk.
3. Estimate the event yields at the paper's integrated luminosity L = 35.9 fb⁻¹
4. Replace the signal values in `HEPRecastData/*.yaml` with your results

## Constraints

- **Ignore** data or background processes for this task
- Do **not** copy yields from the paper text — every data number must come from your simulations and codes.
- Never digitize plots or copy results from the paper
- Never digitize plots or copy results from the paper
