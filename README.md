# LHCRecast-Bench

Agentic AI benchmark for recasting CMS particle-physics papers on NERSC Perlmutter.

Each **agent** reads a CMS paper, generates + runs an `analysis.py` against public CMS Open Data (or locally simulated MC signals), and fills a HEPData YAML template with per-bin yields. Its work is then scored bin-by-bin against the paper's published tables by [`LHCRecastBench/evaluation/score.py`](LHCRecastBench/evaluation/score.py) and, optionally, judged by a second LLM.

## Quick start

```bash
# One-time: create / activate the conda environment
source /opt/cray/pe/lmod/lmod/init/bash && module load conda && conda activate cms_analysis

# Run one agent end-to-end (wraps in salloc/srun automatically)
scripts/run-agent --config configs/claude_simple.yaml

# Score + rubric + judge + plots after a run
scripts/launch_eval.sh <run_dir>

# Run the test suite
python -m pytest
```

## Repository layout

```
agent_runtime/        — how we run agents (infra)
  runners.py            Runner ABC + Claude / Codex / Aider CLI wrappers
  sandbox.py            Pluggable Sandbox (bwrap, none). See SANDBOX.md.
  launch.py             Shared single-run scaffolding
  workspace.py          build_workspace(): per-run sandbox layout
  naming.py             Run-dir naming, config schema, effort parsing
  preflight.py          AST lint for analysis.py
  stream_display.py     stream-json → terminal renderer
  shell/agent_env.sh    Conda + Lmod + SLURM bootstrap

LHCRecastBench/       — what we test against (benchmark)
  papers/               Per-paper inputs (PDF, templates, truth)
  evaluation/           score.py, rubric_scorer.py, llm_judge.py, plot_recast.py
  tools/                Agent-facing HEP libraries (streaming, sim helpers)
  bin/                  Agent-facing CLIs (hepdata, cms-opendata, read-paper, simulate)

agents/
  simple/               Single-shot: one LLM call, score.
  baseline/             Single-shot with fuller agent_context + skills/.
  iterative/            Loop: re-run simple with inherited artifacts until pass.
  sisyphus/             Three-role loop: planner → executor → critic (per iter).

configs/              — YAML configs with `extends: base.yaml`
scripts/              — run-agent dispatcher, launch_eval.sh
tests/                — pytest smoke suite (offline, no SLURM, no LLM calls)
```

## The five things you actually run

| Command | What it does |
|---|---|
| `scripts/run-agent --config configs/claude_simple.yaml` | Launch one agent on the configured paper |
| `scripts/run-agent --config configs/claude_sisyphus.yaml --max-iters 5` | Multi-iteration sisyphus run |
| `scripts/launch_eval.sh <run_dir>` | Score + judge + plot a finished run |
| `python -m pytest` | Offline smoke tests |
| `python -m LHCRecastBench.evaluation.render_eval <run_dir>` | Re-render `summary.md` from cached JSONs |

## Agent architecture at a glance

All agents share the same I/O contract: given a paper PDF + null-valued HEPRecastData YAMLs, produce filled YAMLs + `analysis.py` + `datasets.yaml` + `report.md`.

- **simple**: one Claude session, minimal guidance.
- **baseline**: one Claude session, heavier `agent_context/` and `skills/`.
- **iterative**: Python loop. Each iteration respawns the simple agent, inherits prior artifacts (analysis.py, datasets.yaml, HEPRecastData, score.json), stops when the score passes.
- **sisyphus**: three-role loop.
  - **Planner** (Sonnet, once): reads paper + templates, writes stable `plan.md`.
  - **Executor** (Opus, each iter): the actual recast. Sees `plan.md` + prior-iter `critique.md`.
  - **Critic** (Sonnet, after each non-converged iter): reads executor artifacts + reference + score.json, writes structured `critique.md` that seeds the next iter.

Each role is a separate Claude process with a separate sandbox, tool allowlist, and (optionally) model.

## Sandboxing

Agents run inside a pluggable sandbox — see [`agent_runtime/SANDBOX.md`](agent_runtime/SANDBOX.md).

The default on Linux is `bwrap`: the repo root is tmpfs'd, `workspace/` is rw-rebound, `LHCRecastBench/` is ro with `papers/` + `evaluation/` tmpfs'd to hide reference answers. `LHC_RECAST_SANDBOX=none` disables isolation (do not use for scored runs).

## Configs

Each agent + runner combo has a YAML config under [`configs/`](configs/). Configs use `extends: base.yaml` to pull shared compute defaults (account, qos, walltime). Unknown keys raise at load time (see [`agent_runtime/naming.py`](agent_runtime/naming.py)'s `ALLOWED_CONFIG_KEYS`).

```yaml
# configs/claude_simple.yaml
extends: base.yaml
agent:   simple
paper:   CMS-SUS-16-047
runner:  claude
model:   claude-opus-4-7
effort:  medium
```

CLI flags on `scripts/run-agent` override config values.

## Running the tests

```bash
python -m pytest          # all tests
python -m pytest -x       # stop on first failure
python -m pytest tests/test_workspace.py -v
```

The suite is offline and fast (~2 s). It covers: config parsing, workspace build, prompt rendering, sandbox wrapping, runner command construction. It does **not** invoke any LLM or SLURM — see [`tests/README.md`](tests/README.md) for why.

## See also

- [`agent_runtime/SANDBOX.md`](agent_runtime/SANDBOX.md) — sandbox backend contract, how to add a new one (Docker/Podman/Shifter).
- [`LHCRecastBench/BENCHMARK.md`](LHCRecastBench/BENCHMARK.md) — benchmark tool / data reference (agent-facing).
- [`LHCRecastBench/evaluation/EVAL.md`](LHCRecastBench/evaluation/EVAL.md) — scoring, rubric, and judge internals.
- [`CLAUDE.md`](CLAUDE.md) — environment notes for Claude Code assistants.
