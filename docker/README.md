# Container image

One canonical image: `lhc-bench:latest`. Built from
[`docker/Dockerfile`](Dockerfile) (no chain, no overlays).

| Layer | Contents |
|---|---|
| Conda env (`lhc_analysis`) | analysis libs (uproot, awkward, vector, hist, mplhep, hepdata, …) + Pythia8 Python bindings + Node 20 |
| Sim stack (`/opt/sim/*`) | MG5_aMC, Pythia8, Delphes, Prospino — vendored, version-pinned |
| Vendor agent CLIs (`/opt/node-global/bin`) | `claude`, `codex`, `gemini` (npm-installed) |
| Harness (`/app`) | `agent_runtime/`, `agents/`, `configs/` |

Host-mounted at run time (never baked): `ColliderBench/` (tasks, tools, evaluation, bin), OAuth creds (`~/.claude`, `~/.codex`, `~/.gemini`), `/cvmfs` if present, workspace and `runs/`.

## Build

The Dockerfile produces an OCI image usable by any OCI-compliant runtime — Docker, Podman, Apptainer, Shifter. Pick the command matching your host:

```bash
# NERSC Perlmutter (login or compute node):
podman-hpc build -f docker/Dockerfile -t lhc-bench:latest .

# Linux laptop with Docker:
docker build -f docker/Dockerfile -t lhc-bench:latest .

# Any host with rootless podman:
podman build -f docker/Dockerfile -t lhc-bench:latest .
```

First build: ~15-20 minutes (conda solve + ~650 MB sim stack COPY + npm install). Final size: ~3.5 GB.

### Pre-build prerequisite: pack the conda env

The Dockerfile's `COPY docker/lhc_analysis_env.tar.gz` requires a packed copy of the host's conda env. Generate it once:

```bash
conda activate lhc_analysis
conda pack -n lhc_analysis -o docker/lhc_analysis_env.tar.gz \
    --compress-level 1 --n-threads 8
```

(Tarball is gitignored; re-pack after env changes.)

## Pull from registry

```bash
podman pull   ghcr.io/dfaroughy/lhc-bench:latest
docker pull   ghcr.io/dfaroughy/lhc-bench:latest
apptainer pull docker://ghcr.io/dfaroughy/lhc-bench:latest
```

## Run one task (via our runner)

```bash
./scripts/run-agent --config configs/claude_simple.yaml --sandbox podman
```

Use `--sandbox apptainer` on hosts where Apptainer is the only runtime. The `Sandbox` class in [`agent_runtime/sandbox.py`](../agent_runtime/sandbox.py) handles all binds (`ColliderBench`, workspace, OAuth creds, `/cvmfs`, baked `/opt/sim`). `$LHC_BENCH_IMAGE` overrides the image ref (default: `ghcr.io/dfaroughy/lhc-bench:latest`).

## Pre-built SIF for Apptainer users

```bash
apptainer build lhc-bench.sif docker-daemon://localhost/lhc-bench:latest
export LHC_BENCH_IMAGE=/path/to/lhc-bench.sif
```

Or publish to a registry (ghcr.io / Docker Hub) and `apptainer pull` from anywhere.

## Run the benchmark image with your own agent

If you're not using our `agent_runtime/`, mount the benchmark content and drive your own CLI:

```bash
apptainer exec \
  --bind "$PWD/ColliderBench:$PWD/ColliderBench" \
  --bind "$PWD/runs:$PWD/runs" \
  --bind /cvmfs:/cvmfs:ro \
  lhc-bench.sif \
  <your-agent-command>
```

Then score with:

```bash
apptainer exec \
  --bind "$PWD/ColliderBench:$PWD/ColliderBench" \
  --bind "$PWD/runs:$PWD/runs" \
  lhc-bench.sif \
  python -m ColliderBench.evaluation.score runs/<your-run>
```

## Sim-tool overrides

`bin/simulate` honours env-var overrides for every tool. The image exports these pointing at the baked copies under `/opt/sim/`; the host copy is shadowed so users get the same versions regardless of what their `ColliderBench/tools/sim/` tree contains:

| Env var | Default (in image) |
|---|---|
| `MG5_DIR` | `/opt/sim/MG5_aMC_v3_7_0` |
| `PYTHIA8_DIR` | `/opt/sim/pythia8313` |
| `DELPHES_DIR` | `/opt/sim/delphes` |
| `PROSPINO_DIR` | `/opt/sim/prospino` |

Outside the container, these are unset and `bin/simulate` falls back to `${REPO_ROOT}/ColliderBench/tools/sim/*`.
