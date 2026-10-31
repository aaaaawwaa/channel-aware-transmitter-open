"""Implementation-side tests for the QAM64/RECT receiver.

The ideal waveform/reference in this file is an independent local implementation
of the frozen TX contract (PRBS7, the eight-level axis table, RECT/SPS20, 4096
cyclic).  It does not import the receiver under test.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_qam64_receiver as R
from rf_rx_types import RxIqFrame, RxReadback

LEVEL_TABLE = (7, 5, 1, 3, -7, -5, -1, -3)


def bits(seed, count):
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return np.array(out, dtype=int)


def axis_level(three):
    return LEVEL_TABLE[(int(three[0]) << 2) | (int(three[1]) << 1) | int(three[2])]


def symbols(seed):
    b = bits(seed, 6 * 127)
    return np.array([complex(axis_level(b[6 * i:6 * i + 3]),
                             axis_level(b[6 * i + 3:6 * i + 6]))
                     for i in range(127)], dtype=complex)


def frame(seed=39, *, iq_order="IQ", offset=0, phase=0.0, cfo=0.0,
          samples=4096, corrupt_comparison=False, corrupt_alignment=False,
          clip=False, zeros=False, amplitude=180.0):
    sym = symbols(seed).copy()
    if corrupt_comparison:
        sym[63:] = -sym[63:]
    if corrupt_alignment:
        rng = np.random.default_rng(20261025)
        choices = np.array([complex(i, q) for i in LEVEL_TABLE
                            for q in LEVEL_TABLE])
        sym[:63] = rng.choice(choices, size=63)
    base = np.resize(np.repeat(sym, 20), 4096)
    n = np.arange(4096)
    z = base * amplitude * np.exp(1j * (phase + 2 * np.pi * cfo * n / 60_000_000))
    if offset:
        z = np.roll(z, offset)
    if zeros:
        z = np.zeros_like(z)
    wire = np.empty((4096, 2), dtype="<i2")
    wire[:, 0] = np.rint(z.real)
    wire[:, 1] = np.rint(z.imag)
    if clip:
        wire[0, 0] = 2047
    if iq_order == "QI":
        wire = wire[:, ::-1].copy()
    wire = wire.astype("<i2")
    payload = wire.tobytes()
    if samples != 4096:
        payload = payload[:samples * 4]
    return RxIqFrame(
        capture_id="qam64-test", payload=payload, samples=samples,
        sample_rate_hz=60_000_000, ratio=0,
        daemon_timestamp_ns=1, connection_generation=2, config_revision=5,
        request_id=9, release_id="cd76818a", op="rx.capture", physical=False,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=100.0)


def config(seed=39, **updates):
    values = dict(reference_prbs7_seed=seed, mode="QAM64", shaping="RECT",
                  symbol_rate_hz=3_000_000, iq_order="IQ")
    values.update(updates)
    return R.Qam64Config(**values)


@pytest.mark.parametrize("iq_order", ["IQ", "QI"])
@pytest.mark.parametrize("offset", [0, 7, 19])
@pytest.mark.parametrize("phase,cfo", [(0.0, 0.0), (0.7, 40000),
                                       (np.pi / 2, -70000), (1.1, 80000)])
def test_clean_qam64_syncs_and_compares_only_last_64_symbols(iq_order, offset,
                                                             phase, cfo):
    r = R.receive_qam64(frame(39, iq_order=iq_order, offset=offset,
                              phase=phase, cfo=cfo), config(iq_order=iq_order))
    assert r.reason == ""
    assert r.carrier_sync and r.timing_sync and r.reference_sync
    assert r.valid_symbols == 127
    assert r.alignment_bits == 378
    assert r.comparison_start_symbol == 63
    assert r.bit_errors == 0
    assert r.compared_bits == 384
    assert r.ber == 0.0
    assert r.evm_available is False and r.evm_rms is None
    assert len(r.symbol_preview) <= 64 and len(r.bit_preview) <= 128


def test_comparison_region_damage_does_not_realtime_or_resync():
    """First 63 symbols keep sync; damaged last 64 must only produce BER."""
    r = R.receive_qam64(frame(39, corrupt_comparison=True), config())
    assert r.reason == ""
    assert r.reference_sync is True
    assert r.timing_offset_samples is not None
    assert r.bit_errors is not None and r.bit_errors > 0
    assert r.compared_bits == 384
    assert r.ber == r.bit_errors / 384


def test_wrong_reference_waveform_is_named_without_fabricated_ber():
    r = R.receive_qam64(frame(39, corrupt_alignment=True), config())
    assert r.reason == "REFERENCE_NOT_FOUND"
    assert r.reference_sync is not True
    assert r.bit_errors is None and r.compared_bits is None and r.ber is None


def test_no_signal_and_clipping_are_named():
    low = R.receive_qam64(frame(zeros=True), config())
    assert low.reason == "LOW_ENERGY"
    assert low.bit_errors is None and low.compared_bits is None
    clip = R.receive_qam64(frame(clip=True), config())
    assert clip.reason == "CLIPPED"
    assert clip.bit_errors is None and clip.compared_bits is None


def test_no_reference_and_input_domain_and_unsupported_are_named():
    no_ref = R.receive_qam64(frame(), config(seed=None))
    assert no_ref.reason == "NO_REFERENCE"
    assert no_ref.bit_errors is None
    domain = R.receive_qam64(frame(samples=4095), config())
    assert domain.reason == "INPUT_DOMAIN"
    bad_mode = R.receive_qam64(frame(), config(mode="QAM16"))
    assert bad_mode.reason == "UNSUPPORTED"
    bad_shaping = R.receive_qam64(frame(), config(shaping="RRC"))
    assert bad_shaping.reason == "UNSUPPORTED"


def test_seed_difference_alone_is_not_a_reference_error():
    # Different PRBS7 seeds are cyclic phases of the same m-sequence.  The
    # receiver may find the matching phase; a seed difference alone is not named
    # as a reference error and never invents a BER.
    r = R.receive_qam64(frame(39), config(seed=40))
    assert r.reason == ""
    assert r.reference_sync is True
    assert r.bit_errors == 0 and r.compared_bits == 384


def test_host_performance_is_recorded_only():
    f = frame(39)
    cfg = config()
    times = []
    for _ in range(12):
        started = time.perf_counter()
        r = R.receive_qam64(f, cfg)
        times.append(time.perf_counter() - started)
        assert r.bit_errors == 0
    ordered = sorted(times)
    print("HOST_QAM64_PERF p50=%.6fs p95=%.6fs max=%.6fs" % (
        ordered[len(ordered) // 2], ordered[int(len(ordered) * 0.95)],
        max(ordered)))
    assert all(t < 10.0 for t in times)



def test_all_64_points_and_axis_inverse_mapping():
    for value in range(64):
        trio = ((value >> 5) & 1, (value >> 4) & 1, (value >> 3) & 1)
        qtrio = ((value >> 2) & 1, (value >> 1) & 1, value & 1)
        sym = complex(axis_level(trio), axis_level(qtrio))
        got = R._bits_from_symbols([sym]).tolist()
        assert got == [int(b) for b in (trio + qtrio)], value
        assert R._decision_axis(sym.real) == axis_level(trio)
        assert R._decision_axis(sym.imag) == axis_level(qtrio)


def test_phase_ambiguity_is_bounded_and_clean_frame_has_no_ber():
    r = R.receive_qam64(frame(39), config())
    assert r.phase_ambiguity in (0, 1, 2, 3)
    assert r.reason == "" and r.bit_errors == 0 and r.compared_bits == 384


# ---------------------------------------------------------------------------
# B-line independent verifier.  Expected waveforms/mapping/corruption counts
# come from qam64_reference, not from the receiver under test.  Kept below the
# implementation-side tests so those remain independently useful.
# ---------------------------------------------------------------------------
import qam64_reference as REF
from rf_iq_signal import map_bits_to_symbol
from rf_qam64_pipeline import (Qam64Envelope, analyze_qam64,
                               freeze_qam64_demod_config)
from rf_qam64_receiver import QAM64_VERSION, Qam64Config, receive_qam64


FS = 60_000_000


def b_frame(**kwargs):
    wire, meta = REF.make_frame_bytes(39, **kwargs)
    return b_wrap(wire, meta, **kwargs), meta


def b_wrap(wire, meta, **kwargs):
    return RxIqFrame(
        capture_id="qam64-test-b", payload=wire,
        samples=kwargs.get("samples", REF.REFERENCE_SAMPLES),
        sample_rate_hz=kwargs.get("sample_rate_hz", FS),
        ratio=kwargs.get("ratio", 0), daemon_timestamp_ns=1,
        connection_generation=3, config_revision=4, request_id=9,
        release_id="release-qam64", op="rx", physical=True,
        pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=12.5)


def b_config(**kwargs):
    return Qam64Config(reference_prbs7_seed=kwargs.pop("seed", 39), **kwargs)


def test_b_reference_covers_all_64_points_and_matches_frozen_tx_table():
    points = REF.all_64_symbols()
    assert len(points) == 64
    assert {int(v.real) for v in points.values()} == set(REF.LEVELS)
    assert {int(v.imag) for v in points.values()} == set(REF.LEVELS)
    for bits, symbol in points.items():
        tx = map_bits_to_symbol("QAM64", bits)
        assert (int(symbol.real), int(symbol.imag)) == (tx[0], tx[1])
        assert REF.bits_for_symbol(symbol) == bits


def test_b_clean_qi_and_iq_exact_384_bit_denominator():
    for order in ("IQ", "QI"):
        frame, meta = b_frame(iq_order=order, phase_deg=23.0)
        result = receive_qam64(frame, b_config(seed=39, iq_order=order))
        assert result.reason == ""
        assert result.mode == "QAM64" and result.shaping == "RECT"
        assert result.carrier_sync and result.timing_sync and result.reference_sync
        assert result.valid_symbols == 127
        assert result.alignment_bits == 378
        assert result.comparison_start_symbol == 63
        assert result.bit_errors == 0 and result.compared_bits == 384
        assert result.ber == 0.0
        assert result.evm_available is False and result.evm_rms is None
        assert result.algorithm_version == QAM64_VERSION
        assert result.sample_rate_hz == FS and result.symbol_rate_hz == 3_000_000
        assert result.symbols_available and result.ber_available
        assert meta["compared_bits"] == 384


@pytest.mark.parametrize("seed", [3, 39, 127])
@pytest.mark.parametrize("offset", [0, 1, 7, 19])
@pytest.mark.parametrize("phase", [0.0, 37.0, 91.0, 180.0, 271.0])
def test_b_seed_phase_timing_clean(seed, offset, phase):
    wire, _ = REF.make_frame_bytes(seed, phase_deg=phase, timing_samples=offset)
    result = receive_qam64(b_wrap(wire, {}), b_config(seed=seed))
    assert result.reason == ""
    assert result.reference_sync is True
    assert result.timing_offset_samples == offset
    assert result.bit_errors == 0 and result.compared_bits == 384
    assert result.ber == 0.0


@pytest.mark.parametrize("cfo", [50_000.0, 20_000.0, 0.0, -20_000.0, -50_000.0])
def test_b_cfo_estimated_and_clean(cfo):
    wire, _ = REF.make_frame_bytes(39, cfo_hz=cfo, phase_deg=17.0)
    result = receive_qam64(b_wrap(wire, {}), b_config(seed=39))
    assert result.reason == ""
    assert abs(result.cfo_hz - cfo) < 3000.0
    assert result.bit_errors == 0 and result.ber == 0.0


@pytest.mark.parametrize("noise", [1.0, 2.0])
def test_b_noise_fixture_remains_clean(noise):
    wire, _ = REF.make_frame_bytes(39, noise_rms=noise, phase_deg=11.0)
    result = receive_qam64(b_wrap(wire, {}), b_config(seed=39))
    assert result.reason == ""
    assert result.reference_sync is True
    assert result.bit_errors == 0 and result.compared_bits == 384


def test_b_amplitude_scaling_does_not_change_bits():
    for amplitude in (20, 60, 120):
        wire, _ = REF.make_frame_bytes(39, amplitude=amplitude, phase_deg=7.0)
        result = receive_qam64(b_wrap(wire, {}), b_config(seed=39))
        assert result.reason == ""
        assert result.bit_errors == 0 and result.compared_bits == 384


def test_b_artificial_comparison_errors_are_preserved_exactly_and_sync_stays():
    for start in (63, 70):
        wire, meta = REF.make_frame_bytes(39, corrupt_from=start)
        result = receive_qam64(b_wrap(wire, meta), b_config(seed=39))
        assert result.reason == ""
        assert result.carrier_sync and result.timing_sync and result.reference_sync
        assert result.valid_symbols == 127
        assert result.alignment_bits == 378
        assert result.comparison_start_symbol == 63
        assert result.compared_bits == 384
        assert meta["expected_errors"] is not None
        assert 0 < meta["expected_errors"] < 384
        assert result.bit_errors == meta["expected_errors"]
        assert result.ber == result.bit_errors / 384.0


def test_b_comparison_area_does_not_participate_in_synchronization():
    clean_wire, _ = REF.make_frame_bytes(39, phase_deg=31.0)
    clean = receive_qam64(b_wrap(clean_wire, {}), b_config(seed=39))
    bad_wire, _ = REF.make_frame_bytes(39, phase_deg=31.0, corrupt_from=63)
    bad = receive_qam64(b_wrap(bad_wire, {}), b_config(seed=39))
    assert bad.reference_sync is True and bad.timing_sync is True
    assert (bad.valid_symbols, bad.alignment_bits, bad.comparison_start_symbol) == \
        (clean.valid_symbols, clean.alignment_bits, clean.comparison_start_symbol)
    assert bad.bit_errors is not None and bad.bit_errors > 0
    assert bad.bit_errors != clean.bit_errors


def test_b_partial_comparison_exactness():
    wire, meta = REF.make_frame_bytes(39, corrupt_from=100)
    result = receive_qam64(b_wrap(wire, meta), b_config(seed=39))
    assert result.reference_sync is True
    assert result.bit_errors == meta["expected_errors"]


def test_b_true_mismatching_waveform_has_named_failure_and_no_ber():
    wire, meta = REF.make_frame_bytes(39, wrong_reference=True)
    result = receive_qam64(b_wrap(wire, meta), b_config(seed=39))
    assert result.reason == "REFERENCE_NOT_FOUND"
    assert result.reference_sync is not True
    assert result.bit_errors is None and result.compared_bits is None
    assert result.ber is None
    assert result.symbols_available is False and result.ber_available is False


def test_b_no_reference_has_no_ber():
    frame, _ = b_frame()
    result = receive_qam64(frame, b_config(seed=None))
    assert result.reason == "NO_REFERENCE"
    assert result.reference_sync is not True
    assert result.bit_errors is None and result.ber is None


def test_b_no_signal_and_clipping_fail_without_ber():
    low, _ = b_frame(amplitude=0)
    assert receive_qam64(low, b_config(seed=39)).reason == "LOW_ENERGY"
    clipped, _ = b_frame(clipped=True)
    result = receive_qam64(clipped, b_config(seed=39))
    assert result.reason == "CLIPPED"
    assert result.bit_errors is None and result.ber is None


@pytest.mark.parametrize("kwargs", [
    {"sample_rate_hz": 6_000_000}, {"ratio": 1}, {"samples": 2048},
])
def test_b_out_of_domain_fails_without_ber(kwargs):
    wire, _ = REF.make_frame_bytes(39)
    if kwargs.get("samples") == 2048:
        wire = wire[:2048 * 4]
    result = receive_qam64(b_wrap(wire, {}, **kwargs), b_config(seed=39))
    assert result.reason == "INPUT_DOMAIN"
    assert result.bit_errors is None and result.ber is None


@pytest.mark.parametrize("config", [
    Qam64Config(reference_prbs7_seed=39, mode="QAM16"),
    Qam64Config(reference_prbs7_seed=39, shaping="RRC"),
    Qam64Config(reference_prbs7_seed=39, symbol_rate_hz=1_500_000),
    Qam64Config(reference_prbs7_seed=39, iq_order="XX"),
])
def test_b_unsupported_config_fails_without_ber(config):
    frame, _ = b_frame()
    result = receive_qam64(frame, config)
    assert result.reason == "UNSUPPORTED"
    assert result.bit_errors is None and result.ber is None


def test_b_pipeline_clean_qi_and_envelope_identity():
    wire, _ = REF.make_frame_bytes(39, iq_order="QI", phase_deg=19.0)
    frame = b_wrap(wire, {}, iq_order="QI")
    draft = dict(expected_mode="QAM64", shaping="RECT", symrate_hz=3_000_000,
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=FS)
    submitted = freeze_qam64_demod_config(draft, 7, iq_order="QI")
    env = analyze_qam64(frame, submitted)
    assert isinstance(env, Qam64Envelope)
    assert env.algorithm_version == QAM64_VERSION
    r = env.result
    assert r.reason == "" and r.capture_id == "qam64-test-b"
    assert (r.bit_errors, r.compared_bits) == (0, 384)
    assert (r.valid_symbols, r.alignment_bits, r.comparison_start_symbol) == \
        (127, 378, 63)
    assert r.evm_available is False and r.evm_rms is None


def test_b_pipeline_domain_mismatch_has_named_failure_and_no_ber():
    wire, _ = REF.make_frame_bytes(39)
    frame = b_wrap(wire[:2048 * 4], {}, samples=2048)
    draft = dict(expected_mode="QAM64", shaping="RECT", symrate_hz=3_000_000,
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=FS)
    submitted = freeze_qam64_demod_config(draft, 1, iq_order="IQ")
    env = analyze_qam64(frame, submitted)
    assert env.result.reason == "INPUT_DOMAIN"
    assert env.result.bit_errors is None and env.result.compared_bits is None


def test_b_fixture_sha_records_are_distinct_for_clean_and_corrupt():
    clean, clean_meta = REF.make_frame_bytes(39, phase_deg=23.0)
    corrupt, corrupt_meta = REF.make_frame_bytes(39, phase_deg=23.0,
                                                 corrupt_from=63)
    assert clean_meta["payload_sha256"] != corrupt_meta["payload_sha256"]
    assert clean_meta["reference_sha256"] != corrupt_meta["reference_sha256"]
    assert clean_meta["expected_errors"] == 0
    assert corrupt_meta["expected_errors"] is not None
    assert corrupt_meta["expected_errors"] > 0
    assert len(clean) == len(corrupt) == 4096 * 4
