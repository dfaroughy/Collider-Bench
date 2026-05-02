#!/usr/bin/env bash
# Submit a regular-qos parallel SLURM array for API-backed model runners.
#
# Usage:
#   bash scripts/submit_api_parallel.sh
#   bash scripts/submit_api_parallel.sh forge_deepseek 8
#   bash scripts/submit_api_parallel.sh forge_deepseek 16 0-28
#   bash scripts/submit_api_parallel.sh forge_deepseek 4 13-20
#   bash scripts/submit_api_parallel.sh claude_haiku_api 20 0-19 --job_name haiku-test
#
# Arguments:
#   1. config label under configs/      default: forge_deepseek
#   2. max concurrent array elements    default: 8
#   3. array index range                default: 0-28

set -euo pipefail

CONFIG_LABEL="${1:-forge_deepseek}"
PARALLEL="${2:-8}"
ARRAY_RANGE="${3:-0-28}"
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))
JOB_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job_name)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--job_name requires a non-empty value" >&2
        exit 2
      fi
      JOB_NAME="$2"
      shift 2
      ;;
    --job_name=*)
      JOB_NAME="${1#--job_name=}"
      if [[ -z "${JOB_NAME}" ]]; then
        echo "--job_name requires a non-empty value" >&2
        exit 2
      fi
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

# Local, gitignored API-key file. Default: <repo>/.api_keys.env.
# Override with LHC_RECAST_API_KEYS_FILE=/path/to/file if preferred.
# Expected contents:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   export DEEPSEEK_API_KEY=...
API_KEYS_FILE="${LHC_RECAST_API_KEYS_FILE:-${REPO_ROOT}/.api_keys.env}"
if [[ -f "${API_KEYS_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${API_KEYS_FILE}"
fi

CONFIG="configs/${CONFIG_LABEL}.yaml"
if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi

if ! [[ "${PARALLEL}" =~ ^[1-9][0-9]*$ ]]; then
  echo "parallelism must be a positive integer: ${PARALLEL}" >&2
  exit 2
fi

RUNNER="$(awk -F: '/^runner:/ {gsub(/[ \t]/, "", $2); print $2; exit}' "${CONFIG}")"
PROVIDER="$(awk -F: '/^provider:/ {gsub(/[ \t]/, "", $2); print $2; exit}' "${CONFIG}")"
AUTH="$(awk -F: '/^auth:/ {gsub(/[ \t]/, "", $2); print $2; exit}' "${CONFIG}")"
MODEL="$(awk -F: '/^model:/ {gsub(/[ \t]/, "", $2); print $2; exit}' "${CONFIG}")"
PROVIDER="${PROVIDER:-$([[ "${RUNNER}" == "claude" ]] && echo anthropic || echo "${RUNNER}")}"

SBATCH_EXPORT="ALL"
case "${RUNNER}:${PROVIDER}:${AUTH}" in
  claude:anthropic:api)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "config ${CONFIG_LABEL} uses Claude API auth; set ANTHROPIC_API_KEY in ${API_KEYS_FILE} or export it before submitting." >&2
      exit 2
    fi
    export ANTHROPIC_API_KEY
    SBATCH_EXPORT+=",ANTHROPIC_API_KEY"
    ;;
  claude:deepseek:api)
    if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
      echo "config ${CONFIG_LABEL} uses Claude Code + DeepSeek; set DEEPSEEK_API_KEY in ${API_KEYS_FILE} or export it before submitting." >&2
      exit 2
    fi
    export DEEPSEEK_API_KEY
    export ANTHROPIC_AUTH_TOKEN="${DEEPSEEK_API_KEY}"
    export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-${MODEL:-deepseek-v4-pro[1m]}}"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-deepseek-v4-pro[1m]}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-deepseek-v4-pro[1m]}"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-deepseek-v4-flash}"
    export CLAUDE_CODE_SUBAGENT_MODEL="${CLAUDE_CODE_SUBAGENT_MODEL:-deepseek-v4-flash}"
    export CLAUDE_CODE_EFFORT_LEVEL="${CLAUDE_CODE_EFFORT_LEVEL:-max}"
    SBATCH_EXPORT+=",DEEPSEEK_API_KEY,ANTHROPIC_AUTH_TOKEN,ANTHROPIC_BASE_URL,ANTHROPIC_MODEL"
    SBATCH_EXPORT+=",ANTHROPIC_DEFAULT_OPUS_MODEL,ANTHROPIC_DEFAULT_SONNET_MODEL,ANTHROPIC_DEFAULT_HAIKU_MODEL"
    SBATCH_EXPORT+=",CLAUDE_CODE_SUBAGENT_MODEL,CLAUDE_CODE_EFFORT_LEVEL"
    ;;
  forge:deepseek:*)
    if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
      echo "config ${CONFIG_LABEL} uses Forge/DeepSeek; set DEEPSEEK_API_KEY in ${API_KEYS_FILE} or export it before submitting." >&2
      exit 2
    fi
    export DEEPSEEK_API_KEY
    SBATCH_EXPORT+=",DEEPSEEK_API_KEY"
    ;;
esac

JOB_NAME="${JOB_NAME:-lhc-api-${CONFIG_LABEL}}"
echo "submitting ${JOB_NAME}: array=${ARRAY_RANGE}%${PARALLEL}, qos=regular"
out="$(
  sbatch \
    --export="${SBATCH_EXPORT}" \
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
