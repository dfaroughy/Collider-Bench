"""Shared workspace setup for agent runs.

Builds a sandbox-ready workspace at <repo_root>/runs/<run_dir>/workspace/ with:
  - templates/           agent runtime workspace stubs (report.md, datasets.yaml …)
  - tools/               symlink → ColliderBench/tools/
  - bin/                 merged symlinks from ColliderBench/bin/ + agent_dir/runtime/bin/
  - agent_context/       all *.md files under agent_dir (outside runtime/) + TASK.md
  - results/             copy of tasks/<task_id>/template/  (null-filled yamls;
                         agent fills in place — no separate output dir)
  - papers/              symlink → tasks/shared/<paper>/paper/
                         (materialized into a real dir by sandbox_command)
  - object_efficiencies/ copy of tasks/shared/<paper>/object_efficiencies/
                         merged with tasks/<task_id>/artifacts/ if present
                         (task-specific files override shared ones)

The per-task task.toml is intentionally NOT exposed to the agent — it is
purely metadata for the harness.
"""

from __future__ import annotations

import re
import shutil
import ssl
import textwrap
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agent_runtime import paths
from agent_runtime.config import load_task_toml


_DELPHES_STUBS = (
    "Delphes",
    "DelphesEnv.sh",
    "DelphesHepMC",
    "DelphesHepMC2",
    "DelphesHepMC3",
    "DelphesLHEF",
    "DelphesPythia8",
    "DelphesROOT",
    "DelphesSTDHEP",
)


@dataclass(frozen=True)
class _Ablation:
    """How one disable-able sim tool is removed from a run.

    sim_subdir   directory under /opt/sim shadowed by an empty/stub tree
    env_var      env var the harness redirects (visible-stub tools only)
    bin_names    bin/ shim names the agent would otherwise see on $PATH
    cli_doc      dedicated agent-facing doc under tools/CLI/ to drop (blind)
    stub_message visible-stub mode: text the stub prints to stderr
    blind        True  -> erase every agent-visible trace, no stub left
                 False -> leave a visible "<tool> disabled" stub
    """

    name: str
    sim_subdir: str
    env_var: str
    bin_names: tuple[str, ...]
    cli_doc: str | None
    # tools/CLI implementation module to drop from the shadow. Every entry
    # point (bin/<tool>, even reached directly via the bound ColliderBench/
    # bin/) routes through `python -m ColliderBench.tools.CLI.<module>`,
    # which resolves to the shadow — so removing it both closes the
    # source/`.pyc` info leak and neuters the tool via the back door.
    cli_module: str | None
    stub_message: str
    blind: bool


# Keep the key set in sync with config._ALLOWED_DISABLED_TOOLS — a drift test
# in tests/test_workspace.py asserts the two stay equal.
_ABLATIONS: dict[str, _Ablation] = {
    "delphes": _Ablation(
        name="delphes",
        sim_subdir="delphes",
        env_var="DELPHES_DIR",
        bin_names=_DELPHES_STUBS,
        # No dedicated CLI doc/module — Delphes is documented as a section
        # inside SIMULATE.md (markdown scrubber drops it section-aware) and
        # has no bin/ shim of its own.
        cli_doc=None,
        cli_module=None,
        stub_message="Delphes tool is disabled for this benchmark task.",
        blind=True,
    ),
    "prospino": _Ablation(
        name="prospino",
        sim_subdir="prospino",
        env_var="PROSPINO_DIR",
        bin_names=("prospino",),
        cli_doc="PROSPINO.md",
        cli_module="prospino.py",
        stub_message="",
        blind=True,
    ),
}


def _disabled_tools(tool_policy: dict | None) -> set[str]:
    if not tool_policy:
        return set()
    disabled = tool_policy.get("disabled", [])
    return {str(tool).lower() for tool in disabled}


def _disabled_ablations(tool_policy: dict | None) -> list[_Ablation]:
    return [_ABLATIONS[n] for n in sorted(_disabled_tools(tool_policy)) if n in _ABLATIONS]


def _ablation_root(workspace: Path, ab: _Ablation) -> Path:
    """Where this ablation's artifacts live.

    Visible-stub tools (delphes) keep the legacy in-workspace location
    `disabled_tools/<sub>` — the stub tree IS the fake /opt/sim/<sub>.
    Blind tools put everything under `<run_dir>/.tool_policy/<sub>`, a
    sibling of `workspace/` that sandbox.py never bind-mounts into the
    container, so the agent cannot even see that an ablation happened.
    """
    if ab.blind:
        return workspace.parent / ".tool_policy" / ab.sim_subdir
    return workspace / "disabled_tools" / ab.sim_subdir


