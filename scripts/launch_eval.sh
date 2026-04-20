#!/bin/bash
# Run the full evaluation suite on a completed recast run.
#
# The arXiv ID is read from <run_dir>/run_info.json, so only the run dir (or
# its workspace/) needs to be passed.
#
# Usage:
#   ./launch_eval.sh <recast_run_dir_or_workspace>
#
# Examples:
#   ./launch_eval.sh recast_1707.06193_simple_claude-opus-4-7_ArcaneLagrange_ab12cd34
#   ./launch_eval.sh recast_.../workspace

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <recast_dir_or_workspace>" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"

# Ensure the cms_analysis conda env is active so `python` resolves to the
# py3.11 interpreter with numpy/yaml/mplhep/etc. — without this, bare `python`
# on NERSC login nodes can pick up /usr/bin/python2.7.
if ! activate_cms_analysis; then
  bootstrap_cms_analysis
fi

TARGET=$1

# Accept either the top-level run dir or the workspace itself.
if [ -d "$TARGET/HEPRecastData" ]; then
    WS="$TARGET"
    RUN_DIR="$(dirname "$TARGET")"
elif [ -d "$TARGET/workspace/HEPRecastData" ]; then
    WS="$TARGET/workspace"
    RUN_DIR="$TARGET"
else
    echo "ERROR: no HEPRecastData/ found under $TARGET (tried $TARGET and $TARGET/workspace)" >&2
    exit 1
fi

# Walk up from WS to find run_info.json — handles iter dirs under
# validation/ where run_info lives two levels above the HEPRecastData dir.
INFO=""
probe="$RUN_DIR"
for _ in 1 2 3; do
    if [ -f "$probe/run_info.json" ]; then
        INFO="$probe/run_info.json"
        RUN_DIR="$probe"
        break
    fi
    probe="$(dirname "$probe")"
done
if [ -z "$INFO" ]; then
    echo "ERROR: run_info.json not found anywhere above $TARGET." >&2
    echo "       Either the run didn't complete setup, or this isn't a recast run dir." >&2
    exit 1
fi

ARXIV=$(python -c "import json,sys; print((json.load(open(sys.argv[1])).get('paper_ref') or '').strip())" "$INFO")
if [ -z "$ARXIV" ]; then
    echo "ERROR: paper_ref missing from $INFO" >&2
    exit 1
fi

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

echo "=== Evaluating $WS (paper $ARXIV, from run_info.json) ==="

python -m LHCRecastBench.evaluation.score         "$ARXIV" --recast-dir "$WS/HEPRecastData"
python -m LHCRecastBench.evaluation.rubric_scorer --arxiv "$ARXIV" --agent-dir  "$WS"
python -m LHCRecastBench.evaluation.plot_recast   --arxiv "$ARXIV" --recast-dir "$WS/HEPRecastData"
python -m LHCRecastBench.evaluation.llm_judge     --arxiv "$ARXIV" --agent-dir  "$WS"

# Scorers write to <WS>/../eval/. For iter dirs (WS=.../validation/iter_NNN)
# that lands in validation/eval/, which clobbers across iters — move it into
# the iter dir itself so each iter has its own eval/ and render_eval finds it.
EVAL_SRC="$(dirname "$WS")/eval"
if [[ "$(basename "$(dirname "$WS")")" == "validation" ]] && [ -d "$EVAL_SRC" ]; then
    EVAL_DEST="$WS/eval"
    rm -rf "$EVAL_DEST"
    mv "$EVAL_SRC" "$EVAL_DEST"
    python -m LHCRecastBench.evaluation.render_eval "$WS"
    echo
    echo "Eval outputs in $EVAL_DEST/ (see summary.md)"
else
    python -m LHCRecastBench.evaluation.render_eval "$RUN_DIR"
    echo
    echo "Eval outputs in $RUN_DIR/eval/ (see summary.md)"
fi
