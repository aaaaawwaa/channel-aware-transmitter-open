import numpy as np
import pytest

import scripts.scope_verify as scope_verify
from scripts.scope_verify import (
    _align_rrc_capture,
    match_filter_capture,
    rrc_expected_waveform,
    rrc_eye_margin,
    rrc_scope_cases,
    rrc_spectrum_rolloff,
    rrc_symbol_ber,
)
from siggen.digital_mod import generate_symbols
from siggen.rrc import rrc_coefficients, rrc_filter_symbols


def test_scope_capture_to_digital_compensates_acm2108_inversion():
    capture = np.array([0, 128, 255], dtype=np.uint8)

    actual = scope_verify.scope_capture_to_digital(capture)

    assert np.array_equal(actual, np.array([128, 0, -127], dtype=np.int16))


def test_rrc_expected_waveform_has_four_samples_per_symbol():
    waveform = rrc_expected_waveform("QPSK", 100000, 32)
    assert waveform.shape == (2, 32 * 4 + 32)


def test_rrc_expected_waveform_uses_selected_rolloff():
    i_symbols, q_symbols = generate_symbols("QPSK", 32)
    expected = np.vstack(rrc_filter_symbols(
        i_symbols, q_symbols, coeffs=rrc_coefficients(alpha=0.2)))
    actual = rrc_expected_waveform("QPSK", 100000, 32, rolloff="0.2")
    other = rrc_expected_waveform("QPSK", 100000, 32, rolloff="0.5")

    assert np.array_equal(actual, expected)
    assert not np.array_equal(actual, other)


def test_match_filter_capture_uses_selected_rolloff():
    expected = rrc_expected_waveform("QPSK", 100000, 127, rolloff="0.5")
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000, rolloff="0.5")

    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_match_filter_capture_recovers_known_qpsk_sequence():
    expected = rrc_expected_waveform("QPSK", 100000, 127)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000
    )
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_match_filter_capture_removes_scope_dc_offset():
    expected = rrc_expected_waveform("QPSK", 100000, 127)
    i_capture = expected[0].astype(np.float64) / 32767.0 * 80.0 + 128.0
    q_capture = expected[1].astype(np.float64) / 32767.0 * 80.0 + 130.0
    recovered = match_filter_capture(
        i_capture, q_capture, 100000, xinc=1 / 400_000
    )
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_rrc_ber_accepts_common_scope_polarity_inversion():
    expected = rrc_expected_waveform("QPSK", 100000, 32)
    recovered = match_filter_capture(
        -expected[0], -expected[1], 100000, xinc=1 / 400_000
    )
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_rrc_alignment_finds_cyclic_prbs_start():
    i_symbols, q_symbols = generate_symbols("QPSK", 127)
    offset = 17
    i_symbols = np.concatenate((i_symbols[offset:], i_symbols[:offset]))
    q_symbols = np.concatenate((q_symbols[offset:], q_symbols[:offset]))
    capture = rrc_filter_symbols(i_symbols, q_symbols)
    expected = rrc_expected_waveform("QPSK", 100000, 127)

    best = _align_rrc_capture(
        capture[0], capture[1], 100000, xinc=1 / 400_000, expected=expected
    )

    assert best is not None
    assert best[0] == 0.0


def test_rrc_alignment_searches_all_four_polyphases():
    i_symbols, q_symbols = generate_symbols("QPSK", 127)
    i_wave, q_wave = rrc_filter_symbols(
        np.tile(i_symbols, 3), np.tile(q_symbols, 3)
    )
    sample_phase = 2
    capture = (i_wave[508 + sample_phase:508 + sample_phase + 800],
               q_wave[508 + sample_phase:508 + sample_phase + 800])
    expected = rrc_expected_waveform("QPSK", 10000, 127)

    best = _align_rrc_capture(
        capture[0], capture[1], 10000, xinc=1 / 40000, expected=expected
    )

    assert best is not None
    assert best[0] == 0.0


def test_rrc_spectrum_rolloff_is_low_outside_nominal_band():
    waveform = rrc_expected_waveform("QPSK", 100000, 127)
    ratio = rrc_spectrum_rolloff(waveform[0], waveform[1], 100000, 1 / 400_000)
    assert ratio is not None
def test_rrc_eye_margin_does_not_overflow_int16_minimum():
    samples = np.full(127, -32768, dtype=np.int16)
    assert rrc_eye_margin(samples, samples) == 32768.0


def test_rect_qpsk_does_not_meet_rrc_rolloff_threshold():
    i_symbols, q_symbols = generate_symbols("QPSK", 127)
    i_capture = np.repeat(i_symbols, 4)
    q_capture = np.repeat(q_symbols, 4)
    ratio = rrc_spectrum_rolloff(i_capture, q_capture, 100000, 1 / 400000)
    assert ratio is not None
    assert ratio >= 0.2


def test_center_sample_labels_qam16_quantizes_to_nearest_level():
    i = np.array([100.0, 50.0, -50.0, -100.0, 126.0, 0.0])
    q = np.array([-100.0, -50.0, 50.0, 100.0, -126.0, 0.0])
    li, lq = scope_verify.center_sample_labels(i, q, "QAM16")
    assert np.array_equal(li, np.array([126, 42, -42, -126, 126, -42]))
    assert np.array_equal(lq, np.array([-126, -42, 42, 126, -126, -42]))


