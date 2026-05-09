# Tests

Offline smoke suite. No SLURM, no LLM calls, no network — `python -m pytest` completes in a couple of seconds on any host.

## What's covered

| File | Asserts |
|---|---|
| `test_config.py` | Every shipped YAML config parses and validates; `extends:` chain is followed; unknown keys are rejected; PyYAML bool guard fires. |
| `test_workspace.py` | `build_workspace(agent)` produces the canonical layout (agent_context, bin/, tools/ symlink, papers/ symlink to ColliderBench); run-analysis is on PATH. |
| `test_prompts.py` | simple agent prompt builder renders for any paper_ref. |
| `test_sandbox.py` | Every registered backend instantiates; auto picks one; sandbox_command returns `(list[str], callable)` and the "none" backend is a passthrough. |
| `test_runners.py` | Every runner class instantiates; Claude's build_command has `--disallowedTools ScheduleWakeup` and `--max-thinking-tokens`; Codex's has `exec` + `danger-full-access`; allowlist is threaded through. |

Tests skip automatically when the backing CLI isn't present (e.g. codex not installed on a macOS dev box).

## What's NOT covered

- **End-to-end agent runs** — they cost ~$10 and need SLURM. See [`scripts/run-agent`](../scripts/run-agent) manually when you want to validate a full iteration.
- **Sandbox runtime behavior** — we verify command construction, not that `bwrap` actually isolates (that would need root / namespaces in CI).
- **Scoring correctness** — `ColliderBench/evaluation/score.py` does real physics math; add targeted cases there when you change the metric, not here.

## Running

```bash
python -m pytest                     # full suite
python -m pytest -x -v               # stop on first fail, verbose
python -m pytest tests/test_prompts.py::test_planner_prompt_mentions_plan_md
```

## Adding tests

Keep them **fast and offline**. If a test needs to spawn claude/codex, mock it or `pytest.skip()` when the binary's missing. If it needs the benchmark's conda env, it belongs in a separate integration suite that we explicitly opt into, not the default run.
