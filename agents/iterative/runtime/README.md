# Runtime

Controller machinery for the iterative agent. The iterative agent wraps the
**simple** agent (`agents/simple/`) in a loop: re-seed, re-run, re-score
until `score_recast` passes or `--max-iters` is exhausted.

## Layout

- `bin/`
  - `run-analysis` — invoked by agents inside the sandbox; imports preflight from this runtime.
- `controller/`
  - `recast_loop.py` — the loop. Each iteration sets up `workspace/`, runs the agent,
    archives to `validation/agent_NNN/`, and scores.
  - Launched via `bin/run-agent --config configs/claude_iterative.yaml` from the repo root.
- `templates/workspace/`
  - `datasets.yaml`, `report.md` — minimal stubs copied into every fresh workspace.
- `preflight.py` — AST linting and artifact checks for `analysis.py`.

## Instructions source

Agent-facing instructions are inherited verbatim from `agents/simple/`:
`AGENTS.md` and `TOOLS.md`. The iteration context (prior `analysis.py`,
`datasets.yaml`, partial `HEPRecastData/`, `status.md`, `previous_score.json`)
is supplied via seeded files plus a prompt paragraph added on iter ≥ 1.

## Boundary

This folder launches and supervises agents — it is not itself an agent.
