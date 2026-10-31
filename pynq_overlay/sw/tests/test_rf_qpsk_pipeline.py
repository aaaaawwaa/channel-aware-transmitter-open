"""QPSK pipeline: explicit seed/QI boundary, identity and failure binding."""
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_qpsk_pipeline as P
from test_rf_qpsk_receiver import _frame


def draft(**updates):
    value = dict(expected_mode="QPSK", symrate_hz=3_000_000, shaping="RECT",
                 reference_enabled=False, reference_seed=37, decim=1,
                 rx_len=4096, input_fs_hz=60_000_000)
    value.update(updates)
    return value


def test_freeze_explicit_seed_and_qi_boundary():
    disabled = P.freeze_qpsk_demod_config(draft(reference_seed=37), 4, iq_order="QI")
    assert disabled.config.reference_prbs7_seed is None
    assert disabled.iq_order == "QI"
    assert disabled.revision == 4 and disabled.decim == 1
    assert disabled.rx_len == 4096 and disabled.input_fs_hz == 60_000_000
    enabled = P.freeze_qpsk_demod_config(
        draft(reference_enabled=True, reference_seed=37), 5, iq_order="QI")
    assert enabled.config.reference_prbs7_seed == 37
    assert enabled.config.mode == "QPSK" and enabled.config.shaping == "RECT"
    assert enabled.config.symbol_rate_hz == 3_000_000
    assert enabled.config.iq_order == "QI"


def test_freeze_rejects_missing_or_invalid_explicit_fields():
    with pytest.raises(ValueError):
        P.freeze_qpsk_demod_config(draft(), 1, iq_order="XX")
    with pytest.raises(ValueError):
        P.freeze_qpsk_demod_config(draft(expected_mode="BPSK"), 1)
    with pytest.raises(ValueError):
        P.freeze_qpsk_demod_config(draft(shaping="RRC"), 1)
    with pytest.raises(ValueError):
        P.freeze_qpsk_demod_config(draft(symrate_hz=2_000_000), 1)
    with pytest.raises(ValueError):
        P.freeze_qpsk_demod_config(draft(reference_enabled=True,
                                         reference_seed=None), 1)


def test_analyze_qi_synthetic_frame_and_identity():
    frame = dataclasses.replace(
        _frame(seed=39, offset=4, phase=0.2, iq_order="QI"),
        capture_id="qi-frame", release_id="release")
    submitted = P.freeze_qpsk_demod_config(
        draft(reference_enabled=True, reference_seed=39), 7, iq_order="QI")
    env = P.analyze_qpsk(frame, submitted)
    assert isinstance(env, P.QpskEnvelope)
    assert env.submitted == submitted and env.algorithm_version == P.VERSION
    result = env.result
    assert result.capture_id == "qi-frame" and result.release_id == "release"
    assert result.config_revision == frame.config_revision
    assert result.connection_generation == frame.connection_generation
    assert result.reason == ""
    assert result.carrier_sync and result.timing_sync and result.reference_sync is True
    assert (result.bit_errors, result.compared_bits, result.ber) == (0, 128, 0.0)
    assert result.valid_symbols == 127
    assert result.alignment_bits == 126 and result.comparison_start_symbol == 63
    assert result.evm_available is False and result.evm_rms is None


def test_analyze_iq_fixture_keeps_explicit_iq_order():
    frame = _frame(seed=3, offset=0, phase=0.3, iq_order="IQ")
    submitted = P.freeze_qpsk_demod_config(
        draft(reference_enabled=True, reference_seed=3), 2, iq_order="IQ")
    env = P.analyze_qpsk(frame, submitted)
    assert env.result.reference_sync is True and env.result.ber == 0.0


def test_acquisition_mismatch_is_bound_failure_without_ber():
    frame = _frame(samples=2048)
    submitted = P.freeze_qpsk_demod_config(draft(), 1, iq_order="QI")
    env = P.analyze_qpsk(frame, submitted)
    assert env.result.reason == "INPUT_DOMAIN"
    assert env.result.bit_errors is None and env.result.compared_bits is None
    assert env.result.ber is None
    assert env.result.capture_id == frame.capture_id
    assert env.result.release_id == frame.release_id


def test_failed_qpsk_binds_frame_and_never_has_ber():
    frame = _frame(seed=39)
    submitted = P.freeze_qpsk_demod_config(draft(), 1, iq_order="QI")
    env = P.failed_qpsk(frame, submitted, "CARRIER_NOT_SYNCED")
    assert env.result.reason == "CARRIER_NOT_SYNCED"
    assert env.result.capture_id == frame.capture_id
    assert env.result.release_id == frame.release_id
    assert env.result.bit_errors is None and env.result.compared_bits is None
    assert env.result.ber is None
    unknown = P.failed_qpsk(frame, submitted, "DEMOD_FAILED")
    assert unknown.result.reason == "NOT_RUN" and unknown.result.ber is None
