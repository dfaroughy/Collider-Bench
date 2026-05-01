#!/usr/bin/env bash
# Sequentially run the benchmark tasks against one (runner, model)
# config via interactive-qos allocations. Faster than the regular-queue
# array job when the regular queue is deep, but capped at 4h walltime
# per task by NERSC interactive qos. Sequential = OAuth-safe by
# construction: only one CLI session is ever in flight against the
# subscription.
#
# Usage:
#   bash scripts/multi-run-interactive-codex.sh
#   bash scripts/multi-run-interactive-codex.sh codex_gpt55
#   ...
#
# To run multiple (runner, model) cells in parallel, launch this script
# in separate terminals (or under tmux) — NERSC's interactive qos lets
# 1-2 nodes per user run simultaneously; additional invocations queue.

set -uo pipefail   # not -e — keep going if one task fails so the rest of the cell still runs

CONFIG_LABEL="${1:-codex_gpt54-mini}"
REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
CONFIG="${REPO_ROOT}/configs/${CONFIG_LABEL}.yaml"
RUN_AGENT="${REPO_ROOT}/scripts/run-agent"
SANDBOX="${LHC_RECAST_SANDBOX:-podman}"

if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi


TASKS=(
  exo-17-021_sim-RPVstop_res-btag
  exo-17-021_sim-RPVstop_res-incl
  sus-16-046_val-Nobs
  sus-16-047_val-Nobs_highHT
  sus-16-047_val-Nobs_lowHT
  sus-16-047_yield-T5Wg_highHT
)

cd "${REPO_ROOT}"
echo "[$(date '+%F %T')] config=${CONFIG_LABEL}  sandbox=${SANDBOX}  total tasks=${#TASKS[@]}"
echo

ok=0; fail=0
for i in "${!TASKS[@]}"; do
  t="${TASKS[$i]}"
  printf '\n=== [%s] task %d/%d: %s ===\n' \
         "$(date '+%F %T')" "$((i+1))" "${#TASKS[@]}" "${t}"
  if "${RUN_AGENT}" --config "${CONFIG}" --task "${t}" --sandbox "${SANDBOX}"; then
    ok=$((ok+1))
  else
    rc=$?
    fail=$((fail+1))
    echo "  -> task failed (rc=${rc}); continuing to next."
  fi
done

echo
echo "[$(date '+%F %T')] cell ${CONFIG_LABEL} done: ${ok} ok, ${fail} failed"
