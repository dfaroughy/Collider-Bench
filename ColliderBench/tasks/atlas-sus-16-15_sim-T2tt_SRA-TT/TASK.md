> **Paper:** ATLAS-SUS-16-15
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T2tt_1000_1`
> **Signal region:** SRA-TT (boosted, doubly top-tagged)
> **Observable:** `E_T^miss`

### Task

Implement the search analysis described in **ATLAS-SUS-16-15** and use it to predict the binned differential signal yield in `E_T^miss` for the benchmark point `T2tt_1000_1`, in **signal region SRA-TT**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T2tt_1000_1` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the SRA preselection, and the cuts that define the **SRA-TT** category (see the paper's signal-region definition tables). Apply them to your generated events.
3. Histogram the surviving events in `E_T^miss` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `E_T^miss` > 950 GeV in the rightmost (overflow) bin.

### Definitions

- `E_T^miss` — the magnitude of the negative vector sum of the transverse momenta of all calibrated reconstructed objects plus the track-based soft term.
- `T2tt_1000_1` — pair-produced top squarks at `m(t̃₁) = 1000 GeV`, each decaying via the `T2tt` simplified-model topology to a top quark and a neutralino LSP at `m(χ̃⁰₁) = 1 GeV` (`t̃₁ → t χ̃⁰₁`, 100 % branching ratio). The large `t̃₁`–`χ̃⁰₁` mass splitting puts this point in the boosted-top regime targeted by SRA.
- **SRA** — the boosted (large mass-splitting) selection: zero leptons, ≥ 4 jets, ≥ 2 b-tagged jets, large `E_T^miss`, the `Δφ(jet, p_T^miss)` and `m_T^{b,min}` / `m_T2^{χ²}` requirements of the paper, and top-tagging built from anti-kt R = 0.8 / R = 1.2 reclustered jets.
- **SRA-TT** — the doubly top-tagged category of SRA, in which **both** reclustered R = 1.2 jets are top-tagged (leading and subleading reclustered-jet masses both in the top-mass window). This is the most boosted SRA category, distinguished from SRA-TW and SRA-T0 by the mass of the second reclustered jet.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
