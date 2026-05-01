"""All shipped configs must parse and validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.config import load_config, validate_config


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
SHIPPED_CONFIGS = sorted(CONFIG_DIR.glob("*.yaml"))


@pytest.mark.parametrize("config_path", SHIPPED_CONFIGS, ids=lambda p: p.name)
def test_config_loads_and_validates(config_path):
    cfg = load_config(str(config_path))
    # base.yaml has no `agent:` — that's intentional (it's inherited).
    # Every other config must pin an agent and a task id.
    if config_path.name != "base.yaml":
        assert cfg.get("agent") in {"simple", "baseline", "iterative", "anneal"}
        assert cfg.get("task"), "every non-base config must set `task:` to a task id"


def test_unknown_key_rejected():
    with pytest.raises(ValueError, match="unknown config key"):
        validate_config(
            {"agent": "simple", "task": "sus-16-046_sim-TChiWg", "bogus_key": 1},
            source="<test>",
        )


def test_yaml_bool_guard():
    # PyYAML parses `yes` / `no` / `on` / `off` as bool. The guard must refuse
    # them on string-only fields like `compute` (int isn't in the allowed types
    # there, so a bool would otherwise fall through to the str coercion path).
    with pytest.raises(ValueError, match="must not be a bool"):
        validate_config({"compute": True}, source="<test>")


def test_yaml_bool_guard_rejects_int_typed_fields():
    # bool is a subclass of int in Python; reject it explicitly for numeric
    # config fields so `max_iters: true` cannot silently pass as 1.
    for key in ("cpus", "max_iters", "effort", "anneal_t0"):
        with pytest.raises(ValueError, match="must not be a bool"):
            validate_config({key: True}, source="<test>")


def test_task_key_accepts_free_form_string():
    """Task is now a free-form task id (validated against the filesystem later)."""
    validate_config({"agent": "simple", "task": "sus-16-046_sim-TChiWg"})
    validate_config({"agent": "simple", "task": "whatever-the-user-wants"})


def test_extends_chain_resolved():
    """Non-base configs should inherit compute/account/qos from base.yaml.

    Picks any non-base shipped config; in public clones the per-vendor
    configs are gitignored so we fall back to skipping cleanly.
    """
    candidates = [p for p in SHIPPED_CONFIGS if p.name != "base.yaml"]
    if not candidates:
        pytest.skip(
            "no non-base configs present (public clone — vendor configs "
            "live in the gitignored configs/{claude,codex,gemini,aider}_*.yaml)"
        )
    cfg = load_config(str(candidates[0]))
    # Inherited from base.yaml:
    assert cfg.get("compute") == "perlmutter"
    assert cfg.get("account")
    assert cfg.get("qos")
