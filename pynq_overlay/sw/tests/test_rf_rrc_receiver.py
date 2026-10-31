"""Unit tests for the BPSK/QPSK RRC (alpha=0.35, sps=20) receiver."""
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_iq_signal import _rrc_pulse, generate_digital_frame, map_bits_to_symbol, rrc_taps  # noqa: E402
from rf_rrc_receiver import (RrcConfig, RrcEnvelope, failed_rrc,  # noqa: E402
                             receive_rrc, GROUP_DELAY_SAMPLES,
                             TRANSITION_SYMBOLS, VALID_SYMBOL_START,
                             VALID_SYMBOL_END, COMPARISON_START_SYMBOL,
                             COMPARISON_END_SYMBOL, VERSION)
from rf_rx_types import RxIqFrame, RxReadback  # noqa: E402

FS = 60_000_000
SYM = 3_000_000
CAPTURE = 4096


def _tx(mode, seed, amplitude=60):
    meta, payload = generate_digital_frame(dict(
        mode=mode, filter="RRC", rolloff="0.35", seed=seed,
        symbol_rate_hz=SYM, sample_rate_hz=FS, amplitude=amplitude))
    return np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(float)


def _frame(*, mode="BPSK", seed=39, qpsk=False, offset=0, phase=0.0,
           cfo_hz=0.0, iq_order="IQ", noise=0.0, clip=None, samples=CAPTURE,
           sample_rate_hz=FS, ratio=0):
    arr = _tx(mode, seed)
    base = np.resize(arr, (samples, 2))
    n = np.arange(samples)
    z = base[:, 0] + 1j * base[:, 1]
    z = z * np.exp(1j * (2 * np.pi * cfo_hz * n / sample_rate_hz + phase))
    z = np.roll(z, offset)
    if noise:
        rng = np.random.default_rng(20261024)
        z = z + noise * (rng.normal(size=samples) + 1j * rng.normal(size=samples))
    iq = np.empty((samples, 2), dtype=np.int16)
    iq[:, 0] = np.round(z.real)
    iq[:, 1] = np.round(z.imag)
    if iq_order == "QI":
        iq = iq[:, ::-1].copy()
    if clip is not None:
        iq[0, 0] = clip
    return RxIqFrame(
        capture_id="rrc-frame", payload=iq.astype("<i2").tobytes(),
        samples=samples, sample_rate_hz=sample_rate_hz, ratio=ratio,
        daemon_timestamp_ns=1, connection_generation=2, config_revision=3,
        request_id=4, release_id="release", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=5.0)


def _cfg(mode="BPSK", seed=39, iq_order="IQ"):
    return RrcConfig(mode=mode, reference_prbs7_seed=seed, iq_order=iq_order)


@pytest.mark.parametrize("mode,bits", [("BPSK", 56), ("QPSK", 112)])
def test_clean_rrc_frame_has_actual_intervals_and_zero_ber(mode, bits):
    env = receive_rrc(_frame(mode=mode), _cfg(mode))
    assert isinstance(env, RrcEnvelope)
    assert env.algorithm_version == VERSION
    assert env.group_delay_samples == GROUP_DELAY_SAMPLES == 0
    assert env.transition_symbols == TRANSITION_SYMBOLS == 4
    assert env.valid_symbol_start == VALID_SYMBOL_START == 4
    assert env.valid_symbol_end == VALID_SYMBOL_END == 123
    assert env.comparison_start_symbol == COMPARISON_START_SYMBOL == 67
    assert env.comparison_end_symbol == COMPARISON_END_SYMBOL == 123
    result = env.result
    assert result.mode == mode and result.shaping == "RRC"
    assert result.sample_rate_hz == FS and result.symbol_rate_hz == SYM
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert result.reason == "" and result.valid_symbols == 119
    assert result.bit_errors == 0 and result.compared_bits == bits
    assert result.ber == 0.0
    assert result.evm_available is False and result.evm_rms is None
    assert result.algorithm_version == VERSION
    assert result.alignment_bits == 63 * (1 if mode == "BPSK" else 2)
    assert result.comparison_start_symbol == 67
    assert len(result.symbol_preview) <= 64 and len(result.bit_preview) <= 128


def test_actual_tx_taps_are_161_and_centered():
    taps = rrc_taps(20, 0.35)
    assert len(taps) == 161 and np.argmax(taps) == 80
    assert abs(float(np.sum(taps ** 2)) - 20.0) < 0.1


@pytest.mark.parametrize("seed", [1, 39, 127])
@pytest.mark.parametrize("offset", [0, 7, 19])
@pytest.mark.parametrize("phase", [0.0, 0.7])
@pytest.mark.parametrize("mode", ["BPSK", "QPSK"])
def test_seed_phase_and_sampling_offsets(mode, seed, offset, phase):
    env = receive_rrc(_frame(mode=mode, seed=seed, offset=offset, phase=phase),
                      _cfg(mode, seed))
    assert env.result.reason == ""
    assert env.result.timing_offset_samples == offset
    assert env.result.reference_sync is True
    assert env.result.ber == 0.0


