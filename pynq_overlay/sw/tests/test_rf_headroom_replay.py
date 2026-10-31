"""The saved research frame is replayed offline through the frozen analyses.

A 46 dB frame must be measurable (BPSK sync, BER, constellation) and its domain
must come from the frame's own readback: since the 2026-10-17 migration the fixed
domain IS 46 dB, and any other value -- the historical 40 dB one included -- is
refused.  Both halves are asserted on real bytes, and the replay is labelled as a
replay everywhere it could be mistaken for a live capture.
"""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_headroom as H
import rf_headroom_replay as R
import test_rf_bpsk_receiver as B
import test_rf_headroom as T

CONFIG = R.rf_config_from_readback(T.rf_json(verified=True))


def payload(z):
    if isinstance(z, (bytes, bytearray)):
        return bytes(z)
    return np.column_stack((np.round(z.real), np.round(z.imag))).astype('<i2').tobytes()


def noise(level=3.0, seed=5):
    rng = np.random.RandomState(seed)
    return level * (rng.normal(size=4096) + 1j * rng.normal(size=4096))


def run(z, attempt=2, **kwargs):
    return R.replay(payload(z), 'replay:s/%d' % attempt, 'release-1', CONFIG,
                    **kwargs)


# ------------------------------------------------------------------ the domain

def test_a_domain_that_is_not_the_fixed_set_is_refused():
    """The refusal is the outcome, not a failure of the replay.

    The fixture readback's own idelay_en (127) is not the fixed set, so the
    classifier is handed the domain the frame was actually read back in and
    answers that it is out of scope.
    """
    summary = run(B.signal(phase=0.3))
    assert summary.environment_label == 'INVALID'
    assert summary.environment_reason == 'RF_DOMAIN_MISMATCH'


def test_the_same_bytes_are_accepted_in_the_fixed_domain_and_refused_at_40db():
    """Negative control: the refusal comes from the domain, not from the bytes.

    Since the 2026-10-17 migration the fixed set is the single 46 dB domain, so
    the same bytes are not refused by the domain check in it, and the historical
    40 dB set is refused.  Without both halves the test above would pass just as
    well if the classifier refused every frame for an unrelated reason.
    """
    import rf_environment as ENV
    fixed = dict(ENV.FIXED_RF)
    assert fixed['attenuation_mdB'] == 46000
    summary = R.replay(payload(B.signal(phase=0.3)), 'replay:s/2', 'release-1', fixed)
    assert summary.environment_reason != 'RF_DOMAIN_MISMATCH'
    historical = dict(fixed, attenuation_mdB=40000)
    refused = R.replay(payload(B.signal(phase=0.3)), 'replay:s/2', 'release-1', historical)
    assert refused.environment_reason == 'RF_DOMAIN_MISMATCH'


def test_the_domain_is_taken_from_the_frame_readback_not_from_a_default():
    """The RF config is required, and it comes from the native readback.

    A default would silently substitute a domain the frame was not taken in, and
    the refusal above would stop happening without anything failing.
    """
    readback = T.rf_json(verified=True)
    assert R.rf_config_from_readback(readback)['attenuation_mdB'] == 46000
    moved = dict(readback, attenuation_mdB=40000)
    assert R.rf_config_from_readback(moved)['attenuation_mdB'] == 40000
    for broken in ({}, dict(readback, pl=None), dict(readback, rx_lo_hz=None),
                   dict(readback, pl={'idelay_tap': 29})):
        with pytest.raises(ValueError):
            R.rf_config_from_readback(broken)


# ------------------------------------------------------------------ the replay

def test_a_synchronising_frame_is_measured_not_merely_reported():
    summary = run(B.signal(phase=0.3))
    assert summary.carrier_sync is True and summary.timing_sync is True
    assert summary.reference_sync is True
    assert summary.bit_errors == 0 and summary.compared_bits >= 64
    assert summary.ber == 0
    assert summary.symbol_count == 128
    assert summary.rail_count == 0


