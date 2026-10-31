"""Independent RRC mathematical reference tests (no receiver-under-test import)."""
import hashlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import rrc_reference as R  # noqa: E402

EVIDENCE = R.EVIDENCE
FIXTURES = EVIDENCE / 'fixtures'


def test_constants_and_taps_are_frozen():
    assert (R.ALPHA, R.SPS, R.SPAN, R.TAPS, R.PAYLOAD_SAMPLES,
            R.REFERENCE_SAMPLES, R.GROUP_DELAY) == (0.35, 20, 8, 161, 2540, 4096, 80)
    h = R.rrc_taps()
    assert h.shape == (161,)
    assert np.isclose(float(np.dot(h, h)), 1.0, atol=1e-12)
    assert np.allclose(h, h[::-1], atol=1e-15)
    assert np.isfinite(h[80])
    assert h[80] > 0
    # Known independent tap digest (unit-energy, float64 little-endian).
    assert hashlib.sha256(np.asarray(h, dtype='<f8').tobytes()).hexdigest() == \
        'a25af91d6ecb0a3164c4c0a0603792bf66ddf3aea18c7a8363282a3e06aea3b2'


def test_rrc_formula_singularities_are_finite_and_symmetric():
    h = R.rrc_taps(unit_energy=False)
    for t in (0.0, 1.0 / (4.0 * R.ALPHA), -1.0 / (4.0 * R.ALPHA)):
        value = R.rrc_value(t)
        assert math.isfinite(value)
        assert abs(value - R.rrc_value(-t)) < 1e-12
    assert abs(R.rrc_value(0.0) - (1 - R.ALPHA + 4 * R.ALPHA / math.pi)) < 1e-15
    # alpha=0 limit reduces to a unity DC tap.
    assert abs(R.rrc_value(0.0, alpha=1e-15) - 1.0) < 1e-9


def test_prbs7_mapping_and_bits():
    bits = R.prbs7_bits(39, 8)
    assert bits == [0, 1, 0, 0, 1, 1, 1, 1]
    assert R.map_bits('QPSK', (0, 1)) == (1.0, -1.0)
    assert R.map_bits('QPSK', (1, 1)) == (-1.0, -1.0)
    assert R.map_bits('BPSK', (0,)) == (1.0, 0.0)
    assert len(R.symbol_bits('QPSK', 39)) == 254
    assert len(R.symbol_bits('BPSK', 6)) == 127


def test_payload_reference_lengths_and_independent_sha():
    expected = {
        ('QPSK', 39): ('8717cb35458d38b2c7c75bb764d2c7c8121e1a15532073387cc7e32eb20fcc24',
                       '6255df2aa90c5466f350fb29da8c5a1607907373d284f82eb599ca1836440616'),
        ('QPSK', 40): ('e598d9dfbcf9c998e338ab4d66ba884f1b639187113d44b0c8fb311ee9980937',
                       'cb979161ebc402358aebc34012325222b143918d2df3e0169f68c22c298023ea'),
        ('QPSK', 41): ('ef56cb1c0a5b5670c06a4de831b9b1a1b204f053002533e936a710018e259362',
                       '4a5f77080ed7fbe6b54a7f03a092d7f411553cb6827e90302da9fb1d20ce1cdd'),
        ('BPSK', 6): ('e6a3fbdc99e4e3dd4a1ad8548d63f44ea132072a621ae7597a129f048d2eafa2',
                      'da116499b97bfb1aed9c430f428b5084ed276cc2ea69811d64b5f38c60f6030d'),
    }
    for (mode, seed), (psha, rsha) in expected.items():
        p = R.make_payload(mode, seed)
        assert len(p['payload']) == 2540 * 4
        assert len(p['reference']) == 4096 * 4
        assert p['payload_sha256'] == psha
        assert p['reference_sha256'] == rsha
        assert p['reference'] == (p['payload'] + p['payload'])[:4096 * 4]
        assert p['group_delay_samples'] == 80
        assert len(p['bits']) == (254 if mode == 'QPSK' else 127)


def test_quantization_is_ties_to_even_and_s12_bounded():
    assert R.quantize_s12([0.5, 1.5, 2.5, -0.5, -1.5, -2.5]).tolist() == [0, 2, 2, 0, -2, -2]
    with pytest.raises(ValueError):
        R.quantize_s12([2048.0])
    with pytest.raises(ValueError):
        R.quantize_s12([-2049.0])


def test_product_tx_crosscheck_is_separate_and_agrees_after_normalization():
    """Product TX is a cross-check only; the independent truth above is primary."""
    sys.path.insert(0, str(R.ROOT / 'pynq_overlay' / 'sw'))
    from rf_iq_signal import generate_digital_frame  # noqa: E402
    for mode, seed in (('QPSK', 39), ('QPSK', 40), ('QPSK', 41), ('BPSK', 6)):
        meta, payload = generate_digital_frame(dict(
            mode=mode, filter='RRC', rolloff='0.35', seed=seed,
            symbol_rate_hz=3000000, sample_rate_hz=60000000, amplitude=60))
        ours = R.make_payload(mode, seed)
        assert meta['samples'] == 2540 and len(payload) == 10160
        assert hashlib.sha256(payload).hexdigest() == ours['payload_sha256']
        assert hashlib.sha256((payload + payload)[:4096 * 4]).hexdigest() == ours['reference_sha256']


