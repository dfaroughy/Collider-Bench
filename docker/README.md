# Container images

Two images, matching the code-level decoupling:

| Image | Purpose | Contains |
|---|---|---|
| `lhc-recast-bench` | Vendor-neutral benchmark. Anyone bringing their own agent uses this. | conda env, MG5, Pythia8, Delphes, Prospino |
| `lhc-recast-runtime` | Our opinionated integration. Layered on top of `bench`. | Above + Node + `@google/gemini-cli` + `agent_runtime/`, `agents/`, `configs/` |

Host-mounted at run time (never baked): `LHCRecastBench/` (tasks, tools/CLI, evaluation, bin), OAuth creds (`~/.claude`, `~/.codex`, `~/.gemini`), `/cvmfs` if present (CMSSW etc.), workspace and runs/.

## Build

From the repo root:

```bash
# Benchmark layer (~4 GB, takes several minutes the first time)
docker build -f docker/Dockerfile.bench   -t lhc-recast-bench:latest .

# Runtime layer (fast — just node + gemini-cli + python modules on top)
docker build -f docker/Dockerfile.runtime -t lhc-recast-runtime:latest .
```

## Convert to Apptainer SIF (for HPC use)

```bash
apptainer build lhc-recast-runtime_latest.sif docker-daemon://lhc-recast-runtime:latest
```

Or pull directly from a registry once we publish:

```bash
apptainer build lhc-recast-runtime_latest.sif docker://ghcr.io/<org>/lhc-recast-runtime:latest
```

## Run one task (Apptainer, via our runner)

```bash
export LHC_RECAST_IMAGE=/path/to/lhc-recast-runtime_latest.sif
./scripts/run-agent --config configs/claude_simple.yaml --sandbox apptainer
```

The `ApptainerSandbox` in [`agent_runtime/sandbox.py`](../agent_runtime/sandbox.py) takes care of all binds. If `LHC_RECAST_IMAGE` is unset it defaults to `lhc-recast-runtime:latest` (resolved by the Apptainer engine against its search path).

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
