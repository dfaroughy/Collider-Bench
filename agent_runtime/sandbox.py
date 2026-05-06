"""Filesystem isolation for agent processes — pluggable backend.

Usage:
    from agent_runtime.sandbox import sandbox_command

    cmd, cleanup = sandbox_command(
        workspace=Path("runs/simulate_.../workspace"),
        repo_root=Path("."),
        inner_cmd=["claude", "-p", "...", "--dangerously-skip-permissions"],
        extra_ro_binds=[Path(".../agent_runtime")],
    )
    subprocess.run(cmd, env=env)
    cleanup()

Backend selection (first match wins):
    1. `sandbox=` kwarg passed by the caller
    2. `LHC_RECAST_SANDBOX` environment variable
    3. auto-detect: podman → apptainer → singularity → (warn) none

Available backends:
    podman    — runs the canonical lhc-bench image (default on hosts where
                podman / podman-hpc is installed)
    apptainer — same image via apptainer exec (HPC sites with no podman)
    singularity — same image via singularity exec (legacy/generic HPC)
    bwrap     — bubblewrap on host, bypasses the container; opt-in only
    none      — passthrough, no isolation (CI / debugging)

Adding a new backend (Docker, Shifter, …): subclass Sandbox, register it in
SANDBOXES at the bottom of this file. See agent_runtime/SANDBOX.md.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable, Sequence


# ── Image selection ────────────────────────────────────────────────────────

_DEFAULT_IMAGE = "ghcr.io/dfaroughy/lhc-bench:latest"


_RUNNER_ENV_PASSTHROUGH: tuple[str, ...] = (
    "CODEX_HOME",
    "GEMINI_CLI_HOME",
    "NO_COLOR",
    "CI",
    "TERM",
    "CLAUDE_BIN",
    "CODEX_BIN",
    "GEMINI_BIN",
    "AIDER_BIN",
    # Routing the Claude Code CLI at a non-Anthropic backend (e.g. DeepSeek's
    # Anthropic-compatible endpoint at https://api.deepseek.com/anthropic).
    # The container otherwise defaults to Anthropic and 401s.
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
)


def _container_env_pairs(
    container_env: dict[str, str] | None,
    secret_env_names: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """Return env vars to expose inside container backends.

    Source precedence is explicit launch env first, then host env. API keys are
    never wildcard-forwarded: each runner declares the secret env names it
    requires, and only those names are exposed to the container.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for var in (*_RUNNER_ENV_PASSTHROUGH, *tuple(secret_env_names)):
        val = (container_env or {}).get(var) or os.environ.get(var)
        if val:
            out.append((var, val))
            seen.add(var)
    return out


