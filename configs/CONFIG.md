# Config reference

Agent-facing reference for the YAML configs consumed by `scripts/run-agent`.
Read this when authoring, modifying, or debugging a config — it describes the
schema, the validation contract, and the runtime path from YAML to launched
process. For high-level usage instructions read [`README.md`](../README.md)
instead.

## Files shipped publicly

Only two configs live under version control:

| File | `compute` | `auth` | When to use |
|---|---|---|---|
| [`claude.yaml`](claude.yaml)       | `local` | `api` | Single-host runs on any Linux box or workstation with Podman / Apptainer / Singularity installed. |
| [`claude_slurm.yaml`](claude_slurm.yaml) | `slurm` | `api` | SLURM allocation on Perlmutter (or a similarly-shaped cluster). Wraps the launcher in `salloc`+`srun`. |

Both are minimal working examples — copy and adapt, do not import via `extends:`
(the inheritance mechanism still works for private configs, but the two shipped
files are intentionally self-contained so a new reader sees the full schema in
one place).

Other configs (vendor-specific models, OAuth flows, NERSC-tuned profiles)
are kept private and gitignored. The schema described below applies to those
too.

## Schema

Defined in [`agent_runtime/config.py`](../agent_runtime/config.py) (`ALLOWED_CONFIG_KEYS`
and the `_ALLOWED_*` sets). Unknown keys and wrong types raise at load time.

### Core fields

| Key | Type | Allowed values | Required? | What it controls |
|---|---|---|---|---|
| `agent`    | str | `simple` | yes | Which agent module under [`agents/`](../agents) drives the loop. The public release ships `simple` (one LLM call + score). |
| `task`     | str | any `task_id` from [`ColliderBench/tasks/`](../ColliderBench/tasks) | yes | The benchmark task to run. CLI `--task` overrides. |
| `runner`   | str | `claude`, `codex`, `gemini`, `aider`, `forge` | yes | Which agent CLI is launched inside the sandbox. Each runner is declared as a `RunnerSpec` in [`agent_runtime/vendors.py`](../agent_runtime/vendors.py). |
| `provider` | str | `anthropic`, `openai`, `google`, `deepseek` | only for `forge`/`claude` when the runner can talk to multiple providers | Drives auth env-var selection and CLI flags. |
| `auth`     | str | `oauth`, `api` | yes | `api` → harness reads the runner-specific API key from the environment. `oauth` → harness expects an existing CLI login (`~/.claude`, `~/.codex`, `~/.gemini`) and copies the auth files into the per-run fake `$HOME`. |
| `model`    | str | vendor-specific model ID | yes | Forwarded verbatim to the CLI's `--model` flag. |
| `effort`   | str \| int | `low`, `medium`, `high`, `max`, `xhigh`, or an integer (raw token budget) | no | Mapped per runner: e.g. Claude → `thinking.budget_tokens`, Codex → `--reasoning-effort`. |
| `sandbox`  | str | `auto`, `podman`, `docker`, `apptainer`, `singularity`, `none` | no (default `auto`) | Filesystem-isolation backend. See [`agent_runtime/SANDBOX.md`](../agent_runtime/SANDBOX.md). |
| `compute`  | str | `""`, `local`, `slurm`, `perlmutter` | no (default `""`) | `slurm` triggers the `salloc`/`srun` wrapping in [`agent_runtime/shell/agent_env.sh:run_with_compute`](../agent_runtime/shell/agent_env.sh). `local` and `""` run in the current shell. |
| `tool_policy` | dict | `{ disabled: [str, ...] }` | no | Currently the only honored toggle is `disabled: [delphes]`, which swaps the Delphes installation for an empty placeholder so the agent can't shell out to it. |
| `extends`  | str | path relative to the config file | no | Path-style inheritance. The child overrides the parent key-by-key. Loaded recursively in [`load_config`](../agent_runtime/config.py). |

### SLURM fields

Only consumed when `compute: slurm`. All values are forwarded as raw flags
to `salloc`/`srun` after `shell_defaults()` extracts them in
[`agent_runtime/config.py`](../agent_runtime/config.py).

| Key | Type | Notes |
|---|---|---|
| `account`      | str | SLURM `--account`. NERSC: project ID (e.g. `m4539`). Required by most sites. |
| `partition`    | str | `--partition`. Leave `""` for the default. |
| `constraint`   | str | `--constraint`. Perlmutter: `cpu` or `gpu`. |
| `nodes`        | int \| str | `--nodes`. |
| `ntasks`       | int \| str | `--ntasks`. |
| `cpus`         | int \| str | `--cpus-per-task`. |
| `walltime`     | str | `--time` (HH:MM:SS). |
| `qos`          | str | `--qos`. Perlmutter: `interactive`, `regular`, `shared`. |
| `salloc_extra` | str | Raw extra args, `shlex`-split. |
| `srun_extra`   | str | Raw extra args, `shlex`-split. |
| `env_setup`    | str | Shell snippet sourced inside the allocation before the launcher fires. |

