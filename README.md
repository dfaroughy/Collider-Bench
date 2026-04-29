# Collider-Bench

This is an agentic AI benchmark for reproducing and recasting analyses published by the experimental collaborations at CERN's Large Hadron Collider.

## Quick start (benchmark users)

Everything you need — sim stack (MadGraph5, Pythia8, Delphes, ROOT), Python analysis env, vendor agent CLIs (Claude / Codex / Gemini) — is baked into a single public container image.

```bash
# 1. Pull the prebuilt benchmark image (once, ~3.5 GB)
docker pull ghcr.io/dfaroughy/lhc-bench:latest
#  Or, with podman / apptainer / singularity:
#    podman    pull ghcr.io/dfaroughy/lhc-bench:latest
#    apptainer pull docker://ghcr.io/dfaroughy/lhc-bench:latest

# 2. Clone the harness
git clone https://github.com/dfaroughy/LHCRecast-Bench.git
cd LHCRecast-Bench

# 3. Set the API key for whichever vendor you want to use
export ANTHROPIC_API_KEY=...      # for --runner claude
# or:  export OPENAI_API_KEY=...  # for --runner codex
# or:  export GEMINI_API_KEY=...  # for --runner gemini
# (You only need the key for the vendor you actually invoke.)

# 4. Run one task
scripts/run-agent --config configs/claude_simple.yaml --task <task-id>
```

The image is OCI-compatible and works with any container runtime — `docker`, `podman`, `apptainer` (HPC), `nerdctl` (k8s). Substitute the client; the image reference stays the same.

## Quick start (developers — NERSC dev shell)

```bash
# One-time: create / activate the conda environment
source /opt/cray/pe/lmod/lmod/init/bash && module load conda && conda activate lhc_analysis

# Run one agent end-to-end (wraps in salloc/srun automatically)
scripts/run-agent --config configs/claude_simple.yaml

# Score + rubric + judge + plots after a run
scripts/launch_eval.sh <run_dir>

# Run the test suite
python -m pytest

# Image audit (slower, requires a built local image)
python -m pytest -m image
```

## Repository layout

```
agent_runtime/        — how we run agents (infra)
  runners.py            Runner ABC + Claude / Codex / Aider CLI wrappers
  sandbox.py            Pluggable Sandbox (podman, apptainer, bwrap, none). See SANDBOX.md.
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
  anneal/               Three-role loop with temperature schedule + stochastic rollback:
                        planner → executor → examiner (per iter).

configs/              — YAML configs with `extends: base.yaml`
scripts/              — run-agent dispatcher, launch_eval.sh
tests/                — pytest smoke suite (offline, no SLURM, no LLM calls)
```

## The five things you actually run

| Command | What it does |
|---|---|
| `scripts/run-agent --config configs/claude_simple.yaml` | Launch one agent on the configured paper |
| `scripts/run-agent --config configs/claude_anneal.yaml --max-iters 5` | Multi-iteration anneal run |
| `scripts/launch_eval.sh <run_dir>` | Score + judge + plot a finished run |
| `python -m pytest` | Offline smoke tests |
| `python -m LHCRecastBench.evaluation.render_eval <run_dir>` | Re-render `summary.md` from cached JSONs |

## Agent architecture at a glance

All agents share the same I/O contract: given a paper PDF + null-valued HEPRecastData YAMLs, produce filled YAMLs + `analysis.py` + `datasets.yaml` + `report.md`.

- **simple**: one Claude session, minimal guidance.
- **baseline**: one Claude session, heavier `agent_context/` and `skills/`.
- **iterative**: Python loop. Each iteration respawns the simple agent, inherits prior artifacts (analysis.py, datasets.yaml, HEPRecastData, score.json), stops when the score passes.
- **anneal**: three-role loop with simulated-annealing dynamics.
  - **Planner** (cheap tier, once): reads paper + templates, writes stable `plan.md`.
  - **Executor** (strong tier, each iter): the actual recast. Sees `plan.md` (which the examiner keeps up-to-date in place) and a temperature blurb that nudges exploration vs. refinement.
  - **Examiner** (cheap tier, after each non-converged iter): reads executor artifacts + paper, rewrites `plan.md` with concrete fixes, and maintains an examiner-only `proposals_log.md` of which past fixes worked. The executor never sees the proposals log; the examiner never sees the score or the reference values.
  - **Annealing** (controller-side): a temperature schedule (`linear`/`cosine`/`none`) drives carry-forward depth (high T wipes more state) and stochastic rollback — on regression, the next iter's seed is rewound to the best-so-far iter's workspace with probability `1 - exp(-Δ/T)`, capped by `max_rollbacks`.

Each role is a separate Claude process with a separate sandbox, tool allowlist, and (optionally) model.

## Sandboxing

Agents run inside a pluggable sandbox — see [`agent_runtime/SANDBOX.md`](agent_runtime/SANDBOX.md).

The default is `podman` (falls back to `apptainer` on hosts where podman isn't installed). The agent runs inside the canonical `lhc-bench` image: `workspace/` is rw-bound, `LHCRecastBench/` is ro with `papers/` + `evaluation/` + each task's `template/` and reference answers tmpfs'd to hide them. `LHC_RECAST_SANDBOX=none` disables isolation (do not use for scored runs); `--sandbox bwrap` is available as an opt-in escape hatch but bypasses the container.

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

## Developer setup

After cloning:

```bash
pip install pre-commit
pre-commit install            # installs the git hook (one-time per clone)
```

From then on, every `git commit` runs the hooks in [`.pre-commit-config.yaml`](.pre-commit-config.yaml): whitespace cleanup, YAML validation, `ruff` lint + format, secrets scan. Commits with violations are rejected until fixed (most are auto-fixed in place — just `git add` and retry).

Ad-hoc run on the whole tree:

```bash
pre-commit run --all-files
```

## Continuous integration

[`.github/workflows/test.yml`](.github/workflows/test.yml) runs on every push and PR to `main`:

- **`pytest`** on Python 3.10 / 3.11 / 3.12 (matrix).
- **`pre-commit`** on all files (fails the job if any hook would make a change).

CI runners don't have `claude` / `codex` / `bwrap` installed; the tests that depend on them self-skip cleanly.

## See also

- [`agent_runtime/SANDBOX.md`](agent_runtime/SANDBOX.md) — sandbox backend contract, how to add a new one (Docker/Podman/Shifter).
- [`LHCRecastBench/BENCHMARK-OVERVIEW.md`](LHCRecastBench/BENCHMARK-OVERVIEW.md) — what the benchmark provides: workspace layout, task types, scoring interface (researcher-facing).
- [`LHCRecastBench/tools/TOOLS.md`](LHCRecastBench/tools/TOOLS.md) — tool index (agent-facing; seeded into every workspace).
- [`LHCRecastBench/evaluation/EVAL.md`](LHCRecastBench/evaluation/EVAL.md) — scoring, rubric, and judge internals.
- [`CLAUDE.md`](CLAUDE.md) — environment notes for Claude Code assistants.
