#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LHC_BENCH_CONDA_MODULE="${LHC_BENCH_CONDA_MODULE:-conda/Miniforge3-24.11.3-0}"
LHC_BENCH_ENV_NAME="${LHC_BENCH_ENV_NAME:-lhc_analysis}"
LHC_BENCH_PACKAGES=(
  python=3.11
  pyyaml
  requests
  aiohttp
  uproot
  awkward
  vector
  numpy
  scipy
  matplotlib
  hist
  mplhep
  xrootd
  fsspec-xrootd
  # Physics tools the agent needs at runtime: PDF reading (read-paper),
  # event-record I/O (HEPMC), parton-shower bindings.
  pymupdf
  pyhepmc
  pythia8
)

load_module_stack() {
  if type module >/dev/null 2>&1; then
    return 0
  fi
  # Try known Lmod init paths. The Cray path is the one that actually exists
  # on NERSC Perlmutter; the /usr/share path is the generic distro location.
  # Inside bwrap the parent-shell's module function isn't inherited, so this
  # has to re-source.
  local init
  # Lmod's init references $FPATH and other vars that may be unset under
  # `set -u` (e.g. env -i bash, fresh bwrap shells). Relax nounset, restore
  # after. Also source the Cray-PE init if present — it populates
  # MODULEPATH, which is what `module load conda` actually needs.
  local had_nounset=0
  [[ $- == *u* ]] && had_nounset=1
  set +u
  for init in \
      /opt/cray/pe/lmod/lmod/init/bash \
      /usr/share/lmod/lmod/init/bash \
      /usr/share/Modules/init/bash
  do
    if [[ -f "$init" ]]; then
      # shellcheck disable=SC1090
      source "$init"
      break
    fi
  done
  # Populate MODULEPATH on NERSC / Cray systems. This usually runs at login
  # via /etc/profile.d/, but bwrap/fresh shells skip /etc/profile.
  for craype in /etc/profile.d/zz-cray-pe.sh /etc/bash.bashrc.local; do
    if [[ -f "$craype" && -z "${MODULEPATH:-}" ]]; then
      # shellcheck disable=SC1090
      source "$craype"
    fi
  done
  (( had_nounset )) && set -u
  type module >/dev/null 2>&1
}

load_conda() {
  # `module load` and conda's shell hook both reference vars that can be
  # unset under `set -u`. Relax nounset across the body, restore on exit.
  local had_nounset=0
  [[ $- == *u* ]] && had_nounset=1
  set +u
  _load_conda_inner
  local rc=$?
  (( had_nounset )) && set -u
  return $rc
}

_load_conda_inner() {
  if command -v conda >/dev/null 2>&1; then
    if ! type conda 2>/dev/null | grep -q 'function'; then
      eval "$(conda shell.bash hook 2>/dev/null)" || true
    fi
    return 0
  fi
  load_module_stack
  module load "${LHC_BENCH_CONDA_MODULE}" >/dev/null 2>&1 || module load conda >/dev/null 2>&1
  if ! command -v conda >/dev/null 2>&1; then
    return 1
  fi
  eval "$(conda shell.bash hook 2>/dev/null)" || true
  command -v conda >/dev/null 2>&1
}

conda_env_exists() {
  conda env list | awk 'NF > 0 && $1 !~ /^#/ {print $1}' | grep -Fxq "${LHC_BENCH_ENV_NAME}"
}

activate_lhc_analysis() {
  if ! load_conda; then
    return 1
  fi
  # conda_env_exists + conda activate can both touch unset vars; relax nounset.
  local had_nounset=0
  [[ $- == *u* ]] && had_nounset=1
  set +u
  if ! conda_env_exists; then
    echo "Missing conda environment '${LHC_BENCH_ENV_NAME}'. Run ${REPO_ROOT}/LHCRecastBench/bin/bootstrap-recast-tools first." >&2
    (( had_nounset )) && set -u
    return 1
  fi
  conda activate "${LHC_BENCH_ENV_NAME}" >/dev/null 2>&1
  local rc=$?
  (( had_nounset )) && set -u
  # Disable SSL verification globally for CERN's self-signed certificates
  export PYTHONHTTPSVERIFY=0
  return $rc
}

