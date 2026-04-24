# Container images

Two images, matching the code-level decoupling:

| Image | Purpose | Contains |
|---|---|---|
| `lhc-recast-bench` | Vendor-neutral benchmark. Anyone bringing their own agent uses this. | conda env, MG5, Pythia8, Delphes, Prospino |
| `lhc-recast-runtime` | Our opinionated integration. Layered on top of `bench`. | Above + Node + `@google/gemini-cli` + `agent_runtime/`, `agents/`, `configs/` |

Host-mounted at run time (never baked): `LHCRecastBench/` (tasks, tools/CLI, evaluation, bin), OAuth creds (`~/.claude`, `~/.codex`, `~/.gemini`), `/cvmfs` if present (CMSSW etc.), workspace and runs/.

## Build

The Dockerfiles produce OCI images usable by any OCI-compliant runtime —
Docker, Podman, Apptainer, Shifter. NERSC Perlmutter ships `podman-hpc`
(no Docker, no Apptainer); pick whichever command matches your host:

```bash
# On NERSC Perlmutter (login or compute node):
podman-hpc build -f docker/Dockerfile.bench   -t lhc-recast-bench:latest .
podman-hpc build -f docker/Dockerfile.runtime -t lhc-recast-runtime:latest .

# On a laptop with Docker:
docker build -f docker/Dockerfile.bench   -t lhc-recast-bench:latest .
docker build -f docker/Dockerfile.runtime -t lhc-recast-runtime:latest .

# On any host with rootless podman:
podman build -f docker/Dockerfile.bench   -t lhc-recast-bench:latest .
podman build -f docker/Dockerfile.runtime -t lhc-recast-runtime:latest .
```

First bench build: ~10 minutes (conda solve + ~650 MB sim stack COPY).
Runtime build is fast (just Node + npm + python layer).
Final sizes: bench ~2.9 GB, runtime ~3.1 GB.

## Run one task (via our runner)

```bash
./scripts/run-agent --config configs/claude_simple.yaml --sandbox podman
```

(Use `--sandbox apptainer` on hosts where Apptainer is installed instead
of podman.) The Sandbox class in
[`agent_runtime/sandbox.py`](../agent_runtime/sandbox.py) handles all
binds (LHCRecastBench, workspace, OAuth creds, /cvmfs, baked /opt/sim).
`$LHC_RECAST_IMAGE` overrides the image ref (default:
`lhc-recast-runtime:latest` resolved against the runtime's local store).

## Pre-built SIF for Apptainer users (outside NERSC)

```bash
# From a host with both podman and apptainer (e.g. Linux laptop):
apptainer build lhc-recast-runtime.sif \
    docker-daemon://localhost/lhc-recast-runtime:latest
# Then point LHC_RECAST_IMAGE at the .sif file
export LHC_RECAST_IMAGE=/path/to/lhc-recast-runtime.sif
```

Or publish to a registry (ghcr.io, Docker Hub) and `apptainer pull` from
any host that has Apptainer.

## Run the benchmark image with your own agent

If you're not using our `agent_runtime/`, just mount the benchmark content and drive your own CLI:

```bash
apptainer exec \
  --bind "$PWD/LHCRecastBench:$PWD/LHCRecastBench" \
  --bind "$PWD/runs:$PWD/runs" \
  --bind /cvmfs:/cvmfs:ro \
  lhc-recast-bench_latest.sif \
  <your-agent-command>
```

Then score with:

```bash
apptainer exec \
  --bind "$PWD/LHCRecastBench:$PWD/LHCRecastBench" \
  --bind "$PWD/runs:$PWD/runs" \
  lhc-recast-bench_latest.sif \
  python -m LHCRecastBench.evaluation.score runs/<your-run>
```

## Sim-tool overrides

`bin/simulate` honours env-var overrides for every tool. The runtime image
exports these pointing at the baked copies under `/opt/sim/`; the host copy
is shadowed so users get the same versions regardless of what their
`LHCRecastBench/tools/sim/` tree contains:

| Env var | Default (in image) |
|---|---|
| `MG5_DIR` | `/opt/sim/MG5_aMC_v3_7_0` |
| `PYTHIA8_DIR` | `/opt/sim/pythia8313` |
| `DELPHES_DIR` | `/opt/sim/delphes` |
| `PROSPINO_DIR` | `/opt/sim/prospino` |

Outside the container, these are unset and `bin/simulate` falls back to
`${REPO_ROOT}/LHCRecastBench/tools/sim/*` as today.
