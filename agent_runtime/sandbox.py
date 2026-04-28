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
    3. auto-detect: bwrap if installed, else none (with a warning)

Available backends:
    bwrap   — bubblewrap user-space namespaces (Linux only; recommended)
    none    — passthrough, no isolation (macOS / CI / debugging)

Adding a new backend (e.g. Podman, Docker, Shifter): subclass Sandbox, register
it in SANDBOXES at the bottom of this file. See agent_runtime/SANDBOX.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable, Sequence


# ── Image selection ────────────────────────────────────────────────────────


def _default_image() -> str:
    """Image used when $LHC_RECAST_IMAGE is unset.

    Prefers the private dev overlay (`localhost/lhc-recast-dev:latest`)
    when it's present in the local image store — that's the image with
    vendor agent CLIs (claude / codex / gemini) baked on top of the
    public runtime. Falls back to `localhost/lhc-recast-runtime:latest`
    (vendor-neutral, what ships with the public release) otherwise.

    Probed once at module import; result is cached for the process.
    `podman-hpc image exists` is unreliable on NERSC's wrapper (returns
    rc=1 even for present images), so we list and substring-match.
    """
    dev = "localhost/lhc-recast-dev:latest"
    runtime = "localhost/lhc-recast-runtime:latest"
    for engine in ("podman-hpc", "podman"):
        if not shutil.which(engine):
            continue
        try:
            out = subprocess.run(
                [engine, "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if out.returncode == 0 and any(line.strip() == dev for line in out.stdout.splitlines()):
            return dev
        break  # engine present, dev image absent — fall through to runtime
    return runtime


_DEFAULT_IMAGE = _default_image()  # cache once; nothing changes mid-process


# Files copied into a per-container ephemeral $HOME so each agent run gets
# isolated CLI state. Anything not on this list (history.jsonl, projects/,
# sessions/, todos/, caches, telemetry, MCP needs-auth caches…) is excluded
# so prior-run artefacts cannot leak into a benchmark run, and concurrent
# containers don't race on shared state.
_HOME_WHITELIST: tuple[str, ...] = (
    # Claude Code
    ".claude.json",
    ".claude/.credentials.json",
    ".claude/settings.json",
    # Codex CLI
    ".codex/auth.json",
    ".codex/config.toml",
    ".codex/installation_id",
    # Gemini CLI
    ".gemini/oauth_creds.json",
    ".gemini/google_accounts.json",
    ".gemini/installation_id",
    ".gemini/settings.json",
    ".gemini/trustedFolders.json",
)


# Subset of _HOME_WHITELIST that holds OAuth tokens. Containers may rotate
# (refresh) these during a run; if they do, the new tokens are written into
# the per-container staging copy. We sync them back to the host on cleanup
# so that the next sequential run picks up the fresh tokens — otherwise the
# OAuth refresh-token rotation kills any subsequent run with a 401.
# Within a `%1`-throttled SLURM array this is race-free; across-lane
# parallelism is safe because each provider's credentials live in
# independent files.
_HOME_CREDENTIAL_FILES: tuple[str, ...] = (
    ".claude.json",
    ".claude/.credentials.json",
    ".codex/auth.json",
    ".gemini/oauth_creds.json",
    ".gemini/google_accounts.json",
)


def _prepare_isolated_home(host_home: Path) -> Path:
    """Build a per-container staging $HOME containing only the credential
    and config files in `_HOME_WHITELIST`.

    The container is then launched with `HOME=<staging>` and the staging
    dir bind-mounted into the container at the same path; the CLI sees a
    pristine home with just the auth it needs and writes any session
    state into the staging dir, which is destroyed at cleanup. The host's
    real `~/.claude/`, `~/.codex/`, `~/.gemini/` are never touched
    *except* for the credential files in `_HOME_CREDENTIAL_FILES`, which
    `_sync_credentials_back_to_host` propagates back if the container
    refreshed them mid-run.
    """
    staging = Path(tempfile.mkdtemp(prefix="lhc-recast-home-"))
    for rel in _HOME_WHITELIST:
        src = host_home / rel
        if not src.is_file():
            continue
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return staging


def _sync_credentials_back_to_host(staging: Path, host_home: Path) -> None:
    """Copy refreshed OAuth credential files from `staging` back to host.

    OAuth refresh-token rotation: when a CLI in the container refreshes
    its access token, the auth server invalidates the old refresh token
    on its side. The container's staging copy now holds the new tokens;
    the host's copy holds tokens that are dead in Anthropic/OpenAI/
    Google's eyes. Without this sync, the *next* run starts from a
    stale refresh token and immediately 401s on the first refresh.

    We compare mtimes — only copy back when the staging file is newer
    than the host file (i.e. the container actually wrote it). Best-
    effort; never raises so it can't break the cleanup path.
    """
    for rel in _HOME_CREDENTIAL_FILES:
        try:
            src = staging / rel
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
    ) -> tuple[list[str], Callable[[], None]]:
        """Return (command, cleanup_fn).

        The returned command, when executed, runs `inner_cmd` with the agent
        isolated per this backend's policy:
          - <workspace> is the only read-write path under the repo
          - LHCRecastBench/ is read-only, but LHCRecastBench/papers and LHCRecastBench/evaluation
            must be hidden (they hold reference answers / judge rubric)
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

    def wrap(self, workspace, repo_root, inner_cmd, extra_ro_binds=None):
        from agent_runtime import paths as bench_paths

        benchmark_dir = bench_paths.benchmark_dir(repo_root)

        # bwrap can't mount over a symlink; swap any top-level workspace
        # symlinks (bin/papers/tools) for empty dirs, then bind the resolved
        # targets back in. Restore the symlinks after the agent exits.
        extra_mounts: list[tuple[str, str]] = []
        symlink_restore: list[tuple[str, str]] = []
        for name in ("bin", "papers", "tools"):
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
        for install in ("MG5_aMC_v3_7_0", "delphes"):
            install_path = sim_dir / install
            if install_path.is_dir():
                cmd.extend(["--bind", str(install_path), str(install_path)])
        for host_path, mount_path in extra_mounts:
            cmd.extend(["--ro-bind", host_path, mount_path])
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


# ── Apptainer / Singularity (portable OCI runner, HPC-friendly) ────────────


class ApptainerSandbox(Sandbox):
    """OCI-image sandbox via `apptainer exec`.

    Pairs with docker/Dockerfile.runtime (or docker/Dockerfile.bench for a
    vendor-neutral setup). Runs on Linux laptops, NERSC / generic HPC, and
    anywhere else apptainer-exec is installed — same image everywhere.

    Image selection:
      $LHC_RECAST_IMAGE   — path to a .sif file or a registry ref
                            (default: "lhc-recast-runtime:latest").

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
        return shutil.which("apptainer") is not None or shutil.which("singularity") is not None

    def _engine(self) -> str:
        return "apptainer" if shutil.which("apptainer") else "singularity"

    def wrap(self, workspace, repo_root, inner_cmd, extra_ro_binds=None):
        from agent_runtime import paths as bench_paths

        image = os.environ.get("LHC_RECAST_IMAGE") or _DEFAULT_IMAGE

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
            # paper PDF (via the workspace/papers symlink target).
            "--bind",
            f"{bench_paths.tools_dir(repo_root)}:{bench_paths.tools_dir(repo_root)}:ro",
            "--bind",
            f"{bench_paths.bin_dir(repo_root)}:{bench_paths.bin_dir(repo_root)}:ro",
        ]
        papers_link = workspace / "papers"
        if papers_link.is_symlink():
            target = papers_link.resolve()
            if target.is_dir():
                cmd.extend(["--bind", f"{target}:{target}:ro"])

        # Per-container ephemeral $HOME. The CLIs see only the whitelisted
        # credential / config files; everything they write (session logs,
        # transcripts, todos, caches) goes to this throwaway dir and is
        # deleted on cleanup. Host's real $HOME is never touched, and
        # concurrent containers don't share state.
        staging_home = _prepare_isolated_home(Path.home())
        cmd.extend(
            [
                "--bind",
                f"{staging_home}:{staging_home}",
                "--env",
                f"HOME={staging_home}",
            ]
        )

        # CVMFS (CMSSW, ATLAS sw, ...) — ro when available on the host.
        if Path("/cvmfs").is_dir():
            cmd.extend(["--bind", "/cvmfs:/cvmfs:ro"])

        for path in extra_ro_binds or []:
            if Path(path).exists():
                cmd.extend(["--bind", f"{path}:{path}:ro"])

        # IS_SANDBOX=1 tells Claude Code we're in a sandboxed environment, so
        # `--dangerously-skip-permissions` is allowed even if the container
        # process runs as UID 0 (which happens on rootless podman with
        # subuid limits that prevent keep-id mapping).
        cmd.extend(["--env", "IS_SANDBOX=1"])

        # Sim-tool locations ($MG5_DIR etc.) are baked into the image's ENV
        # directives — bin/simulate reads them to find /opt/sim/<tool>
        # inside the container. We only propagate the API/OAuth env vars
        # the agent CLIs look for.
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "CODEX_HOME",
            "CLAUDE_BIN",
            "CODEX_BIN",
            "GEMINI_BIN",
        ):
            val = os.environ.get(var)
            if val:
                cmd.extend(["--env", f"{var}={val}"])

        cmd.append(image)
        cmd.extend(_rewrite_host_cli_to_container(inner_cmd))

        def cleanup() -> None:
            _sync_credentials_back_to_host(staging_home, Path.home())
            shutil.rmtree(staging_home, ignore_errors=True)

        return cmd, cleanup


