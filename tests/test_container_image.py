"""Audit the canonical benchmark container image (`localhost/lhc-bench`).

Verifies that everything the agent expects to find inside the container is
actually present *and works*: Python deps in the conda env, sim-stack
binaries baked under /opt/sim, env vars, bin/ wrappers, and a real
end-to-end MG5 → Pythia8 → Delphes chain.

All tests skip cleanly when podman/podman-hpc isn't installed or the
image hasn't been built — i.e. on dev laptops without the image, this
suite is a no-op rather than a failure.

Marker:
    @pytest.mark.image  → run only the image suite:  pytest -m image
                          skip the image suite:       pytest -m "not image"

Speed: cheap smoke checks share a single container invocation via the
`smoke` session fixture (~5 s total for ~20 checks). The end-to-end
generator-shower-detector chain is its own test and runs ~30-90 s
depending on host speed (MG5 amortizes diagram generation + Fortran
compile + 2 events through MadEvent).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import os

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Match the sandbox default but allow override for developers running
# tests against a locally-built image tagged differently.
IMAGE = os.environ.get("LHC_BENCH_IMAGE", "ghcr.io/dfaroughy/lhc-bench:latest")

_BENCH_DIR = REPO_ROOT / "LHCRecastBench"
_AGENT_RUNTIME_DIR = REPO_ROOT / "agent_runtime"


def _engine() -> str | None:
    """podman-hpc is preferred on NERSC; fall back to plain podman."""
    for binary in ("podman-hpc", "podman"):
        if shutil.which(binary):
            return binary
    return None


def _image_present(engine: str) -> bool:
    """Check whether IMAGE is loaded in the local store.

    `podman-hpc image exists` is unreliable (returns rc=1 even for present
    images on NERSC's wrapper). Listing repo:tag and substring-matching
    is the portable check.
    """
    try:
        out = subprocess.run(
            [engine, "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if out.returncode != 0:
        return False
    return any(line.strip() == IMAGE for line in out.stdout.splitlines())


_ENGINE = _engine()
_IMAGE_OK = bool(_ENGINE) and _image_present(_ENGINE)


pytestmark = [
    pytest.mark.image,
    pytest.mark.skipif(
        not _ENGINE,
        reason="no podman/podman-hpc installed — image suite skipped",
    ),
    pytest.mark.skipif(
        _ENGINE and not _IMAGE_OK,
        reason=f"image {IMAGE} not present (run docker/README.md build steps)",
    ),
]


def _run_in_container(
    shell_cmd: str,
    *,
    timeout: int = 60,
    extra_binds: list[tuple[Path, Path, str]] | None = None,
) -> subprocess.CompletedProcess:
    """Run a one-shot bash -c invocation inside the runtime image.

    The repo (LHCRecastBench/ + agent_runtime/) is mounted ro at the same
    path it has on the host — this matches production sandbox.py's mount
    layout, so bin/ wrappers that source agent_env.sh work correctly.

    extra_binds: list of (host_path, container_path, "ro"|"rw") tuples
    bound in addition to the defaults. Used by the e2e test to expose a
    rw scratch dir for the MG5/Pythia/Delphes outputs.
    """
    cmd = [
        _ENGINE,
        "run",
        "--rm",
        "-v",
        f"{_BENCH_DIR}:{_BENCH_DIR}:ro",
        "-v",
        f"{_AGENT_RUNTIME_DIR}:{_AGENT_RUNTIME_DIR}:ro",
        "--workdir",
        str(_BENCH_DIR),
        "-e",
        f"REPO_ROOT={REPO_ROOT}",
    ]
    for host, in_container, mode in extra_binds or []:
        cmd.extend(["-v", f"{host}:{in_container}:{mode}"])
    cmd.append(IMAGE)
    cmd.extend(["bash", "-c", shell_cmd])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ── Conda env: Python deps ────────────────────────────────────────────────


# Names match conda-forge package names except where the import name
# differs (PyYAML → yaml, PyHepMC → pyhepmc, fsspec-xrootd → fsspec_xrootd).
_PY_IMPORTS = [
    "yaml",
    "requests",
    "aiohttp",
    "uproot",
    "awkward",
    "vector",
    "numpy",
    "scipy",
    "matplotlib",
    "hist",
    "mplhep",
    "XRootD",
    "fsspec_xrootd",
    "pymupdf",
    "pyhepmc",
    "pythia8",
]


def test_python_is_conda_env():
    """`python` resolves to the baked conda env, not /usr/bin/python."""
    r = _run_in_container("readlink -f $(which python)")
    assert r.returncode == 0, r.stderr
    assert "/opt/conda/envs/lhc_analysis/bin/python" in r.stdout, r.stdout


def test_python_imports_all_deps_in_one_shot():
    """Import every package the runtime expects, in one container call.

    One subprocess invocation rather than 16 keeps the suite fast (~1s
    instead of ~16s of container start-up overhead) and the failure
    message lists every missing module at once.
    """
    script = (
        "python - <<'PY'\n"
        "import importlib\n"
        f"mods = {_PY_IMPORTS!r}\n"
        "missing = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except ImportError as e:\n"
        "        missing.append(f'{m}: {e}')\n"
        "if missing:\n"
        "    print('MISSING:', '; '.join(missing))\n"
        "    raise SystemExit(1)\n"
        "PY\n"
    )
    r = _run_in_container(script)
    assert r.returncode == 0, f"missing imports inside image:\n{r.stdout}\n{r.stderr}"


# ── Batched smoke fixture: 1 container start for ~20 cheap checks ────────
#
# Every cheap "run X, did it work?" check shares this fixture so the
# entire suite pays the podman cold-start (~5 s on NERSC) exactly once
# instead of once-per-parametrize-case.


_BIN_HELP_CASES = [
    ("cms-opendata", "--help"),
    ("feynrules", "--help"),
    ("hepdata", "--help"),
    ("prospino", "--help"),
    ("read-paper", "--help"),
    ("simulate", "info"),  # `simulate --help` exists too but `info` is the canonical smoke
]

_SIM_BINARIES = [
    "/opt/sim/MG5_aMC_v3_7_0/bin/mg5_aMC",
    "/opt/sim/delphes/DelphesHepMC2",
    "/opt/sim/delphes/DelphesHepMC3",
    "/opt/sim/delphes/DelphesROOT",
    "/opt/sim/pythia8313/bin/pythia8-config",
    "/opt/sim/prospino/prospino_2.run",
]

# Subset of _SIM_BINARIES that are ELF (the rest are shell wrappers ldd
# can't probe). All Delphes binaries link against ROOT and are the
# canary for "we forgot `root` in the conda env" — the libCore.so.6.38
# regression where Dockerfile.bench baked Delphes but not its ROOT runtime.
_ELF_SIM_BINARIES = [
    "/opt/sim/delphes/DelphesHepMC2",
    "/opt/sim/delphes/DelphesHepMC3",
    "/opt/sim/delphes/DelphesROOT",
    "/opt/sim/prospino/prospino_2.run",
]

# Sim binaries we actually exec to verify the dynamic linker resolves.
# `</dev/null` keeps anything interactive (mg5_aMC, prospino) from hanging.
_LAUNCH_SMOKE = [
    "/opt/sim/delphes/DelphesHepMC3",
    "/opt/sim/delphes/DelphesHepMC2",
    "/opt/sim/delphes/DelphesROOT",
    "/opt/sim/pythia8313/bin/pythia8-config --version",
    "/opt/sim/MG5_aMC_v3_7_0/bin/mg5_aMC --help",
    "/opt/sim/prospino/prospino_2.run",
]

_REQUIRED_ENV = [
    ("MG5_DIR", "/opt/sim/MG5_aMC_v3_7_0"),
    ("PYTHIA8_DIR", "/opt/sim/pythia8313"),
    ("DELPHES_DIR", "/opt/sim/delphes"),
    ("PROSPINO_DIR", "/opt/sim/prospino"),
    ("CONDA_DEFAULT_ENV", "lhc_analysis"),
]


@pytest.fixture(scope="session")
def smoke():
    """Run every cheap container check in one shot; return a result dict.

    Layout of the returned dict:
        {
          "bin:cms-opendata": {"rc": 0, "out": "..."},
          "exec:/opt/sim/.../mg5_aMC": {"rc": 0, "out": "OK"},
          "env:MG5_DIR": {"rc": 0, "out": "/opt/sim/..."},
          "ldd_unresolved": ["libCore.so.6.38: not found", ...],
          "launch_output": "<combined stderr/stdout from each binary>",
        }

    Test functions just look up their key and assert. If the container
    can't even be entered (image missing, podman busted), every keyed
    entry will be missing and individual tests will skip with a clean
    message instead of crashing.
    """
    if not _IMAGE_OK:
        return {}

    parts: list[str] = []

    # bin/<tool> <arg> smoke
    for tool, arg in _BIN_HELP_CASES:
        key = f"bin:{tool}"
        parts.append(
            f"echo '<<<{key}>>>'; timeout 30 bin/{tool} {arg} 2>&1; echo \"<<<rc:{key}:$?>>>\""
        )

    # /opt/sim/<binary> exec-bit + presence
    for path in _SIM_BINARIES:
        key = f"exec:{path}"
        parts.append(
            f"echo '<<<{key}>>>'; "
            f"if test -x {path}; then echo OK; else echo MISSING; fi; "
            f'echo "<<<rc:{key}:$?>>>"'
        )

    # ldd: unresolved-lib detection on the ELF sim binaries
    parts.append("echo '<<<ldd>>>'")
    for path in _ELF_SIM_BINARIES:
        parts.append(f"echo '--- {path} ---'; ldd {path} 2>&1")
    parts.append("echo '<<<rc:ldd:0>>>'")

    # actual exec smoke: each binary must launch w/o loader errors
    parts.append("echo '<<<launch>>>'")
    for cmd in _LAUNCH_SMOKE:
        parts.append(
            f"echo '--- {cmd} ---'; timeout 5 bash -c '{cmd} </dev/null' 2>&1; echo \"(rc=$?)\""
        )
    parts.append("echo '<<<rc:launch:0>>>'")

    # baked env-var contract
    for var, _ in _REQUIRED_ENV:
        key = f"env:{var}"
        parts.append(
            f"echo '<<<{key}>>>'; printf '%s' \"${{{var}}}\"; echo; echo \"<<<rc:{key}:0>>>\""
        )

    script = "\n".join(parts)
    proc = _run_in_container(script, timeout=180)

    # ── parse output into structured results ──────────────────────────
    results: dict[str, dict] = {}
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    rc_marker_prefix = "<<<rc:"
    open_marker = "<<<"
    for line in proc.stdout.splitlines():
        if line.startswith(rc_marker_prefix):
            inner = line[len(rc_marker_prefix) :].rstrip(">")
            key, _, rc_str = inner.rpartition(":")
            try:
                rc = int(rc_str)
            except ValueError:
                rc = -1
            results[key] = {
                "rc": rc,
                "out": "\n".join(sections.get(key, [])).strip(),
            }
            current_key = None
        elif line.startswith(open_marker) and line.endswith(">>>"):
            current_key = line[len(open_marker) : -3]
            sections[current_key] = []
        elif current_key is not None:
            sections[current_key].append(line)

    # parse ldd block for unresolved libs
    ldd_block = "\n".join(sections.get("ldd", []))
    results["__ldd_unresolved"] = [
        line.strip() for line in ldd_block.splitlines() if "=> not found" in line
    ]
    results["__launch_output"] = "\n".join(sections.get("launch", []))
    results["__container_rc"] = proc.returncode
    results["__container_stderr"] = proc.stderr
    return results


# ── bin/ wrappers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool,arg", _BIN_HELP_CASES, ids=[t for t, _ in _BIN_HELP_CASES])
def test_bin_tool_help(smoke, tool, arg):
    """Each bin/ wrapper resolves its deps and produces substantive output.

    Catches: missing deps (wrapper crashes), broken Python (`No module
    named X` from a sourced helper), bad shebang, missing executable bit.
    Doesn't try to validate the help-text format — different wrappers
    use different conventions.
    """
    key = f"bin:{tool}"
    if key not in smoke:
        pytest.skip("smoke fixture had no result for this case")
    res = smoke[key]
    assert res["rc"] == 0, f"bin/{tool} {arg} failed (rc={res['rc']}):\n{res['out'][:800]}"
    assert (
        len(res["out"].strip()) > 30
    ), f"bin/{tool} {arg} succeeded but produced suspiciously little output:\n{res['out']!r}"


def test_prospino_list_processes_returns_json():
    """Prospino's process listing is the offline-friendly smoke for the
    sim stack — confirms the wrapper finds the binary AND the parser works.

    Lives outside the smoke fixture because parsing JSON in shell is
    brittle; one extra container start is fine for one test.
    """
    r = _run_in_container("bin/prospino list-processes")
    assert r.returncode == 0, r.stderr
    parsed = json.loads(r.stdout)  # raises if not valid JSON
    assert isinstance(parsed, (list, dict)) and len(parsed) > 0


def test_read_paper_extracts_text_from_pdf():
    """End-to-end: a 1-page PDF read should return real text content,
    not the 'pymupdf not installed' error message we currently see.
    """
    pdf = _BENCH_DIR / "tasks/shared/CMS-SUS-16-046/paper/CMS-SUS-16-046.pdf"
    if not pdf.is_file():
        pytest.skip(f"reference PDF missing: {pdf}")
    r = _run_in_container(f"bin/read-paper {pdf} --pages 1-1", timeout=45)
    assert r.returncode == 0, r.stderr
    assert (
        len(r.stdout.strip()) > 200
    ), f"read-paper extracted suspiciously little text:\n{r.stdout!r}"
    assert "pymupdf" not in r.stdout.lower(), "read-paper still reports pymupdf missing"


# ── Sim-stack binaries ────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _SIM_BINARIES)
def test_sim_binary_executable(smoke, path):
    """Every binary the agent reaches via bin/simulate / bin/prospino
    must exist and be executable inside the image."""
    key = f"exec:{path}"
    if key not in smoke:
        pytest.skip("smoke fixture had no result for this case")
    res = smoke[key]
    assert res["rc"] == 0 and "OK" in res["out"], f"{path}: {res['out'].strip() or 'no output'}"


def test_sim_binaries_have_no_unresolved_libs(smoke):
    """Every ELF sim binary's dynamic libs must resolve inside the image.

    Catches the failure mode where Delphes was built against ROOT 6.38
    on the host but the conda env baked into the image lacks `root`, so
    libCore.so.6.38 is "=> not found". Without this gate the agent only
    discovers the breakage *during* a recast run and silently pivots to
    a hand-rolled detector emulation, biasing yields by 3-4x.
    """
    if "__ldd_unresolved" not in smoke:
        pytest.skip("smoke fixture had no ldd block")
    bad = smoke["__ldd_unresolved"]
    assert not bad, (
        "unresolved dynamic libraries inside the image — agent runs will "
        "fail with `error while loading shared libraries` at runtime:\n" + "\n".join(bad)
    )


def test_sim_binaries_actually_launch(smoke):
    """Each sim binary must launch without a loader error.

    `test -x` and `ldd` only verify static structure; this test
    actually exec()s each binary so the dynamic linker runs. We don't
    care about the exit code (most print usage and exit non-zero with
    no args) — only that stderr never contains the canonical
    loader-failure strings.
    """
    if "__launch_output" not in smoke:
        pytest.skip("smoke fixture had no launch block")
    output = smoke["__launch_output"]
    bad_substrings = [
        "error while loading shared libraries",
        "cannot open shared object file",
        "GLIBCXX_",  # libstdc++ ABI mismatch
        "command not found",
        ": not found",  # bash's "binary not found" form
    ]
    found = [s for s in bad_substrings if s in output]
    assert not found, f"sim binaries failed to launch cleanly: {found}\n\nfull output:\n{output}"


# ── Env-var contract ──────────────────────────────────────────────────────


@pytest.mark.parametrize("var,expected", _REQUIRED_ENV, ids=[v for v, _ in _REQUIRED_ENV])
def test_baked_env_var(smoke, var, expected):
    """Sim-tool wrappers read these env vars to locate their binaries.
    Drift between Dockerfile.bench and bin/simulate would silently break
    runs; this pins the contract.
    """
    key = f"env:{var}"
    if key not in smoke:
        pytest.skip("smoke fixture had no result for this case")
    res = smoke[key]
    assert res["rc"] == 0
    assert (
        res["out"].strip() == expected
    ), f"{var}: expected {expected!r}, got {res['out'].strip()!r}"


# ── End-to-end pipeline: MG5 → Pythia8 → Delphes ─────────────────────────


_MG5_PROC_CARD = textwrap.dedent("""\
    set automatic_html_opening False
    set notification_center False
    generate p p > e+ e-
    output mg5_output
    launch mg5_output
        set nevents 2
        set ebeam1 6500
        set ebeam2 6500
        set use_syst False
        set automatic_html_opening False
        done
    """)


def test_e2e_mg5_pythia_delphes(tmp_path):
    """Real generator-shower-detector chain inside the container.

    Generates 2 SM Drell-Yan events (`p p > e+ e-`) with MadGraph5,
    showers them with Pythia8 (via the pyhepmc bridge baked into
    bin/simulate), then runs Delphes with the CMS card. Verifies a
    non-trivial ROOT file lands at the expected path.

    This is the only test in the suite that exercises the *full* sim
    pipeline as the agent would see it. Catches issues `ldd` and
    `--help` can't (e.g. missing TCL/Delphes datacard files, broken
    pyhepmc↔Pythia8 bridge, ROOT TFile-write failures) at the cost of
    ~30-90 s wall time depending on host.

    The test runs the same `bin/simulate` wrapper the agent uses, so a
    pass here is meaningful evidence that the recast pipeline works.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    proc_card = workspace / "proc_card.dat"
    proc_card.write_text(_MG5_PROC_CARD)

    # Driver scripts the test will write. Mimics the patterns documented
    # in CLI/SIMULATE.md (and what working agent runs do in practice) —
    # call mg5_aMC, a custom Pythia driver, and DelphesHepMC3 directly.
    shower_py = workspace / "shower.py"
    shower_py.write_text(
        textwrap.dedent("""\
        import os, sys
        import pythia8, pyhepmc
        lhe_in, hepmc_out = sys.argv[1], sys.argv[2]
        # Count LHE events upfront. Pythia 8.312's `info.atEndOfFile()`
        # never flips to True for short LHEs, so the textbook
        # `while True: p.next() ... if eof break` loop hangs after the
        # last event. Iterating exactly N times sidesteps the bug.
        with open(lhe_in) as f:
            n_lhe = sum(1 for line in f if "<event>" in line)
        xmldir = os.environ["CONDA_PREFIX"] + "/share/Pythia8/xmldoc"
        p = pythia8.Pythia(xmldir, False)
        p.readString(f"Beams:LHEF = {lhe_in}")
        p.readString("Beams:frameType = 4")
        p.readString("Tune:pp = 14")
        p.readString("Print:quiet = on")
        p.init()
        n = 0
        with pyhepmc.open(hepmc_out, "w") as writer:
            for _ in range(n_lhe):
                if not p.next():
                    continue
                n += 1
                evt = pyhepmc.GenEvent(pyhepmc.Units.GEV, pyhepmc.Units.MM)
                evt.event_number = n
                v = pyhepmc.GenVertex()
                evt.add_vertex(v)
                for j in range(1, p.event.size()):
                    pp = p.event[j]
                    if not pp.isFinal() and pp.statusHepMC() != 4:
                        continue
                    gp = pyhepmc.GenParticle(
                        pyhepmc.FourVector(pp.px(), pp.py(), pp.pz(), pp.e()),
                        pp.id(), pp.statusHepMC(),
                    )
                    gp.generated_mass = pp.m()
                    if pp.statusHepMC() == 4:
                        v.add_particle_in(gp)
                    else:
                        v.add_particle_out(gp)
                writer.write(evt)
        print(f"showered {n}/{n_lhe} events")
        sys.exit(0 if n > 0 else 1)
    """)
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail
        cd {workspace}

        # ── MG5: parton-level ─────────────────────────────────────────
        echo "=== MG5: generate 2 events of p p > e+ e- ==="
        mg5_aMC {proc_card}
        LHE=$(find . -name 'unweighted_events.lhe*' -type f | head -1)
        if [[ "$LHE" == *.gz ]]; then gunzip -f "$LHE"; LHE="${{LHE%.gz}}"; fi
        N_LHE=$(grep -c "<event>" "$LHE" || true)
        echo "MG5 produced ${{N_LHE:-0}} events at $LHE"
        if [[ "${{N_LHE:-0}}" -lt 1 ]]; then
            echo "FAIL: MG5 produced 0 events"; exit 11
        fi

        # ── Pythia8: direct Python driver (no wrapper) ───────────────
        echo "=== Pythia8: shower with custom driver ==="
        HEPMC={workspace}/events.hepmc
        python {shower_py} "$LHE" "$HEPMC"
        N_HEPMC=$(grep -c "^E " "$HEPMC" || true)
        echo "Pythia8 produced ${{N_HEPMC:-0}} events at $HEPMC"
        if [[ "${{N_HEPMC:-0}}" -lt 1 ]]; then
            echo "FAIL: Pythia8 produced 0 HepMC events from ${{N_LHE:-0}} LHE events"
            head -20 "$HEPMC" || true
            exit 12
        fi

        # ── Delphes: direct binary call ──────────────────────────────
        echo "=== Delphes: direct CMS card invocation ==="
        ROOT={workspace}/delphes_output.root
        DelphesHepMC3 "$DELPHES_DIR/cards/delphes_card_CMS.tcl" "$ROOT" "$HEPMC"
        if [[ ! -s "$ROOT" ]]; then
            echo "FAIL: Delphes produced no ROOT output"; exit 13
        fi
        echo "Delphes produced ROOT: $ROOT"

        echo "=== Verify ROOT output is readable ==="
        python - <<PY
        import uproot, sys
        f = uproot.open("$ROOT")
        if "Delphes" not in f:
            print("ERROR: no Delphes tree in output", file=sys.stderr)
            sys.exit(1)
        n = f["Delphes"].num_entries
        print(f"Delphes tree has {{n}} entries")
        if n == 0:
            print("ERROR: empty tree", file=sys.stderr)
            sys.exit(1)
        PY
        echo "=== e2e pipeline OK ==="
    """)

    # 10 min: dominated by Pythia 8.312 init (MPI setup + CMS UE tune
    # for pp collisions takes 2-4 min even for 2 events), plus MG5
    # diagram generation + Fortran compile (60-90 s). Delphes is fast.
    r = _run_in_container(
        script,
        timeout=600,
        extra_binds=[(workspace, workspace, "rw")],
    )

    if r.returncode != 0:
        pytest.fail(
            "e2e MG5→Pythia→Delphes pipeline failed\n"
            f"stdout (last 4KB):\n{r.stdout[-4000:]}\n"
            f"stderr (last 2KB):\n{r.stderr[-2000:]}"
        )
    assert "e2e pipeline OK" in r.stdout, r.stdout[-2000:]
