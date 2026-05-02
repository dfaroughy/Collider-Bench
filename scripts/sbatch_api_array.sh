#!/bin/bash
# SLURM array job for API-backed agent runners.
#
# This is the regular-qos counterpart to the interactive multi-run scripts.
# It runs one benchmark task per array element and is intended for providers
# whose auth is API-key based, so parallel elements do not race on OAuth token
# refresh. For OAuth-backed CLIs, keep using sbatch_simple_array.sh with %1.
#
# Submit through:
#   bash scripts/submit_api_parallel.sh forgecode/forge_deepseek 8
#
# Or directly:
#   sbatch --array=0-19%8 scripts/sbatch_api_array.sh forgecode/forge_deepseek

#SBATCH --account=m4539
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --time=03:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --array=0-19%20
#SBATCH --output=runs/_logs/%x-%A_%a.out
#SBATCH --error=runs/_logs/%x-%A_%a.err

set -euo pipefail

CONFIG_LABEL="${1:-${CONFIG_LABEL:-forgecode/forge_deepseek}}"
SANDBOX="${LHC_RECAST_SANDBOX:-podman}"

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)}"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

resolve_config_path() {
  local label="$1"
  local path
  if [[ "${label}" == /* || "${label}" == *.yaml ]]; then
    path="${label}"
  else
    path="configs/${label}.yaml"
  fi
  if [[ -f "${path}" ]]; then
    printf '%s\n' "${path}"
    return 0
  fi

  # Compatibility for old flat labels submitted around the config-layout
  # refactor, e.g. forge_deepseek -> configs/forgecode/forge_deepseek.yaml.
  local base
  base="$(basename "${path}")"
  mapfile -t matches < <(find "${REPO_ROOT}/configs" -mindepth 2 -maxdepth 2 -type f -name "${base}" | sort)
  if [[ ${#matches[@]} -eq 1 ]]; then
    printf '%s\n' "${matches[0]}"
    return 0
  fi
  return 1
}

if ! CONFIG="$(resolve_config_path "${CONFIG_LABEL}")"; then
  if [[ "${CONFIG_LABEL}" == /* || "${CONFIG_LABEL}" == *.yaml ]]; then
    CONFIG="${CONFIG_LABEL}"
  else
    CONFIG="configs/${CONFIG_LABEL}.yaml"
  fi
fi
if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi

# NeurIPS benchmark tasks. Keep this list in sync with scripts/neurips_tasks.txt
# and with the default --array=0-19.
TASKS=(
  sus-16-034_shape-TChiWZ
  sus-16-034_sim-TChiWZ
  sus-16-046_shape-T5Wg
  sus-16-046_shape-TChiWg
  sus-16-046_sim-T5Wg
  sus-16-046_sim-TChiWg
  sus-16-047_shape-T5Wg_highHT
  sus-16-047_shape-T5Wg_lowHT
  sus-16-047_shape-T6gg_highHT
  sus-16-047_shape-T6gg_lowHT
  sus-16-047_sim-T5Wg_highHT
  sus-16-047_sim-T5Wg_lowHT
  sus-16-047_sim-T6gg_highHT
  sus-16-047_sim-T6gg_lowHT
  sus-16-051_shape-T2tt_SRG
  sus-16-051_shape-T2bW_SRG
  sus-16-051_shape-T2tt_comp
  sus-16-051_sim-T2tt_SRG
  sus-16-051_sim-T2bW_SRG
  sus-16-051_sim-T2tt_comp
)

if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  echo "SLURM_ARRAY_TASK_ID is unset; submit via sbatch --array." >&2
  exit 2
fi

TASK_ID="${TASKS[$SLURM_ARRAY_TASK_ID]:-}"
if [[ -z "${TASK_ID}" ]]; then
  echo "No task at array index ${SLURM_ARRAY_TASK_ID}" >&2
  exit 2
fi

# Fresh SLURM allocations do not inherit the interactive shell's module state.
# Use the same bootstrap helper as scripts/run-agent; direct `conda activate`
# under `set -u` can fail in conda deactivate hooks that reference unset vars.
# shellcheck disable=SC1091
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"
activate_lhc_analysis

RUNNER="$(
  python - "${CONFIG}" <<'PY'
import sys

from agent_runtime.config import load_config

print(load_config(sys.argv[1]).get("runner") or "unknown")
PY
)"

echo "[$(date '+%F %T')] config=${CONFIG_LABEL} runner=${RUNNER} sandbox=${SANDBOX}"
echo "[$(date '+%F %T')] idx=${SLURM_ARRAY_TASK_ID} task=${TASK_ID}"
echo "[$(date '+%F %T')] node=$(hostname) job=${SLURM_JOB_ID} array_job=${SLURM_ARRAY_JOB_ID}"

# We are already inside the SLURM allocation, so call the simple-agent Python
# entrypoint directly. This avoids scripts/run-agent's interactive salloc path.
export PYTHONUNBUFFERED=1
exec srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-16}" --unbuffered \
  python -m agents.simple.run \
    --config "${CONFIG}" \
    --task "${TASK_ID}" \
    --sandbox "${SANDBOX}"
