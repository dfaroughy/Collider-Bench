#!/bin/bash
# Run the evaluation suite on a completed benchmark run.
#
# Always runs: score, rubric_scorer, plot_recast, render_eval.
# The LLM-based judge is selectable via --judge:
#   trajectory  (default) 9-mode failure-mode taxonomy (Terminal-Bench TAT,
#                         trajectory_judge.json)
#   llm                   output-correctness judge with CORRECTED provenance
#                         check (judge_scores.json)
#   both                  run both
#   none                  skip LLM judging entirely (free, offline)
#
# Results go to <run_dir>/eval/ (or <iter_dir>/eval/ for per-iter runs);
# arxiv and task are read from run_info.json by each tool.
#
# Usage:
#   ./launch_eval.sh <run_path>
#   ./launch_eval.sh <run_path> --judge llm
#   ./launch_eval.sh <run_path> --judge both
#   ./launch_eval.sh <run_path> --judge none
#
# run_path can be any of:
#   runs/simulate_<paper>_<agent>_...                    (top-level)
#   runs/simulate_<paper>_.../workspace                  (artifact dir)
#   runs/simulate_<paper>_.../validation/iter_NNN        (per-iter)
#   runs/simulate_<paper>_.../workspace/HEPRecastData    (scoring dir)

set -euo pipefail

usage() {
    echo "Usage: $0 <run_path> [--judge trajectory|llm|both|none]" >&2
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
JUDGE=trajectory

while [ $# -gt 0 ]; do
    case "$1" in
        --judge)
            [ $# -ge 2 ] || usage
            JUDGE=$2
            shift 2
            ;;
        --judge=*)
            JUDGE=${1#*=}
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

case "$JUDGE" in
    trajectory|llm|both|none) ;;
    *)
        echo "Invalid --judge=$JUDGE (expected: trajectory|llm|both|none)" >&2
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"

# Ensure the cms_analysis conda env is active so `python` resolves to the
# py3.11 interpreter with numpy/yaml/mplhep/etc. — without this, bare `python`
# on NERSC login nodes can pick up /usr/bin/python2.7.
if ! activate_cms_analysis; then
  bootstrap_cms_analysis
fi

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

echo "=== Evaluating $RUN_PATH (judge=$JUDGE) ==="

python -m LHCRecastBench.evaluation.score             "$RUN_PATH"
python -m LHCRecastBench.evaluation.rubric_scorer     "$RUN_PATH"
python -m LHCRecastBench.evaluation.plot_recast       "$RUN_PATH"

case "$JUDGE" in
    trajectory)
        python -m LHCRecastBench.evaluation.trajectory_judge "$RUN_PATH"
        ;;
    llm)
        python -m LHCRecastBench.evaluation.llm_judge        "$RUN_PATH"
        ;;
    both)
        python -m LHCRecastBench.evaluation.llm_judge        "$RUN_PATH"
        python -m LHCRecastBench.evaluation.trajectory_judge "$RUN_PATH"
        ;;
    none)
        echo "Skipping LLM judge (--judge none)"
        ;;
esac

# render_eval resolves its own output dir from the run path.
python -m LHCRecastBench.evaluation.render_eval       "$RUN_PATH"
