# `prospino` — NLO cross sections for SUSY pair production

Wraps Prospino 2.1 (Beenakker, Hoepker, Spira, Zerwas). Upstream has no CLI;
this tool renders `prospino_main.f90` from a template, builds on first use,
runs, and parses the output into a stable JSON schema.

Agent-facing tool. Runs inside the agent's sandboxed workspace — all state
lands in `./prospino_scratch/` (workspace-local).

## Commands

```
prospino list-processes                                  # process catalogue (JSON)
prospino help-process <name>                             # required masses + ipart semantics
prospino run --process <name> [OPTIONS]
```

`run` options:

| Flag         | Default | Meaning |
|---|---|---|
| `--process`  | —       | One of the 10 Prospino channels (see catalogue). Required. |
| `--slha`     | —       | SLHA2 spectrum file. Required for any realistic recast. |
| `--collider` | `lhc`   | `lhc` (pp) or `tevatron` (pp̄). |
| `--sqrts`    | `13000` | Beam energy in GeV. Typical: 7000, 8000, 13000, 14000 (LHC); 1960 (Tevatron). |
| `--order`    | `NLO`   | `LO` or `NLO`. |
| `--ipart1`   | `1`     | Final-state particle 1 index. Semantics per-process — see `help-process`. |
| `--ipart2`   | `1`     | Final-state particle 2 index. Semantics per-process — see `help-process`. |
| `--isq-ng`   | `1`     | `1` = use SLHA squark masses (default, what you want); `0` = degenerate average. |

## Process catalogue

| Token | Channel | Key SLHA masses |
|---|---|---|
| `ng` | neutralino/chargino + squark | squarks, gluino, χ̃ |
| `nn` | neutralino/chargino pair | χ̃ⁱ, χ̃ʲ |
| `ns` | neutralino/chargino + slepton | χ̃, slepton |
| `ll` | slepton pair | slepton |
| `sb` | squark-antisquark | squarks, gluino |
| `ss` | squark-squark | squarks, gluino |
| `tb` | stop-antistop | stop1, stop2 |
| `bb` | sbottom-antisbottom | sbottom1, sbottom2 |
| `gg` | gluino pair | gluino, squark |
| `sg` | squark + gluino | squarks, gluino |

For the full `ipart1/2` semantics per channel (which integer picks which
state), call `prospino help-process <name>` — it prints the mapping as JSON.

## Inputs

- **Spectrum:** SLHA2 file via `--slha`. The agent is responsible for
  producing the SLHA that corresponds to the paper's mass spectrum. The tool
  does not invent masses.
- **Process + indices:** pick the Prospino channel and (if it uses ipart)
  the final-state indices.
- **Collider + energy:** defaults match current LHC Run 2/3; switch for
  Tevatron papers or 7/8 TeV analyses.

## Output

One JSON object on stdout:

```json
{
  "process": "gg",
  "collider": "lhc",
  "sqrts_gev": 13000,
  "order": "NLO",
  "ipart1": 1,
  "ipart2": 1,
  "isq_ng": 1,
  "xsec_pb": {"lo": 1.23e-4, "nlo": 2.04e-4, "k_factor": 1.66},
  "slha": "/abs/path/spectrum.slha",
  "runtime_s": 12.7,
  "prospino_dat": "./prospino_scratch/gg-<hash>/prospino.dat",
  "scratch_dir":  "./prospino_scratch/gg-<hash>"
}
```

- `xsec_pb.lo` / `xsec_pb.nlo` are taken from Prospino's free-squark-mass
  (`LO_ms` / `NLO_ms`) columns when non-zero — i.e. the SLHA-driven path —
  and fall back to the degenerate-mass columns otherwise.
- `xsec_pb.k_factor` is Prospino's own column (NLO/LO). When `--order LO`,
  NLO and K are both `0.0`.
- Raw `prospino.dat` with scale variations and all columns stays under
  `scratch_dir` for inspection.

## Scope and limits

- **LHC and Tevatron colliders** only (what Prospino 2.1 supports).
- **CTEQ6** is the built-in PDF. Prospino 2.1 does not expose a runtime PDF
  switch; rebuilding against a different PDF grid would mean re-vendoring.
- **Theory uncertainties** (scale variation, PDF error) are computed by
  Prospino and kept in `prospino.dat`, but not surfaced in the JSON — this
  tool exists to normalize MC samples (σ·BR), not to build systematic bands.
