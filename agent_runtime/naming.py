"""Shared helpers for naming recast run directories.

Format:  recast_<paper_ref>_<agent_name>_<model_name>_<Adj><Physicist>_<hex8>
Example: recast_1707.06193_simple_claude-opus-4-7_QuantumFeynman_a1b2c3d4

The <Adj><Physicist>_<hex8> suffix is the canonical short "agent_id" used to
refer to a run in logs, summaries, and cross-run comparisons.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


PHYSICS_ADJ: list[str] = [
    "Agile",
    "Arcane",
    "Atomic",
    "Bold",
    "Bright",
    "Calm",
    "Candid",
    "Cosmic",
    "Curious",
    "Daring",
    "Deep",
    "Electric",
    "Elegant",
    "Fierce",
    "Flashy",
    "Formal",
    "Gentle",
    "Grand",
    "Grim",
    "Happy",
    "Humble",
    "Keen",
    "Kind",
    "Lively",
    "Boring",
    "Lucid",
    "Lucky",
    "Merry",
    "Mystic",
    "Nimble",
    "Noble",
    "Romantic",
    "Odd",
    "Playful",
    "Prime",
    "Proud",
    "Quantum",
    "Quick",
    "Annoying",
    "Quiet",
    "Radiant",
    "Rapid",
    "Rare",
    "Sharp",
    "Creepy",
    "Special",
    "Silly",
    "Sly",
    "Solid",
    "Strange",
    "Swift",
    "Witty",
    "Wise",
    "Goofy",
    "Gay",
    "Fuzzy",
    "Sassy",
    "Wacky",
    "Quirky",
    "Cheerful",
    "Slow",
    "Brave",
    "Clever",
    "Eccentric",
    "Funky",
    "Sad",
    "Salty",
    "Spicy",
    "Bubbly",
    "Stubborn",
    "Zany",
    "Jolly",
    "Mysterious",
    "Nerdy",
    "Peculiar",
    "Cool",
    "Geeky",
    "Chubby",
    "Fat",
    "Skinny",
]

PHYSICIST_LAST: list[str] = [
    "Einstein",
    "Feynman",
    "Pauli",
    "Dirac",
    "Bohr",
    "Curie",
    "Nielsen",
    "Mayer",
    "Planck",
    "Newton",
    "Maxwell",
    "Faraday",
    "Galilei",
    "Kepler",
    "Higgs",
    "Schrodinger",
    "Heisenberg",
    "Boltzmann",
    "Noether",
    "Hawking",
    "Tesla",
    "Rutherford",
    "Lorentz",
    "Hertz",
    "Laplace",
    "Lagrange",
    "Gauss",
    "Quinn",
    "Huygens",
    "Ampere",
    "Volta",
    "Ohm",
    "Compton",
    "deBroglie",
    "Salam",
    "Rubin",
    "Bose",
    "Fermi",
    "Majorana",
    "Mach",
    "Born",
    "Sommerfeld",
    "Wu",
    "Zeldovich",
    "Chandrasekhar",
    "Witten",
    "Bhabha",
    "Meitner",
    "Bethe",
    "Cherenkov",
    "Yang",
    "Wigner",
    "GellMann",
    "Landau",
    "Poincare",
    "Alcubierre",
    "Penrose",
    "Susskind",
    "tHooft",
    "Weinberg",
    "Glashow",
    "Cabibbo",
    "Yukawa",
    "Georgi",
    "Nambu",
    "Wilson",
    "Gibbs",
    "Hamilton",
    "Poisson",
    "Chadwick",
    "Gamow",
]


def physicist_bigram(hash_hex: str) -> str:
    """Build `<Adj><Physicist>_<hex8>` from a hex string (min 8 chars)."""
    a = int(hash_hex[:2], 16) % len(PHYSICS_ADJ)
    b = int(hash_hex[2:4], 16) % len(PHYSICIST_LAST)
    short = hash_hex[:8]
    return f"{PHYSICS_ADJ[a]}{PHYSICIST_LAST[b]}_{short}"


def _normalize_model_name(raw: str) -> str:
    return raw.replace("/", "-").replace(" ", "-")


def generate_run_info(
    paper_ref: str,
    agent_name: str,
    model_name: str,
) -> dict:
    """Return a dict with the canonical run metadata.

    Keys:
      agent_id   — <Adj><Physicist>_<hex8>       (canonical short name)
      run_dir  — full directory name
      paper_ref, agent, model                  (echoed for convenience)
    """
    hex_hash = os.urandom(8).hex()
    agent_id = physicist_bigram(hex_hash)
    run_dir = f"recast_{paper_ref}_{agent_name}_{_normalize_model_name(model_name)}_{agent_id}"
    return {
        "agent_id": agent_id,
        "run_dir": run_dir,
        "paper_ref": paper_ref,
        "agent": agent_name,
        "model": model_name,
    }


# ── Effort levels ──────────────────────────────────────────────────────────

# Map a symbolic effort level to max_thinking_tokens for the Claude CLI.
# "medium" mirrors the Claude CLI's own default; "high" matches what CC Code
# uses for heavy reasoning tasks. Users can also pass a raw integer.
EFFORT_THINKING_TOKENS: dict[str, int] = {
    "low": 2000,
    "medium": 8000,
    "high": 31999,
}


def resolve_effort(effort: str | int | None) -> tuple[str, int]:
    """Return (label, max_thinking_tokens) for a user-supplied effort value.

    Accepts: "low" | "medium" | "high" | integer string | int | None.
    None → "medium" (CLI default).
    """
    if effort is None or effort == "":
        effort = "medium"
    if isinstance(effort, int):
        return (f"custom({effort})", effort)
    s = str(effort).strip().lower()
    if s.isdigit():
        n = int(s)
        return (f"custom({n})", n)
    if s in EFFORT_THINKING_TOKENS:
        return (s, EFFORT_THINKING_TOKENS[s])
    # Unknown label → fall back to medium rather than erroring
    return ("medium", EFFORT_THINKING_TOKENS["medium"])


# ── Config schema ──────────────────────────────────────────────────────────

# Typed schema for YAML configs. Unknown keys raise; values may be None.
ALLOWED_CONFIG_KEYS: dict[str, tuple[type, ...]] = {
    "extends": (str,),
    "agent": (str,),
    "paper": (str,),
    "runner": (str,),
    "model": (str,),
    "effort": (str, int),
    "max_iters": (int,),
    "min_iters": (int,),
    "compute": (str,),
    "account": (str,),
    "cpus": (int, str),
    "walltime": (str,),
    "qos": (str,),
    "sandbox": (str,),
    "task": (str,),
    # Sisyphus agent: separate model / effort for the planner and critic roles.
    "critic_model": (str,),
    "critic_effort": (str, int),
    "planner_effort": (str, int),
}

_ALLOWED_AGENTS = {"simple", "baseline", "iterative", "sisyphus"}
_ALLOWED_RUNNERS = {"claude", "codex", "aider"}
_ALLOWED_COMPUTE = {"", "perlmutter"}
_ALLOWED_EFFORT_LABELS = {"low", "medium", "high"}
_ALLOWED_SANDBOX = {"auto", "bwrap", "none"}
_ALLOWED_TASKS = {"validate", "simulate", "recast"}


def validate_config(cfg: dict, source: str = "<config>") -> None:
    """Validate cfg in-place: raise ValueError on unknown keys, wrong types,
    or invalid enum values. Coerces obvious numeric↔string mismatches (PyYAML
    parses `1707.06193` as float; `128` for cpus is fine as int or str).
    """
    unknown = sorted(set(cfg) - set(ALLOWED_CONFIG_KEYS))
    if unknown:
        raise ValueError(
            f"{source}: unknown config key(s) {unknown}. " f"Allowed: {sorted(ALLOWED_CONFIG_KEYS)}"
        )
    for key, val in list(cfg.items()):
        if val is None:
            continue
        expected = ALLOWED_CONFIG_KEYS[key]
        if isinstance(val, bool) and int not in expected:
            # Guard: PyYAML parses "yes/no" as bool, which silently passes int checks.
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
    task = cfg.get("task")
    if task is not None and task not in _ALLOWED_TASKS:
        raise ValueError(f"{source}: task={task!r}; must be one of {sorted(_ALLOWED_TASKS)}")


# ── Config loading ─────────────────────────────────────────────────────────


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


# ── Run info persistence ───────────────────────────────────────────────────


def write_run_info(recast_dir: Path, info: dict) -> Path:
    """Write <recast_dir>/run_info.json and return the path."""
    recast_dir = Path(recast_dir)
    recast_dir.mkdir(parents=True, exist_ok=True)
    out = recast_dir / "run_info.json"
    # Add UTC timestamp on write so the file captures when the run started.
    info = {
        **info,
        "started_at": info.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out.write_text(json.dumps(info, indent=2))
    return out


def parse_session_log_usage(session_log: Path) -> dict:
    """Extract usage (cost, tokens) from a claude --output-format stream-json log.

    Returns {} if the log is missing or unreadable. Tolerant of truncation —
    returns whatever the last `result` event contained.
    """
    session_log = Path(session_log) if session_log else None
    if session_log is None or not session_log.exists():
        return {}
    total_cost = 0.0
    input_tokens = output_tokens = 0
    cache_read = cache_creation = 0
    n_turns = 0
    try:
        with open(session_log) as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "result":
                    continue
                c = ev.get("total_cost_usd")
                if isinstance(c, (int, float)):
                    total_cost = float(c)
                u = ev.get("usage") or {}
                input_tokens = int(u.get("input_tokens", input_tokens) or input_tokens)
                output_tokens = int(u.get("output_tokens", output_tokens) or output_tokens)
                cache_read = int(u.get("cache_read_input_tokens", cache_read) or cache_read)
                cache_creation = int(
                    u.get("cache_creation_input_tokens", cache_creation) or cache_creation
                )
                n_turns = int(ev.get("num_turns", n_turns) or n_turns)
    except OSError:
        return {}
    if not (total_cost or input_tokens or output_tokens):
        return {}
    return {
        "api_cost_usd": round(total_cost, 6),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "tokens_total_billed": input_tokens + output_tokens + cache_creation,
        "n_turns": n_turns,
    }


def finalize_run_info(
    recast_dir: Path,
    exit_code: int,
    started_at: float | None = None,
    scores: dict | None = None,
    session_logs: list[Path] | None = None,
) -> Path:
    """Merge end-of-run metadata into run_info.json. Best-effort; never raises.

    Adds: ended_at, exit_code, duration_wall_s, final_score (from scores),
    and `usage` (summed across session_logs) if any claude stream-json was
    captured.
    """
    try:
        recast_dir = Path(recast_dir)
        info_path = recast_dir / "run_info.json"
        info: dict = {}
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text())
            except json.JSONDecodeError:
                info = {}
        info["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        info["exit_code"] = int(exit_code)
        if started_at is not None:
            info["duration_wall_s"] = round(time.time() - started_at, 2)
        if scores is not None:
            info["final_score"] = {
                "overall_score": scores.get("overall_score"),
                "overall_pass": scores.get("overall_pass"),
                "n_pass": scores.get("n_pass"),
                "n_filled": scores.get("n_filled"),
            }
        usage_total = {
            "api_cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tokens_total_billed": 0,
            "n_turns": 0,
        }
        any_usage = False
        for log_path in session_logs or []:
            if not log_path:
                continue
            u = parse_session_log_usage(Path(log_path))
            if not u:
                continue
            any_usage = True
            for k in usage_total:
                usage_total[k] += u.get(k, 0) or 0
        if any_usage:
            usage_total["api_cost_usd"] = round(usage_total["api_cost_usd"], 6)
            info["usage"] = usage_total
        info_path.write_text(json.dumps(info, indent=2))
        return info_path
    except Exception as exc:
        sys.stderr.write(f"finalize_run_info: {exc}\n")
        return Path(recast_dir) / "run_info.json"


# ── Fail-fast input validation ─────────────────────────────────────────────


def validate_launch_inputs(repo_root: Path, paper_ref: str, task: str = "recast") -> None:
    """Validate prerequisites before doing any workspace setup. Raises on failure.

    Named to not collide with the per-agent `preflight.py` module (which runs
    postflight checks on the agent's produced analysis.py).
    """
    if not paper_ref:
        raise ValueError("paper_ref is required (set `paper:` in --config or pass --paper-ref)")
    paper_dir = repo_root / "LHCRecastBench" / "papers" / paper_ref
    shared = paper_dir / "shared"
    if not shared.is_dir():
        raise FileNotFoundError(
            f"Paper shared inputs missing: {shared}\n"
            f"  Expected layout: LHCRecastBench/papers/{paper_ref}/shared/papers/{paper_ref}.pdf"
        )
    task_dir = paper_dir / "tasks" / task
    if not task_dir.is_dir():
        available = (
            sorted(p.name for p in (paper_dir / "tasks").iterdir() if p.is_dir())
            if (paper_dir / "tasks").is_dir()
            else []
        )
        raise FileNotFoundError(
            f"Task missing: {task_dir}\n" f"  Available tasks for {paper_ref}: {available}"
        )
    for required in (task_dir / "TASK.md", task_dir / "templates" / "HEPRecastData"):
        if not required.exists():
            raise FileNotFoundError(f"Missing {required}")
