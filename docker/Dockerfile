# Canonical LHC-Bench image: HEP sim stack + harness + vendor agent CLIs in one.
#
# A new user only needs:
#   docker pull ghcr.io/dfaroughy/lhc-bench:latest
#   export ANTHROPIC_API_KEY=...   # or OPENAI / GEMINI key for those vendors
#   scripts/run-agent --config configs/<vendor>_simple.yaml --task <task_id>
#
# Build:
#   podman build -f docker/Dockerfile -t lhc-bench:latest .
#   # (or `podman-hpc build` on NERSC)
#
# Image layout:
#   /opt/conda/envs/lhc_analysis  conda env (analysis libs + nodejs + python deps)
#   /opt/sim/{MG5_aMC_v3_7_0,pythia8313,delphes,prospino}  vendored sim binaries
#   /opt/node-global              npm prefix for vendor CLIs (claude/codex/gemini)
#   /app/{agent_runtime,agents,configs}  vendor-neutral harness Python
#   /workspace                    runtime working directory (bind-mount target)

FROM docker.io/condaforge/mambaforge:latest

ARG DEBIAN_FRONTEND=noninteractive

# ── System deps for building + running the vendored sim stack ───────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      gfortran g++ make cmake git wget curl ca-certificates \
      libx11-dev libxpm-dev libxft-dev libxext-dev \
      zlib1g-dev libssl-dev \
      && rm -rf /var/lib/apt/lists/*

# ── Conda env: bit-for-bit copy of the host's lhc_analysis env ──────────────
#
# Rationale: a curated package list rebuilt from conda-forge is fragile —
# the solver drifts (e.g., adding `root` flipped pythia8 to 8.312, breaking
# bin/simulate's API expectations). We `conda pack` the host env into a
# tarball and unpack it inside the image. Identical libs, no solver fight,
# no archspec workarounds.
#
# To regenerate the tarball:
#   conda activate lhc_analysis
#   conda pack -n lhc_analysis -o docker/lhc_analysis_env.tar.gz \
#     --compress-level 1 --n-threads 8
#
# (Tarball is gitignored; rebuilders re-pack after env changes.)
COPY docker/lhc_analysis_env.tar.gz /tmp/env.tar.gz
# `--no-same-owner`: rootless podman maps the host uid (e.g. 105125) into
# a userns where it has no name, so tar's default chown fails on every
# file. Discarding host ownership and letting everything land as root
# inside the image is correct here — the env is read-only metadata and
# Python finds its libs by path, not owner.
RUN mkdir -p /opt/conda/envs/lhc_analysis \
    && tar --no-same-owner -xzf /tmp/env.tar.gz -C /opt/conda/envs/lhc_analysis \
    && rm /tmp/env.tar.gz \
    && /opt/conda/envs/lhc_analysis/bin/conda-unpack

# Default activation for every RUN/CMD. CONDA_PREFIX matters because
# pythia drivers locate the xmldoc settings DB via
# `os.environ["CONDA_PREFIX"] + "/share/Pythia8/xmldoc"` — without it,
# `pythia.init()` aborts with `unmatched version numbers`.
ENV PATH=/opt/conda/envs/lhc_analysis/bin:${PATH}
ENV CONDA_DEFAULT_ENV=lhc_analysis
ENV CONDA_PREFIX=/opt/conda/envs/lhc_analysis
# Pin the env name for `activate_lhc_analysis` (agent_env.sh).
ENV LHC_BENCH_ENV_NAME=lhc_analysis
# LD_LIBRARY_PATH points at the conda env's lib dir so the dynamic
# linker finds:
#   - libCore.so.6.38 / libImt / libRIO / ... (ROOT, used by Delphes)
#   - the conda libstdc++ (which has GLIBCXX_3.4.29+; the host system
#     libstdc++ in mambaforge:latest is too old for Delphes-built-on-host).
# Without this Delphes binaries fail at exec with `error while loading
# shared libraries: libCore.so.6.38: cannot open shared object file`.
ENV LD_LIBRARY_PATH=/opt/conda/envs/lhc_analysis/lib:${LD_LIBRARY_PATH}

# Cache redirects so the agent never tries to write into a root-owned
# directory. Under rootless podman the agent IS UID 0 inside its
# namespace and could write anywhere, but apptainer / singularity /
# `podman --userns=keep-id` run the agent as a non-root user against
# this image's root-owned filesystem and would otherwise hit EPERM on
# pip, matplotlib, or pycache writes. /tmp is always world-writable.
ENV PIP_CACHE_DIR=/tmp/pip-cache \
    MPLCONFIGDIR=/tmp/mpl-cache \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    XDG_CACHE_HOME=/tmp/xdg-cache

# ── Vendored sim stack ──────────────────────────────────────────────────────
# Baked at /opt/sim so the host's ColliderBench/tools/sim can be shadowed
# by this immutable copy when the host dir doesn't ship pre-built binaries.
# Sizes (approx): mg5 200 MB, pythia 290 MB, delphes 110 MB, prospino 60 MB.
COPY ColliderBench/tools/sim/MG5_aMC_v3_7_0 /opt/sim/MG5_aMC_v3_7_0
COPY ColliderBench/tools/sim/pythia8313     /opt/sim/pythia8313
COPY ColliderBench/tools/sim/delphes        /opt/sim/delphes
COPY ColliderBench/tools/sim/prospino       /opt/sim/prospino

ENV MG5_DIR=/opt/sim/MG5_aMC_v3_7_0
ENV PYTHIA8_DIR=/opt/sim/pythia8313
ENV DELPHES_DIR=/opt/sim/delphes
ENV PROSPINO_DIR=/opt/sim/prospino
# Put the sim CLIs on PATH so agents can call them directly (no wrapper).
# MG5: $MG5_DIR/bin/mg5_aMC; Delphes: $DELPHES_DIR/Delphes* (no bin/ subdir).
# Pythia8 has no CLI; it's exposed as the `pythia8` Python module from the env.
ENV PATH=/opt/sim/MG5_aMC_v3_7_0/bin:/opt/sim/delphes:${PATH}

# ── Node 20 (interpreter only — vendor CLIs are mounted from host) ──────────
# Node is needed because npm-globals are JS scripts that shebang `#!/usr/bin/env
# node`. The CLIs themselves (claude / codex / gemini / grok / aider / ...) are
# NOT installed here; the sandbox bind-mounts the host's npm prefix to
# /opt/node-global at run time. PATH is pre-set so anything the user has
# installed via `npm i -g <pkg>` on the host appears on PATH inside the
# container automatically.
RUN mamba install -n lhc_analysis -y -c conda-forge 'nodejs>=20' \
      && mamba clean -afy
ENV NPM_CONFIG_PREFIX=/opt/node-global
ENV PATH=/opt/node-global/bin:${PATH}

# Harness Python (`agent_runtime/`, `agents/<active>/`, `configs/`) is
# bind-mounted from the cloned repo at run time. The sandbox sets PYTHONPATH
# to the host repo root so `from agent_runtime.* import ...` resolves there.
# Nothing repo-side is baked into the image.

# Make /opt/sim readable+executable for non-root users. The vendored sim
# binaries land here from `COPY` with their host-side perms preserved
# (typically 750/640 for HEP installs), which is fine under rootless
# podman because the agent is mapped to UID 0 inside its namespace, but
# under singularity / apptainer (no UID remap, --fakeroot disabled on
# many clusters incl. Rutgers Amarel) the agent runs as the invoking
# host UID and cannot read or exec these files.
#
# `o+rX` (capital X) only adds +x on directories and already-executable
# files — plain data files don't accidentally become executable. We
# intentionally don't add +w: /opt/sim is meant to be read-only at run
# time, and agents write their scratch state under their workspace.
RUN chmod -R o+rX /opt/sim

WORKDIR /workspace
CMD ["/bin/bash"]