def _shared_docs_shadow(workspace: Path) -> Path:
    """One shadow of CLI/ + TOOLS.md + simulate, scrubbed of *all* blind
    tools, bound once per destination. `_docs` can't collide with a tool
    name (sim_subdir values are real tool dirs)."""
    return workspace.parent / ".tool_policy" / "_docs"


def _write_disabled_tool_stub(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "{message}" >&2
            return 1 2>/dev/null || exit 1
            """
        )
    )
    path.chmod(0o755)


def _strip_inline_lists(line: str, tok: str) -> str:
    """Drop the tool from inline comma / slash lists, keeping the prose valid.

    "(MG5, Pythia8, Delphes, Prospino)" -> "(MG5, Pythia8, Prospino)"
    "Simulation stack — MadGraph5 / Pythia8 / Delphes" -> "... / Pythia8"
    """
    for pat in (
        rf",\s*{tok}\b",
        rf"\b{tok}\s*,\s*",
        rf"\s*/\s*{tok}\b",
        rf"\b{tok}\s*/\s*",
    ):
        line = re.sub(pat, "", line, flags=re.IGNORECASE)
    return line


def _matching_close(lines: list[str], start: int, opener: str, closer: str) -> int:
    """Index of the `closer` that balances `lines[start]`'s `opener` (nested)."""
    depth = 0
    op = re.compile(rf"(^|;|\s|&&|\|\|)\s*{opener}\b")
    cl = re.compile(rf"^\s*{closer}\b")
    for i in range(start, len(lines)):
        if op.search(lines[i]):
            depth += 1
        if cl.match(lines[i].strip()):
            depth -= 1
            if depth == 0:
                return i
    return len(lines) - 1


# Shell control-flow openers → their terminators. A to-be-dropped opener
# takes its whole body with it, so the script stays valid bash.
_SH_BLOCKS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(^|;|\s)do\s*$"), "do", "done"),
    (re.compile(r"(^|;|\s)then\s*$"), "then", "fi"),
    (re.compile(r"^\s*case\b.*\bin\s*$"), "case", "esac"),
)


def _scrub_tool_lines(text: str, tool: str, *, kind: str = "markdown") -> str:
    """Erase every agent-visible mention of `tool`, keeping the file valid.

    `kind="shell"` keeps the output parseable bash: a to-be-dropped
    control-flow opener (`for … ; do`, `if … then`, `case … in`) takes its
    whole body + terminator with it instead of leaving an orphan `done`.
    `kind="markdown"` drops a whole section when its header still names the
    tool (no dangling `##`), drops offending table rows and fenced-code
    lines, and otherwise drops the line. Inline comma/slash lists are
    thinned first so sentences that merely name-drop the tool survive.
    Blind ablation favors zero-trace over preserving incidental prose.
    """
    src = text.splitlines()
    out: list[str] = []
    in_fence = False
    drop_section_level = 0  # >0 while skipping a markdown section
    i = 0
    while i < len(src):
        raw = src[i]
        stripped = _strip_inline_lists(raw, re.escape(tool))
        is_fence = kind == "markdown" and raw.lstrip().startswith("```")
        is_header = kind == "markdown" and not in_fence and re.match(r"^#{1,6}\s", raw) is not None

        # Inside a section being dropped: continue until a header of equal
        # or shallower depth (code fences can't end it; track them so a
        # `# comment` inside ```bash isn't mistaken for a header).
        if drop_section_level:
            if is_fence:
                in_fence = not in_fence
            if is_header and not in_fence:
                level = len(re.match(r"^(#{1,6})", raw).group(1))
                if level <= drop_section_level:
                    drop_section_level = 0
                    continue  # re-evaluate this header normally
            i += 1
            continue

        if is_fence:
            in_fence = not in_fence
            out.append(raw)
            i += 1
            continue

        if tool not in stripped.lower():
            out.append(stripped)
            i += 1
            continue

        # The (stripped) line still names the tool → it must go.
        if is_header:
            drop_section_level = len(re.match(r"^(#{1,6})", raw).group(1))
            i += 1
            continue
        if kind == "shell" and not in_fence:
            for pat, opener, closer in _SH_BLOCKS:
                if pat.search(raw):
                    i = _matching_close(src, i, opener, closer) + 1
                    break
            else:
                i += 1
            continue
        # markdown table row / fenced-code line / plain line: drop just it.
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def apply_tool_policy(workspace: Path, tool_policy: dict | None) -> None:
    """Apply per-run tool ablations to the workspace bin/ + /opt/sim mask.

    `delphes` is ablated with a *visible* stub: the agent sees a clear
    "Delphes tool is disabled" message. `prospino` is ablated *blind* — the
    bin shim is removed outright and the agent-visible docs are scrubbed by
    `scrub_tool_policy_docs()` (called once agent_context exists).

    Always enforced with run-local stub/mask trees and env overrides, never
    by mutating the shared benchmark tool installation.
    """
    bin_dir = workspace / "bin"
    for ab in _disabled_ablations(tool_policy):
        root = _ablation_root(workspace, ab)
        if ab.blind:
            # Wipe any stale tree from a reused run dir, then leave an empty
            # mask that shadows the image's /opt/sim/<sub>.
            if root.exists():
                shutil.rmtree(root)
            (root / "opt_sim").mkdir(parents=True, exist_ok=True)
            for name in ab.bin_names:
                (bin_dir / name).unlink(missing_ok=True)
        else:
            for name in ab.bin_names:
                _write_disabled_tool_stub(bin_dir / name, ab.stub_message)
                _write_disabled_tool_stub(root / name, ab.stub_message)
            (root / "cards").mkdir(parents=True, exist_ok=True)


