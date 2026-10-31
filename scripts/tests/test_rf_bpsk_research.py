"""Tests for the independent research demodulator/metrics and the reference vector set."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pynq_overlay/sw'))

import rf_bpsk_research as research  # noqa: E402
from scripts import rf_reference_vectors as vectors  # noqa: E402


def rotated_waveform(cfo_hz, amplitude=1000):
    bits = research.prbs7_bits(1, 210)
    wave = vectors.base_waveform(bits, amplitude)
    return vectors.rotate(wave, cfo_hz=cfo_hz)


def test_prbs7_matches_the_spec_sequence_for_seed_127():
    bits = research.prbs7_bits(127, 16)
    assert ''.join(map(str, bits)) == '1111111000000100'


def test_prbs7_has_period_127_and_refuses_seed_zero():
    bits = research.prbs7_bits(1, 254)
    assert bits[:127] == bits[127:]
    assert len(set(bits)) == 2
    for seed in (1, 5, 63, 126, 127):
        assert len(set(research.prbs7_bits(seed, 127))) == 2
    for bad in (0, 128, -1, 'x'):
        with pytest.raises(ValueError):
            research.prbs7_bits(bad, 1)


def test_reference_symbol_mapping_is_bit0_positive():
    reference = research.reference_symbols(1)
    bits = research.prbs7_bits(1, 127)
    assert list(reference[:8]) == [1 - 2 * bit for bit in bits[:8]]
    assert set(reference) == {-1.0, 1.0}


@pytest.mark.parametrize('cfo', [0.0, 100.0, -500.0, 2700.0, -2700.0])
def test_independent_cfo_estimator_recovers_small_offsets(cfo):
    estimate = research.estimate_cfo_hz(research.iq_from_bytes(
        vectors.to_s16_bytes(rotated_waveform(cfo))))
    assert abs(estimate - cfo) < 10.0


def test_independent_demod_pure_vector_syncs_with_zero_errors():
    raw = vectors.to_s16_bytes(rotated_waveform(0.0))
    result = research.independent_demod(research.iq_from_bytes(raw), seed=1)
    assert result['reason'] == ''
    assert result['reference_sync'] is True
    assert result['bit_errors'] == 0
    assert result['compared_bits'] == 138
    assert result['total_symbols'] == 201
    assert result['evm_rms'] < 1e-9


def test_independent_demod_detects_clipping_and_no_energy():
    symbols = np.asarray(vectors.base_waveform(research.prbs7_bits(1, 210)), dtype=float)
    clipped = np.empty(len(symbols), dtype='<i2')
    clipped[0] = 2047
    pairs = np.zeros((len(symbols), 2), dtype='<i2')
    pairs[:, 0] = clipped
    assert research.independent_demod(research.iq_from_bytes(pairs.tobytes()),
                                      seed=1)['reason'] == 'CLIPPED'
    zeros = np.zeros(RX_LEN := 4096, dtype='<i2')
    zero_pairs = np.zeros((RX_LEN, 2), dtype='<i2')
    assert research.independent_demod(
        research.iq_from_bytes(zero_pairs.tobytes()), seed=1)['reason'] == 'NO_ENERGY'


def test_evm_is_zero_for_a_noiseless_constellation_and_positive_with_noise():
    reference = research.reference_symbols(1, 138)
    symbols = reference * 1000.0 + 0j
    assert research.evm_rms(symbols, reference) < 1e-12
    rng = np.random.default_rng(7)
    noisy = symbols + 20.0 * (rng.standard_normal(138)
                              + 1j * rng.standard_normal(138))
    assert research.evm_rms(noisy, reference) > 0.01


def test_soft_llr_sign_follows_the_decisions():
    symbols = np.asarray([1000 + 0j, -1000 + 0j, 500 + 0j, -500 + 0j])
    llr = research.soft_llr(symbols, 100.0)
    assert list(np.sign(llr)) == [1.0, -1.0, 1.0, -1.0]


def test_the_analytic_ber_curve_is_monotonic_and_ebn0_conversion_is_exact():
    bers = [research.analytic_awgn_ber(db) for db in (6, 10, 15, 20, 30)]
    assert all(earlier > later for earlier, later in zip(bers, bers[1:]))
    assert research.per_sample_snr_to_eb_n0_db(6.0) == pytest.approx(16.0)
    assert research.analytic_awgn_ber(6.0) > 0


def test_the_research_module_never_imports_the_frozen_receiver():
    import inspect
    source = inspect.getsource(research)
    assert 'rf_bpsk_receiver' not in source
    assert 'pynq_status' not in source


def test_generated_vector_set_covers_the_approved_grid_and_passes_the_two_sided_check(tmp_path):
    document = vectors.write_set(tmp_path / 'vectors')
    ids = [row['id'] for row in document['vectors']]
    assert document['vector_count'] == 27
    for expected_id in ('v01-pure', 'v02-cfo-+100', 'v02-cfo--2700',
                        'v03-phase-0.3', 'v03-phase-1.0', 'v04-awgn-30db',
                        'v04-awgn-6db', 'v05-clip-1500', 'v05-clip-2047',
                        'v06-invert-off', 'v06-invert-on', 'v07-frac-0.25',
                        'v07-frac-0.75', 'v08-combo'):
        assert expected_id in ids
    report = vectors.check_expected(tmp_path / 'vectors')
    assert report['passed'] is True, report['failed']
    assert report['vector_count'] == 27
    assert report['cross_check'] == 'independent model vs frozen receive_bpsk'


def test_generation_is_deterministic():
    first = vectors.build_vectors()
    second = vectors.build_vectors()
    digest = [vectors.vector_digest([{'id': row['id'],
                                      'sha256': __import__('hashlib').sha256(
                                          row['payload']).hexdigest()}
                                     for row in first])]
    digest2 = [vectors.vector_digest([{'id': row['id'],
                                       'sha256': __import__('hashlib').sha256(
                                           row['payload']).hexdigest()}
                                      for row in second])]
    assert digest == digest2


def test_research_evaluation_states_the_four_candidate_verdicts(tmp_path):
    from scripts import rf_bpsk_research_eval as evaluation
    vectors.write_set(tmp_path / 'vectors')
    report = evaluation.evaluate(tmp_path / 'vectors')
    statuses = {row['status'] for row in report['verdicts']}
    assert statuses == {'REPLACEABLE', 'NEEDS_NEW_CONTRACT', 'NOT_VIABLE'}
    assert all(group['all_agree'] for group in report['summary'].values())
    assert all(group['max_bit_errors'] in (None, 0)
               for group in report['summary'].values())
    assert report['limitations']
