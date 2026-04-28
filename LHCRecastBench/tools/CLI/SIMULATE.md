# Simulation stack — MadGraph5 / Pythia8 / Delphes

**Purpose.** Generate signal events end-to-end (parton-level → parton
shower → detector simulation) when no matching MC is available in CMS
Open Data. The CLIs are simple enough that there's no wrapper; you call
them directly. `bin/simulate info` lists installed models and cards;
`bin/simulate --doc` shows this page.

**When to use.** For BSM signals the paper defines but nobody has
produced for Open Data (SUSY benchmark points, EFT signals, etc.). For
SM backgrounds you usually want `cms-opendata` instead — save compute.

---

## Quick discovery

```bash
bin/simulate info        # installed UFO models + Delphes cards + tool paths
bin/simulate --doc       # this page
```

The relevant env vars (set inside the bench image; fall back to the
in-repo paths otherwise):

| Var | Default in image | Contains |
|---|---|---|
| `MG5_DIR` | `/opt/sim/MG5_aMC_v3_7_0` | `bin/mg5_aMC`, `models/`, `Template/` |
| `PYTHIA8_DIR` | `/opt/sim/pythia8313` | `share/Pythia8/xmldoc` (settings DB), examples |
| `DELPHES_DIR` | `/opt/sim/delphes` | `DelphesHepMC2`, `DelphesHepMC3`, `cards/` |

The conda env `cms_analysis` provides the Python bridge: `import pythia8`
and `import pyhepmc` resolve against the same Pythia/HepMC the CLIs use.

---

## MadGraph5 — parton level

`mg5_aMC` reads a process card (a script of MG5 commands) and runs the
whole chain. The card already encodes everything: process, output dir,
event count, beam energy.

```bash
# Minimal proc_card.dat for a SUSY signal:
cat > proc_card.dat <<'EOF'
import model MSSM_SLHA2
generate p p > go go, (go > q q n1)
output mg5_T5Wg
launch mg5_T5Wg
    set nevents 50000
    set ebeam1 6500
    set ebeam2 6500
    set use_syst False
    done
EOF

mg5_aMC proc_card.dat
```

Output lands in the directory named in `output` — events at
`mg5_T5Wg/Events/run_01/unweighted_events.lhe.gz`. Decompress with
`gunzip` before passing to Pythia.

**Multi-core MG5.** Set `nb_core = N` inside the **run_card** (edited
through the launch dialog or by hand in `mg5_T5Wg/Cards/run_card.dat`).
This is an MG5-internal setting; there's no CLI flag.

**Provenance recommendation.** Land outputs under `sim/run_mg5_NNN/`
(numbered) and copy the proc_card next to them — makes debugging and
report-writing easier later.

---

## Pythia8 — parton shower + hadronization

There is **no Pythia8 CLI**; you write a small Python driver against
the `pythia8` module.

### Single-process driver (~30 lines)

```python
# shower.py — usage: python shower.py events.lhe events.hepmc
import os, sys
import pythia8, pyhepmc

lhe_in, hepmc_out = sys.argv[1], sys.argv[2]

# Count LHE events upfront. Pythia 8.312's `info.atEndOfFile()` never
# flips to True for short LHEs (known bug), so the natural
# `while True: ... break-on-eof` loop hangs after the last event.
# Iterating exactly N times sidesteps the bug.
with open(lhe_in) as f:
    n_lhe = sum(1 for line in f if "<event>" in line)

xmldir = os.environ["CONDA_PREFIX"] + "/share/Pythia8/xmldoc"
p = pythia8.Pythia(xmldir, False)
p.readString(f"Beams:LHEF = {lhe_in}")
p.readString("Beams:frameType = 4")     # read beams from LHE header
p.readString("Tune:pp = 14")             # CMS UE tune CP1
p.readString("Print:quiet = on")
p.init()

n_ok = 0
with pyhepmc.open(hepmc_out, "w") as writer:
    for _ in range(n_lhe):
        if not p.next():
            continue
        n_ok += 1
        evt = pyhepmc.GenEvent(pyhepmc.Units.GEV, pyhepmc.Units.MM)
        evt.event_number = n_ok
        # Populate vertices/particles from p.event — see pyhepmc docs
        # for full conversion; the simplest pattern walks p.event[i] and
        # adds GenParticle objects to a single GenVertex on the event.
        writer.write(evt)
print(f"showered {n_ok} events → {hepmc_out}")
```

For 50k events on one core this is typically ~3-10 minutes of Pythia
generation + writing. Fine for a single benchmark point.

### Multi-CPU Pythia (split + N drivers + concatenate)

Pythia drivers are independent processes — each one initialises its own
Pythia instance and consumes a slice of the LHE file. The pattern is:

1. **Split** the LHE into N event-balanced chunks (event blocks are
   delimited by `<event>...</event>`; the LHE header/footer must be
   prepended/appended to each chunk).
2. **Run** N copies of `shower.py` in parallel, each on its own chunk.
3. **Concatenate** the resulting HepMC3 files.

