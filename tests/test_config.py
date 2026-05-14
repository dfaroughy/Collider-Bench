"""All shipped configs must parse and validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.config import load_config, shell_defaults, validate_api_auth_env, validate_config


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
SHIPPED_CONFIGS = sorted(CONFIG_DIR.rglob("*.yaml"))
PROFILE_CONFIGS = set((CONFIG_DIR / "utils").glob("*.yaml"))
RUNNABLE_CONFIGS = [p for p in SHIPPED_CONFIGS if p not in PROFILE_CONFIGS]


@pytest.mark.parametrize("config_path", SHIPPED_CONFIGS, ids=lambda p: p.name)
def test_config_loads_and_validates(config_path):
    cfg = load_config(str(config_path))
    # Profile configs only carry allocation defaults; runnable harness configs
    # must pin an agent and task id.
    if config_path not in PROFILE_CONFIGS:
        assert cfg.get("agent") == "simple"
        assert cfg.get("task"), "every runnable config must set `task:` to a task id"


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
    # config fields so `cpus: true` cannot silently pass as 1.
    for key in ("nodes", "ntasks", "cpus", "effort"):
        with pytest.raises(ValueError, match="must not be a bool"):
            validate_config({key: True}, source="<test>")


def test_task_key_accepts_free_form_string():
    """Task is now a free-form task id (validated against the filesystem later)."""
    validate_config({"agent": "simple", "task": "sus-16-046_sim-TChiWg"})
    validate_config({"agent": "simple", "task": "whatever-the-user-wants"})


def test_singularity_is_valid_sandbox_config():
    validate_config({"sandbox": "singularity"}, source="<test>")


def test_unknown_sandbox_rejected():
    with pytest.raises(ValueError, match="sandbox"):
        validate_config({"sandbox": "docker"}, source="<test>")


def test_tool_policy_disabled_delphes_validates():
    validate_config({"tool_policy": {"disabled": ["delphes"]}}, source="<test>")


def test_tool_policy_rejects_unknown_policy_key():
    with pytest.raises(ValueError, match="tool_policy has unknown key"):
        validate_config({"tool_policy": {"degraded": {"delphes": "no_btag"}}}, source="<test>")


def test_tool_policy_rejects_unknown_disabled_tool():
    with pytest.raises(ValueError, match="unknown disabled tool"):
        validate_config({"tool_policy": {"disabled": ["root"]}}, source="<test>")


@pytest.mark.parametrize(
    "config_path",
    RUNNABLE_CONFIGS,
    ids=lambda p: p.name,
)
def test_non_base_configs_pin_sandbox(config_path):
    cfg = load_config(str(config_path))
    assert cfg.get("sandbox"), f"{config_path.name}: every runnable config must pin sandbox"
    validate_config({"sandbox": cfg["sandbox"]}, source=str(config_path))


def test_claude_api_auth_requires_anthropic_api_key():
    cfg = {"agent": "simple", "runner": "claude", "provider": "anthropic", "auth": "api"}
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        validate_api_auth_env(cfg, environ={})

    env_name = "ANTHROPIC" + "_API_KEY"
    validate_api_auth_env(cfg, environ={env_name: "present"})


def test_deepseek_api_auth_requires_deepseek_key():
    for runner in ("claude", "forge"):
        cfg = {"agent": "simple", "runner": runner, "provider": "deepseek", "auth": "api"}
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            validate_api_auth_env(cfg, environ={})

        env_name = "DEEPSEEK" + "_API_KEY"
        validate_api_auth_env(cfg, environ={env_name: "present"})


def test_extends_chain_resolved():
    """Runnable configs should inherit compute/account/qos from profile configs.

    Picks any runnable shipped config; in public clones some local development
    configs are gitignored so we fall back to skipping cleanly.
    """
    candidates = RUNNABLE_CONFIGS
    if not candidates:
        pytest.skip(
            "no runnable configs present (public clone — vendor configs "
            "live in the gitignored configs/{claude,codex,gemini,aider}_*.yaml)"
        )
    cfg = load_config(str(candidates[0]))
    # Inherited from configs/utils:
    assert cfg.get("compute") == "slurm"
    assert cfg.get("account")
    assert "partition" in cfg
    assert cfg.get("constraint") == "cpu"
    assert cfg.get("nodes") == 1
    assert cfg.get("ntasks") == 1
    assert cfg.get("qos")


def test_slurm_resource_keys_validate():
    validate_config(
        {
            "compute": "perlmutter",
            "account": "m4539",
            "partition": "cpu",
            "constraint": "cpu",
            "nodes": 1,
            "ntasks": 1,
            "cpus": 16,
            "walltime": "04:00:00",
            "qos": "interactive",
            "salloc_extra": "--exclusive",
            "srun_extra": "--cpu-bind=cores",
            "env_setup": "source ~/.bashrc",
        },
        source="<test>",
    )


def test_shell_defaults_include_slurm_resource_keys():
    defaults = shell_defaults(CONFIG_DIR / "utils" / "perlmutter_interactive.yaml")
    assert defaults["COMPUTE"] == "slurm"
    assert defaults["ACCOUNT"] == "m4539"
    assert defaults["PARTITION"] == ""
    assert defaults["CONSTRAINT"] == "cpu"
    assert defaults["NODES"] == "1"
    assert defaults["NTASKS"] == "1"
    assert defaults["CPUS"] == "128"
    assert defaults["SALLOC_EXTRA"] == ""
    assert defaults["SRUN_EXTRA"] == ""
    assert defaults["ENV_SETUP"] == ""


def test_api_profile_defaults_are_regular_qos():
    defaults = shell_defaults(CONFIG_DIR / "utils" / "perlmutter_api.yaml")
    assert defaults["COMPUTE"] == "slurm"
    assert defaults["ACCOUNT"] == "m4539"
    assert defaults["CONSTRAINT"] == "cpu"
    assert defaults["CPUS"] == "16"
    assert defaults["WALLTIME"] == "03:00:00"
    assert defaults["QOS"] == "regular"


# ── validate_api_auth_env: cases not covered by tests above ────────────────


def test_validate_api_auth_env_passes_when_auth_oauth():
    # OAuth path doesn't need any env var, regardless of empty environ.
    validate_api_auth_env({"auth": "oauth", "runner": "claude"}, environ={})


def test_validate_api_auth_env_passes_when_no_auth_field():
    validate_api_auth_env({"runner": "claude"}, environ={})


def test_validate_api_auth_env_passes_for_unregistered_runner_provider():
    # No entry in _API_AUTH_ENV → no-op (e.g. gemini, codex).
    validate_api_auth_env({"auth": "api", "runner": "gemini"}, environ={})
