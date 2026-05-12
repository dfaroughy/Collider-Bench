# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Collider-Bench is a benchmark for evaluating whether LLM agents can reproduce experimental analyses from the Large Hadron Collider using only public papers and open scientific software. Each task gives an agent a CMS analysis paper + a null-filled HEPData-style results template; the agent must build a simulation + selection pipeline and fill in predicted bin yields. The harness scores submissions against published reference values using a relative-L² metric, runs everything inside a pinned container, and supports the major agent CLIs (Claude, Codex, Gemini, Aider, Forge).

The repo has three logical layers:
1. **`ColliderBench/`** — the benchmark (tasks, scoring, agent-facing tools).
2. **`agent_runtime/`** — the harness that launches an agent against a task.
3. **`agents/`** — agent implementations (the public one is `simple`: one-shot LLM call → score).

## Essential Commands

### One-time setup

```bash
# 1. Clone + install the harness
git clone https://github.com/dfaroughy/Collider-Bench.git
cd Collider-Bench
pip install -e ".[dev]"            # dev = pytest + pytest-xdist

# 2. Pull the prebuilt benchmark image (MadGraph5 + Pythia8 + Delphes + ROOT
#    + a conda env are baked in). Pick whichever container engine you have:
docker     pull ghcr.io/dfaroughy/lhc-bench:latest
podman     pull ghcr.io/dfaroughy/lhc-bench:latest
apptainer  pull docker://ghcr.io/dfaroughy/lhc-bench:latest
singularity pull lhc-bench.sif docker://ghcr.io/dfaroughy/lhc-bench:latest

# 3. Install one agent CLI on the host (the sandbox mounts it into the container)
npm i -g @anthropic-ai/claude-code   # → ~/.local/bin/claude
npm i -g @openai/codex                # → ~/.local/bin/codex
npm i -g @google/gemini-cli           # → ~/.local/bin/gemini

# 4. Set the API key for whichever vendor you're using
export ANTHROPIC_API_KEY=...     # for --runner claude (API auth)
export OPENAI_API_KEY=...        # for --runner codex
export GEMINI_API_KEY=...        # for --runner gemini
export DEEPSEEK_API_KEY=...      # for --runner forge --provider deepseek

# 5. Install pre-commit hooks (optional but recommended for contributors)
pip install pre-commit && pre-commit install
```

### Running one task

```bash
# Production entry point — reads YAML config, builds a sandboxed workspace,
# launches the agent CLI, scores the result, writes runs/<runner>_<model>/<task>/.
scripts/run-agent --config configs/anthropics/claude_sonnet.yaml \
                  --task sus-16-047_sim-T5Wg_lowHT
```

### Re-scoring or judging an existing run

```bash
# Offline scoring only (no LLM cost). Writes eval/score.json + eval/plots/.
scripts/launch_eval.sh runs/codex_gpt-5.5/run-1/sus-16-047_sim-T5Wg_lowHT_AgileFermi_12abc34d/

# Also run the LLM provenance audit (costs subscription tokens).
scripts/launch_eval.sh runs/<path>  --judge
```

### Running the test suite

```bash
# Full offline smoke suite (no SLURM, no LLM, no network) — ~10 s
python -m pytest                     # full suite (default)
python -m pytest -x -v               # stop on first fail, verbose
python -m pytest tests/test_pricing.py   # single file
```

The `image` marker covers container audits — skipped by default unless `podman-hpc` + a built `lhc-bench` image are available. Run with `pytest -m image` to opt in.

### Linting

```bash
ruff check .                         # lint
ruff format .                        # format in place
pre-commit run --all-files           # everything (ruff + whitespace + secrets)
```

## High-Level Architecture

### Core components

1. **`agent_runtime/launch.py`** — the entry function called by `scripts/run-agent`. Loads a YAML config, builds a workspace, invokes the right runner via `runner_spec.py`, scores the output, finalizes `run_info.json`.

2. **`agent_runtime/workspace.py`** — `build_workspace(repo_root, agent, task, run_dir)` creates `runs/<run_dir>/workspace/` with the canonical layout: `agent_context/` (TASK.md, AGENTS.md), `bin/` (run-analysis + agent CLI symlinks), `tools/` (read-only mount), `papers/`, `results/` (null-filled template the agent fills in place), `object_efficiencies/`.

3. **`agent_runtime/sandbox.py`** — pluggable Sandbox abstraction. Backends: `podman` (default), `apptainer` / `singularity`, `bwrap` (escape hatch, bypasses the container image), `none` (no isolation — never use for scored runs). See `agent_runtime/SANDBOX.md`.

