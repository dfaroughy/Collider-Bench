#!/usr/bin/env bash
# Sequentially run the offline eval over every run in a given run-N directory.
# Default target: codex_gpt-5.5/run-1.
#
# Behavior per run:
#   1. score (always): python -m ColliderBench.Evals.score <run>
#                      → writes eval/score.json + eval/plots/*.png
#   2. judge (opt-in): python -m ColliderBench.Evals.judge <run>
#                      → only when --judge is passed
#
# Usage:
#   ./utils/run_evals_with_judge.sh                                  # score+plots, no judge
#   ./utils/run_evals_with_judge.sh --judge                          # also run judge
#   ./utils/run_evals_with_judge.sh runs/claude_haiku-4-5/run-2
#   ./utils/run_evals_with_judge.sh --judge --judge-model claude-sonnet-4-6
#   ./utils/run_evals_with_judge.sh --n-toys 10000                   # faster shape p-value
#   ./utils/run_evals_with_judge.sh --skip-score --judge             # judge only
#
# The judge calls the `claude` CLI with whatever auth your shell has.
# Run-agent strips ANTHROPIC_API_KEY on OAuth-intended runs; here we leave
# the env untouched so you can pick auth via your shell as usual.

set -uo pipefail   # not -e — one failing run shouldn't kill the loop

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
TARGET=""
RUN_JUDGE=0
SKIP_SCORE=0
JUDGE_MODEL=""
N_TOYS=1000000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --judge)        RUN_JUDGE=1; shift ;;
    --skip-score)   SKIP_SCORE=1; shift ;;
    --judge-model)  JUDGE_MODEL="$2"; shift 2 ;;
    --n-toys)       N_TOYS="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,/^set -uo/p' "${BASH_SOURCE[0]}" | sed -n '/^#/p' | sed 's/^# \?//'
      exit 0 ;;
    -*)
      echo "unknown flag: $1" >&2; exit 2 ;;
    *)
      if [[ -n "${TARGET}" ]]; then
        echo "extra positional arg: $1 (already have target=${TARGET})" >&2; exit 2
      fi
      TARGET="$1"; shift ;;
  esac
done
TARGET="${TARGET:-runs/codex_gpt-5.5/run-1}"

TARGET_ABS="${TARGET}"
[[ "${TARGET}" != /* ]] && TARGET_ABS="${REPO_ROOT}/${TARGET}"
if [[ ! -d "${TARGET_ABS}" ]]; then
  echo "target not a directory: ${TARGET_ABS}" >&2
  exit 2
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/agent_runtime/shell/agent_env.sh"
activate_lhc_analysis >/dev/null 2>&1 || bootstrap_lhc_analysis

mapfile -t RUNS < <(find "${TARGET_ABS}" -mindepth 1 -maxdepth 1 -type d \
                      \( -name 'sus-*' -o -name 'exo-*' -o -name 'cms-*' \) 2>/dev/null | sort)
if [[ ${#RUNS[@]} -eq 0 ]]; then
  echo "no run directories under ${TARGET_ABS}" >&2
  exit 2
fi

echo "[$(date '+%F %T')] target=${TARGET_ABS}  runs=${#RUNS[@]}  score=$([[ "${SKIP_SCORE}" == 0 ]] && echo on || echo off)  judge=$([[ "${RUN_JUDGE}" == 1 ]] && echo on || echo off)"
echo

ok_score=0; fail_score=0
ok_judge=0; fail_judge=0
for i in "${!RUNS[@]}"; do
  run="${RUNS[$i]}"
  printf '\n=== [%s] %d/%d: %s ===\n' \
         "$(date '+%F %T')" "$((i+1))" "${#RUNS[@]}" "$(basename "${run}")"

  if [[ "${SKIP_SCORE}" == "0" ]]; then
    if python -m ColliderBench.Evals.score --n-toys "${N_TOYS}" "${run}"; then
      ok_score=$((ok_score+1))
    else
      fail_score=$((fail_score+1))
      echo "  -> score failed; continuing."
    fi
  fi

  if [[ "${RUN_JUDGE}" == "1" ]]; then
    judge_args=(-m ColliderBench.Evals.judge)
    [[ -n "${JUDGE_MODEL}" ]] && judge_args+=(--model "${JUDGE_MODEL}")
    judge_args+=("${run}")
    if python "${judge_args[@]}"; then
      ok_judge=$((ok_judge+1))
    else
      fail_judge=$((fail_judge+1))
      echo "  -> judge failed; continuing."
    fi
  fi
done

echo
echo "[$(date '+%F %T')] done."
[[ "${SKIP_SCORE}" == "0" ]] && echo "  score: ${ok_score} ok / ${fail_score} failed"
[[ "${RUN_JUDGE}" == "1" ]] && echo "  judge: ${ok_judge} ok / ${fail_judge} failed"
