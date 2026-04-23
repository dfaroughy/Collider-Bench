#!/usr/bin/env python3
"""Agent-facing CLI around Prospino 2.1 for SUSY pair-production cross sections.

The upstream FORTRAN has no real CLI — you edit `prospino_main.f90`, run
`make`, then `./prospino_2.run`. This wrapper hides that: renders the main
file from a template, (re)builds if needed, runs the binary in a scratch
dir inside the caller's CWD (= the agent's bwrapped workspace), and parses
the result into a stable JSON schema.

Usage:

    prospino run --process gg --sqrts 13000 --order NLO --slha spectrum.slha
    prospino list-processes
    prospino help-process gg

Output (stdout, one JSON object):

    {
      "process": "gg",
      "sqrts_gev": 13000,
      "order": "NLO",
      "xsec_pb": {"lo": 1.23e-4, "nlo": 2.04e-4, "k_factor": 1.66},
      "slha": "/abs/path/spectrum.slha",
      "runtime_s": 12.7,
      "prospino_dat": "<workspace>/prospino_scratch/.../prospino.dat"
    }

Per-run state (rendered main file, `prospino.dat`, build logs) is preserved
under `prospino_scratch/` in the workspace so failures are inspectable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ── Layout ──────────────────────────────────────────────────────────────────

# Resolve repo-relative paths from this file's location so the tool works
# whether invoked from the repo, a sandboxed workspace, or a symlink.
_THIS = Path(__file__).resolve()
PROSPINO_SRC = _THIS.parent.parent / "sim" / "prospino"
BINARY = PROSPINO_SRC / "prospino_2.run"
MAIN_F90 = PROSPINO_SRC / "prospino_main.f90"
DAT_FILE = "prospino.dat"


# ── Process catalogue ──────────────────────────────────────────────────────
#
# Prospino's `final_state` string → human description + required spectrum
# fields. These are the 10 canonical channels. Mass-field names are the SLHA
# block+entry keys we'll read from the user's SLHA file (or expose as CLI
# --m-<name> overrides).
#
# NOTE: verify against the actual prospino_main.f90 when the source is
# vendored. Prospino distributions differ slightly in which final_state
# tokens they accept; this list is from the upstream 2.1 release.

# Each entry documents:
#   desc    — one-line channel description
#   masses  — SLHA masses that drive σ for this channel (the agent must set
#             them in the SLHA passed via --slha)
#   ipart1  — what ipart1 indexes for this channel (copied from Prospino's
#             prospino_main.f90 comments, verbatim semantics)
#   ipart2  — same for ipart2 (or "(unused)" if Prospino ignores it here)
PROCESSES: dict[str, dict] = {
    "ng": {
        "desc": "neutralino/chargino + squark",
        "masses": ["m_squark", "m_gluino", "m_chi"],
        "ipart1": "1-4 neutralinos; 5-6 χ̃⁺; 7-8 χ̃⁻",
        "ipart2": "(unused; set to 1)",
    },
    "nn": {
        "desc": "neutralino/chargino pair",
        "masses": ["m_chi_i", "m_chi_j"],
        "ipart1": "1-4 neutralinos; 5-6 χ̃⁺; 7-8 χ̃⁻",
        "ipart2": "1-4 neutralinos; 5-6 χ̃⁺; 7-8 χ̃⁻",
    },
    "ns": {
        "desc": "neutralino/chargino + slepton",
        "masses": ["m_chi", "m_slepton"],
        "ipart1": "1-4 neutralinos; 5-6 χ̃⁺; 7-8 χ̃⁻",
        "ipart2": "(unused; set to 1)",
    },
    "ll": {
        "desc": "slepton pair",
        "masses": ["m_slepton"],
        "ipart1": (
            "0 sel+ser first-gen; 1 seL; 2 seR; 3 snueL; 4 seL+snu; 5 seL-snu; "
            "6 stau1; 7 stau2; 8 stau1-stau2; 9 sntau; 10-13 stau+sntau; 14 H+H-"
        ),
        "ipart2": "(unused; set to 1)",
    },
    "sb": {
        "desc": "squark-antisquark pair",
        "masses": ["m_squark", "m_gluino"],
        "ipart1": "(unused; set to 1)",
        "ipart2": "(unused; set to 1)",
    },
    "ss": {
        "desc": "squark-squark pair",
        "masses": ["m_squark", "m_gluino"],
        "ipart1": "(unused; set to 1)",
        "ipart2": "(unused; set to 1)",
    },
    "tb": {
        "desc": "stop-antistop pair",
        "masses": ["m_stop1", "m_stop2"],
        "ipart1": "1 stop1 pair; 2 stop2 pair",
        "ipart2": "(unused; set to 1)",
    },
    "bb": {
        "desc": "sbottom-antisbottom pair",
        "masses": ["m_sbottom1", "m_sbottom2"],
        "ipart1": "1 sbot1 pair; 2 sbot2 pair",
        "ipart2": "(unused; set to 1)",
    },
    "gg": {
        "desc": "gluino pair",
        "masses": ["m_gluino", "m_squark"],
        "ipart1": "(unused; set to 1)",
        "ipart2": "(unused; set to 1)",
    },
    "sg": {
        "desc": "squark + gluino",
        "masses": ["m_squark", "m_gluino"],
        "ipart1": "(unused; set to 1)",
        "ipart2": "(unused; set to 1)",
    },
}


# ── Build ──────────────────────────────────────────────────────────────────


class ProspinoError(RuntimeError):
    pass


def _require_source() -> None:
    if not PROSPINO_SRC.is_dir() or not (PROSPINO_SRC / "Makefile").exists():
        raise ProspinoError(
            f"Prospino source not vendored at {PROSPINO_SRC}. "
            f"See {PROSPINO_SRC}/README.md for vendoring instructions."
        )


def _build() -> None:
    """Compile prospino_2.run via `make`. Idempotent; skipped if binary exists."""
    _require_source()
    if BINARY.exists():
        return
    subprocess.run(["make", "-C", str(PROSPINO_SRC)], check=True)
    if not BINARY.exists():
        raise ProspinoError(f"Build completed but {BINARY} still absent. Check Makefile output.")


# ── Main-file template ─────────────────────────────────────────────────────
#
# Verified against the vendored prospino_main.f90. Signature is:
#   PROSPINO(inlo, isq_ng_in, icoll_in, energy_in, i_error_in,
#            final_state_in, ipart1_in, ipart2_in, isquark1_in, isquark2_in)
#
# We keep the PROSPINO_CHECK_HIGGS / PROSPINO_CHECK_FS guards from the
# upstream main so unsupported final states fail fast.


_MAIN_TEMPLATE = """\
program main
  use xx_kinds
  use xx_prospino_subroutine
  implicit none

  integer                              :: inlo,isq_ng_in,icoll_in,i_error_in
  integer                              :: ipart1_in,ipart2_in,isquark1_in,isquark2_in
  real(kind=double)                    :: energy_in
  logical                              :: lfinal
  character(len=2)                     :: final_state_in

  inlo = {inlo}
  isq_ng_in = {isq_ng}
  icoll_in  = {icoll}
  energy_in = {sqrts}d0
  i_error_in = 0
  final_state_in = '{process}'
  ipart1_in = {ipart1}
  ipart2_in = {ipart2}
  isquark1_in = 0
  isquark2_in = 0

  call PROSPINO_OPEN_CLOSE(0)
  call PROSPINO_CHECK_HIGGS(final_state_in)
  call PROSPINO_CHECK_FS(final_state_in,ipart1_in,ipart2_in,lfinal)
  if (.not. lfinal) then
     print*, " final state not correct ", final_state_in, ipart1_in, ipart2_in
     call HARD_STOP
  end if

  call PROSPINO(inlo, isq_ng_in, icoll_in, energy_in, i_error_in, &
                final_state_in, ipart1_in, ipart2_in, isquark1_in, isquark2_in)

  call PROSPINO_OPEN_CLOSE(1)
