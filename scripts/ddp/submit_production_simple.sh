#!/usr/bin/env bash
# Production benchmark for the `simple` agent across 6 (runner, model) cells.
#
# Submits three lanes — one per provider — each containing two array jobs
# (different model variants). Within a lane, the second job is dependent
# (`afterany`) on the first, so only one Claude/Codex/Gemini session per
# subscription is in flight at any moment. This sidesteps the OAuth
# refresh-token race we'd otherwise hit when two containers from the same
# account try to refresh independently.
#
# Across lanes, the three providers run in parallel (≤ 3 nodes alive at
# once). Total wall-clock ≈ 2 × 29 × walltime per lane.
#
# Lanes (config labels under configs/):
#   claude lane  : claude_simple        → claude_sonnet_simple
#   codex lane   : codex_simple         → codex_gpt50_simple
#   gemini lane  : gemini_simple        → gemini_pro25_simple
#
# Each cell expands to 29 tasks via the array job in scripts/sbatch_simple_array.sh.
#
# Re-running just one cell:
#   sbatch --job-name=lhc-simple-<label> scripts/sbatch_simple_array.sh <label>
# Re-running a single failing array index:
#   sbatch --job-name=lhc-simple-<label> --array=<idx> \
#       scripts/sbatch_simple_array.sh <label>
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

# Each line: lane name, then space-separated config labels in submission
# order. Listing "primary" (default) first means that's what runs while
# the second is queued behind it.
LANES=(
  "claude  claude_simple        claude_sonnet_simple"
  "codex   codex_simple         codex_gpt50_simple"
  "gemini  gemini_simple        gemini_pro25_simple"
)

echo "submitting 6 (runner, model) cells across 3 OAuth-isolated lanes."
echo
for line in "${LANES[@]}"; do
  read -r lane labels <<<"${line}"
  prev_jobid=""
  for cfg in ${labels}; do
    # Validate config exists before we hit sbatch (clearer error).
    if [[ ! -f "configs/${cfg}.yaml" ]]; then
      echo "  ERROR: configs/${cfg}.yaml missing — skipping ${cfg}" >&2
      continue
    fi
    deps=()
    if [[ -n "${prev_jobid}" ]]; then
      deps=(--dependency="afterany:${prev_jobid}")
    fi
    out=$(sbatch \
      "${deps[@]+"${deps[@]}"}" \
      --job-name="lhc-simple-${cfg}" \
      "scripts/sbatch_simple_array.sh" "${cfg}")
    jobid=$(echo "${out}" | grep -oE '[0-9]+' | head -1)
    if [[ -z "${prev_jobid}" ]]; then
      printf '  [%-7s] %-25s → job %s   (running)\n' "${lane}" "${cfg}" "${jobid}"
    else
      printf '  [%-7s] %-25s → job %s   (waits for %s)\n' "${lane}" "${cfg}" "${jobid}" "${prev_jobid}"
    fi
    prev_jobid="${jobid}"
  done
done

cat <<'EOF'

monitor:    squeue -u $USER -o '%.10i %.30j %.10T %.10M %.10L %R'
logs:       runs/_logs/lhc-simple-<label>-<JOBID>_<ARRAYIDX>.{out,err}
results:    runs/<runner>_<model>/<task_id>_<adj><physicist>_<hex>/

NOTE: with NERSC's regular qos currently backed up (~6 days), expect long
queue time before the first elements start. Once the first per-lane job
runs and finishes, the chained second job inherits its turn — the
dependency just waits on completion, it does not re-queue.
EOF
