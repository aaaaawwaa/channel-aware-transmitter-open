"""Unit tests for the finite QPSK/RECT snapshot receiver (no device)."""
from __future__ import annotations

import numpy as np
import pytest

from rf_iq_signal import map_bits_to_symbol, prbs7_bits
from rf_qpsk_receiver import QpskConfig, receive_qpsk, VERSION
from rf_rx_types import RxIqFrame, RxReadback

FS = 60_000_000
SPS = 20
SAMPLES = 4096


def _frame(symbols=None, *, offset=0, cfo_hz=0.0, phase=0.0, amplitude=500,
           samples=SAMPLES, sample_rate_hz=FS, ratio=0, corrupt_from=None,
           corrupted=False, random_payload=False, seed=39, clip_value=None,
           iq_order="IQ"):
    count = (samples - offset) // SPS
    if random_payload:
        rng = np.random.default_rng(seed)
        choices = np.array([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j])
        syms = rng.choice(choices, size=count)
    else:
        bits = prbs7_bits(seed, 2 * (count + 8))
        syms = np.array([complex(*map_bits_to_symbol("QPSK", bits[2 * i:2 * i + 2]))
                         for i in range(count)], dtype=complex)
    if symbols is not None:
        syms = np.asarray(symbols, dtype=complex)[:count]
    if corrupted and corrupt_from is not None:
        syms = syms.copy()
        syms[corrupt_from:] *= -1.0
    base = np.zeros(samples, dtype=complex)
    wave = np.repeat(syms, SPS)[:samples - offset]
    base[offset:offset + len(wave)] = wave
    n = np.arange(samples)
    z = base * np.exp(1j * (2 * np.pi * cfo_hz * n / sample_rate_hz + phase))
    iq = np.empty((samples, 2), dtype=np.int16)
    iq[:, 0] = np.round(z.real * amplitude)
    iq[:, 1] = np.round(z.imag * amplitude)
    if iq_order == "QI":
        iq = iq[:, ::-1].copy()
    if clip_value is not None:
        iq[0, 0] = clip_value
    return RxIqFrame(
        capture_id="qpsk-test-frame", payload=iq.astype("<i2").tobytes(),
        samples=samples, sample_rate_hz=sample_rate_hz, ratio=ratio,
        daemon_timestamp_ns=123456789, connection_generation=7,
        config_revision=3, request_id=11, release_id="release-test", op="rx",
        physical=True, pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=42.5)


def test_clean_snapshot_identity_sync_and_zero_ber():
    frame = _frame(offset=0, cfo_hz=0.0, phase=0.4)
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=39))
    assert result.reason == ""
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert result.capture_id == frame.capture_id
    assert result.release_id == frame.release_id
    assert result.config_revision == frame.config_revision
    assert result.connection_generation == frame.connection_generation
    assert result.completed_at_monotonic == frame.completed_at_monotonic
    assert result.mode == "QPSK" and result.shaping == "RECT"
    assert result.sample_rate_hz == FS and result.symbol_rate_hz == 3_000_000
    assert result.samples == SAMPLES
    assert result.bit_errors == 0 and result.compared_bits == 128 and result.ber == 0.0
    assert result.valid_symbols == 127
    assert result.alignment_bits == 126
    assert result.comparison_start_symbol == 63
    assert result.phase_ambiguity in (0, 1, 2, 3)
    assert result.evm_available is False and result.evm_rms is None
    assert result.algorithm_version == VERSION
    assert len(result.symbol_preview) <= 64 and len(result.bit_preview) <= 128


@pytest.mark.parametrize("seed", [3, 39, 127])
@pytest.mark.parametrize("offset", [0, 7, 19])
@pytest.mark.parametrize("phase", [0.0, 0.7, 2.2])
def test_seed_offset_and_initial_phase(seed, offset, phase):
    result = receive_qpsk(_frame(offset=offset, phase=phase, seed=seed),
                          QpskConfig(reference_prbs7_seed=seed))
    assert result.reason == ""
    assert result.reference_sync is True
    assert result.bit_errors == 0
    assert result.ber == 0.0
    assert result.timing_offset_samples == offset
    assert result.valid_symbols == 127
    assert result.compared_bits == 128


def test_all_twenty_timing_offsets():
    for offset in range(20):
        result = receive_qpsk(_frame(offset=offset, phase=0.7),
                              QpskConfig(reference_prbs7_seed=39))
        assert result.reason == ""
        assert result.timing_offset_samples == offset
        assert result.reference_sync is True
        assert result.bit_errors == 0 and result.ber == 0.0


@pytest.mark.parametrize("cfo", [100_000.0, 50_000.0, 0.0, -50_000.0, -100_000.0])
def test_positive_and_negative_cfo(cfo):
    result = receive_qpsk(_frame(cfo_hz=cfo, phase=0.3),
                          QpskConfig(reference_prbs7_seed=39))
    assert result.reason == ""
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert abs(result.cfo_hz - cfo) < 5000.0
    assert result.bit_errors == 0 and result.ber == 0.0


