"""Tests for the Prospino CLI wrapper.

Two tiers:
  - Always-run: CLI surface (arg parsing, process catalogue, error paths) —
    exercised without touching the FORTRAN binary.
  - Build-required: golden value test, skipped unless prospino_2.run exists.
    A CI machine without gfortran + the source tree can still run the suite.
"""

from __future__ import annotations

import json

import pytest

from LHCRecastBench.tools.CLI import prospino


# ── Always-run: structure & CLI surface ────────────────────────────────────


def test_process_catalogue_covers_ten_channels():
    # Prospino 2.1 ships these 10 canonical final_state tokens.
    assert set(prospino.PROCESSES) == {"ng", "nn", "ns", "ll", "sb", "ss", "tb", "bb", "gg", "sg"}


def test_every_process_has_nonempty_mass_list():
    for name, spec in prospino.PROCESSES.items():
        assert spec["desc"]
        assert spec["masses"], f"{name} has no mass list"


def test_render_main_rejects_unknown_process():
    with pytest.raises(prospino.ProspinoError, match="Unknown process"):
        prospino._render_main("xx", 13000, "NLO")


def test_render_main_rejects_bad_order():
    with pytest.raises(prospino.ProspinoError, match="order must be LO or NLO"):
        prospino._render_main("gg", 13000, "LOL")


def test_render_main_contains_process_and_energy():
    src = prospino._render_main("gg", 13000, "NLO")
    assert "final_state_in = 'gg'" in src
    assert "13000d0" in src
    assert "inlo = 1" in src  # NLO


def test_render_main_lo_flag():
    src = prospino._render_main("ss", 14000, "LO")
    assert "inlo = 0" in src


def test_source_not_vendored_gives_clear_error(tmp_path, monkeypatch):
    # Point PROSPINO_SRC at an empty dir so `_require_source` trips.
    monkeypatch.setattr(prospino, "PROSPINO_SRC", tmp_path / "empty")
    monkeypatch.setattr(prospino, "BINARY", tmp_path / "empty" / "prospino_2.run")
    with pytest.raises(prospino.ProspinoError, match="not vendored"):
        prospino._require_source()


def test_cli_help_runs():
    # Just confirm --help doesn't crash at import/parser construction time.
    with pytest.raises(SystemExit) as exc:
        prospino.main(["--help"])
    assert exc.value.code == 0


def test_cli_list_processes_prints_json(capsys):
    rc = prospino.main(["list-processes"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "gg" in payload


def test_cli_help_process_for_known_channel(capsys):
    rc = prospino.main(["help-process", "nn"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # Every entry must carry the fields an agent needs to call `run` correctly.
    assert payload["nn"]["desc"]
    assert payload["nn"]["masses"]
    assert "neutralinos" in payload["nn"]["ipart1"]
    assert "neutralinos" in payload["nn"]["ipart2"]


def test_cli_help_process_for_unknown_channel_nonzero(capsys):
    rc = prospino.main(["help-process", "not_a_process"])
    assert rc == 2


def test_parse_dat_reads_data_row(tmp_path):
    # Columns per Prospino 2.1: [0]=process, [1]=i1, [2]=i2, [3,4]=dummy,
    # [5]=scafac, [6]=m1, [7]=m2, [8]=angle, [9]=LO, [10]=relerr,
    # [11]=NLO, [12]=relerr, [13]=K, [14]=LO_ms, [15]=NLO_ms. Header follows
    # the data row in the real output.
    dat = tmp_path / "prospino.dat"
    dat.write_text(
        "gg  1  1   0.0   0.0   1.0   1750   1750   0.000   "
        "1.23e-4   0.01   2.04e-4   0.01   1.66   1.23e-4   2.04e-4\n"
        "    i1 i2  dummy0 dummy1 scafac  m1  m2  angle  LO[pb]  rel_error  "
        "NLO[pb]  rel_error  K  LO_ms[pb]  NLO_ms[pb]\n"
    )
    result = prospino._parse_dat(dat)
    assert result["lo"] == pytest.approx(1.23e-4)
    assert result["nlo"] == pytest.approx(2.04e-4)
    assert result["k_factor"] == pytest.approx(1.66)


def test_parse_dat_prefers_ms_columns_over_degenerate(tmp_path):
    # If the free-squark-mass columns (14, 15) are nonzero, they should win —
    # that's the SLHA-driven path agents use.
    dat = tmp_path / "prospino.dat"
    dat.write_text(
        "gg  1  1   0.0   0.0   1.0   1750   1750   0.000   "
        "1.00e-4   0.01   2.00e-4   0.01   2.00   5.55e-5   1.11e-4\n"
    )
    result = prospino._parse_dat(dat)
    assert result["lo"] == pytest.approx(5.55e-5)
    assert result["nlo"] == pytest.approx(1.11e-4)


def test_parse_dat_rejects_empty(tmp_path):
    dat = tmp_path / "prospino.dat"
    dat.write_text("# only comments\n")
    with pytest.raises(prospino.ProspinoError, match="no data row"):
        prospino._parse_dat(dat)


# ── Build-required golden test ─────────────────────────────────────────────

_BINARY_ABSENT = not prospino.BINARY.exists()


@pytest.mark.skipif(_BINARY_ABSENT, reason="prospino_2.run not built — vendor source first")
def test_golden_chargino_pair_14tev(tmp_path, monkeypatch):
    """LO σ(χ̃₁⁺ χ̃₁⁻) at √s=14 TeV with the vendored default SLHA.

    Uses Prospino's own `prospino.in.les_houches` sample (m(χ̃₁±) ≈ 178.8 GeV)
    so the result is a reproducible regression target for this exact build.
    Prospino's first vegas call converges to ~0.61 pb.
    """
    monkeypatch.chdir(tmp_path)  # scratch lives in CWD
    slha = prospino.PROSPINO_SRC / "prospino.in.les_houches"
    result = prospino.compute("nn", sqrts_gev=14000, order="LO", slha_path=slha, ipart1=5, ipart2=7)
    lo = result["xsec_pb"]["lo"]
    assert 0.55 < lo < 0.70, f"σ_LO(χ̃₁⁺ χ̃₁⁻, 14 TeV) = {lo} pb, expected ~0.61"
