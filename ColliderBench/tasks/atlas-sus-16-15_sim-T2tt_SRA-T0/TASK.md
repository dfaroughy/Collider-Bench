> **Paper:** ATLAS-SUS-16-15
> **Centre-of-mass energy:** 13 TeV
> **Luminosity:** 36.1 fb⁻¹
> **Task type:** simulation
> **Signal benchmark:** `T2tt_1000_1`
> **Signal region:** SRA-T0 (boosted, single top-tag)
> **Observable:** `m_T2^{χ²}`

### Task

Implement the search analysis described in **ATLAS-SUS-16-15** and use it to predict the binned differential signal yield in `m_T2^{χ²}` for the benchmark point `T2tt_1000_1`, in **signal region SRA-T0**, normalized to 36.1 fb⁻¹ at √s = 13 TeV.

You should:

1. Generate `T2tt_1000_1` events using a matrix-element generator + parton shower + detector simulation chain of your choice.
2. Read the paper to determine the object identification, the SRA preselection, and the cuts that define the **SRA-T0** category (see the paper's signal-region definition tables). Apply them to your generated events.
3. Histogram the surviving events in `m_T2^{χ²}` using the bin edges already present in the `results/*.yaml` template (do not modify them). Place all events with `m_T2` > 950 GeV in the rightmost (overflow) bin.

### Definitions

- `m_T2^{χ²}` — the χ²-variant of the stransverse mass `m_T2`. Two top-quark candidates are reconstructed from the event's jets via a χ² jet-assignment that minimises agreement with the top- and W-boson masses; `m_T2` is then computed from the two top candidates and `p_T^miss`, taking the invisible particles to be massless. See the paper for the precise definition.
- `T2tt_1000_1` — pair-produced top squarks at `m(t̃₁) = 1000 GeV`, each decaying via the `T2tt` simplified-model topology to a top quark and a neutralino LSP at `m(χ̃⁰₁) = 1 GeV` (`t̃₁ → t χ̃⁰₁`, 100 % branching ratio). The large `t̃₁`–`χ̃⁰₁` mass splitting puts this point in the boosted-top regime targeted by SRA.
- **SRA** — the boosted (large mass-splitting) selection: zero leptons, ≥ 4 jets, ≥ 2 b-tagged jets, large `E_T^miss`, the `Δφ(jet, p_T^miss)` and `m_T^{b,min}` / `m_T2^{χ²}` requirements of the paper, and top-tagging built from anti-kt R = 0.8 / R = 1.2 reclustered jets.
- **SRA-T0** — the single-top-tag category of SRA, in which the leading reclustered R = 1.2 jet is top-tagged but the **subleading** reclustered R = 1.2 jet has a low mass (below the top/W window). This is distinguished from SRA-TT (both reclustered jets top-tagged) and SRA-TW (top + W) by the mass of the second reclustered jet.

### Output requirements

| Artifact | Purpose |
|---|---|
| `results/*.yaml` | Fill the `null` bin values with your predicted signal yields. |
| `analysis/*.py` | Event-selection code, runnable on your generated sample(s). |
| `data/*.root`, `sims/*.dat` | Selected-event files + generator / detector cards. |
| `report.md` | What you produced, the methodological choices you made, and where you deviated from the paper (if at all). |

### Important

> The goal is to *predict* the signal event distribution from your own simulation and analysis pipeline. Do not extract bin values from the paper's figures, tables, HEPData record, or elsewhere.