@pytest.mark.parametrize("seed", [3, 39, 127])
@pytest.mark.parametrize("offset", [0, 7, 19])
@pytest.mark.parametrize("phase", [0.0, 0.7, 2.2])
def test_qi_wire_order_matches_reference(seed, offset, phase):
    frame = _frame(seed=seed, offset=offset, phase=phase, iq_order="QI")
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=seed, iq_order="QI"))
    assert result.reason == ""
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert result.bit_errors == 0 and result.compared_bits == 128
    assert result.ber == 0.0 and result.valid_symbols == 127


def test_qi_is_not_silently_guessed_from_default_iq():
    frame = _frame(seed=39, phase=0.3, iq_order="QI")
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=39))
    assert result.reference_sync is not True
    assert result.ber is None


def test_invalid_iq_order_is_rejected():
    result = receive_qpsk(_frame(), QpskConfig(reference_prbs7_seed=39, iq_order="XX"))
    assert result.reason == "UNSUPPORTED"
    assert result.ber is None


def test_no_reference_has_no_fabricated_ber():
    result = receive_qpsk(_frame(phase=0.2), QpskConfig(reference_prbs7_seed=None))
    assert result.reason == "NO_REFERENCE"
    assert result.carrier_sync and result.timing_sync
    assert result.reference_sync is None
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None


def test_random_payload_is_reference_not_found_without_ber():
    result = receive_qpsk(_frame(random_payload=True, phase=0.2),
                          QpskConfig(reference_prbs7_seed=39))
    assert result.reason == "REFERENCE_NOT_FOUND"
    assert result.reference_sync is False
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None


def test_invalid_reference_seed_is_reference_not_found():
    result = receive_qpsk(_frame(), QpskConfig(reference_prbs7_seed=128))
    assert result.reason == "REFERENCE_NOT_FOUND"
    assert result.reference_sync is False
    assert result.ber is None


def test_low_energy_is_rejected():
    frame = _frame(amplitude=0)
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=39))
    assert result.reason == "LOW_ENERGY"
    assert result.ber is None


def test_clipping_is_rejected_before_ber():
    result = receive_qpsk(_frame(clip_value=2047), QpskConfig(reference_prbs7_seed=39))
    assert result.reason == "CLIPPED"
    assert result.bit_errors is None and result.ber is None


@pytest.mark.parametrize("kwargs", [
    {"sample_rate_hz": 6_000_000},
    {"ratio": 1},
    {"samples": 2048},
])
def test_input_domain_is_rejected(kwargs):
    result = receive_qpsk(_frame(**kwargs), QpskConfig(reference_prbs7_seed=39))
    assert result.reason == "INPUT_DOMAIN"
    assert result.ber is None


@pytest.mark.parametrize("config", [
    QpskConfig(reference_prbs7_seed=39, mode="BPSK"),
    QpskConfig(reference_prbs7_seed=39, shaping="RRC"),
    QpskConfig(reference_prbs7_seed=39, symbol_rate_hz=1_500_000),
])
def test_unsupported_acquisition_settings(config):
    result = receive_qpsk(_frame(), config)
    assert result.reason == "UNSUPPORTED"
    assert result.ber is None


def test_ber_excludes_alignment_and_uses_only_later_symbols():
    frame = _frame(corrupted=True, corrupt_from=64)
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=39))
    assert result.reference_sync is True
    assert result.alignment_bits == 126
    assert result.comparison_start_symbol == 63
    assert result.compared_bits == 2 * (result.valid_symbols - 63)
    assert result.bit_errors is not None and result.bit_errors > 0
    assert result.ber is not None and result.ber > 0.0


def test_previews_are_bounded_and_bits_are_binary():
    result = receive_qpsk(_frame(), QpskConfig(reference_prbs7_seed=39))
    assert 0 < len(result.symbol_preview) <= 64
    assert 0 < len(result.bit_preview) <= 128
    assert set(result.bit_preview) <= {0, 1}
    assert all(np.isfinite(x) and np.isfinite(y) for x, y in result.symbol_preview)


def test_noise_like_snapshot_never_reports_successful_ber():
    rng = np.random.default_rng(20261023)
    z = rng.normal(size=4096) + 1j * rng.normal(size=4096)
    iq = np.empty((4096, 2), dtype=np.int16)
    iq[:, 0] = np.round(z.real * 300)
    iq[:, 1] = np.round(z.imag * 300)
    frame = _frame(symbols=None)
    frame = RxIqFrame(
        capture_id=frame.capture_id,
        payload=iq.astype("<i2").tobytes(), samples=SAMPLES,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=0,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=False,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=0.0)
    result = receive_qpsk(frame, QpskConfig(reference_prbs7_seed=39))
    assert result.reference_sync is not True
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None