## Validation contract

Three checks fire during config load:

1. **`validate_config`** ([`config.py`](../agent_runtime/config.py)) — rejects unknown keys, wrong types, and out-of-enum values. PyYAML bool coercion is explicitly blocked so `yes`/`no` strings don't silently become booleans.
2. **`validate_api_auth_env`** ([`config.py`](../agent_runtime/config.py)) — when `auth: api`, the harness checks that the corresponding env var is set *before* the runner is launched. Mapping (`_API_AUTH_ENV`):
   - `runner: claude, provider: anthropic` (or default) → `ANTHROPIC_API_KEY`
   - `runner: claude, provider: deepseek` → `DEEPSEEK_API_KEY`
   - `runner: forge, provider: deepseek` → `DEEPSEEK_API_KEY`
3. **`validate_launch_inputs`** — confirms the resolved `task` matches an existing task directory and template.

Failures from any of these raise *before* a sandbox is materialized, so a
broken config never spends API tokens.

## Runtime path from YAML to launched process

```
scripts/run-agent --config configs/<file>.yaml
        │
        ├─ resolve_config_path()                     # configs/<file>.yaml literal hit
        ├─ source agent_runtime/shell/agent_env.sh
        ├─ activate_lhc_analysis                     # conda env (or bootstrap)
        │
        ├─ python agent reads the AGENT field
        │
        └─ run_with_compute(...)
              if compute == "slurm":
                  salloc <fields from config> -- bash -lc "
                      srun <srun_extra> bash -lc 'python -m agent_runtime.launch ...'
                  "
              else:
                  python -m agent_runtime.launch ...
                        │
                        ├─ load_config(args.config)         # follows extends:
                        ├─ validate_*                       # see above
                        ├─ build_workspace(...)             # runs/<runner>_<model>/<run>/
                        └─ Runner.run(...)
                              └─ sandbox_command(...)        # podman run / apptainer exec / ...
                                    └─ runner CLI is exec'd inside the container
```

Key handoff points:

- Compute dispatch is shell-level, not Python — [`agent_env.sh:run_with_compute`](../agent_runtime/shell/agent_env.sh).
- Sandbox selection is Python-level — [`agent_runtime/sandbox.py:get_sandbox`](../agent_runtime/sandbox.py). The string from `sandbox:` (or `LHC_RECAST_SANDBOX`, or `--sandbox`) maps to a class in `SANDBOXES`.
- The CLI binary on the host is resolved via `which <runner>`; the runner detects it as an absolute path under `$HOME` and bind-mounts only its parent directory into the container (see [`_prepare_runner_cli`](../agent_runtime/sandbox.py)).

## Authoring a new config

Two valid patterns:

1. **Self-contained** — every field set inline, no `extends:`. Use this for the shipped public configs and for one-off experiments.
2. **`extends:` inheritance** — common SLURM/compute fields in a base file, vendor-specific overrides in the leaf file. The base file is found relative to the *child* config's directory.

Minimum viable config:

```yaml
agent:   simple
task:    sus-16-046_sim-T5Wg
runner:  claude
auth:    api          # or oauth
model:   claude-opus-4-7
sandbox: podman       # podman | docker | apptainer | singularity | none
compute: local        # or slurm + the SLURM fields above
```

Everything else has a sensible default (validated against the type list, no
runtime effect when absent).

## Gotchas

- **`auth: oauth` is fragile across boundaries.** The CLI must have an active
  login on the host that runs `scripts/run-agent`; the harness copies a small
  allowlist of credential files into the per-run fake `$HOME`. Inside a
  SLURM allocation on a fresh compute node, the OAuth refresh path is
  brittle — `auth: api` is the supported path for batch eval.
- **`task` is overridden by `--task` on the CLI.** Use the CLI flag for
  per-invocation choice; keep the YAML's `task` as a working default.
- **Compute and sandbox are orthogonal.** `compute: local` + `sandbox: podman`
  is the standard local industry path. `compute: slurm` + `sandbox: podman`
  is the standard Perlmutter path. `sandbox: none` skips isolation entirely
  and **must not be used for scored runs** — the agent can read the
  reference yields under `ColliderBench/tasks/shared/*/reference/`.
- **`effort: max` is not the same across runners.** It dispatches per runner to
  whatever that vendor's "highest reasoning budget" means. For numeric
  reproducibility across vendors, pass an integer.
- **Boolean values are rejected.** `auth: yes` parses as a Python `bool` and
  the validator refuses it. Quote it: `auth: "api"`.
