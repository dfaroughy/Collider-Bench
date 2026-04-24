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
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable, Sequence


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
            str(benchmark_dir / "papers"),  # legacy reference tree (being phased out)
            "--tmpfs",
            str(benchmark_dir / "evaluation"),  # judge rubric + scorers
            "--tmpfs",
            "/tmp",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]

        # Shadow the new reference pool: tasks/shared/<paper>/histograms/
        # carries the ground-truth histogram values, and tasks/<task-id>/template/
        # carries null-filled skeletons that were already copied into
        # workspace/results/ — hide both from the agent so it can't peek.
        tasks_dir = bench_paths.tasks_root(repo_root)
        if tasks_dir.is_dir():
            shared_root = bench_paths.shared_root(repo_root)
            if shared_root.is_dir():
                for paper_dir in shared_root.iterdir():
                    hist = paper_dir / "histograms"
                    if hist.is_dir():
                        cmd.extend(["--tmpfs", str(hist)])
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

        image = os.environ.get("LHC_RECAST_IMAGE", "localhost/lhc-recast-runtime:latest")

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
            # under tasks/shared/*/histograms/ and scoring code under
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

        # OAuth credential caches — mounted rw so token refreshes persist
        # back to the host. Apptainer rootless already maps the host UID
        # through, so host $HOME paths stay valid inside the container.
        host_home = str(Path.home())
        for d in (".claude", ".codex", ".gemini"):
            host = Path.home() / d
            if host.is_dir():
                cmd.extend(["--bind", f"{host}:{host_home}/{d}"])
        claude_json = Path.home() / ".claude.json"
        if claude_json.is_file():
            cmd.extend(["--bind", f"{claude_json}:{host_home}/.claude.json"])

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

        return cmd, (lambda: None)


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

        image = os.environ.get("LHC_RECAST_IMAGE", "localhost/lhc-recast-runtime:latest")

        # Container runs as root (UID 0) by default under rootless podman —
        # that's safe because rootless podman maps container-root to the
        # host user, so "root" inside the container is us outside. Don't
        # try `--userns=keep-id`: on NERSC compute nodes the subuid range
        # doesn't cover the host UID, so the userns map fails at run time.
        # Claude Code's "no --dangerously-skip-permissions as root" check
        # is bypassed below via IS_SANDBOX=1.
        host_home = str(Path.home())
        cmd: list[str] = [
            self._engine(),
            "run",
            "--rm",
            "--workdir",
            str(workspace),
            "-e",
            f"HOME={host_home}",
            # Workspace: the only rw path under the repo — agent's scratch.
            "-v",
            f"{workspace}:{workspace}",
            # Selective benchmark binds, ro. We do NOT bind the whole
            # LHCRecastBench/ tree: that would expose the reference pool
            # under tasks/shared/*/histograms/ and the scoring code under
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

        # OAuth creds — rw so token refresh persists back to host.
        for d in (".claude", ".codex", ".gemini"):
            host = Path.home() / d
            if host.is_dir():
                cmd.extend(["-v", f"{host}:{host_home}/{d}"])
        claude_json = Path.home() / ".claude.json"
        if claude_json.is_file():
            cmd.extend(["-v", f"{claude_json}:{host_home}/.claude.json"])

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

        return cmd, (lambda: None)


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

    Order: bwrap → apptainer → podman → none.
    Users can force any via --sandbox <name> or LHC_RECAST_SANDBOX=<name>.
    """
    for cls in (BwrapSandbox, ApptainerSandbox, PodmanSandbox):
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
