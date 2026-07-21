"""All shipped configs must parse and validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.config import (
    load_config,
    preflight_local_endpoint,
    shell_defaults,
    validate_api_auth_env,
    validate_config,
)


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


def test_docker_is_valid_sandbox_config():
    validate_config({"sandbox": "docker"}, source="<test>")


def test_unknown_sandbox_rejected():
    with pytest.raises(ValueError, match="sandbox"):
        validate_config({"sandbox": "rkt"}, source="<test>")


def test_tool_policy_disabled_delphes_validates():
    validate_config({"tool_policy": {"disabled": ["delphes"]}}, source="<test>")


def test_tool_policy_disabled_prospino_validates():
    validate_config({"tool_policy": {"disabled": ["prospino"]}}, source="<test>")


def test_tool_policy_allowlist_matches_ablation_registry():
    """config._ALLOWED_DISABLED_TOOLS must not drift from workspace._ABLATIONS."""
    from agent_runtime.config import _ALLOWED_DISABLED_TOOLS
    from agent_runtime.workspace import _ABLATIONS

    assert _ALLOWED_DISABLED_TOOLS == set(_ABLATIONS)


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


def test_opencode_local_auth_requires_glm_env(monkeypatch, tmp_path):
    # Point the fallback file at a non-existent path so the validator
    # behaves identically regardless of what's on the host.
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("opencode", "local"),
        str(tmp_path / "absent.env"),
    )
    cfg = {"agent": "simple", "runner": "opencode", "provider": "local", "auth": "api"}
    with pytest.raises(ValueError, match="GLM_API_BASE"):
        validate_api_auth_env(cfg, environ={})
    # Both vars are required — base alone is insufficient.
    with pytest.raises(ValueError, match="GLM_API_KEY"):
        validate_api_auth_env(cfg, environ={"GLM" + "_API_BASE": "http://node:8000/v1"})
    validate_api_auth_env(
        cfg,
        environ={
            "GLM" + "_API_BASE": "http://node:8000/v1",
            "GLM" + "_API_KEY": "token",  # pragma: allowlist secret
        },
    )


def test_opencode_local_auth_loads_env_fallback_file(monkeypatch, tmp_path):
    """When the shell lacks the GLM vars, the validator should source the
    runner-specific fallback file (lets the vLLM launcher rewrite the
    hostname on every restart without users having to re-source)."""
    fb = tmp_path / "glm47_api.env"
    fb.write_text(
        "# auto-written by start_glm47_service.sh\n"
        "export GLM_API_BASE=http://nid001234:8000/v1\n"
        "export GLM_API_KEY=rotated-token\n"
        "export GLM_NODE=nid001234\n"
    )
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("opencode", "local"),
        str(fb),
    )
    cfg = {"agent": "simple", "runner": "opencode", "provider": "local", "auth": "api"}
    env: dict[str, str] = {}
    validate_api_auth_env(cfg, environ=env)
    assert env["GLM" + "_API_BASE"] == "http://nid001234:8000/v1"
    assert env["GLM" + "_API_KEY"] == "rotated-token"  # pragma: allowlist secret


def test_opencode_local_auth_fallback_overrides_stale_shell(monkeypatch, tmp_path):
    """The vLLM launcher rewrites the env file on every restart with the
    fresh node hostname. A `GLM_API_BASE` left in the shell from an
    earlier `source` of an older file is stale and would route requests
    to a dead node, so file values must override shell values."""
    fb = tmp_path / "glm47_api.env"
    fb.write_text("export GLM_API_BASE=http://fresh-node:8000/v1\nexport GLM_API_KEY=fresh\n")
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("opencode", "local"),
        str(fb),
    )
    cfg = {"agent": "simple", "runner": "opencode", "provider": "local", "auth": "api"}
    env = {
        "GLM" + "_API_BASE": "http://stale-node:8000/v1",  # dead, from a previous server
        "GLM" + "_API_KEY": "stale",  # pragma: allowlist secret
    }
    validate_api_auth_env(cfg, environ=env)
    assert env["GLM" + "_API_BASE"] == "http://fresh-node:8000/v1"
    assert env["GLM" + "_API_KEY"] == "fresh"  # pragma: allowlist secret


def test_opencode_runner_and_local_provider_validate():
    validate_config({"runner": "opencode", "provider": "local"}, source="<test>")


def test_opencode_local_air_provider_validates():
    validate_config({"runner": "opencode", "provider": "local-air"}, source="<test>")


def test_opencode_local_air_auth_requires_glm_air_env(monkeypatch, tmp_path):
    """Air is the 4-node GLM-4.5-Air cluster — uses a different env-var
    pair (`GLM_AIR_API_BASE` / `GLM_AIR_API_KEY`) so Flash and Air can
    coexist in the same shell without clobbering each other."""
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("opencode", "local-air"),
        str(tmp_path / "absent.env"),
    )
    cfg = {"agent": "simple", "runner": "opencode", "provider": "local-air", "auth": "api"}
    with pytest.raises(ValueError, match="GLM_AIR_API_BASE"):
        validate_api_auth_env(cfg, environ={})
    with pytest.raises(ValueError, match="GLM_AIR_API_KEY"):
        validate_api_auth_env(cfg, environ={"GLM" + "_AIR_API_BASE": "http://node:8000/v1"})
    validate_api_auth_env(
        cfg,
        environ={
            "GLM" + "_AIR_API_BASE": "http://node:8000/v1",
            "GLM" + "_AIR_API_KEY": "token",  # pragma: allowlist secret
        },
    )


def test_opencode_local_air_preflight_distinct_from_flash():
    """The Flash and Air probes must read different env vars — otherwise
    a stale Flash hostname could falsely satisfy the Air preflight."""
    from agent_runtime.config import _API_PREFLIGHT_PROBES

    flash = _API_PREFLIGHT_PROBES[("opencode", "local")]
    air = _API_PREFLIGHT_PROBES[("opencode", "local-air")]
    assert flash["base_var"] == "GLM" + "_API_BASE"
    assert air["base_var"] == "GLM" + "_AIR_API_BASE"
    assert flash["base_var"] != air["base_var"]


# ── Claude Code as harness, vLLM/GLM as backend ────────────────────────────


def test_claude_local_provider_validates_for_both_flash_and_air():
    """`provider: local` and `provider: local-air` must be accepted under
    the `claude` runner — this is the path that lets Claude Code drive
    a local GLM model via vLLM's Anthropic /v1/messages endpoint."""
    validate_config({"runner": "claude", "provider": "local"}, source="<test>")
    validate_config({"runner": "claude", "provider": "local-air"}, source="<test>")