4. **`agent_runtime/runners.py` + `runner_spec.py` + `vendors.py`** — Runner ABC + a declarative `RunnerSpec` for each vendor. The five registered runners are `claude`, `codex`, `gemini`, `aider`, `forge`. Each spec encodes the CLI invocation, stream-json parser, and auth model.

5. **`agent_runtime/config.py`** — YAML config loader + validator. Configs use `extends:` to inherit from compute profiles in `configs/utils/`. Validates required env vars at launch (`validate_api_auth_env`) so a missing `ANTHROPIC_API_KEY` fails fast instead of mid-run.

6. **`ColliderBench/Evals/`** — scoring module.
   - `score.py:score_run()` is the canonical scorer. Reads the agent's `results/*.yaml`, aligns bins to the hidden reference, writes `eval/score.json` + `eval/plots/*.png`.
   - `judge.py:run_judge()` is the optional LLM-based provenance audit. Reads the stream-json trajectory + filled results, classifies each series (`VERIFIED` / `FABRICATED` / `COPIED_OR_LEAKED` / …), writes `eval/judge_scores.json` + `eval/judge_trajectory.md`, and (if it found correctable values) writes `eval/results_corrected_by_judge/`.
   - `metrics/` — `bin_error.relative_l2`, `Delta`, `baker_cousins_p_value`, `jensen_shannon`, `rmsle`, `per_bin_disagreement`. The primary metric is `relative_l2` (norm and shape).

7. **`ColliderBench/tasks/`** — ten `sim` tasks (the shipped corpus) plus `secondary_tasks/` (shape, yield, val variants for diagnostics) and `shared/` (paper PDFs + hidden references). Each `sus-16-xxx_sim-XXX/` directory has `TASK.md`, `task.toml`, `template/*.yaml`.

8. **`ColliderBench/tools/`** — agent-facing HEP libraries (`streaming.py`, sim helpers) and CLIs (`hepdata`, `cms-opendata`, `read-paper`, `simulate`, `feynrules`, `prospino`). The agent calls these via `$PATH` inside the sandbox.

9. **`agents/simple/run.py`** — the public reference agent. One LLM call, no retries, no planning. New agent patterns go in a sibling directory.

### Key architectural patterns

- **One workspace per run** — every run gets a fresh `runs/<runner>_<model>/<run-label>/<task>_<hash>/workspace/` so concurrent runs never collide.
- **Hidden vs. agent-visible separation** — the agent's workspace exposes `tasks/<task>/template/` and `tasks/shared/<paper>/paper/`, but **not** `tasks/shared/<paper>/reference/`. The scorer only resolves the reference at score-time, so the agent cannot leak the answer key.
- **Declarative RunnerSpec** — adding a new agent CLI means dropping a `RunnerSpec(...)` into `vendors.py`, not subclassing.
- **Pluggable sandbox** — `LHC_RECAST_SANDBOX=apptainer` or `--sandbox bwrap` switches backends without touching code.
- **YAML config inheritance** — runnable configs `extends: ../utils/perlmutter_interactive.yaml` (compute profile) and override only what differs. Validation rejects unknown keys.
- **Status filter on replicates** — `runs.json` carries a `status ∈ {pass, hung, wall, cheat}` per replicate. Downstream metrics drop `cheat` runs and keep `hung`/`wall` (which often produced legitimate partial numbers before halting).

### Output schema

A completed run writes:

```
runs/<runner>_<model>/<run-label>/<task>_<hash>/
├── workspace/                  # agent's filesystem
│   ├── agent_context/          # TASK.md, AGENTS.md, TOOLS.md, …
│   ├── bin/                    # agent CLI + run-analysis on $PATH
│   ├── results/*.yaml          # the agent fills these in place
│   └── papers/                 # symlink to the paper PDF
├── eval/
│   ├── score.json              # canonical metric output
│   ├── plots/*.png             # reference vs agent histograms
│   ├── judge_scores.json       # only if --judge was passed
│   └── judge_trajectory.md
├── run_info.json               # wall_s, cost_usd, tokens_total_billed, …
└── *.stream.jsonl              # raw agent stream-json
```

`score.json` schema (key fields): `task_id`, `paper`, `score_mode` (`yield_norm`, `shape`, or `shape_norm`), `normalization.{Delta, relative_l2, rmsle, mean_abs_frac_error_pct}`, `shape.{p_value, relative_l2, jensen_shannon, d_bar, d_max}`, `n_bins`, `n_filled`.

