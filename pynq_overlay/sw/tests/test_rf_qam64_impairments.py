"""Independent QAM64 impairment regression and A/B historical-frame replay.

Only synthetic fixtures from ``qam64_impairment_reference`` are used for the
impairment matrix; no live capture, no A/B-specific branch, no hard-coded
compensation and no production/A-test modification.  The optional historical
replay uses the already-downloaded A/B IQ and does not collect new VAL data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import qam64_impairment_reference as I
import qam64_reference as R
from rf_qam64_pipeline import analyze_qam64, freeze_qam64_demod_config
from rf_qam64_receiver import Qam64Config, receive_qam64
from rf_rx_types import RxIqFrame, RxReadback

FS = 60_000_000
LIVE = Path(__file__).resolve().parents[3] / (
    "evidence/rf-teaching/qam64-real-rx-2026-10-25/result")


def _frame(wire, meta, *, physical=True):
    return RxIqFrame(
        capture_id="qam64-impair", payload=wire,
        samples=meta.get("samples", 4096),
        sample_rate_hz=FS, ratio=0,
        daemon_timestamp_ns=1, connection_generation=2, config_revision=4,
        request_id=9, release_id="release-qam64", op="rx",
        physical=physical, pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)


def _config(seed=39):
    return Qam64Config(reference_prbs7_seed=seed, iq_order="QI")


def _recv(**kwargs):
    wire, meta = I.make_impaired_frame(39, **kwargs)
    return receive_qam64(_frame(wire, meta), _config()), meta


def _assert_clean(r):
    assert r.reason == ""
    assert r.carrier_sync and r.timing_sync and r.reference_sync
    assert r.valid_symbols == 127
    assert r.alignment_bits == 378
    assert r.comparison_start_symbol == 63
    assert r.bit_errors == 0 and r.compared_bits == 384
    assert r.ber == 0.0
    assert r.evm_available is False and r.evm_rms is None


@pytest.mark.parametrize("gain", [
    dict(gain_i=1.10, gain_q=0.90),
    dict(gain_i=1.30, gain_q=0.70),
])
def test_axis_gain(gain):
    r, _ = _recv(**gain)
    _assert_clean(r)


@pytest.mark.parametrize("cross", [
    dict(cross_iq=0.08, cross_qi=0.05),
    dict(cross_iq=0.15, cross_qi=0.10),
])
def test_axis_cross(cross):
    r, _ = _recv(**cross)
    _assert_clean(r)


@pytest.mark.parametrize("dc", [
    dict(dc_i=8.0, dc_q=-6.0),
    dict(dc_i=20.0, dc_q=-15.0),
])
def test_dc_offset(dc):
    r, _ = _recv(**dc)
    _assert_clean(r)


@pytest.mark.parametrize("cfo", [777.0, 1234.0, 4321.0, -2345.0])
def test_non_grid_cfo(cfo):
    r, _ = _recv(cfo_hz=cfo)
    _assert_clean(r)
    assert abs(r.cfo_hz - cfo) <= 500.0


@pytest.mark.parametrize("phase", [37.0, 91.0, 180.0, 271.0])
def test_phase(phase):
    r, _ = _recv(phase_deg=phase)
    _assert_clean(r)


@pytest.mark.parametrize("timing", list(range(20)))
def test_timing_0_to_19(timing):
    r, _ = _recv(timing_samples=timing)
    _assert_clean(r)
    assert r.timing_offset_samples == timing


@pytest.mark.parametrize("noise", [1.0, 2.0])
def test_noise(noise):
    r, _ = _recv(noise_rms=noise)
    _assert_clean(r)


def test_combined_impairment():
    r, _ = _recv(gain_i=1.10, gain_q=0.90, cross_iq=0.08, cross_qi=0.05,
                 dc_i=8.0, dc_q=-6.0, cfo_hz=1234.0, phase_deg=37.0,
                 timing_samples=7, noise_rms=1.0)
    _assert_clean(r)


@pytest.mark.parametrize("kwargs,reason", [
    (dict(no_signal=True), "LOW_ENERGY"),
    (dict(dc_i=50.0, dc_q=50.0), "REFERENCE_NOT_FOUND"),
    (dict(wrong_reference=True), "REFERENCE_NOT_FOUND"),
    (dict(clip=True), "CLIPPED"),
])
def test_pathological_named_without_ber(kwargs, reason):
    r, meta = _recv(**kwargs)
    assert r.reason == reason
    assert r.reference_sync is not True
    assert r.bit_errors is None and r.compared_bits is None and r.ber is None
    assert r.evm_available is False and r.evm_rms is None


@pytest.mark.parametrize("start", [63, 100])
def test_comparison_damage_preserves_fit_and_exact_errors(start):
    clean_r, _ = _recv()
    damaged_r, damaged_meta = _recv(corrupt_from=start)
    _assert_clean(clean_r)
    assert damaged_r.reason == ""
    assert damaged_r.carrier_sync and damaged_r.timing_sync
    assert damaged_r.reference_sync is True
    assert damaged_r.valid_symbols == 127
    assert damaged_r.alignment_bits == 378
    assert damaged_r.comparison_start_symbol == 63
    assert damaged_r.compared_bits == 384
    assert damaged_meta["expected_errors"] is not None
    assert 0 < damaged_meta["expected_errors"] < 384
    assert damaged_r.bit_errors == damaged_meta["expected_errors"]
    assert damaged_r.ber == damaged_r.bit_errors / 384.0
    # Only [0,63) fits; the comparison-only damage must not move those estimates.
    assert damaged_r.cfo_hz == clean_r.cfo_hz
    assert damaged_r.timing_offset_samples == clean_r.timing_offset_samples
    assert damaged_r.phase_ambiguity == clean_r.phase_ambiguity


def test_matrix_has_no_ab_or_fixed_compensation_branch():
    matrix = I.scenario_matrix()
    assert set(matrix) == {"axis_gain", "axis_cross", "dc", "non_grid_cfo",
                           "phase", "timing", "noise", "combined",
                           "pathological", "comparison_damage"}
    assert matrix["timing"] == list(range(20))
    assert 63 in matrix["comparison_damage"] and 100 in matrix["comparison_damage"]
    # The reference is synthetic-only; no A/B frame identity is accepted here.
    assert not hasattr(I, "A_TAG")
    assert not hasattr(I, "B_TAG")


def test_historical_ab_frames_replay_without_new_val():
    frames = ("qam64-rect-39-a", "qam64-rect-39-b")
    if not all((LIVE / t / (t + ".iq")).is_file() for t in frames):
        pytest.skip("historical A/B result not present")
    for tag in frames:
        iq = (LIVE / tag / (tag + ".iq")).read_bytes()
        meta = json.loads((LIVE / tag / (tag + ".json")).read_text(encoding="utf-8"))
        sub = meta["rx_submission"]
        draft = dict(expected_mode="QAM64", shaping="RECT", symrate_hz=3_000_000,
                     reference_enabled=True,
                     reference_seed=sub["config"]["reference_prbs7_seed"],
                     decim=1, rx_len=4096, input_fs_hz=FS)
        submitted = freeze_qam64_demod_config(draft, sub["revision"],
                                              iq_order="QI")
        frame = _frame(iq, {"samples": 4096})
        r = analyze_qam64(frame, submitted).result
        assert r.reason == ""
        assert r.carrier_sync and r.timing_sync and r.reference_sync
        assert (r.valid_symbols, r.alignment_bits, r.comparison_start_symbol) == \
            (127, 378, 63)
        assert r.compared_bits == 384
        assert 0 <= r.bit_errors <= 384
        assert r.evm_available is False and r.evm_rms is None
