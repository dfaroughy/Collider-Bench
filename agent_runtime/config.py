"""YAML config loading + validation, task.toml loading, launch preflight.

Three concerns that all answer the question "is this run-request well-formed?"
  - validate_config / load_config:    the user's YAML is sane
  - load_task_toml:                   the task definition exists and parses
  - validate_launch_inputs:           the (repo, task_id) pair is launchable
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


# Typed schema for YAML configs. Unknown keys raise; values may be None.
ALLOWED_CONFIG_KEYS: dict[str, tuple[type, ...]] = {
    "extends": (str,),
    "agent": (str,),
    "runner": (str,),
    "provider": (str,),
    "auth": (str,),
    "model": (str,),
    "effort": (str, int),
    "compute": (str,),
    "account": (str,),
    "partition": (str,),
    "constraint": (str,),
    "nodes": (int, str),
    "ntasks": (int, str),
    "cpus": (int, str),
    "walltime": (str,),
    "qos": (str,),
    "salloc_extra": (str,),
    "srun_extra": (str,),
    "env_setup": (str,),
    "sandbox": (str,),
    "tool_policy": (dict,),
    "task": (str,),  # free-form task id (e.g. sus-16-046_sim-TChiWg)
}

_ALLOWED_AGENTS = {"simple"}
_ALLOWED_RUNNERS = {"claude", "codex", "gemini", "aider", "forge", "opencode"}
# `local` = GLM-4.7-Flash on a single GPU node (see configs/opencode/glm47_flash.yaml).
# `local-air` = GLM-4.5-Air on a 4-node Ray cluster (see configs/opencode/glm45_air.yaml).
# Separate provider names so the (runner, provider) lookup picks the right
# env-file fallback + preflight target.
_ALLOWED_PROVIDERS = {"anthropic", "openai", "google", "deepseek", "local", "local-air"}
_ALLOWED_AUTH = {"oauth", "api"}
_ALLOWED_COMPUTE = {"", "local", "slurm", "perlmutter"}
_ALLOWED_EFFORT_LABELS = {"low", "medium", "high", "max", "xhigh"}
_ALLOWED_SANDBOX = {"auto", "apptainer", "singularity", "podman", "docker", "none"}
_ALLOWED_TOOL_POLICY_KEYS = {"disabled"}
# Keep in sync with agent_runtime.workspace._ABLATIONS (a drift test in
# tests/test_workspace.py asserts the two stay equal). `delphes` is ablated
# with visible "disabled" stubs; `prospino` is ablated *blind* — every
# agent-visible trace (bin shim, CLI doc, TOOLS.md row, AGENTS.md mentions,
# bin/simulate hint, $PROSPINO_DIR, /opt/sim/prospino) is removed.
_ALLOWED_DISABLED_TOOLS = {"delphes", "prospino"}
_API_AUTH_ENV: dict[tuple[str, str], tuple[str, ...]] = {
    ("claude", "anthropic"): ("ANTHROPIC_API_KEY",),
    ("claude", "deepseek"): ("DEEPSEEK_API_KEY",),
    ("forge", "deepseek"): ("DEEPSEEK_API_KEY",),
    # `opencode` driving a local vLLM server (e.g. GLM-4.7-Flash) needs the
    # base URL + token written into the user's shell by
    # /pscratch/sd/d/dfarough/LLMs/start_glm47_service.sh.
    ("opencode", "local"): ("GLM_API_BASE", "GLM_API_KEY"),
    # GLM-4.5-Air on the 4-node ray cluster — see
    # /pscratch/sd/d/dfarough/LLMs/start_glm45_air_4node_service.sh, which
    # writes `GLM_AIR_API_BASE` / `GLM_AIR_API_KEY` into glm45_air_api.env.
    ("opencode", "local-air"): ("GLM_AIR_API_BASE", "GLM_AIR_API_KEY"),
}

# Per-(runner, provider) env-file fallback: when API-auth env vars are
# missing from the live shell, the validator will source one of these
# files (parsing `export KEY=value` lines) and re-check. Lets long-lived
# servers (e.g. a vLLM job on a GPU node whose hostname changes on every
# salloc restart) be picked up without users having to re-source the env
# file in every shell after a server restart.
_API_AUTH_ENV_FALLBACK_FILES: dict[tuple[str, str], str] = {
    ("opencode", "local"): "/pscratch/sd/d/dfarough/LLMs/glm47_api.env",
    ("opencode", "local-air"): "/pscratch/sd/d/dfarough/LLMs/glm45_air_api.env",
}


def _parse_export_env_file(path: str | os.PathLike) -> dict[str, str]:
    """Parse `export KEY=value` lines from a shell env file. Best-effort."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def validate_config(cfg: dict, source: str = "<config>") -> None:
    """Validate cfg in-place: raise ValueError on unknown keys, wrong types,
    or invalid enum values. Coerces obvious numeric↔string mismatches (PyYAML
    parses `1707.06193` as float; `128` for cpus is fine as int or str).
    """
    unknown = sorted(set(cfg) - set(ALLOWED_CONFIG_KEYS))
    if unknown:
        raise ValueError(
            f"{source}: unknown config key(s) {unknown}. Allowed: {sorted(ALLOWED_CONFIG_KEYS)}"
        )
    for key, val in list(cfg.items()):
        if val is None:
            continue
        expected = ALLOWED_CONFIG_KEYS[key]
        if isinstance(val, bool):
            # Guard: PyYAML parses "yes/no" as bool, and bool is a subclass
            # of int in Python. No current config key intentionally accepts
            # booleans, so reject them before the type checks.
            raise ValueError(f"{source}: {key}={val!r} must not be a bool")
        # Coerce numeric → string for string-only fields (arxiv IDs, account
        # names). Only when int/float aren't already accepted, otherwise we'd
        # stringify e.g. `effort: 1500` or `cpus: 128` that want to stay numeric.
        if (
            str in expected
            and int not in expected
            and float not in expected
            and isinstance(val, (int, float))
            and not isinstance(val, bool)
        ):
            cfg[key] = val = str(val)
        if not isinstance(val, expected):
            raise ValueError(
                f"{source}: {key}={val!r} has type {type(val).__name__}; "
                f"expected {[t.__name__ for t in expected]}"
            )
    agent = cfg.get("agent")
    if agent and agent not in _ALLOWED_AGENTS:
        raise ValueError(f"{source}: agent={agent!r}; must be one of {sorted(_ALLOWED_AGENTS)}")
    runner = cfg.get("runner")
    if runner and runner not in _ALLOWED_RUNNERS:
        raise ValueError(f"{source}: runner={runner!r}; must be one of {sorted(_ALLOWED_RUNNERS)}")
    provider = cfg.get("provider")
    if provider and provider not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"{source}: provider={provider!r}; must be one of {sorted(_ALLOWED_PROVIDERS)}"
        )
    auth = cfg.get("auth")
    if auth is not None and auth not in _ALLOWED_AUTH:
        raise ValueError(f"{source}: auth={auth!r}; must be one of {sorted(_ALLOWED_AUTH)}")
    compute = cfg.get("compute")
    if compute is not None and compute not in _ALLOWED_COMPUTE:
        raise ValueError(
            f"{source}: compute={compute!r}; must be one of {sorted(_ALLOWED_COMPUTE)}"
        )
    effort = cfg.get("effort")
    if (
        isinstance(effort, str)
        and effort
        and not effort.isdigit()
        and effort not in _ALLOWED_EFFORT_LABELS
    ):
        raise ValueError(f"{source}: effort={effort!r}; must be low|medium|high or an integer")
    sandbox = cfg.get("sandbox")
    if sandbox is not None and sandbox not in _ALLOWED_SANDBOX:
        raise ValueError(
            f"{source}: sandbox={sandbox!r}; must be one of {sorted(_ALLOWED_SANDBOX)}"
        )
    tool_policy = cfg.get("tool_policy")
    if tool_policy is not None:
        unknown_policy = sorted(set(tool_policy) - _ALLOWED_TOOL_POLICY_KEYS)
        if unknown_policy:
            raise ValueError(
                f"{source}: tool_policy has unknown key(s) {unknown_policy}. "
                f"Allowed: {sorted(_ALLOWED_TOOL_POLICY_KEYS)}"
            )
        disabled = tool_policy.get("disabled", [])
        if not isinstance(disabled, list) or any(not isinstance(x, str) for x in disabled):
            raise ValueError(f"{source}: tool_policy.disabled must be a list of strings")
        unknown_tools = sorted(set(disabled) - _ALLOWED_DISABLED_TOOLS)
        if unknown_tools:
            raise ValueError(
                f"{source}: unknown disabled tool(s) {unknown_tools}. "
                f"Allowed: {sorted(_ALLOWED_DISABLED_TOOLS)}"
            )
    # `task` is a free-form task id — validated against the filesystem in
    # validate_launch_inputs(), not against an enum here.


