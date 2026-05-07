#!/bin/bash
# Run the evaluation suite on a completed benchmark run.
#
# Always runs: LHCRecastBench.Evals.score (writes eval/score.json + eval/plots/).
#
# Pass --judge to run the LLM provenance audit + trajectory narrative
# (eval/judge_scores.json + eval/judge_trajectory.md). Without --judge,
# evaluation is offline-only.
#
# `score.json` is the only metric artifact — no derived summary.md is
# produced. task_id + paper are read from run_info.json + task.toml.
#
# Usage:
#   ./launch_eval.sh <run_path>
#   ./launch_eval.sh /global/cfs/cdirs/m4539/ColliderBench/<run_path> --judge
#
# run_path is the run directory: runs/<runner>_<model>/<task_id>_<hex>/

set -euo pipefail

usage() {
    echo "Usage: $0 <run_path> [--judge]" >&2
    exit 2
}

if [ $# -lt 1 ]; then
    usage
fi

case "$1" in
    -h|--help) usage ;;
esac

RUN_PATH=$1
shift
RUN_JUDGE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --judge)
            RUN_JUDGE=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"

# Ensure the lhc_analysis conda env is active so `python` resolves to the
# py3.11 interpreter with numpy/yaml/mplhep/etc. — without this, bare `python`
# on NERSC login nodes can pick up /usr/bin/python2.7.
if ! activate_lhc_analysis; then
  bootstrap_lhc_analysis
fi

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

if [ "$RUN_JUDGE" -eq 1 ]; then
    echo "=== Evaluating $RUN_PATH (judge=llm) ==="
else
    echo "=== Evaluating $RUN_PATH (judge=off) ==="
fi

# score.py handles both metrics and plotting in one pass (drop --no-plots
# to suppress PNG generation when iterating quickly).
python -m LHCRecastBench.Evals.score "$RUN_PATH"

if [ "$RUN_JUDGE" -eq 1 ]; then
    python -m LHCRecastBench.Evals.judge "$RUN_PATH"
else
    echo "Skipping LLM judge (pass --judge to run it)"
fi