```bash
# split.py — splits an LHE into $N chunks under chunks/chunk_NNN.lhe
python - "$LHE" chunks "$N" <<'PY'
import sys
from pathlib import Path
lhe, outdir, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
outdir.mkdir(exist_ok=True, parents=True)

events, header, footer, current, in_evt = [], [], "", [], False
with open(lhe) as f:
    for line in f:
        if "<event>" in line:
            in_evt, current = True, [line]
        elif "</event>" in line:
            current.append(line); events.append(current); in_evt = False
        elif in_evt:
            current.append(line)
        else:
            header.append(line)

# Split header/footer at </LesHouchesEvents>
for i, line in enumerate(header):
    if "</LesHouchesEvents>" in line:
        footer = "".join(header[i:]); header = header[:i]; break
header = "".join(header)

per = max(1, len(events) // n)
for i in range(n):
    chunk = events[i*per : (i+1)*per] if i < n - 1 else events[i*per:]
    if not chunk: break
    with open(outdir / f"chunk_{i:03d}.lhe", "w") as out:
        out.write(header)
        for evt in chunk: out.writelines(evt)
        out.write(footer)
PY

# Run N drivers in parallel via xargs (one process per chunk).
ls chunks/chunk_*.lhe | xargs -P "$N" -I{} bash -c '
  CHUNK="{}"
  HEPMC="${CHUNK%.lhe}.hepmc"
  python shower.py "$CHUNK" "$HEPMC" > "${CHUNK%.lhe}.log" 2>&1
'

# Concatenate HepMC3 chunks (drop intermediate START/END markers).
python - chunks events.hepmc <<'PY'
import sys
from pathlib import Path
chunks_dir, out = Path(sys.argv[1]), sys.argv[2]
total = 0
with open(out, "w") as o:
    o.write("HepMC::Version 3.03.01\n")
    o.write("HepMC::Asciiv3-START_EVENT_LISTING\n")
    for f in sorted(chunks_dir.glob("chunk_*.hepmc")):
        with open(f) as i:
            for line in i:
                if line.startswith("HepMC::"): continue
                if line.startswith("E "): total += 1
                o.write(line)
    o.write("HepMC::Asciiv3-END_EVENT_LISTING\n")
print(f"merged {total} events into {out}")
PY
```

On a 32-core node, 50k events finishes in ~30-90 s wall-clock instead
of ~10 min single-core. The CMS-SUS-16-047 reference run
(`sims/generate_T5Wg.py` in any successful claude_opus run) is a
working example of this pattern and produced 50k events in well under
5 minutes.

---

## Delphes — fast detector simulation

Three positional arguments: card, output ROOT file, input HepMC. Pick
the binary by HepMC version.

```bash
# HepMC3 ASCII (modern, what pyhepmc writes by default):
DelphesHepMC3 "$DELPHES_DIR/cards/delphes_card_CMS.tcl" out.root events.hepmc

# HepMC2 (legacy):
DelphesHepMC2 "$DELPHES_DIR/cards/delphes_card_CMS.tcl" out.root events.hepmc

# Detect version from file header:
if head -1 events.hepmc | grep -q "HepMC::Version 3"; then
    DELPHES=DelphesHepMC3
else
    DELPHES=DelphesHepMC2
fi
"$DELPHES" "$DELPHES_DIR/cards/delphes_card_CMS.tcl" out.root events.hepmc
```

Available cards under `$DELPHES_DIR/cards/` — `bin/simulate info`
lists them. Common ones: `delphes_card_CMS.tcl`,
`delphes_card_ATLAS.tcl`, `delphes_card_CMS_PileUp.tcl`.

**Multi-CPU Delphes.** Same split-merge idea as Pythia, but with
`hadd` to merge ROOT files:

```bash
# split HepMC by event count, run N DelphesHepMC3 in parallel, then merge:
hadd -f out.root chunks/chunk_*.root
```

Delphes is fast — usually only worth parallelizing for >100k events.

---

## Output ROOT schema

Delphes output is **not** NanoAOD-compatible. Branches are Delphes
classes (`Jet`, `Photon`, `MissingET`, `Track`, `Tower`, ...). Read
with `uproot`:

```python
import uproot
f = uproot.open("out.root")
tree = f["Delphes"]
n = tree.num_entries
# Each branch is a jagged array of physics objects per event.
jet_pt = tree["Jet/Jet.PT"].array()
met    = tree["MissingET/MissingET.MET"].array()
```

For analyses that want NanoAOD shape, cms-opendata is the better
starting point (real data + matched MC, already nanoAOD-formatted).

---

## Provenance recommendation

Keep generation outputs alongside their inputs so the run is
reproducible and auditable:

```
workspace/sim/run_mg5_001/
    proc_card.dat
    mg5_T5Wg/Events/run_01/unweighted_events.lhe   # decompressed
    mg5.log

workspace/sim/run_pythia8_001/
    shower.py                # the driver you wrote
    events.hepmc
    pythia8.log

workspace/sim/run_delphes_001/
    delphes_card.tcl         # copy of the card you used
    delphes_output.root
    delphes.log
```

Numbered run dirs avoid clobbering when you iterate. Cite these paths
in the final report so reviewers can re-run any single step.
