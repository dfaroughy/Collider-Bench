# Sandbox backends

The benchmark isolates each agent run inside a **sandbox** — a pluggable filesystem-isolation layer. A backend is a single Python class that wraps the inner command in whatever isolation primitives the host provides.

Today there are four built-ins:

| Backend | Status | Typical host | Network | FS isolation |
|---|---|---|---|---|
| `podman` | auto default when available | NERSC Perlmutter, Linux with Podman | open | yes, canonical container image |
| `apptainer` | auto fallback | HPC sites with Apptainer/Singularity | open | yes, canonical container image |
| `bwrap` | opt-in host sandbox | Linux with `bubblewrap` | open | yes (tmpfs + ro-binds), no container image |
| `none`  | always available | macOS, CI, free-range debugging | open | **none — do not use for scored runs** |

Selection order (first match wins): `--sandbox` flag → `sandbox:` config key → `LHC_RECAST_SANDBOX` env var → auto (`podman` → `apptainer` → `none` with a warning).

The runtime captures the choice in `run_info.json["sandbox"]` so provenance is preserved.

## Using a backend

**Via config:**
```yaml
extends: base.yaml
agent: simple
task: sus-16-046_sim-T5Wg
sandbox: podman       # auto | podman | apptainer | bwrap | none
```

**Via CLI flag:**
```bash
python -m agents.simple.run --sandbox none ...
```

**Via environment variable** (useful for CI / one-off):
```bash
LHC_RECAST_SANDBOX=none python -m agents.simple.run ...
```

## Adding a new backend

All backends live in [`agent_runtime/sandbox.py`](sandbox.py). A backend is a subclass of `Sandbox` implementing two methods.

```python
class PodmanSandbox(Sandbox):
    name = "podman"

    def available(self) -> bool:
        return shutil.which("podman-hpc") is not None

    def wrap(
        self,
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=None,
        container_env=None,
        secret_env_names=(),
        home_dir_name=".agent_home",
        home_files=(),
        home_credential_files=(),
    ):
        # Mount workspace rw, benchmark ro (minus papers/ and evaluation/),
        # and each path in extra_ro_binds ro.
        mounts = [
            "-v", f"{workspace}:{workspace}:rw",
            "-v", f"{repo_root/'LHCRecastBench'}:{repo_root/'LHCRecastBench'}:ro",
            "--tmpfs", str(repo_root/"LHCRecastBench"/"papers"),
            "--tmpfs", str(repo_root/"LHCRecastBench"/"evaluation"),
            "--tmpfs", "/tmp",
        ]
        for p in (extra_ro_binds or []):
            mounts += ["-v", f"{p}:{p}:ro"]
        cmd = ["podman-hpc", "run", "--rm", "--workdir", str(workspace),
               *mounts, "lhc-recast:latest", *inner_cmd]
        return cmd, lambda: None    # no cleanup needed
```

Register it:

```python
SANDBOXES = {
    "bwrap":  BwrapSandbox,
    "none":   NoneSandbox,
    "podman": PodmanSandbox,      # ← add here
}
```

Add the backend name to `_ALLOWED_SANDBOX` in [`agent_runtime/config.py`](config.py) so config validation accepts it.

## Contract every backend must honour

1. **`<workspace>` is the only rw path under the repo.** Nothing in `repo_root/` (other than the bound-in workspace) should be writable.
2. **Only the benchmark surfaces needed by the agent are bound into container backends.** Current Podman/Apptainer runs expose `LHCRecastBench/tools/`, `LHCRecastBench/bin/`, and the resolved paper directory read-only. They do not expose `tasks/shared/*/reference/` or `evaluation/`, which would let the agent cheat.
3. **`/tmp` is fresh.** Prevents cross-run leakage and signals.
4. **Every `Path` in `extra_ro_binds` is mounted read-only.** The launcher uses this to re-expose Python packages (`agent_runtime/`, per-agent `runtime/`) hidden by rule #1.
5. **`cleanup()` is safe to call once after the wrapped process exits**, including on failure. Return `lambda: None` if nothing needs restoring.
6. **Network can be left open.** The agent legitimately needs to reach the model API and public data sources (arxiv, HEPData, CMS Open Data). Backends that want egress filtering should document it.

Anything else (PID/IPC namespace unshare, capability drop, user-namespace remap) is backend-specific and up to you.

## Known caveats

- **`bwrap` on NERSC** can't tmpfs `$HOME` (autofs). The agent retains rw access to the user's home directory. Acceptable on single-user trusted setups; run under a scrubbed service account for multi-tenant use.
- **`none`** provides no isolation. The passthrough exists for macOS dev and CI where bwrap isn't available. Never score runs produced with `sandbox: none`.
- **Container backends (Docker, Podman, Shifter)** need an image built with the benchmark toolchain (MadGraph, Pythia, Delphes, conda env). On HPC nodes without Docker, `podman-hpc migrate` is usually required to convert a locally built image.
- **Singularity 3.x compatibility (HPC sites stuck on old singularity).** `ApptainerSandbox` works against both `apptainer` and `singularity`. The wrap path uses `SINGULARITYENV_*` host env vars (instead of the `--env KEY=VAL` flag added in singularity 3.6) and the `--home src:dest` flag (instead of an env var, which singularity special-cases) so the same code path works on singularity 3.0+ and all apptainer versions. Sites still on pre-3.6 singularity (e.g. Rutgers Amarel's `singularity/3.1.0`) can't `singularity pull` from `docker://` reliably either — pre-pull the SIF on a host with newer singularity/apptainer and point the harness at the local file via `LHC_BENCH_IMAGE=/path/to/lhc-bench_latest.sif`.