# ── Helpers ─────────────────────────────────────────────────────────────────


_CONTAINER_CLIS = {"claude", "codex", "gemini", "aider", "python", "python3"}


def _rewrite_host_cli_to_container(inner_cmd: Sequence[str]) -> list[str]:
    """If inner_cmd[0] is an absolute host path to a known agent CLI, replace
    with the unqualified basename so the container's PATH resolves it against
    the in-image install (e.g. /opt/node-global/bin/claude)."""
    if not inner_cmd:
        return list(inner_cmd)
    head = inner_cmd[0]
    if Path(head).is_absolute() and Path(head).name in _CONTAINER_CLIS:
        return [Path(head).name, *inner_cmd[1:]]
    return list(inner_cmd)


# ── Podman / Podman-HPC (NERSC, rootless containers) ───────────────────────


class PodmanSandbox(Sandbox):
    """OCI-image sandbox via `podman` or `podman-hpc` (NERSC wrapper).

    Same image, same bind contract as ApptainerSandbox — just a different
    runtime. Use this when apptainer isn't installed but podman is (NERSC
    Perlmutter: podman-hpc only).

    Image ref in $LHC_RECAST_IMAGE (default: lhc-recast-runtime:latest).
    """

    name = "podman"

    def available(self) -> bool:
        return shutil.which("podman-hpc") is not None or shutil.which("podman") is not None

    def _engine(self) -> str:
        return "podman-hpc" if shutil.which("podman-hpc") else "podman"

    def wrap(self, workspace, repo_root, inner_cmd, extra_ro_binds=None):
        from agent_runtime import paths as bench_paths

        image = os.environ.get("LHC_RECAST_IMAGE") or _DEFAULT_IMAGE

        # Container runs as root (UID 0) by default under rootless podman —
        # that's safe because rootless podman maps container-root to the
        # host user, so "root" inside the container is us outside. Don't
        # try `--userns=keep-id`: on NERSC compute nodes the subuid range
        # doesn't cover the host UID, so the userns map fails at run time.
        # Claude Code's "no --dangerously-skip-permissions as root" check
        # is bypassed below via IS_SANDBOX=1.
        staging_home = _prepare_isolated_home(Path.home())
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
            f"HOME={staging_home}",
            # Per-container ephemeral $HOME — see _prepare_isolated_home.
            "-v",
            f"{staging_home}:{staging_home}",
            # Workspace: the only rw path under the repo — agent's scratch.
            "-v",
            f"{workspace}:{workspace}",
            # Selective benchmark binds, ro. We do NOT bind the whole
            # LHCRecastBench/ tree: that would expose the reference pool
            # under tasks/shared/*/reference/ and the scoring code under
            # evaluation/. Agent sees only tools/ + bin/ + this run's
            # paper PDF (via the workspace/papers symlink target).
            "-v",
            f"{bench_paths.tools_dir(repo_root)}:{bench_paths.tools_dir(repo_root)}:ro",
            "-v",
            f"{bench_paths.bin_dir(repo_root)}:{bench_paths.bin_dir(repo_root)}:ro",
        ]

        # Resolve workspace/papers -> tasks/shared/<paper>/paper and bind
        # only that directory, so the agent's paper PDF symlink works
        # without exposing the rest of tasks/shared/<paper>/.
        papers_link = workspace / "papers"
        if papers_link.is_symlink():
            target = papers_link.resolve()
            if target.is_dir():
                cmd.extend(["-v", f"{target}:{target}:ro"])

        # OAuth creds + minimal CLI config live inside the staging $HOME
        # bound above; nothing else is shared with the host's real $HOME.

        if Path("/cvmfs").is_dir():
            cmd.extend(["-v", "/cvmfs:/cvmfs:ro"])

        for path in extra_ro_binds or []:
            if Path(path).exists():
                cmd.extend(["-v", f"{path}:{path}:ro"])

        # IS_SANDBOX=1 tells Claude Code we're in a sandboxed environment, so
        # `--dangerously-skip-permissions` is allowed even if the container
        # process runs as UID 0 (unavoidable under rootless podman on NERSC
        # compute nodes because keep-id fails on subuid range limits).
        cmd.extend(["-e", "IS_SANDBOX=1"])

        # Sim-tool locations ($MG5_DIR etc.) are baked into the image's ENV —
        # bin/simulate reads them to find /opt/sim/<tool> inside the container.
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "CODEX_HOME",
            "CLAUDE_BIN",
            "CODEX_BIN",
            "GEMINI_BIN",
        ):
            val = os.environ.get(var)
            if val:
                cmd.extend(["-e", f"{var}={val}"])

        cmd.append(image)
        cmd.extend(_rewrite_host_cli_to_container(inner_cmd))

        def cleanup() -> None:
            _sync_credentials_back_to_host(staging_home, Path.home())
            shutil.rmtree(staging_home, ignore_errors=True)

        return cmd, cleanup


