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
    "model": (str,),
    "effort": (str, int),
    "max_iters": (int,),
    "min_iters": (int,),
    "combined_threshold": (float, int),
    "compute": (str,),
    "account": (str,),
    "cpus": (int, str),
    "walltime": (str,),
    "qos": (str,),
    "sandbox": (str,),
    "task": (str,),  # free-form task id (e.g. sus-16-046_sim-TChiWg)
    # Anneal agent: separate model / effort for the planner and examiner roles.
    "examiner_model": (str,),
    "examiner_effort": (str, int),
    "planner_effort": (str, int),
    # Anneal agent: temperature schedule + stochastic rollback knobs.
    "anneal_t0": (float, int),
    "anneal_schedule": (str,),
    "max_rollbacks": (int,),
    "rollback_noise_floor": (float, int),
}

_ALLOWED_AGENTS = {"simple", "baseline", "iterative", "anneal"}
_ALLOWED_RUNNERS = {"claude", "codex", "gemini", "aider", "forge"}
_ALLOWED_COMPUTE = {"", "perlmutter"}
_ALLOWED_EFFORT_LABELS = {"low", "medium", "high", "max", "xhigh"}
_ALLOWED_SANDBOX = {"auto", "bwrap", "apptainer", "podman", "none"}
_ALLOWED_ANNEAL_SCHEDULE = {"none", "linear", "cosine"}


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
    compute = cfg.get("compute")
    if compute is not None and compute not in _ALLOWED_COMPUTE:
        raise ValueError(f"{source}: compute={compute!r}; must be '' (login node) or 'perlmutter'")
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
    schedule = cfg.get("anneal_schedule")
    if schedule is not None and schedule not in _ALLOWED_ANNEAL_SCHEDULE:
        raise ValueError(
            f"{source}: anneal_schedule={schedule!r}; "
            f"must be one of {sorted(_ALLOWED_ANNEAL_SCHEDULE)}"
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
        for key in ("compute", "account", "cpus", "walltime", "qos")
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
        help="Print shell assignments for compute/account/cpus/walltime/qos.",
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