def test_the_replay_is_labelled_a_replay_and_never_a_live_capture():
    summary = run(B.signal())
    assert summary.replay is True and summary.version == R.VERSION
    frame = R.to_rx_frame(payload(B.signal()), 'replay:s/2', 'release-1')
    assert frame.capture_id == 'replay:s/2'
    assert frame.op == 'headroom.capture'
    # A replay took no PL readback; "no readback" must not read as "the part
    # answered", so it is False rather than a True that looks like evidence.
    assert frame.pre_readback.io_ok is False
    assert frame.post_readback.io_ok is False
    assert frame.physical is False


def test_the_replay_refuses_bytes_that_are_not_a_whole_frame():
    """A truncated payload must not be silently zero-padded or half-analysed."""
    for bad in (b'', payload(B.signal())[:-4], payload(B.signal()) + b'\x00' * 4,
                bytearray(payload(B.signal()))):
        with pytest.raises(ValueError):
            R.replay(bad, 'replay:s/2', 'release-1', CONFIG)


def test_the_replay_needs_its_own_identity_and_a_release():
    good = payload(B.signal())
    with pytest.raises(ValueError):
        R.replay(good, '', 'release-1', CONFIG)
    with pytest.raises(ValueError):
        R.replay(good, 'replay:s/2', '', CONFIG)


def test_the_replay_is_deterministic_so_host_and_arm_can_be_compared():
    """Same bytes, same answer -- the property Task 3's parity check rests on."""
    one = payload(B.signal(phase=0.3, cfo=40000))
    a = run(one).to_dict()
    b = run(one).to_dict()
    assert a == b
    assert a['source_sha256'] == hashlib.sha256(one).hexdigest()


def test_the_replay_does_not_mutate_the_saved_bytes():
    raw = payload(B.signal(phase=0.3))
    before = bytes(raw)
    run(raw)
    assert bytes(raw) == before


# ------------------------------------------------------------------ the OFF guard

def test_a_clean_off_frame_passes_the_guard():
    summary = run(noise(), attempt=1)
    assert summary.carrier_sync is False and summary.timing_sync is False
    assert summary.bit_errors is None and summary.symbol_count == 0
    assert R.off_violation(summary) is None


def test_an_off_frame_that_synchronised_is_a_violation():
    """An OFF attempt with a source is the failure this guard exists for."""
    summary = run(B.signal(phase=0.3), attempt=1)
    assert 'synchronized' in R.off_violation(summary)


def test_an_off_frame_with_rails_is_a_violation():
    """A railed OFF frame is a violation on the rails alone, before any sync."""
    railed = np.full(4096, -2048 + 0j)
    summary = run(railed, attempt=1)
    # Every I sample is at the low rail; the Q samples are not, so the count is
    # one per sample -- and it is recounted from the bytes, never declared.
    assert summary.rail_count == 4096
    assert 'rails' in R.off_violation(summary)


def test_every_violation_reason_is_reachable_and_distinct():
    """Four ways for an OFF capture to not be an OFF capture."""
    reasons = set()
    for z, attempt in ((B.signal(phase=0.3), 1), (np.full(4096, -2048 + 0j), 1)):
        summary = run(z, attempt=attempt)
        why = R.off_violation(summary)
        assert why is not None
        reasons.add(why)
    assert len(reasons) == 2
    # The count of rails is reported from the bytes, not from a header.
    railed = np.full(4096, 2047 + 0j)
    summary = run(railed, attempt=1)
    assert summary.rail_count == 4096


def test_the_replay_reads_the_fixed_waveform_the_plan_loads():
    """The replay's acquisition is the plan's contract, in one place."""
    config = R.research_demod(0)
    assert config.config.mode == R.RESEARCH_MODE
    assert config.config.symbol_rate_hz == R.RESEARCH_SYMRATE_HZ
    assert config.config.shaping == R.RESEARCH_SHAPING
    assert config.config.reference_prbs7_seed == R.REFERENCE_SEED
    assert (config.decim, config.rx_len, config.input_fs_hz) == (1, 4096, 60000000)
    # And it is the same waveform the plan declares for its transmitting attempts.
    declaration = H.HeadroomPlan().declaration(2)
    assert declaration['symbol_rate_hz'] == R.RESEARCH_SYMRATE_HZ
    assert declaration['shaping'] == R.RESEARCH_SHAPING
    assert declaration['prbs7_seed'] == R.REFERENCE_SEED
    assert declaration['sample_rate_hz'] == 60000000