@pytest.mark.parametrize("mode", ["BPSK", "QPSK"])
@pytest.mark.parametrize("cfo", [-80_000.0, -20_000.0, 20_000.0, 80_000.0])
def test_supported_cfo(mode, cfo):
    env = receive_rrc(_frame(mode=mode, cfo_hz=cfo, phase=0.2),
                      _cfg(mode))
    assert env.result.reason == ""
    assert env.result.reference_sync is True
    assert env.result.ber == 0.0
    assert abs(env.result.cfo_hz - cfo) <= 1500.0


@pytest.mark.parametrize("mode", ["BPSK", "QPSK"])
def test_qi_wire_order_is_explicit(mode):
    frame = _frame(mode=mode, iq_order="QI", offset=7, phase=0.3)
    env = receive_rrc(frame, _cfg(mode, iq_order="QI"))
    assert env.result.reason == "" and env.result.ber == 0.0
    # QPSK has a four-way constellation phase; a wrong wire order is observable.
    # BPSK's 0/pi ambiguity makes a pure QI-swapped frame indistinguishable after
    # arbitrary carrier-phase recovery, so only the explicit QPSK case is asserted.
    if mode == "QPSK":
        wrong = receive_rrc(frame, _cfg(mode, iq_order="IQ"))
        assert wrong.result.reason in ("REFERENCE_NOT_FOUND", "TIMING_NOT_SYNCED")
        assert wrong.result.ber is None


@pytest.mark.parametrize("mode", ["BPSK", "QPSK"])
def test_noise_and_moderate_impairment(mode):
    env = receive_rrc(_frame(mode=mode, noise=8.0), _cfg(mode))
    assert env.result.reason == ""
    assert env.result.reference_sync is True
    assert env.result.bit_errors is not None and env.result.compared_bits > 0
    assert 0.0 <= env.result.ber <= 0.1


def test_no_reference_and_invalid_reference_have_no_ber():
    env = receive_rrc(_frame(), RrcConfig(mode="BPSK", reference_prbs7_seed=None))
    assert env.result.reason == "NO_REFERENCE"
    assert env.result.reference_sync is False
    assert env.result.bit_errors is None and env.result.compared_bits is None
    assert env.result.ber is None
    # Random data has no PRBS alignment.
    rng = np.random.default_rng(9)
    sym = rng.choice([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j], size=127)
    arr = _rrc_pulse(np.stack([sym.real, sym.imag], axis=1), 20, 0.35)
    arr = arr / np.max(np.abs(arr)) * 60
    iq = np.empty((4096, 2), dtype=np.int16)
    tile = np.resize(arr.astype(complex), 4096)
    iq[:, 0] = np.round(tile.real)
    iq[:, 1] = np.round(tile.imag)
    frame = dataclasses.replace(_frame(), payload=iq.astype("<i2").tobytes())
    env = receive_rrc(frame, _cfg("BPSK"))
    assert env.result.reason in ("REFERENCE_NOT_FOUND", "TIMING_NOT_SYNCED")
    assert env.result.ber is None


@pytest.mark.parametrize("kwargs,reason", [
    (dict(clip=2047), "CLIPPED"),
    (dict(samples=2048), "INPUT_DOMAIN"),
    (dict(sample_rate_hz=30_000_000), "INPUT_DOMAIN"),
    (dict(ratio=1), "INPUT_DOMAIN"),
])
def test_domain_clipping_and_length_refusals(kwargs, reason):
    env = receive_rrc(_frame(**kwargs), _cfg())
    assert env.result.reason == reason
    assert env.result.bit_errors is None and env.result.ber is None


def test_low_energy_refusal():
    frame = _frame()
    zero_payload = bytes(len(frame.payload))
    frame = dataclasses.replace(frame, payload=zero_payload)
    env = receive_rrc(frame, _cfg())
    assert env.result.reason == "LOW_ENERGY"
    assert env.result.ber is None


@pytest.mark.parametrize("changes", [
    dict(mode="QAM16"), dict(rolloff=0.2), dict(sps=10),
    dict(sample_rate_hz=30_000_000), dict(symbol_rate_hz=1_500_000),
    dict(iq_order="XX"),
])
def test_unsupported_config_is_explicit(changes):
    with pytest.raises(ValueError):
        RrcConfig(**changes)


def test_failed_rrc_binds_frame_and_has_no_ber():
    frame = _frame()
    env = failed_rrc(frame, _cfg(), "CARRIER_NOT_SYNCED")
    assert env.result.reason == "CARRIER_NOT_SYNCED"
    assert env.result.capture_id == frame.capture_id
    assert env.result.release_id == frame.release_id
    assert env.result.bit_errors is None and env.result.compared_bits is None
    assert env.result.ber is None
    assert env.comparison_start_symbol is None and env.comparison_end_symbol is None
    unknown = failed_rrc(frame, _cfg(), "DEMOD_FAILED")
    assert unknown.result.reason == "NOT_RUN" and unknown.result.ber is None
