"""Tests for the offline QAM16/RECT receiver; no hardware or product integration."""
from __future__ import annotations

import numpy as np
import pytest

import qam16_reference as R
from rf_iq_signal import map_bits_to_symbol
from rf_qam16_receiver import QAM16_VERSION, Qam16Config, receive_qam16
from rf_rx_types import RxIqFrame, RxReadback

FS = 60_000_000


def _frame(**kwargs):
    wire, meta = R.make_frame_bytes(39, **kwargs)
    return RxIqFrame(
        capture_id="qam16-test", payload=wire, samples=R.REFERENCE_SAMPLES,
        sample_rate_hz=kwargs.get("sample_rate_hz", FS), ratio=0,
        daemon_timestamp_ns=1, connection_generation=3, config_revision=4,
        request_id=9, release_id="release-qam16", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=12.5), meta


def _domain_frame(*, samples=4096, fs=FS, ratio=0, payload_len=None):
    wire, _ = R.make_frame_bytes(39)
    if samples != 4096:
        wire = wire[:samples * 4]
    if payload_len is not None:
        wire = wire[:payload_len]
    return RxIqFrame(
        capture_id="qam16-domain", payload=wire, samples=samples,
        sample_rate_hz=fs, ratio=ratio, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="release-qam16", op="rx", physical=False,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)


def test_reference_covers_all_16_points_and_matches_tx_map():
    for bits, symbol in R.all_16_symbols().items():
        tx = map_bits_to_symbol("QAM16", bits)
        assert (tx[0], tx[1]) == (int(symbol.real), int(symbol.imag))
        assert R.bits_for_symbol(symbol) == bits
    levels = {int(s.real) for s in R.all_16_symbols().values()}
    assert levels == {-3, -1, 1, 3}


def test_clean_identity_sync_and_exact_denominator():
    frame, meta = _frame()
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == ""
    assert result.mode == "QAM16" and result.shaping == "RECT"
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert result.valid_symbols == 127
    assert result.alignment_bits == 252
    assert result.comparison_start_symbol == 63
    assert result.bit_errors == 0 and result.compared_bits == 256
    assert result.ber == 0.0
    assert result.evm_available is False and result.evm_rms is None
    assert result.algorithm_version == QAM16_VERSION
    assert result.sample_rate_hz == FS and result.symbol_rate_hz == 3_000_000
    assert result.symbols_available and result.ber_available
    assert len(result.symbol_preview) <= 64 and len(result.bit_preview) <= 128


@pytest.mark.parametrize("seed", [3, 39, 127])
@pytest.mark.parametrize("offset", [0, 1, 7, 19])
@pytest.mark.parametrize("phase", [0.0, 37.0, 91.0, 180.0, 271.0])
def test_data_amplitude_phase_and_timing(seed, offset, phase):
    wire, _ = R.make_frame_bytes(seed, phase_deg=phase, timing_samples=offset)
    frame = RxIqFrame(
        capture_id="qam16-param", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=seed))
    assert result.reason == ""
    assert result.reference_sync is True
    assert result.timing_offset_samples == offset
    assert result.bit_errors == 0 and result.compared_bits == 256
    assert result.ber == 0.0
    assert result.phase_ambiguity in (0, 1, 2, 3)


@pytest.mark.parametrize("cfo", [50_000.0, 20_000.0, 0.0, -20_000.0, -50_000.0])
def test_cfo_is_estimated_and_ber_is_zero(cfo):
    wire, _ = R.make_frame_bytes(39, cfo_hz=cfo, phase_deg=17.0)
    frame = RxIqFrame(
        capture_id="qam16-cfo", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == ""
    assert abs(result.cfo_hz - cfo) < 3000.0
    assert result.bit_errors == 0 and result.ber == 0.0


@pytest.mark.parametrize("iq_order", ["IQ", "QI"])
def test_explicit_wire_order(iq_order):
    wire, _ = R.make_frame_bytes(39, iq_order=iq_order, phase_deg=23.0)
    frame = RxIqFrame(
        capture_id="qam16-order", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39,
                                              iq_order=iq_order))
    assert result.reason == ""
    assert result.bit_errors == 0 and result.ber == 0.0


