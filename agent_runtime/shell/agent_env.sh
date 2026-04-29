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
  # Dispatch a Python -m invocation either to the current host (login node)
  # or to a Perlmutter compute allocation. Caller sets PY_MODULE to the
  # module path (e.g. "agents.simple.run") and forwards "$@" here.
  #
  # Recognized flags (consumed here):
  #   --config PATH           load defaults from a YAML config (CLI flags still override)
  #   --compute perlmutter    wrap in salloc/srun (default: off)
  #   --cpus N                CPUs per task when --compute=perlmutter (default: 128)
  #   --walltime HH:MM:SS     walltime (default: 04:00:00)
  #   --qos NAME              qos (default: interactive)
  #   --account NAME          SLURM account (default: unset)
  # Anything else is forwarded verbatim to the Python module. --config is also
  # forwarded so the Python entrypoint can pick up its own defaults from the
  # same file.
  local CONFIG="" COMPUTE="" CPUS="" WALLTIME="" QOS="" CONSTRAINT="cpu" ACCOUNT=""
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
    # Pull only the shell-level keys (compute, account, cpus, walltime, qos).
    # Follows `extends:` chains so shared defaults in base.yaml are honored.
    eval "$(python - "${CONFIG}" <<'PY'
import sys, yaml
from pathlib import Path

def load_with_extends(p, seen=None):
    seen = seen or []
    p = Path(p).resolve()
    if p in seen:
        raise SystemExit(f"config extends cycle: {seen + [p]}")
    seen.append(p)
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"{p}: top-level YAML must be a mapping")
    parent = raw.pop("extends", None)
    merged = {}
    if parent:
        merged.update(load_with_extends(p.parent / parent, seen))
    merged.update(raw)
    return merged

cfg = load_with_extends(sys.argv[1])
for key in ("compute", "account", "cpus", "walltime", "qos"):
    val = cfg.get(key, "")
    if val is None: val = ""
    print(f'{key.upper()}={val!r}')
PY
)"
  fi

  # Second pass: actual arg parse — CLI overrides config defaults
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)    shift 2 ;;   # consumed above; still threaded to python below
      --compute)   COMPUTE="$2"; shift 2 ;;
      --cpus)      CPUS="$2"; shift 2 ;;
      --walltime)  WALLTIME="$2"; shift 2 ;;
      --qos)       QOS="$2"; shift 2 ;;
      --account)   ACCOUNT="$2"; shift 2 ;;
      *)           REMAINING_ARGS+=("$1"); shift ;;
    esac
  done

  # Final defaults if neither config nor CLI set them
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

  if [[ "${COMPUTE}" == "perlmutter" ]]; then
    echo "Requesting Perlmutter compute node (${CPUS} CPUs, ${WALLTIME}, qos=${QOS})..."
    local ACCT_FLAG=""
    [[ -n "${ACCOUNT}" ]] && ACCT_FLAG="--account=${ACCOUNT}"
    # Explicit Lmod + conda activation — login-node env vars don't propagate.
    # PYTHONUNBUFFERED=1 keeps stream_display line-buffered when stdout is
    # not a TTY (under srun it isn't), so progress actually streams.
    local INNER_CMD="source /opt/cray/pe/lmod/lmod/init/bash && module load conda && conda activate ${LHC_BENCH_ENV_NAME} && cd '${REPO_ROOT}' && export PYTHONUNBUFFERED=1 && python -m ${PY_MODULE}"
    local arg
    for arg in "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"; do
      INNER_CMD+=" $(printf '%q' "$arg")"
    done
    # srun --unbuffered disables srun's own line-buffering on stdout so the
    # agent's streamed output reaches the user's terminal in real time.
    exec salloc --nodes=1 --ntasks=1 --qos="${QOS}" \
      --time="${WALLTIME}" --constraint="${CONSTRAINT}" \
      --cpus-per-task="${CPUS}" ${ACCT_FLAG} \
      srun --ntasks=1 --cpus-per-task="${CPUS}" --unbuffered \
      bash -c "${INNER_CMD}"
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

modules = ["yaml", "requests", "aiohttp", "uproot", "awkward", "vector", "numpy"]
print(f"Python {sys.version.split()[0]}")
for module in modules:
    importlib.import_module(module)
    print(f"{module}: OK")
PY
}
