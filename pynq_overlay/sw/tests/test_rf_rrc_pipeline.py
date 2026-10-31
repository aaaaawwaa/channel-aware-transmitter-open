"""RRC pipeline: explicit frozen domain, IQ boundary and actual intervals."""
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_rrc_pipeline as P
from test_rf_rrc_receiver import _frame


def draft(mode="BPSK", **updates):
    value = dict(expected_mode=mode, shaping="RRC", symrate_hz=3_000_000,
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=60_000_000)
    value.update(updates)
    return value


@pytest.mark.parametrize("mode", ["BPSK", "QPSK"])
def test_freeze_rrc_explicit_domain_and_qi(mode):
    sub = P.freeze_rrc_demod_config(draft(mode), 7, iq_order="QI")
    assert isinstance(sub, P.RrcDemodConfig)
    assert sub.config.mode == mode and sub.config.rolloff == 0.35
    assert sub.config.sps == 20 and sub.config.symbol_rate_hz == 3_000_000
    assert sub.config.iq_order == "QI"
    assert sub.iq_order == "QI"
    assert (sub.decim, sub.rx_len, sub.input_fs_hz) == (1, 4096, 60_000_000)
    assert sub.config.reference_prbs7_seed == 39


def test_freeze_requires_explicit_rrc_seed_shaping_iq_and_domain():
    with pytest.raises(ValueError):
        P.freeze_rrc_demod_config(draft(), 1, iq_order="XX")
    with pytest.raises(ValueError):
        P.freeze_rrc_demod_config(draft(shaping="RECT"), 1)
    with pytest.raises(ValueError):
        P.freeze_rrc_demod_config(draft(expected_mode="QAM16"), 1)
    with pytest.raises(ValueError):
        P.freeze_rrc_demod_config(draft(decim=2), 1)
    with pytest.raises(ValueError):
        P.freeze_rrc_demod_config(draft(reference_enabled=True,
                                        reference_seed=None), 1)


@pytest.mark.parametrize("mode,bits", [("BPSK", 56), ("QPSK", 112)])
def test_analyze_rrc_qi_has_frozen_intervals_and_zero_ber(mode, bits):
    sub = P.freeze_rrc_demod_config(draft(mode), 7, iq_order="QI")
    env = P.analyze_rrc(_frame(mode=mode, iq_order="QI", offset=7, phase=0.3), sub)
    assert isinstance(env, P.RrcEnvelope)
    assert env.submitted == sub and env.algorithm_version == P.VERSION
    assert env.group_delay_samples == 0
    assert env.transition_symbols == 4
    assert (env.valid_symbol_start, env.valid_symbol_end) == (4, 123)
    assert (env.comparison_start_symbol, env.comparison_end_symbol) == (67, 123)
    assert env.result.reason == ""
    assert (env.result.bit_errors, env.result.compared_bits, env.result.ber) == (0, bits, 0.0)
    assert env.result.valid_symbols == 119
    assert env.result.algorithm_version == "rrc-alpha035-sps20-v3"
    assert env.result.evm_available is False and env.result.evm_rms is None


def test_analyze_iq_fixture_keeps_explicit_iq_order():
    sub = P.freeze_rrc_demod_config(draft("QPSK"), 2, iq_order="IQ")
    env = P.analyze_rrc(_frame(mode="QPSK", iq_order="IQ"), sub)
    assert env.result.reason == "" and env.result.ber == 0.0


def test_acquisition_mismatch_is_bound_failure_without_ber():
    sub = P.freeze_rrc_demod_config(draft(), 1, iq_order="QI")
    frame = _frame(samples=2048)
    env = P.analyze_rrc(frame, sub)
    assert env.result.reason == "INPUT_DOMAIN"
    assert env.result.capture_id == frame.capture_id
    assert env.result.release_id == frame.release_id
    assert env.result.bit_errors is None and env.result.compared_bits is None
    assert env.result.ber is None
    assert env.comparison_start_symbol is None


def test_failed_rrc_binds_frame_and_no_ber():
    frame = _frame()
    sub = P.freeze_rrc_demod_config(draft(), 1, iq_order="QI")
    env = P.failed_rrc(frame, sub, "CARRIER_NOT_SYNCED")
    assert env.result.reason == "CARRIER_NOT_SYNCED"
    assert env.result.capture_id == frame.capture_id
    assert env.result.bit_errors is None and env.result.ber is None
    unknown = P.failed_rrc(frame, sub, "DEMOD_FAILED")
    assert unknown.result.reason == "NOT_RUN" and unknown.result.ber is None


def test_reference_off_freezes_none():
    sub = P.freeze_rrc_demod_config(draft(reference_enabled=False), 1)
    assert sub.config.reference_prbs7_seed is None