def test_claude_local_auth_requires_glm_env(monkeypatch, tmp_path):
    """`(claude, local)` shares the Flash env-var contract with
    `(opencode, local)`. If the harness ever silently drops this pair
    from `_API_AUTH_ENV`, the validator becomes a no-op and Claude Code
    launches with a broken ANTHROPIC_BASE_URL."""
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("claude", "local"),
        str(tmp_path / "absent.env"),
    )
    cfg = {"agent": "simple", "runner": "claude", "provider": "local", "auth": "api"}
    with pytest.raises(ValueError, match="GLM_API_BASE"):
        validate_api_auth_env(cfg, environ={})


def test_claude_local_air_auth_requires_glm_air_env(monkeypatch, tmp_path):
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("claude", "local-air"),
        str(tmp_path / "absent.env"),
    )
    cfg = {"agent": "simple", "runner": "claude", "provider": "local-air", "auth": "api"}
    with pytest.raises(ValueError, match="GLM_AIR_API_BASE"):
        validate_api_auth_env(cfg, environ={})


def test_claude_local_air_loads_fallback_env_file(monkeypatch, tmp_path):
    """Same auto-discovery as opencode's local-air — when the shell lacks
    the GLM_AIR_* vars, the validator must source glm45_air_api.env."""
    fb = tmp_path / "glm45_air_api.env"
    fb.write_text(
        "export GLM_AIR_API_BASE=http://airnode:8010/v1\n"
        "export GLM_AIR_API_KEY=air-token\n"  # pragma: allowlist secret
    )
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("claude", "local-air"),
        str(fb),
    )
    cfg = {"agent": "simple", "runner": "claude", "provider": "local-air", "auth": "api"}
    env: dict[str, str] = {}
    validate_api_auth_env(cfg, environ=env)
    assert env["GLM" + "_AIR_API_BASE"] == "http://airnode:8010/v1"
    assert env["GLM" + "_AIR_API_KEY"] == "air-token"  # pragma: allowlist secret


