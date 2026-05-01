#!/bin/bash
# SLURM array job: run the `simple` agent against every task in
# LHCRecastBench/tasks/, one task per array element.
#
# Designed to be submitted three times — once per runner (claude / codex /
# gemini) — with %1 array-throttling so each runner's subscription only
# sees one in-flight CLI session at a time, avoiding OAuth refresh-token
# races (see explanation in the conversation history).
#
# Submit one runner:
#   sbatch --job-name=lhc-simple-claude scripts/sbatch_simple_array.sh claude
#   sbatch --job-name=lhc-simple-codex  scripts/sbatch_simple_array.sh codex
#   sbatch --job-name=lhc-simple-gemini scripts/sbatch_simple_array.sh gemini
#
# Or all three at once: bash scripts/submit_simple_benchmark.sh

#SBATCH --account=m4539
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --time=04:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --array=0-28%1
#SBATCH --output=runs/_logs/%x-%A_%a.out
#SBATCH --error=runs/_logs/%x-%A_%a.err

set -euo pipefail

ARG="${1:-${RUNNER:-}}"
case "${ARG}" in
  "")
    echo "usage: sbatch ... $0 <runner | config-label>" >&2
    echo "  short form (default model): claude | codex | gemini" >&2
    echo "  full form (explicit config): e.g. claude_sonnet_simple, codex_gpt50_simple" >&2
    exit 2 ;;
  claude|codex|gemini)
    CONFIG_LABEL="${ARG}_simple" ;;     # short form: <runner> -> <runner>_simple
  *)
    CONFIG_LABEL="${ARG}" ;;             # full form: pass-through label
esac
CONFIG="configs/${CONFIG_LABEL}.yaml"
if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi
# Derive runner from the YAML for the log header.
RUNNER="$(awk -F: '/^runner:/ {gsub(/[ \t]/, "", $2); print $2; exit}' "${CONFIG}")"
RUNNER="${RUNNER:-unknown}"

# 29 benchmark tasks — keep in sync with --array=0-28 above.
TASKS=(
  exo-17-021_sim-RPVstop_res-btag
  exo-17-021_sim-RPVstop_res-incl
  sus-16-046_yield-T5Wg
  sus-16-046_yield-TChiWg
  sus-16-046_shape-T5Wg
  sus-16-046_shape-TChiWg
  sus-16-046_sim-T5Wg
  sus-16-046_sim-TChiWg
  sus-16-046_val-Nobs
  sus-16-047_yield-T5Wg_highHT
  sus-16-047_yield-T5Wg_lowHT
  sus-16-047_yield-T6gg_highHT
  sus-16-047_yield-T6gg_lowHT
  sus-16-047_shape-T5Wg_highHT
  sus-16-047_shape-T5Wg_lowHT
  sus-16-047_shape-T6gg_highHT
  sus-16-047_shape-T6gg_lowHT
  sus-16-047_sim-T5Wg_highHT
  sus-16-047_sim-T5Wg_lowHT
  sus-16-047_sim-T6gg_highHT
  sus-16-047_sim-T6gg_lowHT
  sus-16-047_val-Nobs_highHT
  sus-16-047_val-Nobs_lowHT
  sus-16-051_yield-T2tt
  sus-16-051_yield-T2tt_comp
  sus-16-051_shape-T2tt
  sus-16-051_shape-T2tt_comp
  sus-16-051_sim-T2tt
  sus-16-051_sim-T2tt_comp
)

if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  echo "SLURM_ARRAY_TASK_ID is unset — submit via sbatch, not directly." >&2
  exit 2
fi

TASK_ID="${TASKS[$SLURM_ARRAY_TASK_ID]:-}"
if [[ -z "${TASK_ID}" ]]; then
  echo "No task at array index ${SLURM_ARRAY_TASK_ID}" >&2
  exit 2
fi

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)}"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

# Login-node env doesn't propagate into a fresh allocation; bring our project
# conda env back up explicitly.
source /opt/cray/pe/lmod/lmod/init/bash
module load conda
conda activate lhc_analysis

echo "[$(date '+%F %T')] runner=${RUNNER}  idx=${SLURM_ARRAY_TASK_ID}  task=${TASK_ID}"
echo "[$(date '+%F %T')] config=${CONFIG}"
echo "[$(date '+%F %T')] node=$(hostname)  job=${SLURM_JOB_ID}  array_job=${SLURM_ARRAY_JOB_ID}"

# We're already inside the allocation; bypass scripts/run-agent's salloc/srun
# path and call python directly under a single srun layer (so MG5/Pythia
# subprocesses see the right --cpus-per-task binding).
export PYTHONUNBUFFERED=1
exec srun --ntasks=1 --cpus-per-task=128 --unbuffered \
  python -m agents.simple.run \
    --config "${CONFIG}" \
    --task "${TASK_ID}"
