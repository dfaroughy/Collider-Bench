# Sandbox backends

The benchmark isolates each agent run inside a **sandbox** — a pluggable filesystem-isolation layer. A backend is a single Python class that wraps the inner command in whatever isolation primitives the host provides.

Today there are four built-ins:

| Backend | Status | Typical host | Network | FS isolation |
|---|---|---|---|---|
| `podman` | auto default when available | NERSC Perlmutter, Linux/Mac with Podman | open | yes, canonical container image |
| `docker` | auto fallback after podman | Industry workstations, Docker Desktop on Mac, cloud VMs | open | yes, canonical container image |
| `apptainer` | auto fallback | HPC sites with Apptainer | open | yes, canonical container image |
| `singularity` | auto fallback | HPC sites with Singularity | open | yes, canonical container image |
| `none`  | always available | CI, free-range debugging | open | **none — do not use for scored runs** |

Selection order (first match wins): `--sandbox` flag → `sandbox:` config key → `LHC_RECAST_SANDBOX` env var → auto (`podman` → `docker` → `apptainer` → `singularity` → `none` with a warning).

The runtime captures the choice in `run_info.json["sandbox"]` so provenance is preserved.

## Using a backend

**Via config:**
```yaml
extends: ../utils/perlmutter_interactive.yaml
agent: simple
task: sus-16-046_sim-T5Wg
sandbox: podman       # auto | podman | docker | apptainer | singularity | none
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
            "-v", f"{repo_root/'ColliderBench'}:{repo_root/'ColliderBench'}:ro",
            "--tmpfs", str(repo_root/"ColliderBench"/"papers"),
            "--tmpfs", str(repo_root/"ColliderBench"/"evaluation"),
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
    "none":   NoneSandbox,
    "podman": PodmanSandbox,
    "apptainer": ApptainerSandbox,
    "singularity": SingularitySandbox,  # ← add here
}
```

Add the backend name to `_ALLOWED_SANDBOX` in [`agent_runtime/config.py`](config.py) so config validation accepts it.

## Contract every backend must honour

1. **`<workspace>` is the only rw path under the repo.** Nothing in `repo_root/` (other than the bound-in workspace) should be writable.
2. **Only the benchmark surfaces needed by the agent are bound into container backends.** Current Podman/Apptainer runs expose `ColliderBench/tools/`, `ColliderBench/bin/`, and the resolved paper directory read-only. They do not expose `tasks/shared/*/reference/` or `evaluation/`, which would let the agent cheat.
3. **`/tmp` is fresh.** Prevents cross-run leakage and signals.
4. **Every `Path` in `extra_ro_binds` is mounted read-only.** The launcher uses this to re-expose Python packages (`agent_runtime/`, per-agent `runtime/`) hidden by rule #1.
5. **`cleanup()` is safe to call once after the wrapped process exits**, including on failure. Return `lambda: None` if nothing needs restoring.
6. **Network can be left open.** The agent legitimately needs to reach the model API and public data sources (arxiv, HEPData, CMS Open Data). Backends that want egress filtering should document it.

Anything else (PID/IPC namespace unshare, capability drop, user-namespace remap) is backend-specific and up to you.

## Known caveats

- **`none`** provides no isolation. The passthrough exists for CI and free-range debugging on hosts without any container engine. Never score runs produced with `sandbox: none`.
- **Container backends (Docker, Podman, Shifter)** need an image built with the benchmark toolchain (MadGraph, Pythia, Delphes, conda env). On HPC nodes without Docker, `podman-hpc migrate` is usually required to convert a locally built image.
