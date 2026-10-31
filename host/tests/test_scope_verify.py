"""Offline checks for the symbol-rate measurement helper."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from siggen.digital_mod import generate_symbols, prbs7_bits, user_expected_symbols
from siggen.rrc import dac_scale, rrc_filter_symbols


SCRIPT = Path(__file__).parents[2] / "scripts" / "scope_verify.py"
spec = importlib.util.spec_from_file_location("scope_verify", SCRIPT)
scope_verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope_verify)


@pytest.mark.parametrize("rate", [10_000, 100_000, 1_000_000])
def test_symbol_period_from_iq_labels_tracks_symbol_rate(rate):
    xinc = 1.0 / 50_000_000
    samples_per_symbol = round(50_000_000 / rate)
    i_labels = np.repeat(np.arange(8) % 2, samples_per_symbol)
    q_labels = np.repeat((np.arange(8) // 2) % 2, samples_per_symbol)

    measured = scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, xinc)

    assert measured == pytest.approx(1.0 / rate, abs=xinc)


def test_symbol_period_uses_joint_iq_transitions():
    i_labels = np.repeat([0, 0, 1, 1], 5)
    q_labels = np.repeat([0, 1, 1, 0], 5)

    measured = scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, 2e-9)

    assert measured == pytest.approx(10e-9)


def test_symbol_period_ignores_short_transition_label_runs():
    run_lengths = [50, 3, 50, 4, 50, 2] + [50] * 9
    states = np.arange(15) % 2
    i_labels = np.repeat(states, run_lengths)
    q_labels = np.repeat((np.arange(15) // 2) % 2, run_lengths)

    measured = scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, 1e-9)

    assert measured == pytest.approx(50e-9, abs=1e-9)


def test_symbol_period_uses_shortest_stable_repeated_runs():
    run_lengths = [50, 100, 50, 150, 50, 100, 50, 200]
    i_labels = np.repeat(np.arange(8) % 2, run_lengths)
    q_labels = np.zeros(sum(run_lengths), dtype=int)

    measured = scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, 1e-9)

    assert measured == pytest.approx(50e-9, abs=1e-9)


def test_symbol_period_accepts_floor_ceil_phase_accumulator_intervals():
    i_labels = np.repeat([0, 1, 0, 1, 0, 1], [10, 11, 10, 11, 10, 11])
    q_labels = np.repeat([0, 0, 1, 1, 0, 0], [10, 11, 10, 11, 10, 11])

    measured = scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, 1e-6)

    assert measured == pytest.approx(10e-6, abs=1e-6)


@pytest.mark.parametrize(
    "i_labels, q_labels, xinc",
    [([], [], 1e-6), ([0, 1], [0], 1e-6), ([0, 1], [0, 1], 0),
     ([0, 1], [0, 1], float("nan"))],
)
def test_symbol_period_rejects_invalid_capture(i_labels, q_labels, xinc):
    assert scope_verify.symbol_period_from_iq_labels(i_labels, q_labels, xinc) is None


def test_digital_inlier_threshold_relaxes_for_1msym_capture():
    assert scope_verify.digital_inlier_threshold(100_000) == pytest.approx(0.99)
    assert scope_verify.digital_inlier_threshold(1_000_000) == pytest.approx(0.95)


def test_serial_reader_preserves_partial_and_multiple_lines():
    class FakeSerial:
        def __init__(self, *chunks):
            self.chunks = list(chunks)
            self.in_waiting = 1 if self.chunks else 0

        def read(self, _size):
            if not self.chunks:
                self.in_waiting = 0
                return b""
            chunk = self.chunks.pop(0)
            self.in_waiting = 1 if self.chunks else 0
            return chunk

    scope_verify.ser = FakeSerial(b"O")
    scope_verify._serial_pending.clear()
    assert scope_verify.read_serial_line(0.01) is None

    scope_verify.ser.chunks.append(b"K\r\nOK\r\n")
    scope_verify.ser.in_waiting = 1
    assert scope_verify.read_serial_line(0.1) == "OK"
    assert scope_verify.read_serial_line(0.1) == "OK"



@pytest.mark.parametrize("name, mode, rate, rolloff, time_scale",
                         scope_verify.rrc_scope_cases())
def test_rrc_scope_cases_are_deterministic(name, mode, rate, rolloff,
                                           time_scale):
    expected_cases = {
        ("QPSK RRC 10kSym/s", "QPSK", 10_000, "0.35"),
        ("QPSK RRC 100kSym/s", "QPSK", 100_000, "0.35"),
        ("QPSK RRC 1MSym/s", "QPSK", 1_000_000, "0.35"),
        ("QAM16 RRC 100kSym/s", "QAM16", 100_000, "0.35"),
        ("QAM64 RRC 100kSym/s", "QAM64", 100_000, "0.35"),
        ("QPSK RRC α=0.2 100kSym/s", "QPSK", 100_000, "0.2"),
        ("QPSK RRC α=0.5 100kSym/s", "QPSK", 100_000, "0.5"),
        ("BPSK RRC 100kSym/s", "BPSK", 100_000, "0.35"),
        ("QAM4 RRC 100kSym/s", "QAM4", 100_000, "0.35"),
        ("QAM16 RRC α=0.2 100kSym/s", "QAM16", 100_000, "0.2"),
        ("QAM16 RRC α=0.5 100kSym/s", "QAM16", 100_000, "0.5"),
        ("QAM64 RRC α=0.2 100kSym/s", "QAM64", 100_000, "0.2"),
        ("QAM64 RRC α=0.5 100kSym/s", "QAM64", 100_000, "0.5"),
    }
    assert (name, mode, rate, rolloff) in expected_cases
    assert time_scale > 0


def _synthetic_rrc_capture(symbol_count=127):
    i_symbols, q_symbols = generate_symbols("QPSK", symbol_count, seed=0x7F)
    i_tx, q_tx = rrc_filter_symbols(i_symbols, q_symbols)
    i_bytes = (dac_scale(i_tx).astype(np.int16) + 128).astype(np.uint8)
    q_bytes = (dac_scale(q_tx).astype(np.int16) + 128).astype(np.uint8)
    return i_bytes, q_bytes


def test_rrc_capture_uses_32_sample_total_delay_and_recovers_zero_ber():
    i_bytes, q_bytes = _synthetic_rrc_capture()

    assert scope_verify.offset_binary_to_signed(
        np.array([0, 127, 128, 255], dtype=np.uint8)).tolist() == [-128, -1, 0, 127]
    assert scope_verify.rrc_qpsk_ber(i_bytes, q_bytes) == 0.0
    assert scope_verify.check_rrc_qpsk_capture(i_bytes, q_bytes)


def test_rrc_capture_reports_corrupted_center_sample_as_ber_failure():
    i_bytes, q_bytes = _synthetic_rrc_capture()
    corrupted = i_bytes.copy()
    corrupted[200:204] = 0

    assert scope_verify.rrc_qpsk_ber(corrupted, q_bytes) > 0.0
    assert not scope_verify.check_rrc_qpsk_capture(corrupted, q_bytes)


# ---- known-sequence (USER/DDR payload) alignment and BER -------------------


def _prbs_payload(nbytes):
    return np.packbits(prbs7_bits(nbytes * 8)).tobytes()


def test_rrc_expected_waveform_from_symbols_matches_prbs_path():
    i_sym, q_sym = generate_symbols("QPSK", 32)
    assert np.array_equal(
        scope_verify.rrc_expected_waveform_from_symbols(i_sym, q_sym),
        scope_verify.rrc_expected_waveform("QPSK", 100_000, 32))


def test_align_known_sequence_finds_periodic_offset():
    exp_i, exp_q = user_expected_symbols("QPSK", _prbs_payload(32))
    shift = 7
    recovered = (np.roll(np.tile(exp_i.astype(float), 3), shift),
                 np.roll(np.tile(exp_q.astype(float), 3), shift))

    ber, offset, inverted = scope_verify.align_known_sequence(
        recovered, exp_i, exp_q, "QPSK")

    assert ber == 0.0
    assert offset == (-shift) % exp_i.size
    assert inverted is False


def test_align_known_sequence_detects_bit_errors():
    exp_i, exp_q = user_expected_symbols("QPSK", _prbs_payload(32))
    rec_i = np.tile(exp_i.astype(float), 3)
    rec_q = np.tile(exp_q.astype(float), 3)
    rec_i[20] = -rec_i[20]

    ber, _, _ = scope_verify.align_known_sequence(
        (rec_i, rec_q), exp_i, exp_q, "QPSK")

    assert ber > 0.0


def test_align_known_sequence_handles_global_polarity_inversion():
    exp_i, exp_q = user_expected_symbols("QPSK", _prbs_payload(32))
    recovered = (np.tile(-exp_i.astype(float), 2),
                 np.tile(-exp_q.astype(float), 2))

    ber, _, inverted = scope_verify.align_known_sequence(
        recovered, exp_i, exp_q, "QPSK")
    assert ber == 0.0 and inverted is True

    ber_direct, _, inv_direct = scope_verify.align_known_sequence(
        recovered, exp_i, exp_q, "QPSK", allow_polarity_inversion=False)
    assert ber_direct > 0.0 and inv_direct is False


def test_align_known_sequence_skips_loop_boundary_transients():
    exp_i, exp_q = user_expected_symbols("QPSK", _prbs_payload(32))
    period = exp_i.size
    rec_i = np.tile(exp_i.astype(float), 2)
    rec_q = np.tile(exp_q.astype(float), 2)
    for index in (0, 1, period - 1, period, period + 2):
        rec_i[index] = -rec_i[index]

    ber, _, _ = scope_verify.align_known_sequence(
        (rec_i, rec_q), exp_i, exp_q, "QPSK", trim_symbols=8)

    assert ber == 0.0


def test_align_known_sequence_qam16_uses_axis_levels():
    exp_i, exp_q = user_expected_symbols("QAM16", _prbs_payload(64))
    assert set(exp_i.tolist()) == {126, 42, -126, -42}
    recovered = (np.tile(exp_i.astype(float), 2),
                 np.tile(exp_q.astype(float), 2))

    ber, _, _ = scope_verify.align_known_sequence(recovered, exp_i, exp_q, "QAM16")

    assert ber == 0.0


def test_rrc_known_sequence_roundtrip_recovers_zero_ber():
    exp_i, exp_q = user_expected_symbols("QPSK", _prbs_payload(32))
    reps = 8
    i_tx, q_tx = rrc_filter_symbols(np.tile(exp_i, reps), np.tile(exp_q, reps))
    i_bytes = (dac_scale(i_tx).astype(np.int16) + 128).astype(np.uint8)
    q_bytes = (dac_scale(q_tx).astype(np.int16) + 128).astype(np.uint8)
    symrate = 100_000
    xinc = 1.0 / (4 * symrate)

    recovered = scope_verify.match_filter_capture(i_bytes, q_bytes, symrate, xinc)
    ber, _, _ = scope_verify.align_known_sequence(recovered, exp_i, exp_q, "QPSK")

    assert ber == 0.0



def test_multipath_frequency_response_notches_at_half_rate():
    """两点等增益多径的 |H(f)| = 2·(g/256)·|cos(π f d / fs)|, 在 f=fs/(2d) 处为零。"""
    fs = 50_000_000
    unit = 255.0 / 256.0
    mags = scope_verify.multipath_frequency_response(
        [255, 255, 0, 0], [0, 20, 0, 0], fs, [0.0, 100e3, 625e3, 1.25e6])

    assert mags[0] == pytest.approx(2 * unit)
    assert mags[1] == pytest.approx(2 * unit * np.cos(np.pi * 100e3 * 20 / fs))
    assert mags[2] == pytest.approx(2 * unit * np.cos(np.pi / 4))
    assert mags[3] == pytest.approx(0.0, abs=1e-9)


def test_multipath_frequency_response_single_tap_is_flat():
    mags = scope_verify.multipath_frequency_response(
        [255, 0, 0, 0], [0, 0, 0, 0], 50_000_000, [0.0, 100e3, 10e6])

    assert mags == pytest.approx([255.0 / 256.0] * 3)


def test_multipath_frequency_response_matches_time_domain_model():
    """与 host 金模型 multipath_fir 的冲激响应 DFT 一致(±2%: 金模型按 DAC LSB 取整)。"""
    from siggen import channel

    fs = 50_000_000
    gains, delays = [255, 128, 0, 0], [0, 20, 0, 0]
    freqs = [0.0, 250e3]
    expected = scope_verify.multipath_frequency_response(gains, delays, fs, freqs)

    impulse = [127] + [0] * 1023
    response = channel.multipath_fir(impulse, gains, delays)
    spectrum = np.abs(np.fft.rfft(response))
    for index, freq in enumerate(freqs):
        bin_index = int(round(freq * len(response) / fs))
        assert spectrum[bin_index] / 127.0 == pytest.approx(expected[index], rel=0.02)


def test_waveform_sigma_is_rms_of_centred_signal():
    t = np.arange(4096)
    sine = 50.0 * np.sin(2 * np.pi * t / 64.0) + 128.0

    assert scope_verify.waveform_sigma(sine) == pytest.approx(50.0 / np.sqrt(2), rel=1e-3)
    assert scope_verify.waveform_sigma(np.full(64, 200.0)) == pytest.approx(0.0, abs=1e-9)