## Configs

Configs are grouped by harness:

- `configs/anthropics/claude_*.yaml`
- `configs/openai/codex_*.yaml`
- `configs/google/gemini_*.yaml`
- `configs/forgecode/forge_*.yaml`
- `configs/utils/` — compute profiles (local, perlmutter_interactive, perlmutter_api, etc.) that runnable configs `extends:`.

CLI flags on `scripts/run-agent` override config values. Allowed agents: `simple`, `anneal` (`anneal` is gitignored — kept locally only).

## Tasks

Ten primary sim tasks under `ColliderBench/tasks/sus-16-xxx_sim-YYY/`. Each one is one CMS paper × one signal model × one observable. The agent gets `TASK.md` + the null-filled `template/*.yaml`; success means filling the template's `values:` with predicted bin yields and writing `report.md`.

`task.toml` holds machine-readable metadata (paper id, score mode, walltime, tolerance) that the harness reads but doesn't expose to the agent. The agent only sees `TASK.md`.

## Important development notes

- **Python >=3.10** required (`from __future__ import annotations` is used throughout; `match` statements appear in a few places).
- **Status filter when computing metrics**: `cheat` replicates are dropped; `hung` and `wall` are kept (they often produced legitimate partial numbers).
- **DeepSeek list-price gross-up**: pricing.py applies a 4× factor for `forge_deepseek-v4-pro` and 8× for `forge_deepseek-r1` so the Pareto x-axis reflects list price, not the active promotional discount. The promo expiry is hardcoded at `2026-05-31`; after that the table emits a `warnings.warn` at import time and should be updated.
- **Auth-env validation runs at config load** — a misconfigured `auth: api` with no `ANTHROPIC_API_KEY` raises before the runner is invoked.
- **Pre-commit hooks** — `.pre-commit-config.yaml` runs whitespace cleanup, YAML check, large-file guard, ruff lint+format, and `detect-secrets`. The secrets baseline excludes `ColliderBench/tools/sim/` (vendored simulators), `runs/`, `old/`, and `utils/neurips/*.json`.
- **Vendored simulators are not modified** — `ColliderBench/tools/sim/{MG5_aMC_v3_7_0, delphes, pythia8313, HepMC3, fastjet}` are upstream code; the rename sweep, the lint config, and the secrets scan all explicitly skip them.
- **`utils/` is gitignored** — local research scratchpad (NeurIPS plot scripts, runs.json snapshot, batch eval wrapper). Not part of the public benchmark. The public eval driver is `scripts/launch_eval.sh`.
- **Run directories live under `runs/`** — also gitignored. Each run is ~10-100 MB after scoring (mostly Delphes ROOT files and stream-json logs).
- **`scripts/run-agent` is the only entrypoint you should normally call**. It dispatches to `agent_runtime.launch:main` after handling SLURM allocation (on Perlmutter) or running locally otherwise.

## Common pitfalls

- **`Task missing: ...sus-16-XXX_shape-YYY`** — the active corpus is sim tasks only. Shape/yield/val variants live in `secondary_tasks/`. Configs that still reference them in the task field will fail.
- **`run-agent` silently picks API auth despite OAuth config** — the `claude` CLI prefers `ANTHROPIC_API_KEY` over the OAuth login if both are set. `run-agent` unsets the env var when `auth: oauth` is configured; do not source `.api_keys.env` manually before launching.
- **Submodule dirty after pull** — `ColliderBench/tools/sim/delphes` and `pythia8313` are git submodules of upstream releases. `git status` will report them as modified if internal build artifacts are present; that's expected.

## Where to look first

| If you want to … | Read |
|---|---|
| Add a new model / vendor | `agent_runtime/vendors.py`, `agent_runtime/runner_spec.py` |
| Add a new task | `ColliderBench/tasks/sus-16-046_sim-T5Wg/` (smallest exemplar) |
| Change scoring | `ColliderBench/Evals/score.py`, `ColliderBench/Evals/metrics/` |
| Change the judge prompt | `ColliderBench/Evals/judge_rubric.md` |
| Change sandbox behavior | `agent_runtime/sandbox.py`, `agent_runtime/SANDBOX.md` |
| Tweak a config | `configs/<vendor>/<model>.yaml` and the profile it `extends:` |
| Wire up a new agent pattern | `agents/simple/run.py` (clone, sibling dir) |
