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

## Pull from registry

```bash
docker pull    ghcr.io/dfaroughy/lhc-bench:latest
podman pull    ghcr.io/dfaroughy/lhc-bench:latest
apptainer pull docker://ghcr.io/dfaroughy/lhc-bench:latest
```

`$LHC_BENCH_IMAGE` overrides the image ref everywhere in the harness (default: `ghcr.io/dfaroughy/lhc-bench:latest`).

## Run a task via the harness

```bash
./scripts/run-agent --config configs/claude.yaml
```

`configs/claude.yaml` already pins `sandbox: podman` and `compute: local`; pass `--sandbox apptainer` on hosts where Apptainer is the only runtime. The `Sandbox` class in [`agent_runtime/sandbox.py`](../agent_runtime/sandbox.py) handles all binds (`ColliderBench`, workspace, OAuth creds, `/cvmfs`, baked `/opt/sim`). See [`configs/CONFIG.md`](../configs/CONFIG.md) for the full schema.

## Run the image standalone

If you're not adopting our `agent_runtime/` harness, mount the benchmark content and drive your own agent CLI directly. Apptainer flavour:

```bash
apptainer exec \
  --bind "$PWD/ColliderBench:$PWD/ColliderBench" \
  --bind "$PWD/runs:$PWD/runs" \
  --bind /cvmfs:/cvmfs:ro \
  lhc-bench.sif \
  <your-agent-command>
```

Docker / Podman flavour:

```bash
docker run --rm \
  -v "$PWD/ColliderBench:$PWD/ColliderBench" \
  -v "$PWD/runs:$PWD/runs" \
  -w "$PWD" \
  ghcr.io/dfaroughy/lhc-bench:latest \
  <your-agent-command>
```

Score the result (works the same way under any engine):

```bash
apptainer exec \
  --bind "$PWD/ColliderBench:$PWD/ColliderBench" \
  --bind "$PWD/runs:$PWD/runs" \
  lhc-bench.sif \
  python -m ColliderBench.Evals.score runs/<your-run>
```

## Pre-built SIF for Apptainer users

```bash
apptainer build lhc-bench.sif docker-daemon://localhost/lhc-bench:latest
export LHC_BENCH_IMAGE=/path/to/lhc-bench.sif
```

Or publish to a registry (ghcr.io / Docker Hub) and `apptainer pull` from anywhere.

## Sim-tool overrides

`bin/simulate` honours env-var overrides for every tool. The image exports these pointing at the baked copies under `/opt/sim/`; the host copy is shadowed so users get the same versions regardless of what their `ColliderBench/tools/sim/` tree contains:

| Env var | Default (in image) |
|---|---|
| `MG5_DIR` | `/opt/sim/MG5_aMC_v3_7_0` |
| `PYTHIA8_DIR` | `/opt/sim/pythia8313` |
| `DELPHES_DIR` | `/opt/sim/delphes` |
| `PROSPINO_DIR` | `/opt/sim/prospino` |

Outside the container, these are unset and `bin/simulate` falls back to `${REPO_ROOT}/ColliderBench/tools/sim/*`.

## Building from source

> **Maintainer-only path.** Public clones do not include the HEP simulator source trees (`ColliderBench/tools/sim/{MG5_aMC_v3_7_0, pythia8313, delphes, prospino}`) — they are gitignored. The image is published on GHCR; almost everyone should just `docker pull` and skip this section. Rebuilding from scratch requires fetching the four upstream sim packages independently and placing them under `ColliderBench/tools/sim/` before `docker build`. That path is currently undocumented because it's not the supported user flow.

The Dockerfile produces an OCI image usable by any OCI-compliant runtime — Docker, Podman, Apptainer, Shifter. Pick the command matching your host:

```bash
# Linux laptop with Docker:
docker build -f docker/Dockerfile -t lhc-bench:latest .

# NERSC Perlmutter (login or compute node):
podman-hpc build -f docker/Dockerfile -t lhc-bench:latest .

# Any host with rootless podman:
podman build -f docker/Dockerfile -t lhc-bench:latest .
```

First build: ~15–20 minutes (conda solve + ~650 MB sim stack COPY + npm install). Final size: ~3.5 GB.

### Prerequisite: pack the conda env

The Dockerfile's `COPY docker/lhc_analysis_env.tar.gz` requires a packed copy of the host's `lhc_analysis` conda env. Generate it once:

```bash
conda activate lhc_analysis
conda pack -n lhc_analysis -o docker/lhc_analysis_env.tar.gz \
    --compress-level 1 --n-threads 8
```

The tarball is gitignored ([`.gitignore`](../.gitignore)); re-pack after env changes.
