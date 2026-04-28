#!/bin/bash
# Run the evaluation suite on a completed benchmark run.
#
# Always runs: score, plot_recast, render_eval (offline, no LLM cost).
#
# Pass --judge to run the LLM provenance audit + trajectory narrative
# (judge_scores.json). Without --judge, evaluation is offline-only.
#
# Results go to <run_dir>/eval/ (or <iter_dir>/eval/ for per-iter runs);
# task_id + paper are read from run_info.json + task.toml by each tool.
#
# Usage:
#   ./launch_eval.sh <run_path>
#   ./launch_eval.sh <run_path> --judge
#
# run_path can be any of:
#   runs/<runner>_<model>/<task_id>_<hex>                 (top-level)
#   runs/<runner>_<model>/<task_id>_<hex>/workspace       (artifact dir)
#   runs/<runner>_<model>/<task_id>_<hex>/validation/iter_NNN  (per-iter)
#   runs/<runner>_<model>/<task_id>_<hex>/workspace/results     (scoring dir)

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

# Ensure the cms_analysis conda env is active so `python` resolves to the
# py3.11 interpreter with numpy/yaml/mplhep/etc. — without this, bare `python`
# on NERSC login nodes can pick up /usr/bin/python2.7.
if ! activate_cms_analysis; then
  bootstrap_cms_analysis
fi

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

if [ "$RUN_JUDGE" -eq 1 ]; then
    echo "=== Evaluating $RUN_PATH (judge=llm) ==="
else
    echo "=== Evaluating $RUN_PATH (judge=off) ==="
fi

python -m LHCRecastBench.evaluation.score             "$RUN_PATH"
python -m LHCRecastBench.evaluation.plot_recast       "$RUN_PATH"

if [ "$RUN_JUDGE" -eq 1 ]; then
    python -m LHCRecastBench.evaluation.llm_judge "$RUN_PATH"
else
    echo "Skipping LLM judge (pass --judge to run it)"
fi

# render_eval resolves its own output dir from the run path.
python -m LHCRecastBench.evaluation.render_eval       "$RUN_PATH"
