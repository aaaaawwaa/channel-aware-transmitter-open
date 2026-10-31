"""Frozen explicit RX configuration, independent of TX and classification."""
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_rf_bpsk_receiver import frame, signal


def pipeline():
    assert importlib.util.find_spec('rf_bpsk_pipeline'), 'BPSK pipeline is not implemented'
    import rf_bpsk_pipeline
    return rf_bpsk_pipeline


def draft(**updates):
    return dict(dict(expected_mode='BPSK', symrate_hz=3000000, shaping='RECT',
                     reference_enabled=False, reference_seed=1, decim=1,
                     rx_len=4096, input_fs_hz=60000000), **updates)


def test_submission_is_frozen_and_never_infers_reference():
    P = pipeline()
    source = draft(reference_seed=73)
    frozen = P.freeze_demod_config(source, 4)
    source['reference_enabled'] = True
    source['symrate_hz'] = 2000000
    assert frozen.config.reference_prbs7_seed is None
    assert frozen.config.symbol_rate_hz == 3000000 and frozen.revision == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.revision = 5
    result = P.analyze_demod(frame(signal()), frozen)
    assert result.result.timing_sync and result.result.ber is None
    assert result.result.reason == 'REFERENCE_NOT_PROVIDED'
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.submitted = None


@pytest.mark.parametrize('seed', [True, False, 0, 128, 1.0, '1', None])
def test_enabled_reference_requires_explicit_integer_seed(seed):
    with pytest.raises(ValueError):
        pipeline().freeze_demod_config(draft(reference_enabled=True, reference_seed=seed), 0)


def test_explicit_reference_matches_same_frame_identity_and_counts():
    P = pipeline()
    f = frame(signal())
    frozen = P.freeze_demod_config(draft(reference_enabled=True), 9)
    result = P.analyze_demod(f, frozen)
    assert result.submitted is frozen
    assert result.result.capture_id == f.capture_id
    assert result.result.config_revision == f.config_revision
    assert result.result.reference_sync and result.result.ber == 0
    assert result.result.compared_bits >= 64


@pytest.mark.parametrize('updates', [dict(expected_mode='QPSK'), dict(shaping='RRC'),
    dict(symrate_hz=2000000), dict(decim=2), dict(rx_len=2048), dict(input_fs_hz=30000000)])
def test_unsupported_submission_never_reports_ber(updates):
    P = pipeline()
    result = P.analyze_demod(frame(signal()), P.freeze_demod_config(draft(**updates), 0))
    assert result.result.reason in ('UNSUPPORTED', 'ACQUISITION_MISMATCH')
    assert result.result.ber is None


def test_failure_envelope_binds_new_frame_and_has_no_values():
    P = pipeline()
    f = frame(signal())
    config = P.freeze_demod_config(draft(reference_enabled=True), 2)
    result = P.failed_demod(f, config, reason='DEMOD_FAILED')
    assert result.submitted is config and result.result.capture_id == f.capture_id
    assert result.result.reason == 'DEMOD_FAILED'
    assert result.result.ber is None and result.result.compared_bits is None

