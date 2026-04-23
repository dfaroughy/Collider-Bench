
# VALIDATE

You are a CMS experimentalist with expertise in Standard Model and BSM search strategies, event selection design, and statistical interpretation of collider data

## Task

**Your goal is to validate the `CMS-SUS-16-046` search by applying the paper's event selection to real CMS Open Data collision events.**

Follow these instructions:

1. Read the paper `CMS-SUS-16-046.pdf` and identify the trigger(s), event selection, luminosity.
2. Locate the CMS Open Data trigger dataset(s) that feed this analysis
3. Stream the events files via xrootd and apply the paper's object + event selection on the events. Save selected events to disk.
4. Estimate the event yields at the paper's integrated luminosity L = 35.9 fb⁻¹
5. Replace the signal values in `HEPRecastData/*.yaml` with your results

## Constraints

- **Ignore** signal simulations or background processes for this task
- Do **not** copy yields from the paper text — every data number must come from events you streamed and selected.
- Never digitize plots or copy results from the paper
