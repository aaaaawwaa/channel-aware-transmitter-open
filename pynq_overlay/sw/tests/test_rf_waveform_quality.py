"""Chunk B model/UI waveform quality contracts: readable names, field visibility, normal/legacy mapping."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ui_model as M
from test_rf_touch_model import configured
from test_ui_view import view


def test_waveform_types_include_pulse_and_have_readable_labels():
    assert "pulse" in M.TX_WAVEFORM_TYPES
    assert len(M.TX_WAVEFORM_TYPES) == len(M.TX_WAVEFORM_NAMES)
    assert M.rf_waveform_name("pulse") == "pulse"
    assert M.rf_waveform_name(len(M.TX_WAVEFORM_TYPES)) == M.TX_WAVEFORM_TYPES[-1]
    assert M.rf_waveform_name("not-a-wave") is None


def test_rf_parameter_error_accepts_readable_names_and_zero_amplitude():
    assert M.rf_parameter_error("tx_waveform", "pulse") is None
    assert M.rf_parameter_error("tx_waveform", len(M.TX_WAVEFORM_TYPES)) is None
    assert M.rf_parameter_error("tx_waveform", "bogus") is not None
    assert M.rf_parameter_error("tx_amplitude", 0) is None
    assert M.rf_parameter_error("tx_amplitude", 2047) is None
    assert M.rf_parameter_error("tx_amplitude", 2048) is not None
    assert M.rf_parameter_error("tx_amplitude_pct", 0) is None
    assert M.rf_parameter_error("tx_amplitude_pct", 100) is None
    assert M.rf_parameter_error("tx_amplitude_pct", 101) is not None
    assert M.rf_parameter_error("duty_pct", 0) is None
    assert M.rf_parameter_error("duty_pct", 100) is None
    assert M.rf_parameter_error("duty_pct", 101) is not None


def test_rf_waveform_fields_contract_for_show_hide():
    assert "tx_frequency_hz" not in M.rf_waveform_fields("cw", advanced=False)
    assert "tx_amplitude_pct" in M.rf_waveform_fields("cw", advanced=False)
    assert "seed" in M.rf_waveform_fields("noise", advanced=False)
    assert "tone2_frequency_hz" in M.rf_waveform_fields("multitone", advanced=False)
    assert "tone2_amplitude_pct" in M.rf_waveform_fields("multitone", advanced=False)
    assert "chirp_f1_hz" in M.rf_waveform_fields("chirp", advanced=False)
    assert "duty_pct" in M.rf_waveform_fields("pulse", advanced=False)
    # Legacy mode still exposes cycle/code fields.
    assert "tx_cycles" in M.rf_waveform_fields("sine", advanced=True)
    assert "tx_amplitude" in M.rf_waveform_fields("sine", advanced=True)
    # Noise has no carrier frequency or cycle field in either mode.
    assert "tx_cycles" not in M.rf_waveform_fields("noise", advanced=True)
    assert "tx_amplitude" in M.rf_waveform_fields("noise", advanced=True)
    assert "seed" in M.rf_waveform_fields("noise", advanced=True)
    assert "tx_frequency_hz" not in M.rf_waveform_fields("noise", advanced=False)


def test_frequency_mode_maps_1mhz_at_60msps_to_17_cycles():
    app = configured()
    assert app.set_rf_parameter("tx_waveform", "sine") is None
    assert app.set_rf_parameter("tx_frequency_hz", 1_000_000) is None
    assert app.set_rf_parameter("tx_amplitude_pct", 50) is None
    args, why = app.rf_parameter_plan("load")
    assert why is None, why
    assert args["waveform"] == "sine"
    assert args["cycles"] == 17
    assert args["amplitude"] == round(2047 * 50 / 100)


def test_frequency_mode_rejects_below_step_and_nyquist():
    app = configured()
    app.set_rf_parameter("tx_waveform", "sine")
    app.set_rf_parameter("tx_amplitude_pct", 50)
    app.set_rf_parameter("tx_frequency_hz", 10_000)
    _args, why = app.rf_parameter_plan("load")
    assert why is not None and "step" in why

    app = configured()
    app.set_rf_parameter("tx_waveform", "sine")
    app.set_rf_parameter("tx_amplitude_pct", 50)
    app.set_rf_parameter("tx_frequency_hz", 30_000_000)
    _args, why = app.rf_parameter_plan("load")
    assert why is not None and "Nyquist" in why


def test_pulse_frequency_plan_includes_duty():
    app = configured()
    for name, value in (("tx_waveform", "pulse"), ("tx_frequency_hz", 1_000_000),
                        ("tx_amplitude_pct", 40), ("duty_pct", 25)):
        assert app.set_rf_parameter(name, value) is None, name
    args, why = app.rf_parameter_plan("load")
    assert why is None, why
    assert args["waveform"] == "pulse"
    assert args["cycles"] == 17
    assert args["amplitude"] == round(2047 * 40 / 100)
    assert args["duty_pct"] == 25


def test_multitone_frequency_plan_rejects_sum_over_2047():
    app = configured()
    for name, value in (("tx_waveform", "multitone"),
                        ("tx_frequency_hz", 1_000_000),
                        ("tx_amplitude_pct", 100),
                        ("tone2_frequency_hz", 500_000),
                        ("tone2_amplitude_pct", 100)):
        assert app.set_rf_parameter(name, value) is None, name
    _args, why = app.rf_parameter_plan("load")
    assert why is not None and "幅度" in why


def test_rf_init_ranges_are_lo_70mhz_to_2p1ghz_and_fs_61p44msps():
    app = M.UiApp()
    assert app.set_rf_parameter("lo_hz", 69_999_999) is not None
    assert app.set_rf_parameter("lo_hz", 70_000_000) is None
    assert app.set_rf_parameter("lo_hz", 2_100_000_000) is None
    assert app.set_rf_parameter("lo_hz", 2_100_000_001) is not None
    assert app.set_rf_parameter("sample_rate_hz", 61_440_000) is None
    assert app.set_rf_parameter("sample_rate_hz", 61_440_001) is not None


def test_transmit_ui_hides_and_shows_fields_by_waveform():
    app, _lcd, v = view(M.PAGE_TX)
    app.select_device("AD9361")
    app.tx_subpage = "transmit"
    app.set_rf_parameter("tx_waveform", "cw")
    app.set_rf_parameter("tx_amplitude_pct", 50)
    v.invalidate()
    v.render()
    assert not v.rf_normal_rows["tx_frequency_hz"].visible
    assert v.rf_normal_rows["tx_amplitude_pct"].visible

    app.set_rf_parameter("tx_waveform", "noise")
    app.set_rf_parameter("seed", 1)
    v.invalidate()
    v.render()
    assert v.rf_normal_rows["seed"].visible

    app.set_rf_parameter("tx_waveform", "multitone")
    app.set_rf_parameter("tx_frequency_hz", 1_000_000)
    app.set_rf_parameter("tx_amplitude_pct", 50)
    app.set_rf_parameter("tone2_frequency_hz", 500_000)
    app.set_rf_parameter("tone2_amplitude_pct", 20)
    v.invalidate()
    v.render()
    assert v.rf_normal_rows["tone2_frequency_hz"].visible
    assert v.rf_normal_rows["tone2_amplitude_pct"].visible

    app.set_rf_parameter("tx_waveform", "pulse")
    app.set_rf_parameter("duty_pct", 30)
    v.invalidate()
    v.render()
    assert v.rf_normal_rows["duty_pct"].visible