def scrub_tool_policy_docs(workspace: Path, benchmark_dir: Path, tool_policy: dict | None) -> None:
    """Blind-ablation step: erase every doc trace of blind-disabled tools.

    (a) scrubs the per-run agent_context copies in place (AGENTS.md,
    TOOLS.md, …) for every blind tool, and (b) materializes ONE *shared*
    filtered shadow of the read-only docs — tools/CLI/, tools/TOOLS.md and
    bin/simulate — scrubbed of *all* blind-disabled tools, under
    `<run_dir>/.tool_policy/_docs/`. sandbox.py binds that single shadow
    over the canonical repo paths.

    The shadow is shared (not per-tool) on purpose: a per-tool shadow would
    bind multiple sources at the same container destination, which podman
    rejects (`exit 125`), and each copy would only be scrubbed of its own
    tool.

    Must run after agent_context is fully populated.
    """
    blind = [ab for ab in _disabled_ablations(tool_policy) if ab.blind]
    if not blind:
        return

    def _scrub_all(text: str, kind: str) -> str:
        for ab in blind:
            text = _scrub_tool_lines(text, ab.name, kind=kind)
        return text

    # (a) scrub the per-run agent_context copies in place.
    agent_context = workspace / "agent_context"
    if agent_context.is_dir():
        for md in agent_context.rglob("*.md"):
            original = md.read_text()
            scrubbed = _scrub_all(original, "markdown")
            if scrubbed != original:
                md.write_text(scrubbed)

    # (b) one shared shadow of the read-only docs, scrubbed of every blind
    #     tool, rebuilt fresh (idempotent on a reused run dir).
    shared = _shared_docs_shadow(workspace)
    if shared.exists():
        shutil.rmtree(shared)
    shared.mkdir(parents=True)
    tools_dir = benchmark_dir / "tools"

    cli_src = tools_dir / "CLI"
    if cli_src.is_dir():
        cli_dst = shared / "CLI"
        # Skip __pycache__ — a stale `prospino.cpython-*.pyc` would itself
        # leak the tool name even after the source module is dropped.
        shutil.copytree(cli_src, cli_dst, ignore=shutil.ignore_patterns("__pycache__"))
        for ab in blind:
            for fname in (ab.cli_doc, ab.cli_module):
                if fname:
                    (cli_dst / fname).unlink(missing_ok=True)
        for md in cli_dst.rglob("*.md"):
            original = md.read_text()
            scrubbed = _scrub_all(original, "markdown")
            if scrubbed != original:
                md.write_text(scrubbed)

    tools_md = tools_dir / "TOOLS.md"
    if tools_md.is_file():
        (shared / "TOOLS.md").write_text(_scrub_all(tools_md.read_text(), "markdown"))

    simulate = benchmark_dir / "bin" / "simulate"
    if simulate.is_file():
        dst = shared / "simulate"
        dst.write_text(_scrub_all(simulate.read_text(), "shell"))
        dst.chmod(0o755)