def test_fixtures_are_written_with_identity_and_negative_truth():
    # No receiver/A import; fixture truth is independent reference bits.
    idx = R.write_fixtures(FIXTURES)
    assert idx['count'] == len(R.fixture_catalog()) >= 20
    assert idx['alpha'] == 0.35 and idx['taps'] == 161 and idx['group_delay'] == 80
    for row in idx['fixtures']:
        fi = FIXTURES / row['iq_file']
        assert fi.is_file()
        assert hashlib.sha256(fi.read_bytes()).hexdigest() == row['iq_sha256']
        assert row['payload_sha256'] and row['reference_sha256']
        assert row['truth_source'].startswith('independent rrc_reference')


def test_clean_qpsk_and_bpsk_fixtures_are_supported_and_have_reference_bits():
    for mode, seed in (('QPSK', 39), ('QPSK', 40), ('QPSK', 41), ('BPSK', 6)):
        f = R.make_fixture(mode, seed, kind='clean')
        assert f['expected']['supported'] is True
        assert f['expected']['sync'] is True
        assert f['expected']['valid_symbols'] == 119
        assert f['expected']['compared_bits'] == (112 if mode == 'QPSK' else 56)
        assert len(f['expected']['final_bits']) == (112 if mode == 'QPSK' else 56)
        assert len(f['bits']) == (254 if mode == 'QPSK' else 127)
        assert len(f['wire']) == 4096 * 4


@pytest.mark.parametrize('kind,kwargs', [
    ('phase', dict(phase_deg=90.0)),
    ('timing', dict(sample_offset=7)),
    ('cfo', dict(cfo_hz=5_000)),
    ('cfo', dict(cfo_hz=-5_000)),
    ('noise', dict(noise_rms=8.0)),
    ('clipped', dict(clip=True)),
])
def test_impaired_fixtures_keep_independent_truth_but_are_not_clean(kind, kwargs):
    f = R.make_fixture('QPSK', 39, kind=kind, **kwargs)
    clean = R.make_fixture('QPSK', 39, kind='clean')
    assert f['bits'] == clean['bits']
    assert f['payload_sha256'] == clean['payload_sha256']
    assert f['reference_sha256'] == clean['reference_sha256']
    assert f['iq_sha256'] != clean['iq_sha256']
    assert f['expected']['sync'] is False  # negative/impairment: no fabricated pass


def test_negative_fixtures_have_no_fabricated_ber_and_bounded_lengths():
    for kind, n in (('truncated', 2048), ('insufficient', 512)):
        f = R.make_fixture('QPSK', 39, kind=kind, frame_samples=n)
        assert f['frame_samples'] == n
        assert len(f['wire']) == n * 4
        assert f['expected']['valid_symbols'] is None
        assert f['expected']['compared_bits'] is None
    no_ref = R.make_fixture('QPSK', 39, kind='no_reference')
    assert no_ref['expected']['sync'] is False
    wrong = R.make_fixture('QPSK', 39, kind='wrong_config')
    assert wrong['expected']['supported'] is True
    assert wrong['expected']['sync'] is False
    assert wrong['actual_payload_sha256'] != R.make_fixture('QPSK', 39, kind='clean')['actual_payload_sha256']


def test_iq_and_qi_are_explicit_and_not_mixed():
    qi = R.make_fixture('QPSK', 39, kind='iq_order', iq_order='QI')
    iq = R.make_fixture('QPSK', 39, kind='iq_order', iq_order='IQ')
    assert qi['iq_order'] == 'QI' and iq['iq_order'] == 'IQ'
    a = np.frombuffer(qi['wire'], dtype='<i2').reshape(-1, 2)
    b = np.frombuffer(iq['wire'], dtype='<i2').reshape(-1, 2)
    assert np.array_equal(a, b[:, ::-1])
    assert qi['iq_sha256'] != iq['iq_sha256']


def test_out_of_domain_sample_rate_is_marked_and_truth_preserved():
    f = R.make_fixture('QPSK', 39, kind='out_of_domain_fs', sample_rate_hz=30_000_000)
    assert f['workpoint']['sample_rate_hz'] == 30_000_000
    assert f['expected']['sync'] is False
    assert f['payload_sha256'] == R.make_payload('QPSK', 39)['payload_sha256']


def test_effective_delay_and_circular_frame_contract():
    p = R.make_payload('QPSK', 39)
    q = np.frombuffer(p['payload'], dtype='<i2').reshape(-1, 2)
    assert q.shape == (2540, 2)
    assert R.GROUP_DELAY == (R.TAPS - 1) // 2 == 80
    # The circular frame wraps: payload is exactly 127 symbols * 20 samples.
    assert len(q) == R.PAYLOAD_SAMPLES == 127 * R.SPS
    # Peak normalization is component-wise at the requested amplitude.
    assert int(np.max(np.abs(q))) == 60


