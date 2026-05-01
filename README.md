# Collider-Bench

This is an agentic AI benchmark for reproducing and recasting analyses published by the experimental collaborations at CERN's Large Hadron Collider.

## Quick start

Everything you need — sim stack (MadGraph5, Pythia8, Delphes, ROOT), Python analysis env, vendor agent CLIs (Claude / Codex / Gemini) — is baked into a single public container image.

```bash
# 1. Pull the prebuilt benchmark image using either of:
docker pull ghcr.io/dfaroughy/lhc-bench:latest
podman pull ghcr.io/dfaroughy/lhc-bench:latest
apptainer pull docker://ghcr.io/dfaroughy/lhc-bench:latest

# 2. Clone the repo and install the harness
git clone https://github.com/dfaroughy/Collider-Bench.git
cd Collider-Bench
pip install -e .                # pulls pyyaml, pydantic, numpy, scipy, ...

# 3. Install whichever vendor agent CLI(s) you want to use, on the host.
#    The container image carries the HEP runtime (conda env + sim stack)
#    but NOT vendor CLIs — you bring your own. The sandbox bind-mounts
#    your ~/.local/{bin,lib} into the container so anything installed
#    there is reachable.
npm i -g @anthropic-ai/claude-code   # Claude Code → ~/.local/bin/claude
npm i -g @openai/codex                # Codex CLI    → ~/.local/bin/codex
npm i -g @google/gemini-cli           # Gemini CLI   → ~/.local/bin/gemini
# (or any other agentic CLI you want — Grok, Cursor, Aider, etc.)

# 4. Set the API key for whichever vendor you want to use
export ANTHROPIC_API_KEY=...       # for --runner claude
export OPENAI_API_KEY=...          # for --runner codex
export GEMINI_API_KEY=...          # for --runner gemini

# 5. Run one task
scripts/run-agent --config configs/claude_simple.yaml --task <task-id>
```

The image is OCI-compatible and works with any container runtime — `docker`, `podman`, `apptainer` (HPC), `nerdctl` (k8s). Substitute the client; the image reference stays the same.

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
  simple/               Single-shot: one LLM call, score. (Add your own pattern
                        here by creating a sibling directory + run.py.)

configs/              — YAML configs with `extends: base.yaml`
scripts/              — run-agent dispatcher, launch_eval.sh
tests/                — pytest smoke suite (offline, no SLURM, no LLM calls)
```

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
