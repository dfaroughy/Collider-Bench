#!/usr/bin/env bash
# Submit the simple-agent benchmark for all three runners (claude, codex,
# gemini). Each runner gets its own SLURM array job throttled to one
# concurrent session (%1 inside --array) to keep OAuth refresh tokens
# stable. The three arrays then run in parallel — at most three nodes
# alive at any moment, one per runner — so total wall-clock is roughly
# (N_tasks * walltime) / 3 if every task uses its full 4h budget.
#
# Re-running just one runner: sbatch scripts/sbatch_simple_array.sh <runner>.
# Re-running a single task on a runner:
#   sbatch --array=<idx> scripts/sbatch_simple_array.sh <runner>
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "${REPO_ROOT}"
mkdir -p runs/_logs

for runner in claude codex gemini; do
  echo "submitting lhc-simple-${runner}..."
  sbatch --job-name="lhc-simple-${runner}" \
    "scripts/sbatch_simple_array.sh" "${runner}"
done

cat <<'EOF'

submitted. monitor with:
  squeue -u $USER -o '%.10i %.20j %.8T %.10M %.6D %R'

per-element logs land at:
  runs/_logs/lhc-simple-<runner>-<JOBID>_<ARRAYIDX>.{out,err}

per-task results at:
  runs/<runner>_<model>/<task_id>_<adj><physicist>_<hex>/
EOF