def test_optional_a_rrc_module_contract_is_not_claimed_when_absent():
    """Integration with A is a separate step; never fake an A result here."""
    try:
        import rf_rrc_receiver  # noqa: F401
    except ImportError:
        pytest.skip('A rf_rrc_receiver absent; integration NOT_RUN (independent reference complete)')
    assert hasattr(rf_rrc_receiver, 'VERSION') or hasattr(rf_rrc_receiver, 'RrcConfig')

# ---- A integration (only when A's new receiver is present) -----------------
def _a_env(fixture, seed=None, no_seed=False):
    pytest.importorskip('rf_rrc_receiver')
    from rf_rrc_receiver import RrcConfig, receive_rrc
    from rf_rx_types import RxIqFrame, RxReadback
    frame = RxIqFrame(
        capture_id='rrc-test', payload=fixture['wire'],
        samples=fixture['frame_samples'],
        sample_rate_hz=fixture['workpoint']['sample_rate_hz'],
        ratio=0, daemon_timestamp_ns=1, connection_generation=1,
        config_revision=1, request_id=1, release_id='rrc-test', op='rx',
        physical=False, pre_readback=RxReadback(io_ok=True),
        post_readback=RxReadback(io_ok=True), completed_at_monotonic=1.0)
    ref_seed = None if no_seed else (fixture['seed'] if seed is None else seed)
    cfg = RrcConfig(mode=fixture['mode'], reference_prbs7_seed=ref_seed,
                    iq_order=fixture['iq_order'])
    return receive_rrc(frame, cfg)


def test_a_rrc_clean_uses_envelope_interval_and_independent_bits():
    """A's envelope denominator must be real; BER must be zero on clean truth."""
    for mode, seed in (('QPSK', 39), ('QPSK', 40), ('QPSK', 41), ('BPSK', 6)):
        f = R.make_fixture(mode, seed, kind='clean', iq_order='QI')
        env = _a_env(f)
        r = env.result
        bps = 2 if mode == 'QPSK' else 1
        assert r.reason == '' and r.carrier_sync and r.timing_sync and r.reference_sync
        assert r.valid_symbols == env.valid_symbol_end - env.valid_symbol_start == 119
        assert r.valid_symbols == f['expected']['valid_symbols']
        assert r.compared_bits == (env.comparison_end_symbol - env.comparison_start_symbol) * bps
        assert r.compared_bits == f['expected']['compared_bits']
        assert isinstance(r.bit_errors, int) and 0 <= r.bit_errors <= r.compared_bits
        assert r.ber is not None and abs(r.ber - r.bit_errors / r.compared_bits) < 1e-15
        # Independent clean truth: no bit error may be reported/erased.
        assert r.bit_errors == 0, 'A clean RRC BER mismatch: %d/%d' % (
            r.bit_errors, r.compared_bits)


def test_a_rrc_negatives_never_invent_counts():
    # clipped
    f = R.make_fixture('QPSK', 39, kind='clipped', iq_order='QI')
    env = _a_env(f)
    assert env.result.reason == 'CLIPPED'
    assert env.result.bit_errors is None and env.result.compared_bits is None
    # truncated/insufficient
    for n in (2048, 512):
        f = R.make_fixture('QPSK', 39, kind='truncated', frame_samples=n, iq_order='QI')
        env = _a_env(f)
        assert env.result.reason == 'INPUT_DOMAIN'
        assert env.result.bit_errors is None and env.result.compared_bits is None
    # no reference
    f = R.make_fixture('QPSK', 39, kind='no_reference', iq_order='QI')
    env = _a_env(f, no_seed=True)
    assert env.result.reason == 'NO_REFERENCE'
    assert env.result.bit_errors is None and env.result.compared_bits is None
    # wrong (RECT-shaped) payload must not be silently accepted as clean RRC
    f = R.make_fixture('QPSK', 39, kind='wrong_config', iq_order='QI')
    env = _a_env(f)
    assert env.result.reference_sync is False or env.result.bit_errors != 0


def test_a_rrc_impairments_are_reported_without_fabricated_denominator():
    for kind, kwargs in (('phase', dict(phase_deg=90.0)),
                         ('cfo', dict(cfo_hz=5_000)),
                         ('cfo', dict(cfo_hz=-5_000)),
                         ('timing', dict(sample_offset=7))):
        f = R.make_fixture('QPSK', 39, kind=kind, iq_order='QI', **kwargs)
        env = _a_env(f)
        r = env.result
        # Either a real result with its own denominator, or an unavailable
        # result with no counts.  Never a mixture.
        if r.compared_bits is None:
            assert r.bit_errors is None and r.ber is None
        else:
            assert r.reference_sync is True
            assert r.compared_bits == (env.comparison_end_symbol - env.comparison_start_symbol) * 2
            assert r.bit_errors is not None and r.ber == r.bit_errors / r.compared_bits
