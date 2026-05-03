#!/usr/bin/env bash
# Sequentially run the benchmark tasks against one (runner, model)
# config via interactive-qos allocations. Faster than the regular-queue
# array job when the regular queue is deep, but capped at 4h walltime
# per task by NERSC interactive qos. Sequential = OAuth-safe by
# construction: only one CLI session is ever in flight against the
# subscription.
#
# Usage:
#   bash scripts/multi-run-interactive-claude.sh                # default: anthropics/claude_sonnet
#   bash scripts/multi-run-interactive-claude.sh anthropics/claude_opus
#   ...
#
# To run multiple (runner, model) cells in parallel, launch this script
# in separate terminals (or under tmux) — NERSC's interactive qos lets
# 1-2 nodes per user run simultaneously; additional invocations queue.

set -uo pipefail   # not -e — keep going if one task fails so the rest of the cell still runs

CONFIG_LABEL="${1:-anthropics/claude_sonnet}"
REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
if [[ "${CONFIG_LABEL}" == /* || "${CONFIG_LABEL}" == *.yaml ]]; then
  CONFIG="${CONFIG_LABEL}"
else
  CONFIG="${REPO_ROOT}/configs/${CONFIG_LABEL}.yaml"
fi
RUN_AGENT="${REPO_ROOT}/scripts/run-agent"
SANDBOX="${LHC_RECAST_SANDBOX:-}"
SANDBOX_ARGS=()
if [[ -n "${SANDBOX}" ]]; then
  SANDBOX_ARGS=(--sandbox "${SANDBOX}")
fi

if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 2
fi


TASKS=(
  sus-16-051_shape-T2tt_SRG
  sus-16-051_shape-T2bW_SRG
  sus-16-051_shape-T2tt_comp
  sus-16-051_sim-T2tt_SRG
  sus-16-051_sim-T2bW_SRG
  sus-16-051_sim-T2tt_comp
)

cd "${REPO_ROOT}"
echo "[$(date '+%F %T')] config=${CONFIG_LABEL}  sandbox=${SANDBOX:-config}  total tasks=${#TASKS[@]}"
echo

ok=0; fail=0
for i in "${!TASKS[@]}"; do
  t="${TASKS[$i]}"
  printf '\n=== [%s] task %d/%d: %s ===\n' \
         "$(date '+%F %T')" "$((i+1))" "${#TASKS[@]}" "${t}"
  if "${RUN_AGENT}" --config "${CONFIG}" --task "${t}" "${SANDBOX_ARGS[@]}"; then
    ok=$((ok+1))
  else
    rc=$?
    fail=$((fail+1))
    echo "  -> task failed (rc=${rc}); continuing to next."
  fi
done

echo
echo "[$(date '+%F %T')] cell ${CONFIG_LABEL} done: ${ok} ok, ${fail} failed"
