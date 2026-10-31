"""Contract tests for the unified finite-snapshot modem result."""
import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_modem_result as M


def base(**over):
    values = dict(capture_id="cap-1", release_id="rel", config_revision=7,
                  connection_generation=3, completed_at_monotonic=12.5,
                  mode="QPSK", shaping="RECT", sample_rate_hz=60000000,
                  symbol_rate_hz=3000000, samples=4096,
                  carrier_sync=True, timing_sync=True, reference_sync=True,
                  valid_symbols=200, alignment_bits=63,
                  bit_errors=1, compared_bits=400,
                  symbol_preview=((1.0, 0.0), (0.0, 1.0)),
                  bit_preview=(1, 0, 1, 0))
    values.update(over)
    return M.ModemResult(**values)


def test_success_contract_has_identity_sync_and_counts():
    r = base()
    assert r.ber == 1 / 400
    assert r.symbols_available and r.ber_available
    assert r.evm_available is False and r.evm_rms is None
    payload = r.to_dict()
    assert payload["capture_id"] == "cap-1"
    assert payload["release_id"] == "rel"
    assert payload["bit_errors"] == 1 and payload["compared_bits"] == 400
    assert payload["ber"] == 1 / 400
    assert len(payload["symbol_preview"]) <= M.MAX_SYMBOL_PREVIEW
    assert len(payload["bit_preview"]) <= M.MAX_BIT_PREVIEW


def test_unavailable_is_named_and_has_no_fake_ber_or_evm():
    r = M.ModemResult.unavailable(reason="NO_REFERENCE", capture_id="cap-2",
                                  release_id="rel", config_revision=7,
                                  connection_generation=3, mode="QPSK",
                                  shaping="RECT", sample_rate_hz=60000000,
                                  symbol_rate_hz=3000000, samples=4096)
    assert r.reason == "NO_REFERENCE"
    assert r.ber is None and r.compared_bits is None and r.bit_errors is None
    assert r.evm_available is False and r.evm_rms is None
    assert not r.ber_available
    with pytest.raises(ValueError):
        M.ModemResult.unavailable(reason="SOMETHING_ELSE")


def test_ber_property_rejects_inconsistent_or_missing_counts():
    assert base(bit_errors=None, compared_bits=None).ber is None
    assert base(bit_errors=0, compared_bits=0).ber is None
    with pytest.raises(ValueError):
        base(bit_errors=2, compared_bits=1)
    with pytest.raises(ValueError):
        base(bit_errors=None, compared_bits=10)


def test_previews_are_bounded_and_finite():
    with pytest.raises(ValueError):
        base(bit_preview=(0,) * (M.MAX_BIT_PREVIEW + 1))
    with pytest.raises(ValueError):
        base(symbol_preview=((float("nan"), 0.0),))
    with pytest.raises(ValueError):
        base(bit_preview=(2,))
    with pytest.raises(ValueError):
        base(reason="free form")


def test_evm_must_be_explicitly_available_before_a_value_is_allowed():
    with pytest.raises(ValueError):
        base(evm_available=False, evm_rms=0.0)
    with pytest.raises(ValueError):
        base(evm_available=True, evm_rms=None)
    r = base(evm_available=True, evm_rms=0.03)
    assert r.evm_available and r.evm_rms == 0.03


def test_result_is_immutable():
    r = base()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ber = 0.0


def test_legacy_bpsk_result_is_adapted_without_changing_it():
    old = SimpleNamespace(
        capture_id="old-cap", release_id="rel", config_revision=4,
        connection_generation=2, completed_at_monotonic=99.0,
        algorithm_version="bpsk-rect-snapshot-v2", reason="",
        carrier_sync=True, timing_sync=True, reference_sync=True,
        cfo_hz=-1200.0, timing_offset_samples=7, polarity=1,
        total_symbols=201, alignment_bits=63,
        bit_errors=0, compared_bits=150,
        bits_preview=tuple([1, 0] * 20),
    )
    frame = SimpleNamespace(sample_rate_hz=60000000, samples=4096)
    r = M.ModemResult.from_bpsk_result(old, frame)
    assert r.mode == "BPSK" and r.shaping == "RECT"
    assert r.capture_id == "old-cap" and r.release_id == "rel"
    assert r.config_revision == 4 and r.connection_generation == 2
    assert r.carrier_sync and r.timing_sync and r.reference_sync
    assert r.valid_symbols == 201 and r.alignment_bits == 63
    assert r.bit_errors == 0 and r.compared_bits == 150 and r.ber == 0
    assert r.phase_ambiguity == 1
    assert r.evm_available is False and r.evm_rms is None
    assert old.bit_errors == 0 and not hasattr(old, "ber")  # adapter does not mutate input
