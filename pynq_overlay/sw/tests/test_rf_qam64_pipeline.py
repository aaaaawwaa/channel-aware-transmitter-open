"""Implementation-side tests for the QAM64/RECT pipeline handoff."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rf_qam64_pipeline as P
import test_rf_qam64_receiver as H


def draft(**updates):
    value = dict(expected_mode="QAM64", symrate_hz=3_000_000, shaping="RECT",
                 reference_enabled=True, reference_seed=39, decim=1,
                 rx_len=4096, input_fs_hz=60_000_000)
    value.update(updates)
    return value


def frozen(**updates):
    return P.freeze_qam64_demod_config(draft(**updates), 7, iq_order="QI")


def test_freeze_envelope_and_clean_result():
    f = H.frame(39, iq_order="QI")
    sub = frozen()
    env = P.analyze_qam64(f, sub)
    assert isinstance(env, P.Qam64Envelope)
    assert env.submitted is sub
    assert env.algorithm_version == P.VERSION
    r = env.result
    assert r.mode == "QAM64" and r.shaping == "RECT"
    assert r.reason == ""
    assert (r.valid_symbols, r.alignment_bits, r.comparison_start_symbol) == (127, 378, 63)
    assert (r.bit_errors, r.compared_bits, r.ber) == (0, 384, 0.0)
    assert r.evm_available is False and r.evm_rms is None


def test_freeze_rejects_wrong_mode_shaping_rate_domain_and_iq():
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(expected_mode="QAM16"), 1)
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(shaping="RRC"), 1)
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(symrate_hz=6_000_000), 1)
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(decim=2), 1)
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(), 1, iq_order="XX")
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(reference_enabled=True, reference_seed=None), 1)


def test_failure_envelope_has_no_ber_and_binds_frame():
    f = H.frame(39, iq_order="QI")
    sub = frozen()
    env = P.failed_qam64(f, sub, "REFERENCE_NOT_FOUND")
    assert isinstance(env, P.Qam64Envelope)
    assert env.result.capture_id == f.capture_id
    assert env.result.reason == "REFERENCE_NOT_FOUND"
    assert env.result.reference_sync is False
    assert env.result.bit_errors is None
    assert env.result.compared_bits is None
    assert env.result.ber is None
    assert env.result.evm_available is False


def test_frame_domain_mismatch_returns_input_domain():
    f = H.frame(39, samples=4095, iq_order="QI")
    env = P.analyze_qam64(f, frozen())
    assert env.result.reason == "INPUT_DOMAIN"
    assert env.result.bit_errors is None


def test_unsupported_config_returns_named_refusal():
    with pytest.raises(ValueError):
        P.freeze_qam64_demod_config(draft(expected_mode="QAM64", shaping="RRC"), 1)
    # A directly-constructed config can still be refused by the receiver.
    f = H.frame(39)
    cfg = P.Qam64Config(mode="QAM16", shaping="RECT", reference_prbs7_seed=39)
    env = P.analyze_qam64(f, P.Qam64DemodConfig(cfg, 1, 1, 4096, 60_000_000))
    assert env.result.reason == "UNSUPPORTED"
    assert env.result.bit_errors is None


def test_envelope_is_frozen():
    env = P.analyze_qam64(H.frame(39, iq_order="QI"), frozen())
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.algorithm_version = "x"
