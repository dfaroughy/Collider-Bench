# Prospino 2.1

NLO cross-section calculator for SUSY pair production at hadron colliders
(squark/gluino, stop/sbottom, neutralino/chargino, slepton). Authors:
W. Beenakker, R. Hoepker, M. Spira, P.M. Zerwas.

## Status

This directory is the drop-in point for the Prospino 2.1 source tree. Until
the tarball is unpacked here, the `prospino` CLI tool will exit with a clear
"source not vendored" error and the `tests/test_prospino.py` golden test is
skipped.

## Vendoring (one-time)

1. Obtain the Prospino 2.1 tarball from the authors' page
   (https://www.thphys.uni-heidelberg.de/~plehn/index.php?show=prospino).
2. Unpack directly into this directory so the layout becomes:
   ```
   LHCRecastBench/tools/sim/prospino/
     Makefile
     prospino_main.f90
     on_shell.f90
     Xintegrand_*.f90
     ...
     README.md           ← this file
   ```
3. First invocation of `bin/prospino` (or `scripts/prospino`) runs `make` in
   this directory. No separate build step is needed.

## Patches (if any)

None currently. If we vendor with local changes, record them here:

- YYYY-MM-DD — short description — rationale

## PDF grids

Prospino 2.1 expects CTEQ6L1/CTEQ6M (built-in) and can also read LHAPDF
tables. For reproducibility we pin CT14nlo via LHAPDF; the LHAPDF library
is provided by the `lhc_analysis` conda env.
