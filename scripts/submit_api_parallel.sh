#!/usr/bin/env bash
# Submit a regular-qos parallel SLURM array for API-backed model runners.
#
# Usage:
#   bash scripts/submit_api_parallel.sh
#   bash scripts/submit_api_parallel.sh forgecode/forge_deepseek 8
#   bash scripts/submit_api_parallel.sh forgecode/forge_deepseek 16 0-19
#   bash scripts/submit_api_parallel.sh forgecode/forge_deepseek 4 13-20
#   bash scripts/submit_api_parallel.sh anthropics/claude_haiku_api 20 0-19 --job_name haiku-test
#
# Arguments:
#   1. config label under configs/      default: forgecode/forge_deepseek
#   2. max concurrent array elements    default: 8
#   3. array index range                default: 0-19

set -euo pipefail

CONFIG_LABEL="${1:-forgecode/forge_deepseek}"
PARALLEL="${2:-8}"
ARRAY_RANGE="${3:-0-19}"
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

if ! [[ "${PARALLEL}" =~ ^[1-9][0-9]*$ ]]; then
  echo "parallelism must be a positive integer: ${PARALLEL}" >&2
  exit 2
fi

# Config values may be inherited through `extends:`, so parse through the
# Python config loader rather than grepping YAML.
# shellcheck disable=SC1091
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"
activate_lhc_analysis
eval "$(
  python - "${CONFIG}" <<'PY'
import shlex
import sys

from agent_runtime.config import load_config

cfg = load_config(sys.argv[1])
runner = str(cfg.get("runner") or "")
provider = str(cfg.get("provider") or ("anthropic" if runner == "claude" else runner))
auth = str(cfg.get("auth") or "")
model = str(cfg.get("model") or "")
resources = {
    "ACCOUNT": cfg.get("account"),
    "PARTITION": cfg.get("partition"),
    "CONSTRAINT": cfg.get("constraint"),
    "NODES": cfg.get("nodes"),
    "NTASKS": cfg.get("ntasks"),
    "CPUS": cfg.get("cpus"),
    "WALLTIME": cfg.get("walltime"),
    "QOS": cfg.get("qos"),
}
for key, value in {
    "RUNNER": runner,
    "PROVIDER": provider,
    "AUTH": auth,
    "MODEL": model,
    **resources,
}.items():
    print(f"{key}={shlex.quote('' if value is None else str(value))}")
PY
)"

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

SAFE_CONFIG_LABEL="${CONFIG_LABEL//\//-}"
SAFE_CONFIG_LABEL="${SAFE_CONFIG_LABEL%.yaml}"
JOB_NAME="${JOB_NAME:-lhc-api-${SAFE_CONFIG_LABEL}}"

SBATCH_ARGS=()
[[ -n "${ACCOUNT:-}" ]] && SBATCH_ARGS+=(--account="${ACCOUNT}")
[[ -n "${PARTITION:-}" ]] && SBATCH_ARGS+=(--partition="${PARTITION}")
[[ -n "${CONSTRAINT:-}" ]] && SBATCH_ARGS+=(--constraint="${CONSTRAINT}")
[[ -n "${QOS:-}" ]] && SBATCH_ARGS+=(--qos="${QOS}")
[[ -n "${WALLTIME:-}" ]] && SBATCH_ARGS+=(--time="${WALLTIME}")
[[ -n "${NODES:-}" ]] && SBATCH_ARGS+=(--nodes="${NODES}")
[[ -n "${NTASKS:-}" ]] && SBATCH_ARGS+=(--ntasks="${NTASKS}")
[[ -n "${CPUS:-}" ]] && SBATCH_ARGS+=(--cpus-per-task="${CPUS}")

echo "submitting ${JOB_NAME}: array=${ARRAY_RANGE}%${PARALLEL}, config=${CONFIG}"
out="$(
  sbatch \
    "${SBATCH_ARGS[@]}" \
    --export="${SBATCH_EXPORT}" \
    --job-name="${JOB_NAME}" \
    --array="${ARRAY_RANGE}%${PARALLEL}" \
    scripts/sbatch_api_array.sh "${CONFIG}"
)"
echo "${out}"
jobid="$(awk '/Submitted batch job/ {print $4; exit}' <<<"${out}")"

cat <<EOF

monitor:
  squeue -u \$USER -o '%.10i %.30j %.10T %.10M %.10L %R'

logs:
  runs/_logs/${JOB_NAME}-${jobid}_<ARRAYIDX>.out
  runs/_logs/${JOB_NAME}-${jobid}_<ARRAYIDX>.err

results:
  runs/<runner>_<model>/<task_id>_<adj><physicist>_<hex>/

single-task rerun example:
  sbatch --job-name=${JOB_NAME}-one --array=13 scripts/sbatch_api_array.sh ${CONFIG}
EOF
