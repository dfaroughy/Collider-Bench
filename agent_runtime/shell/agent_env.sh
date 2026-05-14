#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LHC_BENCH_CONDA_MODULE="${LHC_BENCH_CONDA_MODULE:-conda/Miniforge3-24.11.3-0}"
LHC_BENCH_ENV_NAME="${LHC_BENCH_ENV_NAME:-lhc_analysis}"
# Host-side env package list — used only by the conda bootstrap fallback in
# `bootstrap_lhc_analysis`. Mirrors pyproject.toml's `dependencies` exactly:
# the harness runs entirely from these packages, and none of the HEP-stack
# libraries (pythia8, pyhepmc, xrootd, uproot, awkward, hist) live here.
# Those are baked into the container image instead.
#
# The preferred path is `pip install -e .` from a venv — `activate_lhc_analysis`
# detects that case and skips the conda flow entirely. This list is the
# fallback for Perlmutter and other conda-driven hosts.
LHC_BENCH_PACKAGES=(
  python=3.11
  pyyaml
  pydantic
  numpy
  matplotlib
  mplhep
)

load_module_stack() {
  if type module >/dev/null 2>&1; then
    return 0
  fi
  # Try known Lmod init paths. The Cray path is the one that actually exists
  # on NERSC Perlmutter; the /usr/share path is the generic distro location.
  # Fresh shells without an inherited `module` function need to re-source.
  local init
  # Lmod's init references $FPATH and other vars that may be unset under
  # `set -u` (e.g. env -i bash, fresh shells). Relax nounset, restore after.
  # Also source the Cray-PE init if present — it populates MODULEPATH, which
  # is what `module load conda` actually needs.
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
  # via /etc/profile.d/, but fresh shells skip /etc/profile.
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

# True when the currently-active Python interpreter has the harness's host-side
# dependencies importable. Typically this means the caller is in a venv where
# `pip install -e ".[dev]"` already ran. Used to short-circuit the conda flow
# for non-HEP users (industry researchers, generic Linux hosts).
harness_python_ready() {
  command -v python >/dev/null 2>&1 || return 1
  python - <<'PY' >/dev/null 2>&1
import importlib.util
for mod in ("yaml", "pydantic", "numpy", "matplotlib", "mplhep"):
    if importlib.util.find_spec(mod) is None:
        raise SystemExit(1)
PY
}

activate_lhc_analysis() {
  # Venv-first: if the calling shell already has a Python with the harness
  # deps importable (typical after `pip install -e ".[dev]"` in a venv), use
  # it as-is. This is the supported path for industry / non-HEP users; no
  # conda required.
  if harness_python_ready; then
    export PYTHONHTTPSVERIFY=0
    return 0
  fi

  # Fallback: load conda + activate the lhc_analysis env (Perlmutter / dev path).
  if ! load_conda; then
    echo "activate_lhc_analysis: no harness-ready Python and conda not available." >&2
    echo "  Quickest fix: 'python -m venv .venv && source .venv/bin/activate && pip install -e .[dev]'." >&2
    echo "  HPC users: install conda, then re-run scripts/run-agent to bootstrap '${LHC_BENCH_ENV_NAME}'." >&2
    return 1
  fi
  local had_nounset=0
  [[ $- == *u* ]] && had_nounset=1
  set +u
  if ! conda_env_exists; then
    echo "Missing conda environment '${LHC_BENCH_ENV_NAME}'." >&2
    echo "  Either 'pip install -e .[dev]' in a venv (recommended), or run bootstrap_lhc_analysis." >&2
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
  # Pip-install any host-side harness deps that are missing from the active
  # Python. Kept as a safety net for the conda-bootstrap path; `pip install
  # -e .` in a venv already covers everything here.
  local missing
  missing="$(python - <<'PY'
from importlib.util import find_spec

packages = {
    "yaml": "PyYAML",
    "pydantic": "pydantic",
    "numpy": "numpy",
    "matplotlib": "matplotlib",
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
  # Dispatch a Python -m invocation either to the current host/login node
  # or to a Slurm allocation. Caller sets PY_MODULE to the
  # module path (e.g. "agents.simple.run") and forwards "$@" here.
  #
  # Recognized flags (consumed here):
  #   --config PATH           load defaults from a YAML config (CLI flags still override)
  #   --compute slurm         wrap in salloc/srun (default: off)
  #   --partition NAME        SLURM partition (default: unset)
  #   --constraint NAME        SLURM node constraint (default: cpu)
  #   --nodes N               SLURM nodes (default: 1)
  #   --ntasks N              SLURM tasks (default: 1)
  #   --cpus N                CPUs per task when --compute=slurm (default: 128)
  #   --walltime HH:MM:SS     walltime (default: 04:00:00)
  #   --qos NAME              qos (default: interactive)
  #   --account NAME          SLURM account (default: unset)
  #   --salloc-extra FLAGS    Extra flags appended to salloc (single shell string)
  #   --srun-extra FLAGS      Extra flags appended to srun (single shell string)
  #   --env-setup CMD         Optional command run inside the allocation before env activation
  # Anything else is forwarded verbatim to the Python module. --config is also
  # forwarded so the Python entrypoint can pick up its own defaults from the
  # same file.
  local CONFIG="" COMPUTE="" ACCOUNT="" PARTITION="" CONSTRAINT="" NODES="" NTASKS="" CPUS=""
  local WALLTIME="" QOS="" SALLOC_EXTRA="" SRUN_EXTRA="" ENV_SETUP=""
  local -a REMAINING_ARGS=()

  # First pass: locate --config so its values can serve as defaults
  local -a ALL_ARGS=("$@")
  local i=0
  while [[ $i -lt ${#ALL_ARGS[@]} ]]; do
    if [[ "${ALL_ARGS[$i]}" == "--config" ]]; then
      CONFIG="${ALL_ARGS[$((i+1))]}"
      break
    elif [[ "${ALL_ARGS[$i]}" == --config=* ]]; then
      CONFIG="${ALL_ARGS[$i]#--config=}"
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
      --config=*)  shift ;;
      --compute)   COMPUTE="$2"; shift 2 ;;
      --partition) PARTITION="$2"; shift 2 ;;
      --constraint) CONSTRAINT="$2"; shift 2 ;;
      --nodes)     NODES="$2"; shift 2 ;;
      --ntasks)    NTASKS="$2"; shift 2 ;;
      --cpus)      CPUS="$2"; shift 2 ;;
      --walltime)  WALLTIME="$2"; shift 2 ;;
      --qos)       QOS="$2"; shift 2 ;;
      --account)   ACCOUNT="$2"; shift 2 ;;
      --salloc-extra) SALLOC_EXTRA="$2"; shift 2 ;;
      --srun-extra)   SRUN_EXTRA="$2"; shift 2 ;;
      --env-setup)    ENV_SETUP="$2"; shift 2 ;;
      *)           REMAINING_ARGS+=("$1"); shift ;;
    esac
  done

  # Final defaults if neither config nor CLI set them
  COMPUTE="${COMPUTE:-}"
  CONSTRAINT="${CONSTRAINT:-cpu}"
  NODES="${NODES:-1}"
  NTASKS="${NTASKS:-1}"
  CPUS="${CPUS:-128}"
  WALLTIME="${WALLTIME:-04:00:00}"
  QOS="${QOS:-interactive}"

  # Forward --config to the python entrypoint so it can apply its own defaults
  if [[ -n "${CONFIG}" ]]; then
    REMAINING_ARGS+=("--config" "${CONFIG}")
  fi

  if [[ -z "${PY_MODULE:-}" ]]; then
    echo "run_with_compute: PY_MODULE not set" >&2
    return 2
  fi

  cd "${REPO_ROOT}"

  if [[ "${COMPUTE}" == "slurm" || "${COMPUTE}" == "perlmutter" ]]; then
    if [[ "${COMPUTE}" == "perlmutter" ]]; then
      echo "NOTE: compute=perlmutter is accepted as a legacy alias for compute=slurm." >&2
    fi
    local detail="nodes=${NODES}, ntasks=${NTASKS}, cpus/task=${CPUS}, time=${WALLTIME}"
    [[ -n "${ACCOUNT}" ]] && detail+=", account=${ACCOUNT}"
    [[ -n "${PARTITION}" ]] && detail+=", partition=${PARTITION}"
    [[ -n "${QOS}" ]] && detail+=", qos=${QOS}"
    [[ -n "${CONSTRAINT}" ]] && detail+=", constraint=${CONSTRAINT}"
    echo "Requesting Slurm allocation (${detail})..."

    local -a SALLOC_ARGS=(--nodes="${NODES}" --ntasks="${NTASKS}" --time="${WALLTIME}")
    local -a SRUN_ARGS=(--ntasks="${NTASKS}" --cpus-per-task="${CPUS}" --unbuffered)
    [[ -n "${ACCOUNT}" ]] && SALLOC_ARGS+=(--account="${ACCOUNT}")
    [[ -n "${PARTITION}" ]] && SALLOC_ARGS+=(--partition="${PARTITION}")
    [[ -n "${QOS}" ]] && SALLOC_ARGS+=(--qos="${QOS}")
    [[ -n "${CONSTRAINT}" ]] && SALLOC_ARGS+=(--constraint="${CONSTRAINT}")
    [[ -n "${CPUS}" ]] && SALLOC_ARGS+=(--cpus-per-task="${CPUS}")
    if [[ -n "${SALLOC_EXTRA}" ]]; then
      # shellcheck disable=SC2206
      local -a SALLOC_EXTRA_ARGS=(${SALLOC_EXTRA})
      SALLOC_ARGS+=("${SALLOC_EXTRA_ARGS[@]}")
    fi
    if [[ -n "${SRUN_EXTRA}" ]]; then
      # shellcheck disable=SC2206
      local -a SRUN_EXTRA_ARGS=(${SRUN_EXTRA})
      SRUN_ARGS+=("${SRUN_EXTRA_ARGS[@]}")
    fi

    # Source this shell library inside the allocation instead of embedding
    # Perlmutter-specific module commands. Cluster-specific setup can be
    # injected with `env_setup:` when activate_lhc_analysis cannot discover
    # conda on its own.
    local INNER_CMD=""
    if [[ -n "${ENV_SETUP}" ]]; then
      INNER_CMD+="${ENV_SETUP} && "
    fi
    INNER_CMD+="source '${REPO_ROOT}/agent_runtime/shell/agent_env.sh' && activate_lhc_analysis && cd '${REPO_ROOT}' && export PYTHONUNBUFFERED=1 && python -m ${PY_MODULE}"
    local arg
    for arg in "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"; do
      INNER_CMD+=" $(printf '%q' "$arg")"
    done
    # srun --unbuffered disables srun's own line-buffering on stdout so the
    # agent's streamed output reaches the user's terminal in real time.
    exec salloc "${SALLOC_ARGS[@]}" srun "${SRUN_ARGS[@]}" bash -lc "${INNER_CMD}"
  else
    exec python -m "${PY_MODULE}" "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
  fi
}

print_env_summary() {
  activate_lhc_analysis
  ensure_python_modules
  python - <<'PY'
import importlib
import sys

modules = ["yaml", "pydantic", "numpy", "matplotlib", "mplhep"]
print(f"Python {sys.version.split()[0]}")
for module in modules:
    importlib.import_module(module)
    print(f"{module}: OK")
PY
}