def test_claude_local_air_preflight_target_is_air_server(monkeypatch):
    """Preflight for `(claude, local-air)` must point at the Air vars,
    not Flash's — a Flash-Air confusion here would route the probe at
    the wrong (or stale) server."""
    from agent_runtime.config import _API_PREFLIGHT_PROBES

    spec = _API_PREFLIGHT_PROBES[("claude", "local-air")]
    assert spec["base_var"] == "GLM" + "_AIR_API_BASE"
    assert spec["key_var"] == "GLM" + "_AIR_API_KEY"
    # The label should disambiguate from the opencode/local-air entry.
    assert "Claude Code" in spec["what"]


def test_claude_local_wiring_strips_trailing_v1_for_anthropic_base_url(monkeypatch):
    """The launcher writes `GLM_AIR_API_BASE=http://node:8010/v1` (includes
    `/v1` for OpenAI-compat). Claude Code appends `/v1/messages` to
    ANTHROPIC_BASE_URL, so passing the value verbatim produces
    `http://node:8010/v1/v1/messages` — a 404. The runner_spec block
    must strip the trailing `/v1` before exporting."""
    from agent_runtime.vendors import CLAUDE_SPEC
    from agent_runtime.runner_spec import DeclarativeRunner

    monkeypatch.setenv("GLM" + "_AIR_API_BASE", "http://airnode:8010/v1")
    monkeypatch.setenv("GLM" + "_AIR_API_KEY", "air-token")  # pragma: allowlist secret
    runner = DeclarativeRunner(CLAUDE_SPEC)
    cfg = {
        "auth": "api",
        "runner": "claude",
        "provider": "local-air",
        "model": "glm-4.5-air",
    }
    prep = runner.prepare_launch("/tmp/fake_sandbox", config=cfg)
    assert prep.env["ANTHROPIC_BASE_URL"] == "http://airnode:8010"  # /v1 stripped
    assert prep.env["ANTHROPIC_MODEL"] == "glm-4.5-air"
    # Every alias resolves to the served-model-name (vLLM is single-model).
    for alias in (
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert prep.env[alias] == "glm-4.5-air", alias
    # Auth token must be present so vLLM accepts the call.
    assert prep.env.get("ANTHROPIC_AUTH_TOKEN") == "air-token"  # pragma: allowlist secret
    # Forward the GLM vars too so the container sees them.
    assert "GLM" + "_AIR_API_BASE" in prep.secret_env_names
    assert "GLM" + "_AIR_API_KEY" in prep.secret_env_names
    # Context-management env vars (regression guard — see the
    # standalone test below for the failure mode they prevent).
    assert prep.env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") == "131072"
    assert prep.env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS") == "16000"


def test_claude_local_wiring_sets_compaction_window_to_vllm_max_model_len(monkeypatch):
    """Claude Code defaults its auto-compact threshold for a 200k window
    (the native Anthropic models all have ~200k). With local vLLM at
    --max-model-len 131072, never telling Claude Code the real size
    means no compaction ever fires — the run dies on the first
    `model_context_window_exceeded` API error.

    Regression: keep `CLAUDE_CODE_AUTO_COMPACT_WINDOW` in sync with
    the vLLM launchers (launch_glm47_vllm.sh /
    launch_glm45_air_vllm.sh both set MAX_MODEL_LEN=131072). If
    either launcher's MAX_MODEL_LEN changes, this constant must too.
    """
    from agent_runtime.vendors import CLAUDE_SPEC
    from agent_runtime.runner_spec import DeclarativeRunner

    monkeypatch.setenv("GLM" + "_API_BASE", "http://node:8000/v1")
    monkeypatch.setenv("GLM" + "_API_KEY", "k")  # pragma: allowlist secret
    runner = DeclarativeRunner(CLAUDE_SPEC)
    for prov, model in (("local", "glm-4.7-flash"), ("local-air", "glm-4.5-air")):
        cfg = {"auth": "api", "runner": "claude", "provider": prov, "model": model}
        if prov == "local-air":
            monkeypatch.setenv("GLM" + "_AIR_API_BASE", "http://airnode:8010/v1")
            monkeypatch.setenv("GLM" + "_AIR_API_KEY", "k")  # pragma: allowlist secret
        prep = runner.prepare_launch("/tmp/fake_sandbox", config=cfg)
        # Both providers point at vLLM with --max-model-len 131072.
        assert prep.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "131072", prov
        # max_output_tokens stays well below half the window so context
        # grows can fill the remaining ~115k turn after turn before
        # compaction kicks in.
        assert prep.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16000", prov


def test_claude_local_wiring_handles_base_url_without_v1_suffix(monkeypatch):
    """If a future launcher writes the base URL WITHOUT a trailing /v1
    (e.g. `http://node:8010`), the strip should be a no-op — don't
    over-strip and create `http://node:801`."""
    from agent_runtime.vendors import CLAUDE_SPEC
    from agent_runtime.runner_spec import DeclarativeRunner

    monkeypatch.setenv("GLM" + "_API_BASE", "http://flashnode:8000")
    monkeypatch.setenv("GLM" + "_API_KEY", "flash-token")  # pragma: allowlist secret
    runner = DeclarativeRunner(CLAUDE_SPEC)
    cfg = {
        "auth": "api",
        "runner": "claude",
        "provider": "local",
        "model": "glm-4.7-flash",
    }
    prep = runner.prepare_launch("/tmp/fake_sandbox", config=cfg)
    assert prep.env["ANTHROPIC_BASE_URL"] == "http://flashnode:8000"  # unchanged


def test_opencode_pre_launch_rewrites_small_model_to_run_model(tmp_path):
    """The repo-checked-in `opencode.json` pins `small_model` to Flash. When
    the Air server is up and Flash is down, opencode tries the title pass
    against Flash → empty baseURL → `"\"/chat/completions\" cannot be parsed
    as a URL"`. The pre-launch hook rewrites `small_model` to the run's
    actual model so the title pass uses the same live backend."""
    import json as _json

    from agent_runtime.vendors import _opencode_pre_launch

    cfg = {"model": "glm-air-local/glm-4.5-air"}
    _opencode_pre_launch(tmp_path, cfg)
    spec = _json.loads((tmp_path / "opencode.json").read_text())
    assert spec["model"] == "glm-air-local/glm-4.5-air"
    assert spec["small_model"] == "glm-air-local/glm-4.5-air"
    # Both provider blocks still present — opencode dispatches on
    # provider/model prefix at runtime; we don't strip the other one.
    assert "glm-local" in spec["provider"]
    assert "glm-air-local" in spec["provider"]


def test_opencode_pre_launch_preserves_default_when_no_config(tmp_path):
    """Without config (legacy callers, tests), the hook copies opencode.json
    as-is — no rewrite, no surprise."""
    import json as _json

    from agent_runtime.vendors import _opencode_pre_launch

    _opencode_pre_launch(tmp_path, None)
    spec = _json.loads((tmp_path / "opencode.json").read_text())
    # Whatever the source file declares — don't assert specifics, just that
    # we didn't drop or rewrite anything.
    assert spec.get("model")
    assert spec.get("small_model")


def test_opencode_spec_forwards_every_local_providers_env_vars():
    """Every (opencode, *) entry in `_API_AUTH_ENV` lists env vars that
    `opencode.json`'s provider blocks read via `{env:…}`. The runner
    spec's `secret_env_names` is the bridge — vars listed there are the
    ones that actually enter the container. If you add a new local
    provider (e.g. opencode.json gets a `glm-foo-local` block) and
    register its auth-env pair in `_API_AUTH_ENV` but forget this
    forward list, opencode will start, fail to build the API URL, and
    die with `"\"/chat/completions\" cannot be parsed as a URL."` — a
    very expensive 18-second failure to diagnose. This test makes the
    forgotten-forward a unit-test failure instead.
    """
    from agent_runtime.config import _API_AUTH_ENV
    from agent_runtime.vendors import OPENCODE_SPEC

    required = {
        var
        for (runner, _provider), env_vars in _API_AUTH_ENV.items()
        if runner == "opencode"
        for var in env_vars
    }
    missing = required - set(OPENCODE_SPEC.secret_env_names)
    assert not missing, (
        f"OPENCODE_SPEC.secret_env_names is missing {sorted(missing)}. "
        f"Add to vendors.py:OPENCODE_SPEC.secret_env_names so the harness "
        f"forwards them into the container."
    )


def test_deepseek_api_auth_requires_deepseek_key():
    for runner in ("claude", "forge"):
        cfg = {"agent": "simple", "runner": runner, "provider": "deepseek", "auth": "api"}
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            validate_api_auth_env(cfg, environ={})

        env_name = "DEEPSEEK" + "_API_KEY"
        validate_api_auth_env(cfg, environ={env_name: "present"})


def test_extends_chain_resolved():
    """When a runnable config uses `extends:`, the chain merges in compute /
    account / qos / etc. from the profile.

    The public shipped configs (claude.yaml, claude_slurm.yaml) are
    self-contained by design — no `extends:` — so this test skips on a
    public clone. It still runs against any private maintainer config
    under configs/ that does declare an extends chain.
    """
    candidates = [p for p in RUNNABLE_CONFIGS if "extends:" in p.read_text()]
    if not candidates:
        pytest.skip(
            "no runnable configs with `extends:` present "
            "(public clones ship only self-contained reference configs)"
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
            "account": "YOUR_PROJECT",
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
    """Loaded from a gitignored maintainer profile when present."""
    profile = CONFIG_DIR / "utils" / "perlmutter_interactive.yaml"
    if not profile.is_file():
        pytest.skip("maintainer-only profile not present in this clone")
    defaults = shell_defaults(profile)
    # Structural checks only — every SLURM key is extracted with a string value.
    for key in (
        "COMPUTE",
        "ACCOUNT",
        "PARTITION",
        "CONSTRAINT",
        "NODES",
        "NTASKS",
        "CPUS",
        "WALLTIME",
        "QOS",
        "SALLOC_EXTRA",
        "SRUN_EXTRA",
        "ENV_SETUP",
    ):
        assert key in defaults, f"missing shell-default key: {key}"
        assert isinstance(defaults[key], str)
    assert defaults["COMPUTE"] == "slurm"


def test_api_profile_defaults_are_regular_qos():
    """Same as above for the API/regular-qos profile."""
    profile = CONFIG_DIR / "utils" / "perlmutter_api.yaml"
    if not profile.is_file():
        pytest.skip("maintainer-only profile not present in this clone")
    defaults = shell_defaults(profile)
    assert defaults["COMPUTE"] == "slurm"
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


# ── End-to-end integration tests for the opencode/local launch path ─────────
#
# These corner the failure modes that bit us during opencode/glm47 bring-up:
#   1. `provider` field present in YAML but absent from the argparse
#      Namespace → `(runner, provider)` lookup falls back to
#      `(runner, runner)` → registry misses → fallback file never read
#      → stale `GLM_API_BASE` in the shell silently routes to a dead node.
#   2. vLLM allocation expires mid-run → every call hangs until opencode's
#      stream timeout (~2 min) → terminal looks frozen with no error.
#
# Both used to manifest as "the run just hangs"; these tests turn them into
# loud failures at config-resolution time.


def test_resolve_merges_provider_from_yaml_so_validator_sees_local(monkeypatch, tmp_path):
    """The launch.py merge that produces the validator's input MUST include
    `provider` from the YAML — otherwise `(opencode, local)` becomes
    `(opencode, opencode)` and the fallback file is never read."""
    # Fresh env file with the correct hostname:
    fb = tmp_path / "glm47_api.env"
    fb.write_text("export GLM_API_BASE=http://fresh:8000/v1\nexport GLM_API_KEY=fresh\n")
    monkeypatch.setitem(
        __import__(
            "agent_runtime.config", fromlist=["_API_AUTH_ENV_FALLBACK_FILES"]
        )._API_AUTH_ENV_FALLBACK_FILES,
        ("opencode", "local"),
        str(fb),
    )

    # Reproduce launch.py's _resolve() merge: take cfg from YAML, overlay
    # any non-None CLI args. The bug was that `provider` lived only in cfg
    # and dropped out of `vars(args)`-based dicts. We assert the merged
    # dict the validator sees has `provider`.
    cfg = {"runner": "opencode", "provider": "local", "auth": "api"}
    args_namespace_like = {"runner": "opencode", "auth": "api"}  # no provider!
    merged = {**cfg, **{k: v for k, v in args_namespace_like.items() if v is not None}}
    assert merged.get("provider") == "local", (
        "regression: launch.py merge dropped `provider`; the fallback "
        "lookup will fall through to (opencode, opencode) and skip the env file"
    )

    # And the validator with the correct merged dict overwrites stale shell.
    env: dict[str, str] = {
        "GLM" + "_API_BASE": "http://stale:8000/v1",
        "GLM" + "_API_KEY": "stale",  # pragma: allowlist secret
    }
    validate_api_auth_env(merged, environ=env)
    assert env["GLM" + "_API_BASE"] == "http://fresh:8000/v1"


def test_preflight_passes_when_server_responds_200(monkeypatch):
    """Healthy vLLM → preflight is silent."""
    calls = []

    def fake_probe(base, key, timeout_s=3.0):
        calls.append((base, key))
        return True, ""

    cfg = {"auth": "api", "runner": "opencode", "provider": "local"}
    env = {"GLM" + "_API_BASE": "http://alive:8000/v1", "GLM" + "_API_KEY": "k"}
    preflight_local_endpoint(cfg, environ=env, probe=fake_probe)
    assert calls == [("http://alive:8000/v1", "k")]


def test_preflight_raises_when_server_dead(monkeypatch):
    """Dead vLLM (SLURM job revoked, hostname rotated, etc.) → loud abort
    with a hint pointing at `squeue` and the relaunch script. Without this,
    the run hangs silently for ~2 min inside opencode's stream timeout."""

    def fake_probe(base, key, timeout_s=3.0):
        return False, "connect: timed out"

    cfg = {"auth": "api", "runner": "opencode", "provider": "local"}
    env = {"GLM" + "_API_BASE": "http://dead:8000/v1", "GLM" + "_API_KEY": "k"}
    with pytest.raises(ValueError, match="GLM.*vLLM server unreachable.*dead.*timed out"):
        preflight_local_endpoint(cfg, environ=env, probe=fake_probe)


def test_preflight_no_op_when_not_local_or_no_auth():
    """Probe must NOT run for unrelated runners (we don't want to ping the
    public Anthropic / OpenAI / Google endpoints every launch)."""
    sentinel = {"called": False}

    def fake_probe(*a, **k):
        sentinel["called"] = True
        return True, ""

    # Wrong runner/provider combination — no registration.
    preflight_local_endpoint(
        {"auth": "api", "runner": "claude", "provider": "anthropic"},
        environ={"ANTHROPIC" + "_API_KEY": "k"},
        probe=fake_probe,
    )
    # oauth never probes either.
    preflight_local_endpoint(
        {"auth": "oauth", "runner": "opencode", "provider": "local"},
        environ={},
        probe=fake_probe,
    )
    assert sentinel["called"] is False


def test_preflight_against_real_http_server(tmp_path):
    """Hit a tiny local stdlib HTTP server end-to-end — no mocks. Catches
    regressions in the urllib glue (timeout shape, header format, response
    parsing) that a mocked probe would miss."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.headers.get("Authorization") == "Bearer good-key"
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def log_message(self, *a, **k):
            pass  # silence test output

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        cfg = {"auth": "api", "runner": "opencode", "provider": "local"}
        good_env = {
            "GLM" + "_API_BASE": f"http://127.0.0.1:{port}/v1",
            "GLM" + "_API_KEY": "good-key",  # pragma: allowlist secret
        }
        # Healthy path: no raise.
        preflight_local_endpoint(cfg, environ=good_env)

        # Bad key path: server returns 401, preflight should refuse.
        bad_env = dict(good_env, **{"GLM" + "_API_KEY": "bad"})
        with pytest.raises(ValueError, match="HTTP 401"):
            preflight_local_endpoint(cfg, environ=bad_env)
    finally:
        srv.shutdown()

    # Dead-server path: after shutdown, the same URL should fail-fast.
    cfg_dead = {"auth": "api", "runner": "opencode", "provider": "local"}
    with pytest.raises(ValueError, match="unreachable"):
        preflight_local_endpoint(cfg_dead, environ=good_env)