def test_all_20_timing_offsets_are_recovered():
    for offset in range(20):
        wire, _ = R.make_frame_bytes(39, timing_samples=offset)
        frame = RxIqFrame(
            capture_id="qam16-offset", payload=wire, samples=4096,
            sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
            connection_generation=1, config_revision=1, request_id=1,
            release_id="r", op="rx", physical=True,
            pre_readback=RxReadback(io_ok=True),
            post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
        result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
        assert result.reason == ""
        assert result.timing_offset_samples == offset
        assert result.bit_errors == 0 and result.ber == 0.0


def test_all_16_points_appear_in_reference_and_decode():
    seed = next(s for s in range(1, 128)
                if len({R.bits_for_symbol(int(np.real(v)) + 1j * int(np.imag(v)))
                        for v in R.reference_symbols(s)}) == 16)
    wire, _ = R.make_frame_bytes(seed, phase_deg=13.0)
    frame = RxIqFrame(
        capture_id="qam16-16", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=seed))
    assert result.reason == ""
    assert result.bit_errors == 0 and result.compared_bits == 256
    assert result.ber == 0.0


def test_nonzero_ber_is_retained_without_deleting_bits():
    wire, _ = R.make_frame_bytes(39, corrupt_from=70)
    frame = RxIqFrame(
        capture_id="qam16-neg", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == ""
    assert result.reference_sync is True
    assert result.valid_symbols == 127
    assert result.compared_bits == 256
    assert result.bit_errors is not None and result.bit_errors > 0
    assert result.ber is not None and result.ber > 0.0


def test_wrong_reference_has_no_fabricated_ber():
    rng = np.random.default_rng(20261024)
    choices = np.array(list(R.all_16_symbols().values()), dtype=complex)
    syms = rng.choice(choices, size=128)
    samples = np.resize(np.repeat(syms, R.SPS), R.REFERENCE_SAMPLES)
    iq = np.empty((len(samples), 2), dtype=np.int16)
    iq[:, 0] = np.rint(samples.real * 10.0).astype(np.int16)
    iq[:, 1] = np.rint(samples.imag * 10.0).astype(np.int16)
    frame = RxIqFrame(
        capture_id="qam16-wrong", payload=iq.astype("<i2").tobytes(),
        samples=R.REFERENCE_SAMPLES,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == "REFERENCE_NOT_FOUND"
    assert result.reference_sync is False
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None


def test_no_reference_has_no_ber():
    frame, _ = _frame()
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=None))
    assert result.reason == "NO_REFERENCE"
    assert result.reference_sync is False
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None


def test_no_signal_and_clipping_fail_without_ber():
    frame, _ = _frame(amplitude=0)
    no_signal = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert no_signal.reason == "LOW_ENERGY"
    assert no_signal.bit_errors is None and no_signal.ber is None
    clipped, _ = _frame(clipped=True)
    result = receive_qam16(clipped, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == "CLIPPED"
    assert result.bit_errors is None and result.ber is None


@pytest.mark.parametrize("kwargs", [
    {"fs": 6_000_000}, {"ratio": 1}, {"samples": 2048},
])
def test_out_of_domain_fails_without_ber(kwargs):
    result = receive_qam16(_domain_frame(**kwargs),
                           Qam16Config(reference_prbs7_seed=39))
    assert result.reason == "INPUT_DOMAIN"
    assert result.bit_errors is None and result.ber is None


@pytest.mark.parametrize("config", [
    Qam16Config(reference_prbs7_seed=39, mode="QPSK"),
    Qam16Config(reference_prbs7_seed=39, shaping="RRC"),
    Qam16Config(reference_prbs7_seed=39, symbol_rate_hz=1_500_000),
    Qam16Config(reference_prbs7_seed=39, iq_order="XX"),
])
def test_unsupported_config_fails_without_ber(config):
    frame, _ = _frame()
    result = receive_qam16(frame, config)
    assert result.reason == "UNSUPPORTED"
    assert result.bit_errors is None and result.ber is None


def test_qi_is_not_silently_guessed_from_iq_default():
    frame, _ = _frame(iq_order="QI", phase_deg=11.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reference_sync is not True
    assert result.ber is None



@pytest.mark.parametrize("amplitude", [10, 30, 60, 200])
def test_amplitude_scale_is_normalized(amplitude):
    wire, _ = R.make_frame_bytes(39, amplitude=amplitude, phase_deg=33.0)
    frame = RxIqFrame(
        capture_id="qam16-amp", payload=wire, samples=4096,
        sample_rate_hz=FS, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="r", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert result.reason == ""
    assert result.bit_errors == 0 and result.compared_bits == 256
    assert result.ber == 0.0

def test_previews_are_bounded_and_binary():
    frame, _ = _frame()
    result = receive_qam16(frame, Qam16Config(reference_prbs7_seed=39))
    assert 0 < len(result.symbol_preview) <= 64
    assert 0 < len(result.bit_preview) <= 128
    assert set(result.bit_preview) <= {0, 1}
    assert all(np.isfinite(x) and np.isfinite(y) for x, y in result.symbol_preview)