def disabled_tool_env(workspace: Path, tool_policy: dict | None) -> dict[str, str]:
    """Env-var redirects for visible-stub tools.

    Blind tools deliberately get NO env override: leaving `$PROSPINO_DIR`
    at its image default (now an empty masked dir) keeps the environment
    indistinguishable from a run where the tool was never offered.
    """
    return {
        ab.env_var: str(_ablation_root(workspace, ab))
        for ab in _disabled_ablations(tool_policy)
        if not ab.blind
    }


def tool_policy_binds(
    workspace: Path, repo_root: Path
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Binds that enforce tool ablation, split so each container destination
    is mounted exactly once.

    Returns ``(doc_subs, extra)``:

    * ``doc_subs`` — ``{canonical_dst: shadow_src}`` for paths the canonical
      ``_tools_bind_args`` already binds (``tools/CLI``, ``tools/TOOLS.md``).
      The caller must bind the *shadow* source AT that destination instead
      of the real source — it must NOT emit a second mount for the same
      destination. podman rejects duplicate mount destinations outright
      (``exit 125``); apptainer would silently last-win. Substituting keeps
      both backends correct and identical.
    * ``extra`` — additive ``(src, dst)`` whose destinations are not
      otherwise bound: the empty ``/opt/sim/<sub>`` masks and the nested
      ``bin/simulate`` override (a file inside the already-bound ``bin/``
      dir — a distinct destination, so a legal nested mount, not a dup).

    Derived from which run-local ablation trees exist (sandbox.py has no
    tool_policy).
    """
    from agent_runtime import paths as bench_paths

    doc_subs: dict[str, str] = {}
    extra: list[tuple[str, str]] = []

    # Per-tool empty /opt/sim/<sub> masks — each a unique destination.
    for ab in _ABLATIONS.values():
        root = _ablation_root(workspace, ab)
        mask = (root / "opt_sim") if ab.blind else root
        if mask.is_dir():
            extra.append((str(mask), f"/opt/sim/{ab.sim_subdir}"))

    # The single shared doc shadow (scrubbed of every blind tool): CLI and
    # TOOLS.md substitute the canonical source; simulate is one nested bind.
    shared = _shared_docs_shadow(workspace)
    if shared.is_dir():
        tools_dir = bench_paths.tools_dir(repo_root)
        if (shared / "CLI").is_dir():
            doc_subs[str(tools_dir / "CLI")] = str(shared / "CLI")
        if (shared / "TOOLS.md").is_file():
            doc_subs[str(tools_dir / "TOOLS.md")] = str(shared / "TOOLS.md")
        if (shared / "simulate").is_file():
            extra.append(
                (str(shared / "simulate"), str(bench_paths.bin_dir(repo_root) / "simulate"))
            )
    return doc_subs, extra


def build_workspace(
    repo_root: Path,
    agent_name: str,
    task_id: str,
    run_dir: str,
    tool_policy: dict | None = None,
) -> Path:
    """Create a fresh workspace under <repo_root>/runs/<run_dir>/workspace.

    agent_name must match a directory under agents/ (e.g. 'simple').
    task_id must match a directory under ColliderBench/tasks/.
    Raises FileNotFoundError if prerequisites are missing. Returns the workspace Path.
    """
    agent_dir = repo_root / "agents" / agent_name
    benchmark_dir = paths.benchmark_dir(repo_root)
    workspace = repo_root / "runs" / run_dir / "workspace"

    toml = load_task_toml(repo_root, task_id)
    paper_ref = (toml.get("task") or {}).get("paper")
    if not paper_ref:
        raise ValueError(f"task.toml: [task].paper is required (task_id={task_id})")

    task_dir = paths.task_dir(repo_root, task_id)
    shared = paths.shared_paper_dir(repo_root, paper_ref)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Missing task dir: {task_dir}")
    if not shared.is_dir():
        raise FileNotFoundError(f"Missing shared paper dir: {shared}")

    task_md = task_dir / "TASK.md"
    template_src = task_dir / "template"
    if not task_md.is_file():
        raise FileNotFoundError(f"Missing {task_md}")
    if not template_src.is_dir():
        raise FileNotFoundError(f"Missing {template_src}")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    # Workspace templates (stubs like datasets.yaml, report.md) from the agent
    template_dir = agent_dir / "runtime" / "templates" / "workspace"
    if template_dir.exists():
        for f in template_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, workspace / f.name)

    # tools/ is a symlink (ColliderBench/tools is ro-bind-mounted by sandbox.py)
    (workspace / "tools").symlink_to(benchmark_dir / "tools")

    # bin/ merges benchmark + agent scripts
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    for script in (benchmark_dir / "bin").iterdir():
        (bin_dir / script.name).symlink_to(script)
    for script in (agent_dir / "runtime" / "bin").iterdir():
        (bin_dir / script.name).symlink_to(script)
    apply_tool_policy(workspace, tool_policy)

    # Agent instructions (AGENTS.md, SOUL.md, skills/*.md, ...). TOOLS.md comes
    # from the benchmark (canonical index), not the per-agent copy.
    agent_context = workspace / "agent_context"
    agent_context.mkdir()
    for src in agent_dir.rglob("*.md"):
        rel = src.relative_to(agent_dir)
        if rel.parts[0] == "runtime":
            continue
        if rel.name == "TOOLS.md":
            continue
        dest = agent_context / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text()
        if paper_ref:
            text = text.replace("{arxiv_id}", paper_ref)
        dest.write_text(text)

    # Canonical TOOLS.md seeded from the benchmark.
    tools_index = benchmark_dir / "tools" / "TOOLS.md"
    if tools_index.is_file():
        shutil.copy2(tools_index, agent_context / "TOOLS.md")

    # Task spec — copied into agent_context alongside AGENTS.md.
    task_text = task_md.read_text()
    if paper_ref:
        task_text = task_text.replace("{arxiv_id}", paper_ref)
    (agent_context / "TASK.md").write_text(task_text)

    # Blind-ablation doc scrub — runs last, after every agent_context doc
    # exists, so e.g. prospino leaves no trace in AGENTS.md / TOOLS.md / the
    # shadowed CLI docs + bin/simulate.
    scrub_tool_policy_docs(workspace, benchmark_dir, tool_policy)

    # results/ — null-filled yaml skeleton (metadata embedded at top of
    # the yaml itself: instructions, target, luminosity, …), copied from
    # the task's template/. Agent fills nulls in place. task.toml itself
    # is intentionally NOT copied (harness metadata only).
    results = workspace / "results"
    results.mkdir()
    for src in template_src.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(template_src)
        dest = results / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # papers/ — symlink to the shared paper dir (PDF is large and immutable).
    shared_paper = shared / "paper"
    if shared_paper.is_dir():
        (workspace / "papers").symlink_to(shared_paper.resolve())

    # object_efficiencies/ — copy the shared pool; overlay task-specific
    # artifacts/ on top (task file with same name wins).
    obj_eff = workspace / "object_efficiencies"
    shared_eff = shared / "object_efficiencies"
    if shared_eff.is_dir():
        shutil.copytree(shared_eff, obj_eff, dirs_exist_ok=True)
    task_artifacts = task_dir / "artifacts"
    if task_artifacts.is_dir():
        obj_eff.mkdir(exist_ok=True)
        for src in task_artifacts.iterdir():
            if src.is_file():
                shutil.copy2(src, obj_eff / src.name)

    # Fetch the paper PDF once into the canonical shared location if missing.
    pdf_path = shared / "paper" / f"{paper_ref}.pdf"
    if not pdf_path.exists():
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://arxiv.org/pdf/{paper_ref}"
        print(f"Downloading: {url}")
        _fetch_arxiv_pdf(url, pdf_path)

    return workspace


def _fetch_arxiv_pdf(url: str, dest: Path) -> None:
    """Fetch ``url`` to ``dest`` with TLS verification + a PDF magic-byte check.

    urllib enforces TLS certificate validation by default since Python 3.6;
    we pass an explicit `ssl.create_default_context()` to make that
    intent visible and to fail loudly rather than silently fall through to
    an unverified connection if a future stdlib change relaxes the default.
    The download lands at ``dest.tmp`` first; we promote it to ``dest`` only
    after confirming the body starts with the PDF magic bytes (``%PDF``),
    so partially-written files or HTML error pages from arXiv don't shadow
    a legitimate cached PDF.
    """
    ctx = ssl.create_default_context()
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "lhc-recast/0.1"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    # PDF files start with "%PDF-". Reject anything that doesn't (e.g. an
    # HTML "withdrawn paper" page from arXiv would otherwise sit on disk
    # forever, masquerading as the paper).
    with open(tmp, "rb") as fh:
        head = fh.read(5)
    if head != b"%PDF-":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"downloaded {url} is not a PDF (first bytes={head!r}); refusing to cache"
        )
    tmp.rename(dest)
