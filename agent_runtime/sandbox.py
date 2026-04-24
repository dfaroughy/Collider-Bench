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
        benchmark_dir = repo_root / "LHCRecastBench"

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
        tasks_dir = benchmark_dir / "tasks"
        if tasks_dir.is_dir():
            shared_root = tasks_dir / "shared"
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
        sim_dir = benchmark_dir / "tools" / "sim"
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
    "none": NoneSandbox,
    # Add future backends here: "podman": PodmanSandbox, "docker": DockerSandbox, ...
}


def _auto_select() -> Sandbox:
    """Pick the best available backend, prefer isolating ones."""
    for cls in (BwrapSandbox,):
        inst = cls()
        if inst.available():
            return inst
    # Nothing isolating is installed — fall back to passthrough with a warning.
    sys.stderr.write(
        "sandbox: no isolating backend available (bwrap not found); "
        "falling back to 'none' (NO ISOLATION). "
        "Install bubblewrap or set LHC_RECAST_SANDBOX=none to silence this.\n"
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
