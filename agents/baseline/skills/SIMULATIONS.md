# Signal Simulation

Only needed for signal processes that aren't in CMS Open Data. **Never simulate backgrounds.** The chain is:

**MadGraph5** (parton-level) → **Pythia8** (shower/hadronization) → **Delphes** (fast detector simulation)

Archive every run under `sim/PROC_<signal_name>/run_<tool>_NNN/` — keep proc cards, run cards, logs, and outputs. Record each generated sample in `datasets.yaml`.

Start with `bin/simulate info` to see the available models and detector cards.

## Step 1 — Identify the model

Read the paper to determine the signal model: particle content, production mechanism, decay chain, and mass spectrum. Then find the matching UFO **before writing any proc card** — the model name is the first line of the proc card, so you need it nailed down first.

Check the bundled models first; fall back to FeynRules for anything BSM-specific.

Three general-purpose UFOs are bundled in `tools/sim/MG5_aMC_v3_7_0/models/`:

| Model | Contains | Use for |
|---|---|---|
| `sm` | Standard Model | SM processes |
| `MSSM_SLHA2` | Full MSSM | Stops, gluinos, neutralinos, charginos (supply SLHA param card) |
| `SMEFTsim` | SMEFT v3 Warsaw basis, **general flavor, α-scheme** | Dimension-6 operator deviations from SM. Enable any Wilson coefficient in the param card. |

The bundled `SMEFTsim` is one variant of SMEFTsim v3: **general flavor** (no flavor-symmetry assumption — any Wilson coefficient can be set independently) and **α-scheme** ({α_em, m_Z, G_F} as EW inputs, the CMS convention). If the paper uses a different SMEFTsim scheme (U35, MFV, top, topU3l, or MwScheme), fetch the corresponding tarball from FeynRules:

```bash
bin/feynrules fetch SMEFT --file SMEFTsim_U35_alphaScheme_UFO.tar.gz --extract --dest sim/models
bin/feynrules info SMEFT   # lists all 10 variants
```

To inspect a model's particle content, read its `particles.py`:
```bash
cat tools/sim/MG5_aMC_v3_7_0/models/<model>/particles.py
```

**For GMSB cascades (χ̃ → γ/W/Z + gravitino), use MSSM_SLHA2 + an SLHA decay
table rather than a gravitino UFO.** Generate production only, then hand
Pythia an SLHA block with the desired branching ratio — no goldstino matrix
element is needed. Example:

```
DECAY 1000022 1e-3          # χ̃₁⁰ width (arbitrary non-zero)
    1.0   2   22   1000039  # 100% BR to photon + gravitino
```
enabled in Pythia with `SLHA:useDecayTable = on`.

For anything else BSM (heavy neutrinos, leptoquarks, vector-like quarks,
Z′, 2HDM, DM simplified models…) query the FeynRules wiki database with
`bin/feynrules`:

```bash
bin/feynrules list --search "vector-like quark"       # substring search across all categories
bin/feynrules list --category NLOModels               # browse one category (7 total)
bin/feynrules info <model>                            # list all attachments on the model page
bin/feynrules fetch <model> --extract --dest sim/models  # download UFO tarball(s) and unpack
```

`fetch` defaults to UFO-format archives; use `--file <name>` for a specific
attachment or `--all` for everything on the model page. Once extracted into
`sim/models/<model>/`, point the MG5 proc card at it with
`import model sim/models/<model>/<ufo_dir>`. See `TOOLS.md` for details.

## Step 2 — Generate parton-level events (MadGraph5)

Write a proc card specifying the process, model, and parameters:

```bash
cat > proc_card.dat << 'EOF'
import model MSSM_SLHA2
generate p p > t1 t1~
output signal_stop
launch signal_stop
set nevents 50000
set ebeam1 6500
set ebeam2 6500
done
EOF
bin/simulate mg5 proc_card.dat
```

Add partonic cuts in `run_card.dat` if needed. For parallel generation, add `set nb_core N` at the top of the proc card. Output: LHE events under `sim/PROC_<signal_name>/run_mg5_NNN/`.

## Step 3 — Shower and hadronize (Pythia8)

```bash
bin/simulate pythia8 sim/PROC_<signal_name>/run_mg5_001/.../unweighted_events.lhe --parallel 32
```

Splits the LHE file, showers each chunk in parallel, merges HepMC output. Output: `sim/PROC_<signal_name>/run_pythia8_NNN/events.hepmc`.

## Step 4 — Detector simulation (Delphes)

```bash
bin/simulate delphes sim/PROC_<signal_name>/run_pythia8_001/events.hepmc --card cms --parallel 32
```

Card aliases: `cms`, `atlas`, `cms_pileup`. Output: `sim/PROC_<signal_name>/run_delphes_NNN/delphes_output.root`.

## Reading Delphes output

Delphes ROOT files have a **different format** from NanoAOD — inspect branches with `uproot` before writing selection code.