def _validate_home_dir_name(name: str) -> str:
    """Validate the runner-specific fake-home directory name.

    Fake homes are created as `<run>/<name>`. Accept one plain relative path
    segment only, so a bad spec cannot delete or populate paths outside the run
    directory.
    """
    p = Path(name)
    if (
        not name
        or p.is_absolute()
        or len(p.parts) != 1
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise ValueError(f"invalid fake-home directory name: {name!r}")
    return name


def _validate_home_file(rel: str) -> str:
    """Validate a host-home-relative file copied into fake HOME."""
    p = Path(rel)
    if (
        not rel
        or rel == "."
        or p.is_absolute()
        or any(part in {"", ".", ".."} for part in p.parts)
        or "\\" in rel
    ):
        raise ValueError(f"invalid fake-home file path: {rel!r}")
    return rel


def _fake_home_target(workspace: Path, home_dir_name: str) -> Path:
    return workspace.parent / _validate_home_dir_name(home_dir_name)


def _materialize_papers_dir(workspace: Path) -> None:
    """Replace workspace/papers symlink with a real in-workspace copy.

    Some agent CLIs, notably Gemini, reject tool reads when a path inside the
    workspace resolves through a symlink to a target outside the workspace.
    The paper directory contains only public PDFs, so copying it into the
    workspace before sandbox wrapping preserves the benchmark hiding contract
    while satisfying those CLI workspace guards.
    """
    papers = workspace / "papers"
    if not papers.is_symlink():
        return
    target = papers.resolve()
    if not target.is_dir():
        return
    papers.unlink()
    shutil.copytree(target, papers)


def _disabled_delphes_dir(workspace: Path) -> Path | None:
    path = workspace / "disabled_tools" / "delphes"
    return path if path.is_dir() else None


def _prepare_isolated_home(
    host_home: Path,
    target: Path | None = None,
    rel_files: Iterable[str] = (),
) -> Path:
    """Build a per-container fake $HOME containing selected files only.

    The container is then launched with `HOME=<fake-home>` and the fake-home
    dir bind-mounted into the container at the same path; the CLI sees a
    pristine home with just the runner-specific auth/config it needs and
    writes any session state into the fake-home dir. The copied files are
    provided by the active runner's LaunchPrep, so unrelated vendor state
    cannot leak into the run.

    `target` is the host path to use as fake HOME. When omitted (legacy
    callers + tests) we fall back to a tempdir; production launches pass
    a runner-specific path such as `<recast>/.claude_home` so the
    conversation DBs / state dirs that
    forge and claude write to `~/.forge` / `~/.claude` survive container
    cleanup. (Codex and Gemini use their own workspace-relative state
    dirs via CODEX_HOME / GEMINI_CLI_HOME — they don't depend on this.)
    """
    fake_home = target if target is not None else Path(tempfile.mkdtemp(prefix="lhc-recast-home-"))
    if target is not None and fake_home.exists():
        if fake_home.is_symlink() or fake_home.is_file():
            fake_home.unlink()
        else:
            shutil.rmtree(fake_home)
    fake_home.mkdir(parents=True, exist_ok=True)
    for rel in rel_files:
        rel = _validate_home_file(rel)
        src = host_home / rel
        if not src.is_file():
            continue
        dst = fake_home / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
    return fake_home


def _sync_credentials_back_to_host(
    fake_home: Path,
    host_home: Path,
    rel_files: Iterable[str] = (),
) -> None:
    """Copy refreshed OAuth credential files from `fake_home` back to host.

    OAuth refresh-token rotation: when a CLI in the container refreshes
    its access token, the auth server invalidates the old refresh token
    on its side. The container's fake-home copy now holds the new tokens;
    the host's copy holds tokens that are dead in Anthropic/OpenAI/
    Google's eyes. Without this sync, the *next* run starts from a
    stale refresh token and immediately 401s on the first refresh.

    We compare mtimes — only copy back when the fake-home file is newer
    than the host file (i.e. the container actually wrote it). Best-
    effort; never raises so it can't break the cleanup path.
    """
    for rel in rel_files:
        rel = _validate_home_file(rel)
        try:
            src = fake_home / rel
            host = host_home / rel
            if not src.is_file() or not host.is_file():
                continue
            if src.stat().st_mtime > host.stat().st_mtime:
                shutil.copy2(src, host)
        except OSError:
            pass  # never block cleanup on a credential-sync hiccup


# ── Backend protocol ────────────────────────────────────────────────────────


class Sandbox(ABC):
    """Filesystem-isolation backend for a single agent invocation."""

    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """True if the backend can run on the current host."""

    @abstractmethod
    def wrap(
        self,
        workspace: Path,
        repo_root: Path,
        inner_cmd: Sequence[str],
        extra_ro_binds: Iterable[Path] | None = None,
        container_env: dict[str, str] | None = None,
        secret_env_names: Iterable[str] = (),
        home_dir_name: str = ".agent_home",
        home_files: Iterable[str] = (),
        home_credential_files: Iterable[str] = (),
    ) -> tuple[list[str], Callable[[], None]]:
        """Return (command, cleanup_fn).

        The returned command, when executed, runs `inner_cmd` with the agent
        isolated per this backend's policy:
          - <workspace> is the only read-write path under the repo
          - benchmark reference answers and evaluator internals are hidden
          - any paths in extra_ro_binds are mounted read-only
          - /tmp should be a fresh private tmpfs (or equivalent)

        cleanup_fn must be safe to call exactly once after the process exits,
        even on failure.
        """


# ── bubblewrap (Linux user-space namespaces) ───────────────────────────────


class BwrapSandbox(Sandbox):
    """User-space sandbox via `bwrap` (bubblewrap).

    Exposes the host filesystem but shadows the entire repo with a tmpfs, then
    re-binds only workspace/ (rw) and a minimal LHCRecastBench/ subset (ro). PID /
    IPC / UTS namespaces are unshared; network is left intact so the agent can
    reach the model API and public data sources.

    Known limitation on NERSC: $HOME is on autofs and cannot be tmpfs'd from
    inside the bwrap namespace. The agent therefore retains read-write access
    to $HOME. Acceptable on trusted single-user setups; run under a scrubbed
    service account if this matters.
    """

    name = "bwrap"

    def available(self) -> bool:
        return shutil.which("bwrap") is not None

    def wrap(
        self,
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=None,
        container_env=None,
        secret_env_names=(),
        home_dir_name=".agent_home",
        home_files=(),
        home_credential_files=(),
    ):
        from agent_runtime import paths as bench_paths

        benchmark_dir = bench_paths.benchmark_dir(repo_root)

        # bwrap can't mount over a symlink; swap top-level workspace
        # symlinks (bin/tools) for empty dirs, then bind the resolved
        # targets back in. Restore the symlinks after the agent exits.
        extra_mounts: list[tuple[str, str]] = []
        symlink_restore: list[tuple[str, str]] = []
        for name in ("bin", "tools"):
            link = workspace / name
            if link.is_symlink():
                real = link.resolve()
                target = os.readlink(str(link))
                extra_mounts.append((str(real), str(workspace / name)))
                symlink_restore.append((str(link), target))
                link.unlink()
                link.mkdir()

        cmd: list[str] = [
            "bwrap",
            "--bind",
            "/",
            "/",
            "--tmpfs",
            str(repo_root),
            "--bind",
            str(workspace),
            str(workspace),
            "--ro-bind",
            str(benchmark_dir),
            str(benchmark_dir),
            "--tmpfs",
            str(benchmark_dir / "evaluation"),  # judge rubric + scorers
            "--tmpfs",
            "/tmp",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]
        # Legacy `LHCRecastBench/papers/` tree (now under tasks/shared/) was
        # tmpfs'd here historically. Skip the line if the path doesn't exist
        # — bwrap fails to mount over a non-existent directory inside the
        # ro-bound benchmark dir.
        if (benchmark_dir / "papers").is_dir():
            cmd.extend(["--tmpfs", str(benchmark_dir / "papers")])

        # Shadow the new reference pool: tasks/shared/<paper>/reference/
        # carries the ground-truth values, and tasks/<task-id>/template/
        # carries null-filled skeletons that were already copied into
        # workspace/results/ — hide both from the agent so it can't peek.
        tasks_dir = bench_paths.tasks_root(repo_root)
        if tasks_dir.is_dir():
            shared_root = bench_paths.shared_root(repo_root)
            if shared_root.is_dir():
                for paper_dir in shared_root.iterdir():
                    ref = paper_dir / "reference"
                    if ref.is_dir():
                        cmd.extend(["--tmpfs", str(ref)])
            for task_dir in tasks_dir.iterdir():
                if task_dir.name == "shared" or not task_dir.is_dir():
                    continue
                tmpl = task_dir / "template"
                if tmpl.is_dir():
                    cmd.extend(["--tmpfs", str(tmpl)])

        # Some simulators (MG5, Delphes) insist on writing inside their own
        # install tree during init — e.g. MG5 copies Template/LO/Source/make_opts
        # into place before any user command.
        #
        # Ideal fix is an overlay ("writes go to tmpfs, real install untouched"),
        # but overlayfs refuses to mount with lustre as the lowerdir on NERSC
        # (EINVAL on userxattr). Fall back to a plain rw bind. The writes MG5
        # does are deterministic (overwrite with the same content every run)
        # and bin/simulate redirects MG5's `output` directive into the agent
        # workspace, so the install dir sees only template/config refreshes.
        sim_dir = bench_paths.sim_dir(repo_root)
        disabled_delphes = _disabled_delphes_dir(workspace)
        for install in ("MG5_aMC_v3_7_0", "delphes"):
            install_path = sim_dir / install
            if install_path.is_dir():
                if install == "delphes" and disabled_delphes is not None:
                    cmd.extend(["--ro-bind", str(disabled_delphes), str(install_path)])
                else:
                    cmd.extend(["--bind", str(install_path), str(install_path)])
        for host_path, mount_path in extra_mounts:
            cmd.extend(["--ro-bind", host_path, mount_path])
        if disabled_delphes is not None:
            cmd.extend(
                [
                    "--ro-bind",
                    str(disabled_delphes),
                    str(workspace / "tools" / "sim" / "delphes"),
                ]
            )
        for path in extra_ro_binds or []:
            if Path(path).exists():
                cmd.extend(["--ro-bind", str(path), str(path)])
        cmd.extend(
            [
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--chdir",
                str(workspace),
                "--die-with-parent",
                "--",
            ]
        )
        cmd.extend(list(inner_cmd))

        def cleanup() -> None:
            for link_path, target in symlink_restore:
                p = Path(link_path)
                if p.is_dir():
                    shutil.rmtree(p)
                if not p.exists():
                    p.symlink_to(target)

        return cmd, cleanup


# ── Apptainer / Singularity (portable OCI runners, HPC-friendly) ───────────


class ApptainerSandbox(Sandbox):
    """OCI-image sandbox via `apptainer exec`.

    Pairs with docker/Dockerfile (the canonical benchmark image). Runs on
    Linux laptops, NERSC / generic HPC, and anywhere else apptainer-exec
    is installed — same image everywhere.

    Image selection:
      $LHC_BENCH_IMAGE   — path to a .sif file or a registry ref
                            (default: "ghcr.io/dfaroughy/lhc-bench:latest").

    Binds:
      - host repo_root/LHCRecastBench  → same path inside container
        (benchmark content stays editable; tasks/, evaluation/, tools/CLI)
      - workspace                      → same path inside container (rw)
      - ~/.claude, ~/.codex, ~/.gemini → /root/.claude etc.  (OAuth)
      - /cvmfs                         → /cvmfs (ro) if present
      - baked /opt/sim/<tool>          → host .../LHCRecastBench/tools/sim/<tool>
        — the image's baked sim stack overrides whatever the host has.
      - any paths in extra_ro_binds

    Leakage hardening is simpler than bwrap's: by bind-mounting only what
    the agent should see, the rest of the container FS starts from the
    image (no host leak) and the rest of the host FS is not visible.
    """

    name = "apptainer"

    def available(self) -> bool:
        return shutil.which("apptainer") is not None

    def _engine(self) -> str:
        return "apptainer"

    def wrap(
        self,
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=None,
        container_env=None,
        secret_env_names=(),
        home_dir_name=".agent_home",
        home_files=(),
        home_credential_files=(),
    ):
        from agent_runtime import paths as bench_paths

        image = os.environ.get("LHC_BENCH_IMAGE") or _DEFAULT_IMAGE

        cmd: list[str] = [
            self._engine(),
            "exec",
            "--cleanenv",  # don't leak host env; we set what we need below
            "--pwd",
            str(workspace),
            # Bind host paths to the same path inside the container so the
            # agent's absolute-path references (e.g. from run_info.json)
            # don't need rewriting.
            "--bind",
            f"{workspace}:{workspace}",
            # Selective benchmark binds. We do NOT bind the whole
            # LHCRecastBench/ tree — that would expose the reference pool
            # under tasks/shared/*/reference/ and scoring code under
            # evaluation/. Agent sees only tools/, bin/, and this run's
            # paper PDF (copied into workspace/papers before wrapping).
            "--bind",
            f"{bench_paths.tools_dir(repo_root)}:{bench_paths.tools_dir(repo_root)}:ro",
            "--bind",
            f"{bench_paths.bin_dir(repo_root)}:{bench_paths.bin_dir(repo_root)}:ro",
        ]
        disabled_delphes = _disabled_delphes_dir(workspace)
        if disabled_delphes is not None:
            cmd.extend(
                [
                    "--bind",
                    f"{disabled_delphes}:/opt/sim/delphes:ro",
                    "--bind",
                    f"{disabled_delphes}:{bench_paths.sim_dir(repo_root) / 'delphes'}:ro",
                ]
            )
        # Per-run fake $HOME under <recast>/<runner-home>/. The CLIs
        # see only runner-selected credential / config files; everything
        # they write (session logs, conversation DBs, todos, caches)
        # lands here. Persisted with the run so post-run hooks can mine
        # forge's `.forge.db` etc. for usage stats. Host's real $HOME is
        # never touched, and concurrent runs each have their own dir.
        fake_home = _prepare_isolated_home(
            Path.home(),
            _fake_home_target(workspace, home_dir_name),
            home_files,
        )
        cmd.extend(
            [
                "--bind",
                f"{fake_home}:{fake_home}",
                "--env",
                f"HOME={fake_home}",
            ]
        )

        # CVMFS (CMSSW, ATLAS sw, ...) — ro when available on the host.
        if Path("/cvmfs").is_dir():
            cmd.extend(["--bind", "/cvmfs:/cvmfs:ro"])

        for path in extra_ro_binds or []:
            if Path(path).exists():
                cmd.extend(["--bind", f"{path}:{path}:ro"])
        rewritten_cmd, cli_ro_binds = _prepare_runner_cli(inner_cmd)
        for path in cli_ro_binds:
            cmd.extend(["--bind", f"{path}:{path}:ro"])

        # IS_SANDBOX=1 tells Claude Code we're in a sandboxed environment, so
        # `--dangerously-skip-permissions` is allowed even if the container
        # process runs as UID 0 (which happens on rootless podman with
        # subuid limits that prevent keep-id mapping).
        cmd.extend(["--env", "IS_SANDBOX=1"])

        # Sim-tool locations ($MG5_DIR etc.) are baked into the image's ENV
        # directives. Pass through only the narrow runner/API env allowlist.
        for var, val in _container_env_pairs(container_env, secret_env_names):
            cmd.extend(["--env", f"{var}={val}"])
        cmd.append(image)
        cmd.extend(rewritten_cmd)

        def cleanup() -> None:
            _sync_credentials_back_to_host(fake_home, Path.home(), home_credential_files)
            # NOTE: fake_home is intentionally preserved under the run
            # directory so post-run hooks (e.g. forge) can
            # mine the conversation DB, and so debug postmortem on a
            # failed run still has the agent's state to inspect.

        return cmd, cleanup


class SingularitySandbox(ApptainerSandbox):
    """OCI-image sandbox via `singularity exec`.

    Shares the Apptainer bind/env contract, but is exposed as a distinct
    backend so `--sandbox apptainer` and `--sandbox singularity` are explicit.
    """

    name = "singularity"

    def available(self) -> bool:
        return shutil.which("singularity") is not None

    def _engine(self) -> str:
        return "singularity"


# ── Helpers ─────────────────────────────────────────────────────────────────


_HOST_AGENT_CLIS = {"claude", "codex", "gemini", "aider", "forge"}
_IMAGE_CLIS = _HOST_AGENT_CLIS | {"python", "python3"}
_SYSTEM_BIN_DIRS = {Path("/bin"), Path("/usr/bin"), Path("/usr/local/bin")}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prepare_runner_cli(inner_cmd: Sequence[str]) -> tuple[list[str], list[Path]]:
    """Rewrite the CLI command and return any narrow host CLI dirs to bind.

    Container images may bake agent CLIs. When the selected runner binary is a
    host-installed absolute path under the user's home (common for VS Code,
    npm, pipx, and standalone CLI installs), bind only the resolved executable's
    parent directory and invoke that resolved path inside the container. This
    replaces the old whole-`~/.local` bind while still supporting host-managed
    vendor CLIs. System paths fall back to the image binary by basename.
    """
    if not inner_cmd:
        return list(inner_cmd), []
    head = Path(inner_cmd[0])
    name = head.name
    if not head.is_absolute() or name not in _IMAGE_CLIS:
        return list(inner_cmd), []
    if name not in _HOST_AGENT_CLIS:
        return [name, *inner_cmd[1:]], []
    if not head.exists():
        return [name, *inner_cmd[1:]], []
    resolved = head.resolve()
    home = Path.home().resolve()
    if _is_relative_to(resolved, home) and resolved.parent not in _SYSTEM_BIN_DIRS:
        return [str(resolved), *inner_cmd[1:]], [resolved.parent]
    return [name, *inner_cmd[1:]], []


# ── Podman / Podman-HPC (NERSC, rootless containers) ───────────────────────


class PodmanSandbox(Sandbox):
    """OCI-image sandbox via `podman` or `podman-hpc` (NERSC wrapper).

    Same image, same bind contract as ApptainerSandbox — just a different
    runtime. Use this when apptainer isn't installed but podman is (NERSC
    Perlmutter: podman-hpc only).

    Image ref in $LHC_BENCH_IMAGE (default: ghcr.io/dfaroughy/lhc-bench:latest).
    """

    name = "podman"

    def available(self) -> bool:
        return shutil.which("podman-hpc") is not None or shutil.which("podman") is not None

    def _engine(self) -> str:
        return "podman-hpc" if shutil.which("podman-hpc") else "podman"

    def wrap(
        self,
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=None,
        container_env=None,
        secret_env_names=(),
        home_dir_name=".agent_home",
        home_files=(),
        home_credential_files=(),
    ):
        from agent_runtime import paths as bench_paths

        image = os.environ.get("LHC_BENCH_IMAGE") or _DEFAULT_IMAGE

        # Container runs as root (UID 0) by default under rootless podman —
        # that's safe because rootless podman maps container-root to the
        # host user, so "root" inside the container is us outside. Don't
        # try `--userns=keep-id`: on NERSC compute nodes the subuid range
        # doesn't cover the host UID, so the userns map fails at run time.
        # Claude Code's "no --dangerously-skip-permissions as root" check
        # is bypassed below via IS_SANDBOX=1.
        fake_home = _prepare_isolated_home(
            Path.home(),
            _fake_home_target(workspace, home_dir_name),
            home_files,
        )
        cmd: list[str] = [
            self._engine(),
            "run",
            "--rm",
            # -i keeps stdin attached. Required for runners that pipe their
            # prompt through stdin (e.g. CodexRunner uses `codex exec ... -`);
            # without it `podman run` detaches stdin and the inner CLI sees
            # zero bytes ("No prompt provided via stdin.").
            "-i",
            "--workdir",
            str(workspace),
            "-e",
            f"HOME={fake_home}",
            # Per-container ephemeral $HOME — see _prepare_isolated_home.
            "-v",
            f"{fake_home}:{fake_home}",
            # Workspace: the only rw path under the repo — agent's scratch.
            "-v",
            f"{workspace}:{workspace}",
            # Selective benchmark binds, ro. We do NOT bind the whole
            # LHCRecastBench/ tree: that would expose the reference pool
            # under tasks/shared/*/reference/ and the scoring code under
            # evaluation/. Agent sees only tools/ + bin/ + this run's
            # paper PDF (copied into workspace/papers before wrapping).
            "-v",
            f"{bench_paths.tools_dir(repo_root)}:{bench_paths.tools_dir(repo_root)}:ro",
            "-v",
            f"{bench_paths.bin_dir(repo_root)}:{bench_paths.bin_dir(repo_root)}:ro",
        ]
        disabled_delphes = _disabled_delphes_dir(workspace)
        if disabled_delphes is not None:
            cmd.extend(
                [
                    "-v",
                    f"{disabled_delphes}:/opt/sim/delphes:ro",
                    "-v",
                    f"{disabled_delphes}:{bench_paths.sim_dir(repo_root) / 'delphes'}:ro",
                ]
            )

        # OAuth creds + minimal CLI config live inside the fake $HOME
        # bound above; nothing else is shared with the host's real $HOME.

        if Path("/cvmfs").is_dir():
            cmd.extend(["-v", "/cvmfs:/cvmfs:ro"])

        for path in extra_ro_binds or []:
            if Path(path).exists():
                cmd.extend(["-v", f"{path}:{path}:ro"])

        rewritten_cmd, cli_ro_binds = _prepare_runner_cli(inner_cmd)
        for path in cli_ro_binds:
            cmd.extend(["-v", f"{path}:{path}:ro"])

        # ── PATH inside the container ───────────────────────────────────────
        # Use only the image PATH. Host-installed agent CLIs are either baked
        # into the image or mounted narrowly by _prepare_runner_cli().
        # This must stay in sync with docker/Dockerfile's ENV PATH.
        container_path = ":".join(
            [
                "/opt/sim/MG5_aMC_v3_7_0/bin",
                "/opt/sim/delphes",
                "/opt/node-global/bin",
                "/opt/conda/envs/lhc_analysis/bin",
                "/usr/local/sbin",
                "/usr/local/bin",
                "/usr/sbin",
                "/usr/bin",
                "/sbin",
                "/bin",
            ]
        )
        cmd.extend(["-e", f"PATH={container_path}"])

        # ── PYTHONPATH inside the container ─────────────────────────────────
        # The harness Python (`agent_runtime/`, `agents/<active>/runtime/`)
        # is bind-mounted from the host repo via extra_ro_binds; this env
        # var lets `from agent_runtime.* import ...` resolve there. Replaces
        # the old image-baked `ENV PYTHONPATH=/app`.
        cmd.extend(["-e", f"PYTHONPATH={repo_root}"])

        # IS_SANDBOX=1 tells Claude Code we're in a sandboxed environment, so
        # `--dangerously-skip-permissions` is allowed even if the container
        # process runs as UID 0 (unavoidable under rootless podman on NERSC
        # compute nodes because keep-id fails on subuid range limits).
        cmd.extend(["-e", "IS_SANDBOX=1"])

        # Sim-tool locations ($MG5_DIR etc.) are baked into the image's ENV.
        # Pass through only the narrow runner/API env allowlist.
        for var, val in _container_env_pairs(container_env, secret_env_names):
            cmd.extend(["-e", f"{var}={val}"])

        cmd.append(image)
        cmd.extend(rewritten_cmd)

        def cleanup() -> None:
            _sync_credentials_back_to_host(fake_home, Path.home(), home_credential_files)
            # NOTE: fake_home is intentionally preserved under the run
            # directory so post-run hooks (e.g. forge) can
            # mine the conversation DB, and so debug postmortem on a
            # failed run still has the agent's state to inspect.

        return cmd, cleanup


# ── No-op passthrough (macOS / CI / free-range debugging) ──────────────────


class NoneSandbox(Sandbox):
    """Run the agent with no filesystem isolation.

    Use on platforms without bwrap (macOS, restricted Linux distros) or for
    free-range debugging. The agent can read and write anything the calling
    user can. Do NOT use for benchmark runs — reference answers in
    benchmark reference answers and evaluator code are visible and the agent can cheat.
    """

    name = "none"

    def available(self) -> bool:
        return True

    def wrap(
        self,
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds=None,
        container_env=None,
        secret_env_names=(),
        home_dir_name=".agent_home",
        home_files=(),
        home_credential_files=(),
    ):
        # No filesystem wrapper is inserted here. `container_env` is already
        # merged into the subprocess env by the runner layer before Popen.
        return list(inner_cmd), (lambda: None)


# ── Registry + selection ────────────────────────────────────────────────────

SANDBOXES: dict[str, type[Sandbox]] = {
    "bwrap": BwrapSandbox,
    "apptainer": ApptainerSandbox,
    "singularity": SingularitySandbox,
    "podman": PodmanSandbox,
    "none": NoneSandbox,
}


def _auto_select() -> Sandbox:
    """Pick the best available container backend.

    Order: podman → apptainer → singularity. All run the canonical lhc-bench image
    with proper $HOME isolation via the fake-home dir built in
    `_prepare_isolated_home`. Bwrap is intentionally excluded: it
    doesn't use the image at all (runs analysis on the host conda env),
    which defeats the point of the canonical container. To force bwrap,
    pass `--sandbox bwrap` explicitly.
    """
    for cls in (PodmanSandbox, ApptainerSandbox, SingularitySandbox):
        inst = cls()
        if inst.available():
            return inst
    sys.stderr.write(
        "sandbox: no container backend available (podman, apptainer, singularity missing); "
        "falling back to 'none' (NO ISOLATION). "
        "Install one of them, or pass --sandbox bwrap / --sandbox none explicitly.\n"
    )
    return NoneSandbox()


def get_sandbox(name: str | None = None) -> Sandbox:
    """Return a Sandbox instance.

    Resolution order:
      1. explicit `name` argument
      2. LHC_RECAST_SANDBOX environment variable
      3. auto-detect: podman → apptainer → singularity → (warn) none

    "auto" is a valid value everywhere and means the auto-detect path.
    """
    name = name or os.environ.get("LHC_RECAST_SANDBOX") or "auto"
    name = name.strip().lower()
    if name == "auto":
        return _auto_select()
    cls = SANDBOXES.get(name)
    if cls is None:
        raise ValueError(f"Unknown sandbox {name!r}. Available: {sorted(SANDBOXES) + ['auto']}")
    inst = cls()
    if not inst.available():
        raise RuntimeError(
            f"Sandbox {name!r} is not available on this host (required tools missing)."
        )
    return inst


# ── Back-compat shim for existing callers ───────────────────────────────────


def sandbox_command(
    workspace: Path,
    repo_root: Path,
    inner_cmd: Sequence[str],
    extra_ro_binds: Iterable[Path] | None = None,
    container_env: dict[str, str] | None = None,
    sandbox: str | None = None,
    secret_env_names: Iterable[str] = (),
    home_dir_name: str = ".agent_home",
    home_files: Iterable[str] = (),
    home_credential_files: Iterable[str] = (),
) -> tuple[list[str], Callable[[], None]]:
    """Thin wrapper: pick a backend and delegate to its .wrap().

    `sandbox` may be "bwrap" | "none" | "auto" | None. See get_sandbox() for
    resolution rules.
    """
    _materialize_papers_dir(workspace)
    return get_sandbox(sandbox).wrap(
        workspace,
        repo_root,
        inner_cmd,
        extra_ro_binds,
        container_env,
        secret_env_names,
        home_dir_name,
        home_files,
        home_credential_files,
    )
