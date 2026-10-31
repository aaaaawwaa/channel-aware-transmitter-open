"""Independent-reference tests for finite QPSK/RECT snapshots (offline only)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import qpsk_reference as Q  # noqa: E402


def test_prbs7_vector_and_period():
    assert Q.prbs7_bits(1, 20) == [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 1,
                                   0, 0, 0, 0, 1, 0]
    p = Q.prbs7_bits(127, 254)
    assert p[:127] == p[127:]
    for bad in (0, 128, 1.0, True):
        with pytest.raises(Q.QpskReferenceError):
            Q.prbs7_bits(bad, 8)


def test_qpsk_mapping_is_independent_contract():
    assert Q.map_qpsk_bits((0, 0)) == -1 - 1j
    assert Q.map_qpsk_bits((0, 1)) == -1 + 1j
    assert Q.map_qpsk_bits((1, 0)) == 1 - 1j
    assert Q.map_qpsk_bits((1, 1)) == 1 + 1j
    syms = Q.qpsk_symbols(1, 4)
    assert [complex(v) for v in syms] == [-1 - 1j, -1 - 1j, -1 - 1j, 1 - 1j]
    bits = Q.qpsk_bits_from_symbols(syms)
    assert bits.tolist() == [0, 0, 0, 0, 0, 0, 1, 0]


@pytest.mark.parametrize("seed", [1, 3, 39, 127])
@pytest.mark.parametrize("phase_deg", [0.0, 45.0, 180.0])
def test_seed_and_initial_phase(seed, phase_deg):
    iq, meta, ref_seed = Q.make_fixture(seed=seed, phase_deg=phase_deg)
    r = Q.analyze_iq(iq, ref_seed, meta)
    assert r["reason"] == ""
    assert r["carrier_sync"] and r["timing_sync"] and r["reference_sync"]
    assert r["bit_errors"] == 0 and r["ber"] == 0.0
    assert r["cfo_hz"] == pytest.approx(0.0, abs=500.0)


@pytest.mark.parametrize("cfo", [-80_000.0, -20_000.0, 20_000.0, 80_000.0])
def test_positive_and_negative_cfo(cfo):
    iq, meta, seed = Q.make_fixture(seed=39, cfo_hz=cfo)
    r = Q.analyze_iq(iq, seed, meta)
    assert r["reason"] == ""
    assert r["reference_sync"] is True
    assert r["bit_errors"] == 0
    assert abs(r["cfo_hz"] - cfo) <= 1000.0


@pytest.mark.parametrize("tau", list(range(20)))
def test_all_integer_timing_offsets(tau):
    iq, meta, seed = Q.make_fixture(seed=39, timing_offset=tau)
    r = Q.analyze_iq(iq, seed, meta)
    assert r["reason"] == ""
    assert r["timing_offset_samples"] == tau
    assert r["bit_errors"] == 0


def test_alignment_is_separate_from_final_ber_interval():
    iq, meta, seed = Q.make_fixture(seed=39)
    r = Q.analyze_iq(iq, seed, meta)
    assert r["alignment_bits"] == 126
    assert r["comparison_start_symbol"] == 63
    assert r["valid_symbols"] == 127
    assert r["compared_bits"] == 2 * (127 - 63) == 128
    assert r["bit_errors"] == 0
    # Corrupt one final (not alignment) symbol by +90 degrees; only final BER moves.
    x = iq.astype(np.int16).copy()
    z = x[:, 0].astype(float) + 1j * x[:, 1].astype(float)
    z[70 * Q.SPS:(71) * Q.SPS] *= 1j
    x[:, 0] = np.clip(np.rint(z.real), -2048, 2047).astype(np.int16)
    x[:, 1] = np.clip(np.rint(z.imag), -2048, 2047).astype(np.int16)
    r2 = Q.analyze_iq(x, seed, meta)
    assert r2["reference_sync"] is True
    assert r2["alignment_bits"] == 126
    assert r2["comparison_start_symbol"] == 63
    assert r2["compared_bits"] == 128
    assert r2["bit_errors"] >= 1


def test_wrong_reference_never_zero_ber_success():
    iq, meta, seed = Q.make_fixture(seed=39)
    rng = np.random.default_rng(20261023)
    bad = rng.choice([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j], size=127)
    r = Q.analyze_iq(iq, seed, meta, reference_symbols=bad)
    assert not (r["reference_sync"] is True and r["ber"] == 0.0)
    if r["reason"] == "REFERENCE_NOT_FOUND":
        assert r["ber"] is None and r["compared_bits"] is None


def test_negative_fixtures_have_named_reasons_and_no_fake_ber():
    cases = [
        (dict(off=True), "NO_REFERENCE"),
        (dict(clip=True, amplitude=4000.0), "CLIPPED"),
        (dict(amplitude=0.1), "LOW_ENERGY"),
        (dict(metadata_override={"sample_rate_hz": 30_000_000}), "INPUT_DOMAIN"),
        (dict(length=2048), "INVALID_IQ"),
    ]
    for kwargs, reason in cases:
        iq, meta, seed = Q.make_fixture(seed=39, **kwargs)
        r = Q.analyze_iq(iq, seed, meta)
        assert r["reason"] == reason
        assert r["reference_sync"] is False
        assert r["ber"] is None
        assert r["compared_bits"] is None


def test_noise_is_tolerated_without_breaking_ber_interval():
    iq, meta, seed = Q.make_fixture(seed=39, noise_rms=3.0)
    r = Q.analyze_iq(iq, seed, meta)
    assert r["reason"] == ""
    assert r["reference_sync"] is True
    assert r["ber"] == 0.0
    assert r["compared_bits"] == 128


def _a_receive(iq, seed):
    try:
        from rf_qpsk_receiver import QpskConfig, receive_qpsk
        from rf_rx_types import RxIqFrame, RxReadback
    except Exception:
        pytest.skip("A rf_qpsk_receiver not ready")
    payload = np.asarray(iq, dtype="<i2").tobytes()
    frame = RxIqFrame(
        capture_id="ref-test", payload=payload, samples=4096,
        sample_rate_hz=60_000_000, ratio=0, daemon_timestamp_ns=1,
        connection_generation=1, config_revision=1, request_id=1,
        release_id="test-release", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    return receive_qpsk(frame, QpskConfig(reference_prbs7_seed=seed))


def test_a_receiver_clean_fixture_parity_if_available():
    iq, meta, seed = Q.make_fixture(seed=39, phase_deg=45.0, cfo_hz=20_000.0,
                                    timing_offset=7)
    ref = Q.analyze_iq(iq, seed, meta)
    a = _a_receive(iq, seed)
    assert a.reason == ""
    assert a.reference_sync is True
    assert a.bit_errors == 0 and a.ber == 0.0
    assert ref["reference_sync"] is True and ref["ber"] == 0.0