# ── No-op passthrough (macOS / CI / free-range debugging) ──────────────────


class NoneSandbox(Sandbox):
    """Run the agent with no filesystem isolation.

    Use on platforms without bwrap (macOS, restricted Linux distros) or for
    free-range debugging. The agent can read and write anything the calling
    user can. Do NOT use for benchmark runs — reference answers in
    LHCRecastBench/papers/<arxiv>/tasks/*/reference/ are visible and the agent can cheat.
    """

    name = "none"

    def available(self) -> bool:
        return True

    def wrap(self, workspace, repo_root, inner_cmd, extra_ro_binds=None):
        return list(inner_cmd), (lambda: None)


# ── Registry + selection ────────────────────────────────────────────────────

SANDBOXES: dict[str, type[Sandbox]] = {
    "bwrap": BwrapSandbox,
    "apptainer": ApptainerSandbox,
    "podman": PodmanSandbox,
    "none": NoneSandbox,
}


def _auto_select() -> Sandbox:
    """Pick the best available isolating backend.

    Order: apptainer → podman → bwrap → none. Container backends are
    preferred over bwrap because (a) they isolate $HOME via the staging
    dir built in _prepare_isolated_home, which bwrap does not, and (b)
    on HPC sites with both bwrap and a container runtime installed
    (Perlmutter), the container is the intended path.
    Users can force any via --sandbox <name> or LHC_RECAST_SANDBOX=<name>.
    """
    for cls in (ApptainerSandbox, PodmanSandbox, BwrapSandbox):
        inst = cls()
        if inst.available():
            return inst
    sys.stderr.write(
        "sandbox: no isolating backend available (bwrap, apptainer, podman all missing); "
        "falling back to 'none' (NO ISOLATION). "
        "Install one of them, or set LHC_RECAST_SANDBOX=none to silence this.\n"
    )
    return NoneSandbox()


def get_sandbox(name: str | None = None) -> Sandbox:
    """Return a Sandbox instance.

    Resolution order:
      1. explicit `name` argument
      2. LHC_RECAST_SANDBOX environment variable
      3. auto-detect (prefer bwrap; fall back to none with warning)

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
            f"Sandbox {name!r} is not available on this host " f"(required tools missing)."
        )
    return inst


# ── Back-compat shim for existing callers ───────────────────────────────────


def sandbox_command(
    workspace: Path,
    repo_root: Path,
    inner_cmd: Sequence[str],
    extra_ro_binds: Iterable[Path] | None = None,
    sandbox: str | None = None,
) -> tuple[list[str], Callable[[], None]]:
    """Thin wrapper: pick a backend and delegate to its .wrap().

    `sandbox` may be "bwrap" | "none" | "auto" | None. See get_sandbox() for
    resolution rules.
    """
    return get_sandbox(sandbox).wrap(workspace, repo_root, inner_cmd, extra_ro_binds)