def test_center_sample_labels_qam64_quantizes_to_nearest_level():
    i = np.array([100.0, 60.0, 20.0, 0.0, -100.0])
    li, _ = scope_verify.center_sample_labels(i, np.zeros(5), "QAM64")
    assert np.array_equal(li, np.array([90, 54, 18, -18, -90]))


def test_center_sample_labels_qpsk_matches_legacy_sign_quantization():
    i = np.array([100.0, -1.0, 0.0, -200.0])
    q = np.array([-50.0, 50.0, 0.0, 300.0])
    li, lq = scope_verify.center_sample_labels(i, q, "QPSK")
    li_legacy, lq_legacy = scope_verify.center_sample_qpsk_labels(i, q)
    assert np.array_equal(li, li_legacy)
    assert np.array_equal(lq, lq_legacy)


def test_center_sample_labels_bpsk_quantizes_i_and_zeroes_q():
    i = np.array([100.0, -50.0, 0.0, -200.0, 50.0])
    q = np.array([3.0, -2.0, 1.0, 4.0, -1.0])
    li, lq = scope_verify.center_sample_labels(i, q, "BPSK")
    assert np.array_equal(li, np.array([127, -127, 127, -127, 127], dtype=np.int16))
    assert np.array_equal(lq, np.zeros(5, dtype=np.int16))


def test_center_sample_labels_qam4_matches_qpsk():
    i = np.array([100.0, -1.0, 0.0, -200.0])
    q = np.array([-50.0, 50.0, 0.0, 300.0])
    li, lq = scope_verify.center_sample_labels(i, q, "QAM4")
    li_qpsk, lq_qpsk = scope_verify.center_sample_labels(i, q, "QPSK")
    assert np.array_equal(li, li_qpsk)
    assert np.array_equal(lq, lq_qpsk)


def test_center_sample_labels_rejects_unknown_mode():
    with pytest.raises(ValueError):
        scope_verify.center_sample_labels(np.zeros(4), np.zeros(4), "FOO")


def test_rrc_qam16_symbol_ber_zero_with_expected_reference():
    expected = rrc_expected_waveform("QAM16", 100000, 127)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="QAM16") == 0.0


def test_rrc_qam64_symbol_ber_zero_with_expected_reference():
    expected = rrc_expected_waveform("QAM64", 100000, 127)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="QAM64") == 0.0


def test_rrc_qam16_alignment_finds_cyclic_start():
    expected = rrc_expected_waveform("QAM16", 100000, 127)
    best = _align_rrc_capture(
        expected[0], expected[1], 100000, 1 / 400_000, expected, mode="QAM16")
    assert best is not None
    assert best[0] == 0.0


def test_rrc_scope_cases_include_all_modes_rolloffs_and_qam_modes():
    cases = rrc_scope_cases()
    assert cases == (
        ("QPSK RRC 10kSym/s", "QPSK", 10_000, "0.35", 2e-3),
        ("QPSK RRC 100kSym/s", "QPSK", 100_000, "0.35", 200e-6),
        ("QPSK RRC 1MSym/s", "QPSK", 1_000_000, "0.35", 20e-6),
        ("QAM16 RRC 100kSym/s", "QAM16", 100_000, "0.35", 200e-6),
        ("QAM64 RRC 100kSym/s", "QAM64", 100_000, "0.35", 200e-6),
        ("QPSK RRC α=0.2 100kSym/s", "QPSK", 100_000, "0.2", 200e-6),
        ("QPSK RRC α=0.5 100kSym/s", "QPSK", 100_000, "0.5", 200e-6),
        ("BPSK RRC 100kSym/s", "BPSK", 100_000, "0.35", 200e-6),
        ("QAM4 RRC 100kSym/s", "QAM4", 100_000, "0.35", 200e-6),
        ("QAM16 RRC α=0.2 100kSym/s", "QAM16", 100_000, "0.2", 200e-6),
        ("QAM16 RRC α=0.5 100kSym/s", "QAM16", 100_000, "0.5", 200e-6),
        ("QAM64 RRC α=0.2 100kSym/s", "QAM64", 100_000, "0.2", 200e-6),
        ("QAM64 RRC α=0.5 100kSym/s", "QAM64", 100_000, "0.5", 200e-6),
    )


def test_rrc_bpsk_symbol_ber_zero_with_expected_reference():
    expected = rrc_expected_waveform("BPSK", 100000, 127)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="BPSK") == 0.0


def test_rrc_qam4_symbol_ber_zero_with_expected_reference():
    expected = rrc_expected_waveform("QAM4", 100000, 127)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="QAM4") == 0.0


@pytest.mark.parametrize("alpha", ["0.2", "0.5"])
def test_rrc_qam16_symbol_ber_zero_with_alpha_variants(alpha):
    expected = rrc_expected_waveform("QAM16", 100000, 127, rolloff=alpha)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000, rolloff=alpha)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="QAM16") == 0.0


@pytest.mark.parametrize("alpha", ["0.2", "0.5"])
def test_rrc_qam64_symbol_ber_zero_with_alpha_variants(alpha):
    expected = rrc_expected_waveform("QAM64", 100000, 127, rolloff=alpha)
    recovered = match_filter_capture(
        expected[0], expected[1], 100000, xinc=1 / 400_000, rolloff=alpha)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8, mode="QAM64") == 0.0


def test_rrc_eye_margin_bpsk_ignores_constant_q_axis():
    i = np.full(32, 8000.0)
    i[:16] = -8000.0
    q = np.zeros(32)
    assert rrc_eye_margin(i, q, mode="BPSK") > 0.0