end program main
"""


_COLLIDER_CODES = {"lhc": 1, "tevatron": 0}


def _render_main(
    process: str,
    sqrts_gev: int,
    order: str,
    ipart1: int = 1,
    ipart2: int = 1,
    isq_ng: int = 1,
    collider: str = "lhc",
) -> str:
    if process not in PROCESSES:
        raise ProspinoError(f"Unknown process {process!r}. Available: {sorted(PROCESSES)}")
    if order not in ("LO", "NLO"):
        raise ProspinoError(f"order must be LO or NLO, got {order!r}")
    icoll = _COLLIDER_CODES.get(collider.lower())
    if icoll is None:
        raise ProspinoError(f"collider must be one of {sorted(_COLLIDER_CODES)}, got {collider!r}")
    return _MAIN_TEMPLATE.format(
        process=process,
        sqrts=sqrts_gev,
        inlo=0 if order == "LO" else 1,
        isq_ng=isq_ng,
        ipart1=ipart1,
        ipart2=ipart2,
        icoll=icoll,
    )


# ── Run ────────────────────────────────────────────────────────────────────


def _run_in_scratch(
    process: str,
    sqrts_gev: int,
    order: str,
    slha_path: Path | None,
    ipart1: int,
    ipart2: int,
    isq_ng: int,
    collider: str,
) -> tuple[Path, Path]:
    """Execute Prospino in an isolated scratch dir under CWD. Returns (scratch_dir, prospino.dat).

    CWD is the agent's bwrapped workspace — the only rw path in the sandbox
    — so scratch always lands there. No env-var or flag override.
    """
    _build()

    key = hashlib.sha1(
        f"{process}|{sqrts_gev}|{order}|{slha_path}|{ipart1}|{ipart2}|{isq_ng}|{collider}".encode()
    ).hexdigest()[:12]
    scratch = Path.cwd() / "prospino_scratch" / f"{process}-{key}"
    scratch.mkdir(parents=True, exist_ok=True)

    # Stage a snapshot of the source (symlinked) into scratch so we can
    # overwrite prospino_main.f90 without mutating the vendored tree. We
    # bypass the pre-built prospino_main.o / prospino_2.run so make rebuilds
    # just the main (everything else is reused via symlinked .o files).
    for name in os.listdir(PROSPINO_SRC):
        if name in ("prospino_main.f90", "prospino_main.o", "prospino_2.run"):
            continue
        src = PROSPINO_SRC / name
        dst = scratch / name
        if dst.exists() or dst.is_symlink():
            continue
        if src.is_dir():
            dst.symlink_to(src)
        else:
            shutil.copy2(src, dst)

    (scratch / "prospino_main.f90").write_text(
        _render_main(
            process,
            sqrts_gev,
            order,
            ipart1=ipart1,
            ipart2=ipart2,
            isq_ng=isq_ng,
            collider=collider,
        )
    )
    if slha_path is not None:
        shutil.copy2(slha_path, scratch / "prospino.in.les_houches")

    subprocess.run(["make", "-C", str(scratch)], check=True)
    subprocess.run([str(scratch / "prospino_2.run")], cwd=scratch, check=True)

    dat = scratch / DAT_FILE
    if not dat.exists():
        raise ProspinoError(f"Prospino completed but no {DAT_FILE} in {scratch}")
    return scratch, dat


# ── Output parser ──────────────────────────────────────────────────────────


def _parse_dat(dat: Path) -> dict:
    """Extract (σ_LO, σ_NLO, K) from prospino.dat.

    Layout per the vendored Prospino 2.1 output (one row per invocation):
        [0]  final_state   ('nn', 'gg', ...)
        [1]  i1            (ipart1_in)
        [2]  i2            (ipart2_in)
        [3]  dummy0
        [4]  dummy1
        [5]  scafac         (scale factor)
        [6]  m1             (mass 1, GeV)
        [7]  m2             (mass 2, GeV)
        [8]  angle
        [9]  LO[pb]
        [10] rel_error
        [11] NLO[pb]
        [12] rel_error
        [13] K              (NLO/LO)
        [14] LO_ms[pb]      (free-squark-masses LO)
        [15] NLO_ms[pb]     (free-squark-masses NLO)
    We prefer LO_ms / NLO_ms when they are nonzero (isq_ng_in=1 path — the
    SLHA-driven case our agents actually use); otherwise fall back to the
    degenerate-mass columns.
    """
    # prospino.dat puts the column header AFTER the data row (see vendored
    # samples in tools/sim/prospino/). Match on the first token being a
    # valid process code rather than the last non-comment line.
    cols = None
    for line in dat.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts and parts[0] in PROCESSES:
            cols = parts
            break
    if cols is None or len(cols) < 14:
        raise ProspinoError(f"{dat} has no data row with known process code")
    try:
        lo_avg = float(cols[9])
        nlo_avg = float(cols[11])
        k_avg = float(cols[13]) if len(cols) > 13 else None
        lo_ms = float(cols[14]) if len(cols) > 14 else 0.0
        nlo_ms = float(cols[15]) if len(cols) > 15 else 0.0
    except (IndexError, ValueError) as exc:
        raise ProspinoError(f"Cannot parse {dat}: unexpected columns {cols}.") from exc
    lo = lo_ms if lo_ms > 0 else lo_avg
    nlo = nlo_ms if nlo_ms > 0 else nlo_avg
    # Prefer the K column prospino reports; fall back to NLO/LO.
    if k_avg is not None and k_avg > 0:
        k = k_avg
    else:
        k = (nlo / lo) if lo > 0 else None
    return {"lo": lo, "nlo": nlo, "k_factor": k}


# ── Public entry point ─────────────────────────────────────────────────────


def compute(
    process: str,
    sqrts_gev: int,
    order: str,
    slha_path: Path | None,
    ipart1: int = 1,
    ipart2: int = 1,
    isq_ng: int = 1,
    collider: str = "lhc",
) -> dict:
    t0 = time.time()
    scratch, dat = _run_in_scratch(
        process, sqrts_gev, order, slha_path, ipart1, ipart2, isq_ng, collider
    )
    xsec = _parse_dat(dat)
    return {
        "process": process,
        "collider": collider,
        "sqrts_gev": sqrts_gev,
        "order": order,
        "ipart1": ipart1,
        "ipart2": ipart2,
        "isq_ng": isq_ng,
        "xsec_pb": xsec,
        "slha": str(slha_path.resolve()) if slha_path else None,
        "runtime_s": round(time.time() - t0, 2),
        "prospino_dat": str(dat),
        "scratch_dir": str(scratch),
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def _cmd_list(_args) -> int:
    print(json.dumps(PROCESSES, indent=2))
    return 0


def _cmd_help_process(args) -> int:
    p = PROCESSES.get(args.process)
    if p is None:
        print(f"Unknown process {args.process!r}. Available: {sorted(PROCESSES)}")
        return 2
    print(json.dumps({args.process: p}, indent=2))
    return 0


def _cmd_run(args) -> int:
    slha = Path(args.slha).resolve() if args.slha else None
    if slha and not slha.is_file():
        print(f"ERROR: --slha {slha} not found", file=sys.stderr)
        return 2
    try:
        result = compute(
            args.process,
            args.sqrts,
            args.order,
            slha,
            ipart1=args.ipart1,
            ipart2=args.ipart2,
            isq_ng=args.isq_ng,
            collider=args.collider,
        )
    except ProspinoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: prospino failed (rc={exc.returncode})", file=sys.stderr)
        return exc.returncode
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prospino",
        description="Prospino 2.1 cross-section wrapper (SUSY pair production).",
    )
    sub = parser.add_subparsers(dest="_cmd", required=True)

    p_run = sub.add_parser("run", help="Compute a cross section.")
    p_run.add_argument("--process", required=True, choices=sorted(PROCESSES))
    p_run.add_argument(
        "--collider",
        choices=sorted(_COLLIDER_CODES),
        default="lhc",
        help="Collider: lhc (pp) or tevatron (pp̄). Default lhc.",
    )
    p_run.add_argument(
        "--sqrts", type=int, default=13000, help="Beam energy in GeV (default 13000)."
    )
    p_run.add_argument("--order", choices=["LO", "NLO"], default="NLO")
    p_run.add_argument("--slha", help="SLHA spectrum file.")
    p_run.add_argument(
        "--ipart1",
        type=int,
        default=1,
        help="Final-state particle 1 index (meaningful for nn/ns/ng/ll/tb/bb; "
        "see `help-process <name>`). Ignored otherwise.",
    )
    p_run.add_argument(
        "--ipart2",
        type=int,
        default=1,
        help="Final-state particle 2 index (see --ipart1).",
    )
    p_run.add_argument(
        "--isq-ng",
        dest="isq_ng",
        type=int,
        default=1,
        choices=[0, 1],
        help="Squark mass treatment: 1 = free (from SLHA, default); 0 = degenerate average.",
    )
    p_run.set_defaults(_func=_cmd_run)

    p_list = sub.add_parser("list-processes", help="Print the process catalogue as JSON.")
    p_list.set_defaults(_func=_cmd_list)

    p_help = sub.add_parser("help-process", help="Required masses for one process, as JSON.")
    p_help.add_argument("process")
    p_help.set_defaults(_func=_cmd_help_process)

    args = parser.parse_args(argv)
    return args._func(args)


if __name__ == "__main__":
    raise SystemExit(main())