ensure_python_modules() {
  local missing
  missing="$(python - <<'PY'
from importlib.util import find_spec

packages = {
    "yaml": "PyYAML",
    "requests": "requests",
    "aiohttp": "aiohttp",
    "uproot": "uproot",
    "awkward": "awkward",
    "vector": "vector",
    "numpy": "numpy",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "hist": "hist",
    "mplhep": "mplhep",
}
print(" ".join(pkg for mod, pkg in packages.items() if find_spec(mod) is None))
PY
)"
  if [[ -z "${missing}" ]]; then
    return 0
  fi
  python -m ensurepip --upgrade >/dev/null 2>&1 || true
  python -m pip install --upgrade pip >/dev/null
  python -m pip install ${missing}
}

bootstrap_lhc_analysis() {
  load_conda
  if ! conda_env_exists; then
    conda create -y -n "${LHC_BENCH_ENV_NAME}" -c conda-forge "${LHC_BENCH_PACKAGES[@]}"
  fi
  conda activate "${LHC_BENCH_ENV_NAME}" >/dev/null 2>&1
  ensure_python_modules
}

run_with_compute() {
  # Dispatch a Python -m invocation either to the current host (login node,
  # the default) or to a SLURM allocation. Caller sets PY_MODULE to the
  # module path (e.g. "agents.simple.run") and forwards "$@" here.
  #
  # Recognized flags (consumed here):
  #   --config PATH           load defaults from a YAML config (CLI flags still override)
  #   --compute MODE          ""/"login" → run inline; "slurm" → wrap in salloc/srun
  #   --cpus N                CPUs per task          (default: 4)
  #   --walltime HH:MM:SS     walltime               (default: 04:00:00)
  #   --partition NAME        SLURM partition        (only emitted if set)
  #   --qos NAME              SLURM qos              (only emitted if set)
  #   --account NAME          SLURM account          (only emitted if set)
  #   --constraint NAME       SLURM constraint       (only emitted if set; e.g. "cpu" on NERSC)
  #
  # Cluster-specific bootstrap, optional, sourced from config or env:
  #   lmod_init / LMOD_INIT_SCRIPT     path to Lmod's init/bash (e.g. /opt/cray/pe/lmod/lmod/init/bash)
  #   modules / LHC_BENCH_MODULES      space-separated module list to `module load` (e.g. "conda singularity")
  #   conda_init / CONDA_INIT_SCRIPT   path to conda's profile.d/conda.sh (e.g. \$HOME/miniconda3/etc/profile.d/conda.sh)
  #
  # Anything else is forwarded verbatim to the Python module. --config is also
  # forwarded so the Python entrypoint can pick up its own defaults from the
  # same file.
  local CONFIG="" COMPUTE="" CPUS="" WALLTIME="" QOS="" CONSTRAINT="" ACCOUNT="" PARTITION=""
  local LMOD_INIT="" MODULES="" CONDA_INIT=""
  local -a REMAINING_ARGS=()

  # First pass: locate --config so its values can serve as defaults
  local -a ALL_ARGS=("$@")
  local i=0
  while [[ $i -lt ${#ALL_ARGS[@]} ]]; do
    if [[ "${ALL_ARGS[$i]}" == "--config" ]]; then
      CONFIG="${ALL_ARGS[$((i+1))]}"
      break
    fi
    i=$((i+1))
  done
  if [[ -n "${CONFIG}" ]]; then
    if [[ ! -f "${CONFIG}" ]]; then
      echo "run_with_compute: config not found: ${CONFIG}" >&2
      return 2
    fi
    # Pull only shell-level keys. Python owns YAML parsing/extends/schema
    # validation in agent_runtime.config; keep shell as a thin consumer.
    eval "$(python -m agent_runtime.config --shell-defaults "${CONFIG}")"
  fi

  # Second pass: actual arg parse — CLI overrides config defaults
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)    shift 2 ;;   # consumed above; still threaded to python below
      --compute)   COMPUTE="$2"; shift 2 ;;
      --cpus)      CPUS="$2"; shift 2 ;;
      --walltime)  WALLTIME="$2"; shift 2 ;;
      --qos)        QOS="$2"; shift 2 ;;
      --account)    ACCOUNT="$2"; shift 2 ;;
      --partition)  PARTITION="$2"; shift 2 ;;
      --constraint) CONSTRAINT="$2"; shift 2 ;;
      *)            REMAINING_ARGS+=("$1"); shift ;;
    esac
  done

  WALLTIME="${WALLTIME:-04:00:00}"
  CPUS="${CPUS:-4}"
  # Cluster-specific bootstrap can come from the YAML config (lmod_init etc.)
  # or from env vars. Config keys win if both are set.
  LMOD_INIT="${LMOD_INIT:-${LMOD_INIT_SCRIPT:-}}"
  MODULES="${MODULES:-${LHC_BENCH_MODULES:-}}"
  CONDA_INIT="${CONDA_INIT:-${CONDA_INIT_SCRIPT:-}}"

  # Forward --config to the python entrypoint so it can apply its own defaults
  if [[ -n "${CONFIG}" ]]; then
    REMAINING_ARGS+=("--config" "${CONFIG}")
  fi

  if [[ -z "${PY_MODULE:-}" ]]; then
    echo "run_with_compute: PY_MODULE not set" >&2
    return 2
  fi

  cd "${REPO_ROOT}"

  if [[ -z "${COMPUTE}" || "${COMPUTE}" == "login" ]]; then
    exec python -m "${PY_MODULE}" "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
  fi

  if [[ "${COMPUTE}" != "slurm" ]]; then
    echo "run_with_compute: unknown compute mode '${COMPUTE}' (expected ''/'login' or 'slurm')" >&2
    return 2
  fi

  # ── SLURM allocation ───────────────────────────────────────────────────────
  # All SLURM args except --nodes/--ntasks/--time/--cpus-per-task are
  # optional; we only emit the flag when the corresponding key is set in
  # the config or on the CLI. That keeps the shape cluster-agnostic:
  # Perlmutter sets constraint+qos+account, Amarel sets partition, etc.
  local -a SLURM_ARGS=(
    --nodes=1 --ntasks=1
    --time="${WALLTIME}" --cpus-per-task="${CPUS}"
  )
  [[ -n "${PARTITION}"  ]] && SLURM_ARGS+=(--partition="${PARTITION}")
  [[ -n "${QOS}"        ]] && SLURM_ARGS+=(--qos="${QOS}")
  [[ -n "${ACCOUNT}"    ]] && SLURM_ARGS+=(--account="${ACCOUNT}")
  [[ -n "${CONSTRAINT}" ]] && SLURM_ARGS+=(--constraint="${CONSTRAINT}")

  local DESC="${CPUS} CPUs, ${WALLTIME}"
  [[ -n "${PARTITION}"  ]] && DESC+=", partition=${PARTITION}"
  [[ -n "${QOS}"        ]] && DESC+=", qos=${QOS}"
  [[ -n "${CONSTRAINT}" ]] && DESC+=", constraint=${CONSTRAINT}"
  echo "Requesting SLURM allocation (${DESC})..."

  # Build the inner command. PYTHONUNBUFFERED=1 keeps stream_display
  # line-buffered when stdout is not a TTY (under srun it isn't), so
  # progress streams in real time.
  local PRELUDE=""
  if [[ -n "${LMOD_INIT}" ]]; then
    PRELUDE="[ -f ${LMOD_INIT} ] && source ${LMOD_INIT}"
    [[ -n "${MODULES}" ]] && PRELUDE+=" && module load ${MODULES}"
    PRELUDE+=" 2>/dev/null;"
  elif [[ -n "${MODULES}" ]]; then
    # Module function may already exist (e.g. via /etc/profile); just load.
    PRELUDE="module load ${MODULES} 2>/dev/null;"
  fi

  local CONDA_ACT=""
  if [[ -n "${CONDA_INIT}" ]]; then
    CONDA_ACT="source ${CONDA_INIT} && conda activate ${LHC_BENCH_ENV_NAME} && "
  fi

  local INNER_CMD="${PRELUDE} ${CONDA_ACT}cd '${REPO_ROOT}' && export PYTHONUNBUFFERED=1 && python -m ${PY_MODULE}"
  local arg
  for arg in "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"; do
    INNER_CMD+=" $(printf '%q' "$arg")"
  done

  # srun --unbuffered disables srun's stdout buffering so the agent's
  # streamed output reaches the user's terminal in real time.
  exec salloc "${SLURM_ARGS[@]}" \
    srun --ntasks=1 --cpus-per-task="${CPUS}" --unbuffered \
    bash -c "${INNER_CMD}"
}

print_env_summary() {
  activate_lhc_analysis
  ensure_python_modules
  python - <<'PY'
import importlib
import sys

modules = ["yaml", "requests", "aiohttp", "uproot", "awkward", "vector", "numpy"]
print(f"Python {sys.version.split()[0]}")
for module in modules:
    importlib.import_module(module)
    print(f"{module}: OK")
PY
}
