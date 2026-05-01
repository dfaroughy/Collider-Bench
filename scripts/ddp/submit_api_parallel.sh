#!/usr/bin/env bash
# Submit a regular-qos parallel SLURM array for API-backed model runners.
#
# Usage:
#   bash scripts/submit_api_parallel.sh
#   bash scripts/submit_api_parallel.sh forge_deepseek 8
#   bash scripts/submit_api_parallel.sh forge_deepseek 16 0-28
#   bash scripts/submit_api_parallel.sh forge_deepseek 4 13-20
#
# Arguments:
#   1. config label under configs/      default: forge_deepseek
#   2. max concurrent array elements    default: 8
#   3. array index range                default: 0-28

set -euo pipefail

CONFIG_LABEL="${1:-forge_deepseek}"
PARALLEL="${2:-8}"
ARRAY_RANGE="${3:-0-28}"

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

CONFIG="configs/${CONFIG_LABEL}.yaml"
if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi

if ! [[ "${PARALLEL}" =~ ^[1-9][0-9]*$ ]]; then
  echo "parallelism must be a positive integer: ${PARALLEL}" >&2
  exit 2
fi

JOB_NAME="lhc-api-${CONFIG_LABEL}"
echo "submitting ${JOB_NAME}: array=${ARRAY_RANGE}%${PARALLEL}, qos=regular"
out="$(
  sbatch \
    --job-name="${JOB_NAME}" \
    --array="${ARRAY_RANGE}%${PARALLEL}" \
    scripts/sbatch_api_array.sh "${CONFIG_LABEL}"
)"
echo "${out}"
jobid="$(echo "${out}" | grep -oE '[0-9]+' | head -1)"

cat <<EOF

monitor:
  squeue -u \$USER -o '%.10i %.30j %.10T %.10M %.10L %R'

logs:
  runs/_logs/${JOB_NAME}-${jobid}_<ARRAYIDX>.out
  runs/_logs/${JOB_NAME}-${jobid}_<ARRAYIDX>.err

results:
  runs/<runner>_<model>/<task_id>_<adj><physicist>_<hex>/

single-task rerun example:
  sbatch --job-name=${JOB_NAME}-one --array=13 scripts/sbatch_api_array.sh ${CONFIG_LABEL}
EOF