def load_config(path: str | os.PathLike | None) -> dict:
    """Load a YAML config, resolving `extends:` chains. Returns {} for empty/None.

    Child values override parent values. `extends:` is resolved relative to the
    file that declares it. Cycles and unknown/misspelled keys raise ValueError.
    """
    if not path:
        return {}
    import yaml

    chain: list[Path] = []

    def _load(p: Path) -> dict:
        p = p.resolve()
        if p in chain:
            cycle = " -> ".join(str(x) for x in chain + [p])
            raise ValueError(f"config extends cycle: {cycle}")
        chain.append(p)
        raw = yaml.safe_load(p.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{p}: top-level YAML must be a mapping")
        parent_ref = raw.pop("extends", None)
        merged: dict = {}
        if parent_ref:
            parent = _load((p.parent / parent_ref))
            merged.update(parent)
        merged.update(raw)
        return merged

    cfg = _load(Path(path))
    validate_config(cfg, source=str(Path(path)))
    return cfg


def validate_api_auth_env(cfg: dict, environ: dict[str, str] | None = None) -> None:
    """Fail early when a config requests API-key auth but required env is absent.

    For runners that ship a `_API_AUTH_ENV_FALLBACK_FILES` registration
    (e.g. opencode/local pointing at the GLM vLLM launcher's
    glm47_api.env), the file is the authoritative source — its values
    OVERRIDE anything pre-set in the shell. This is on purpose: the
    file is rewritten on every server restart with the fresh hostname,
    so a stale `GLM_API_BASE` left in the shell from an older `source`
    would otherwise silently route requests to a dead node. If the
    file is absent, the shell values are honoured as-is.
    """
    if cfg.get("auth") != "api":
        return
    runner = str(cfg.get("runner") or "")
    provider = str(cfg.get("provider") or ("anthropic" if runner == "claude" else runner))
    required = _API_AUTH_ENV.get((runner, provider), ())
    if not required:
        return
    env = environ if environ is not None else os.environ
    fallback = _API_AUTH_ENV_FALLBACK_FILES.get((runner, provider))
    if fallback and Path(fallback).is_file():
        parsed = _parse_export_env_file(fallback)
        for name in required:
            if parsed.get(name):
                # File wins. Stale shell values for a moved server are
                # the failure mode this overwrite exists to prevent.
                env[name] = parsed[name]
    missing = [name for name in required if not env.get(name)]
    if missing:
        exports = " ".join(f"export {name}=..." for name in missing)
        hint = f" Or start the local server (writes {fallback})." if fallback else ""
        raise ValueError(
            f"runner={runner!r} auth='api' requires environment variable(s): "
            f"{', '.join(missing)}. Set them before launching, e.g. `{exports}`.{hint}"
        )


# ── Preflight reachability probe for local API endpoints ────────────────────
#
# Some runners (opencode/local → vLLM on a SLURM-allocated GPU node) point at
# a server whose lifecycle is independent of the harness — when the salloc
# expires or the user forgets to start vLLM, every API call hangs until the
# client-side stream timeout (~2 min in opencode). A 2-second preflight ping
# turns those silent hangs into a clear actionable error.


def _probe_local_endpoint(base_url: str, api_key: str, timeout_s: float = 3.0) -> tuple[bool, str]:
    """GET `<base_url>/models` with a bearer token. Stdlib-only, no deps."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            return True, ""
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, f"connect: {exc}"


# Registry of (runner, provider) → probe spec. Add an entry whenever a runner
# points at a local server whose silent death would otherwise hang the run.
_API_PREFLIGHT_PROBES: dict[tuple[str, str], dict] = {
    ("opencode", "local"): {
        "base_var": "GLM_API_BASE",
        "key_var": "GLM_API_KEY",
        "what": "GLM-4.7-Flash vLLM server",
        "hint": (
            "  squeue -u $USER       # is the SLURM allocation still alive?\n"
            "  /pscratch/sd/d/dfarough/LLMs/start_glm47_service.sh   "
            "# (re)launch vLLM"
        ),
    },
    ("opencode", "local-air"): {
        "base_var": "GLM_AIR_API_BASE",
        "key_var": "GLM_AIR_API_KEY",
        "what": "GLM-4.5-Air vLLM cluster (4 nodes via Ray)",
        "hint": (
            "  squeue -u $USER       # is the SLURM allocation still alive?\n"
            "  /pscratch/sd/d/dfarough/LLMs/start_glm45_air_4node_service.sh   "
            "# (re)launch vLLM cluster"
        ),
    },
}


def preflight_local_endpoint(
    cfg: dict,
    environ: dict[str, str] | None = None,
    probe=_probe_local_endpoint,
) -> None:
    """Ping the local API endpoint registered for (runner, provider). No-op
    when no registration matches. Raises ValueError on failure with a
    human-actionable hint. `probe` is injectable for tests."""
    if cfg.get("auth") != "api":
        return
    runner = str(cfg.get("runner") or "")
    provider = str(cfg.get("provider") or "")
    spec = _API_PREFLIGHT_PROBES.get((runner, provider))
    if not spec:
        return
    env = environ if environ is not None else os.environ
    base = env.get(spec["base_var"])
    key = env.get(spec["key_var"])
    if not base or not key:
        return  # validate_api_auth_env already failed if these were required
    ok, detail = probe(base, key)
    if not ok:
        raise ValueError(f"{spec['what']} unreachable at {base} ({detail}).\n" f"{spec['hint']}")


def read_agent_from_config(path: str | os.PathLike) -> str | None:
    """Cheap lookup: follow extends: chain and return the first `agent:` found.

    Used by the unified run-agent dispatcher without validating the full schema.
    """
    if not path:
        return None
    import yaml

    seen: set[Path] = set()
    p = Path(path).resolve()
    while p not in seen:
        seen.add(p)
        try:
            raw = yaml.safe_load(p.read_text()) or {}
        except OSError:
            return None
        if isinstance(raw, dict) and raw.get("agent"):
            return str(raw["agent"])
        parent_ref = (raw or {}).get("extends") if isinstance(raw, dict) else None
        if not parent_ref:
            return None
        p = (p.parent / parent_ref).resolve()
    return None


def shell_defaults(path: str | os.PathLike) -> dict[str, str]:
    """Return shell-level compute defaults from a resolved config."""
    cfg = load_config(path)
    return {
        key.upper(): "" if cfg.get(key) is None else str(cfg.get(key, ""))
        for key in (
            "compute",
            "account",
            "partition",
            "constraint",
            "nodes",
            "ntasks",
            "cpus",
            "walltime",
            "qos",
            "salloc_extra",
            "srun_extra",
            "env_setup",
        )
    }


def load_task_toml(repo_root: Path, task_id: str) -> dict:
    """Read tasks/<task_id>/task.toml. Raises FileNotFoundError if absent."""
    import tomllib

    from agent_runtime import paths

    path = paths.task_dir(repo_root, task_id) / "task.toml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}")
    return tomllib.loads(path.read_text())


def validate_launch_inputs(repo_root: Path, task_id: str) -> dict:
    """Validate a task_id before any workspace setup. Raises on failure.

    Returns the parsed task.toml (so callers don't have to read it twice).
    Named to not collide with the per-agent `preflight.py` module (which runs
    postflight checks on the agent's produced analysis.py).
    """
    from agent_runtime import paths

    if not task_id:
        raise ValueError("task is required (set `task:` in --config or pass --task)")
    tasks_root = paths.tasks_root(repo_root)
    task_dir = paths.task_dir(repo_root, task_id)
    if not task_dir.is_dir():
        available = (
            sorted(p.name for p in tasks_root.iterdir() if p.is_dir() and p.name != "shared")
            if tasks_root.is_dir()
            else []
        )
        raise FileNotFoundError(
            f"Task missing: {task_dir}\n"
            f"  Known tasks: {available[:5]}{'...' if len(available) > 5 else ''}"
        )
    toml = load_task_toml(repo_root, task_id)
    paper_ref = (toml.get("task") or {}).get("paper")
    if not paper_ref:
        raise ValueError(f"{task_dir / 'task.toml'}: [task].paper is required")
    for required in (task_dir / "TASK.md", task_dir / "template"):
        if not required.exists():
            raise FileNotFoundError(f"Missing {required}")
    shared = paths.shared_paper_dir(repo_root, paper_ref)
    if not shared.is_dir():
        raise FileNotFoundError(
            f"Missing shared paper dir: {shared}\n  Expected: {shared}/paper/{paper_ref}.pdf"
        )
    return toml


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Runtime config helpers")
    parser.add_argument(
        "--shell-defaults",
        metavar="CONFIG",
        help=(
            "Print shell assignments for "
            "compute/account/partition/constraint/nodes/ntasks/cpus/walltime/qos/"
            "salloc_extra/srun_extra/env_setup."
        ),
    )
    args = parser.parse_args(argv)

    if args.shell_defaults:
        for key, val in shell_defaults(args.shell_defaults).items():
            print(f"{key}={shlex.quote(val)}")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
