"""Deterministic offline reference vectors for the frozen BPSK domain (zero device).

Generation pipeline (as specified for the reference-vector work package):

    3 MSym/s symbols -> 6 MSPS with sps=2 -> zero-order hold x10 -> 60 MSPS
    (20 samples/symbol) -> 4096-sample signed-12 IQ image (ratio 0)

Impairments are applied independently on that 60 MSPS waveform: CFO, absolute
phase, per-complex-sample AWGN (fixed RNG), fractional sample delay, spectral
inversion (conjugate) and the hard-clipping boundary (code 2047 is the frozen
CLIPPED sentinel; 1500/2000 stay clean).

Expected outcomes are produced by ``pynq_overlay/sw/rf_bpsk_research.independent_demod``
- an independent PRBS7/RECT/CFO/phase/timing implementation written from the spec,
NOT by the frozen receiver.  ``check`` then runs the FROZEN ``receive_bpsk`` and
requires BOTH sides to agree with the manifest (two-sided cross-check).

    python scripts/rf_reference_vectors.py write --output DIR
    python scripts/rf_reference_vectors.py check --directory DIR [--json PATH]
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pynq_overlay/sw'))

import rf_bpsk_research as research  # noqa: E402

FS_HZ = research.FS_HZ
SYMRATE_HZ = research.SYMRATE_HZ
SPS = research.SPS
BASE_FS_HZ = 6000000          # 6 MSPS generation rate
BASE_SPS = 2                  # 2 samples per symbol at 6 MSPS
HOLD = 10                     # zero-order hold: 6 MSPS -> 60 MSPS
RX_LEN = 4096
IQ_MIN, IQ_MAX = -2047, 2046
AMPLITUDE = 1000
AWGN_AMPLITUDE = 300
SEED = 1
SCHEMA = 'rf-bpsk-reference-vectors-v1'
SCOPE = ('deterministic offline vectors with spec-derived expected outcomes; '
         'not a board measurement')
CFO_TOLERANCE_HZ = 50.0
CFO_TOLERANCE_FRACTION = 0.02


def base_waveform(bits, amplitude=AMPLITUDE):
    """3 MSym/s symbols -> 6 MSPS sps=2 -> x10 hold -> 60 MSPS float I samples."""
    symbols = np.asarray([1 - 2 * int(bit) for bit in bits], dtype=float)
    base = np.repeat(symbols, BASE_SPS)
    wave = np.repeat(base, HOLD) * float(amplitude)
    return wave[:RX_LEN]


def rotate(iq, cfo_hz=0.0, phase_rad=0.0, fs=FS_HZ):
    n = np.arange(len(iq), dtype=float)
    return iq.astype(float) * np.exp(1j * (2 * np.pi * float(cfo_hz) * n / fs
                                           + float(phase_rad)))


def add_awgn(z, snr_db, rng_seed=20261020):
    """Circular AWGN at the given per-complex-sample SNR (signal power A^2)."""
    power = float(np.mean(np.abs(z) ** 2))
    sigma2 = power / (10 ** (float(snr_db) / 10.0))
    rng = np.random.default_rng(rng_seed)
    noise = math.sqrt(sigma2 / 2.0) * (rng.standard_normal(len(z))
                                       + 1j * rng.standard_normal(len(z)))
    return z + noise


def frac_delay(z, delay):
    if delay == 0.0:
        return z
    return research.fractional_delay(z, float(delay))


def to_s16_bytes(z, allow_sentinels=False):
    i = np.rint(np.real(z))
    q = np.rint(np.imag(z))
    low, high = (-2048, 2047) if allow_sentinels else (IQ_MIN, IQ_MAX)
    if np.any(i < low) or np.any(i > high) or np.any(q < low) or np.any(q > high):
        raise ValueError('samples leave the legal s12 image')
    pairs = np.empty((len(i), 2), dtype='<i2')
    pairs[:, 0] = i.astype('<i2')
    pairs[:, 1] = q.astype('<i2')
    return pairs.tobytes()


def make_vector(vector_id, group, bits, *, cfo_hz=0.0, phase_rad=0.0, snr_db=None,
                amplitude=AMPLITUDE, invert=False, delay=0.0, note=''):
    wave = base_waveform(bits, amplitude)
    z = rotate(wave, cfo_hz=cfo_hz, phase_rad=phase_rad)
    if invert:
        z = np.conj(z)
    z = frac_delay(z, delay)
    if snr_db is not None:
        z = add_awgn(z, snr_db)
    payload = to_s16_bytes(z, allow_sentinels=(amplitude >= 2047))
    iq = research.iq_from_bytes(payload)
    expected = research.independent_demod(iq, seed=SEED)
    params = {'cfo_hz': cfo_hz, 'phase_rad': phase_rad, 'snr_db': snr_db,
              'amplitude': amplitude, 'invert': invert, 'delay_samples': delay,
              'base_fs_hz': BASE_FS_HZ, 'base_sps': BASE_SPS, 'hold': HOLD,
              'sps': SPS, 'seed': SEED}
    return {'id': vector_id, 'group': group, 'params': params, 'note': note,
            'payload': payload,
            'expected': {key: expected.get(key) for key in
                         ('reason', 'reference_sync', 'bit_errors', 'compared_bits',
                          'total_symbols', 'cfo_hz', 'carrier_coherence',
                          'timing_quality', 'alignment_offset', 'polarity')}}


def build_vectors():
    bits = research.prbs7_bits(SEED, RX_LEN // SPS + 8)
    vectors = [make_vector('v01-pure', 'pure', bits,
                           note='no impairment; analytic BER 0')]
    for cfo in (0.0, 100.0, -100.0, 500.0, -500.0, 2700.0, -2700.0):
        vectors.append(make_vector('v02-cfo-%+d' % cfo, 'cfo', bits, cfo_hz=cfo,
                                   note='rotation only; |CFO| << 100 kHz contract limit'))
    for phase in (0.0, 0.3, 0.6, 1.0):
        vectors.append(make_vector('v03-phase-%.1f' % phase, 'phase', bits,
                                   phase_rad=phase,
                                   note='constant phase; polarity absorbs pi ambiguity'))
    for snr in (30.0, 20.0, 15.0, 10.0, 6.0):
        vectors.append(make_vector('v04-awgn-%ddb' % snr, 'awgn', bits, snr_db=snr,
                                   amplitude=AWGN_AMPLITUDE,
                                   note='per-sample SNR; analytic Eb/N0 = SNR + 10 dB'))
    for amp in (1500, 2000, 2047):
        vectors.append(make_vector('v05-clip-%d' % amp, 'clip', bits, amplitude=amp,
                                   note='code 2047 is the CLIPPED sentinel; 1500/2000 clean'))
    for invert in (False, True):
        vectors.append(make_vector('v06-invert-%s' % ('on' if invert else 'off'),
                                   'inversion', bits, invert=invert,
                                   note='spectral inversion = conjugate; expected decodable'))
    for delay in (0.0, 0.25, 0.5, 0.75):
        vectors.append(make_vector('v07-frac-%.2f' % delay, 'fractional', bits,
                                   delay=delay,
                                   note='fractional sample timing phase'))
    vectors.append(make_vector('v08-combo', 'combo', bits, cfo_hz=500.0,
                               phase_rad=0.6, snr_db=20.0, delay=0.25,
                               amplitude=AWGN_AMPLITUDE,
                               note='CFO+phase+AWGN+fractional timing combination'))
    return vectors


def vector_digest(rows):
    blob = json.dumps([{'id': row['id'], 'sha256': row['sha256']} for row in rows],
                      sort_keys=True).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def write_set(output):
    output = Path(output)
    if output.exists():
        raise SystemExit('output exists: %s' % output)
    (output / 'vectors').mkdir(parents=True)
    vectors = build_vectors()
    manifest = []
    for vector in vectors:
        payload = vector.pop('payload')
        path = output / 'vectors' / (vector['id'] + '.iq')
        path.write_bytes(payload)
        row = dict(vector)
        row['file'] = 'vectors/' + vector['id'] + '.iq'
        row['sha256'] = hashlib.sha256(payload).hexdigest()
        row['bytes'] = len(payload)
        manifest.append(row)
    document = {'schema': SCHEMA, 'scope': SCOPE, 'modulation': 'BPSK',
                'shaping': 'RECT', 'symbol_rate_hz': SYMRATE_HZ,
                'input_fs_hz': FS_HZ, 'rx_len': RX_LEN, 'sps': SPS,
                'generation': {'base_fs_hz': BASE_FS_HZ, 'base_sps': BASE_SPS,
                               'hold': HOLD},
                'legal_code_range': [IQ_MIN, IQ_MAX],
                'clip_sentinels': [-2048, 2047],
                'expected_source': 'pynq_overlay/sw/rf_bpsk_research.independent_demod',
                'cfo_tolerance_hz': CFO_TOLERANCE_HZ,
                'cfo_tolerance_fraction': CFO_TOLERANCE_FRACTION,
                'set_digest': vector_digest(manifest),
                'vector_count': len(manifest),
                'vectors': manifest}
    (output / 'manifest.json').write_text(json.dumps(document, indent=2) + '\n',
                                          encoding='utf-8')
    return document


def load_set(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    for row in manifest['vectors']:
        raw = (directory / row['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise SystemExit('vector hash mismatch: %s' % row['id'])
    return manifest


def frame_from_vector(raw, row):
    from rf_rx_types import RxIqFrame, RxReadback
    return RxIqFrame('vec-' + row['id'], raw, RX_LEN, FS_HZ, 0, 1.0, 1, 1,
                     'req-' + row['id'], 'offline-vectors',
                     'offline.reference.vector', False, RxReadback(True),
                     RxReadback(True), 1.0)


def compare_side(expected, observed):
    """Named disagreements for one side (independent or frozen) against the manifest."""
    problems = []
    if observed.get('reason') != expected.get('reason'):
        problems.append('reason %r != %r' % (observed.get('reason'),
                                             expected.get('reason')))
    if expected.get('reference_sync') is True:
        if observed.get('reference_sync') is not True:
            problems.append('reference_sync not True')
        if observed.get('bit_errors') != expected.get('bit_errors'):
            problems.append('bit_errors %r != %r' % (observed.get('bit_errors'),
                                                     expected.get('bit_errors')))
        if observed.get('compared_bits') != expected.get('compared_bits'):
            problems.append('compared_bits %r != %r' % (observed.get('compared_bits'),
                                                        expected.get('compared_bits')))
        if observed.get('total_symbols') != expected.get('total_symbols'):
            problems.append('total_symbols %r != %r'
                            % (observed.get('total_symbols'),
                               expected.get('total_symbols')))
    return problems


def cfo_agreement(applied, observed):
    if observed is None:
        return 'cfo missing'
    tolerance = max(CFO_TOLERANCE_HZ, abs(float(applied)) * CFO_TOLERANCE_FRACTION)
    delta = abs(float(observed) - float(applied))
    return None if delta <= tolerance else 'cfo %.1f != %.1f (tolerance %.1f)' % (
        observed, applied, tolerance)


def check_expected(directory):
    """Two-sided check: independent model AND frozen receiver must both match."""
    from rf_bpsk_receiver import BpskConfig, receive_bpsk
    manifest = load_set(directory)
    directory = Path(directory)
    rows = []
    for row in manifest['vectors']:
        raw = (directory / row['file']).read_bytes()
        frozen = receive_bpsk(frame_from_vector(raw, row),
                              BpskConfig(mode='BPSK', symbol_rate_hz=SYMRATE_HZ,
                                         shaping='RECT',
                                         reference_prbs7_seed=row['params']['seed'])
                              ).to_dict()
        independent = research.independent_demod(
            research.iq_from_bytes(raw), seed=row['params']['seed'])
        expected = row['expected']
        problems = compare_side(expected, independent)
        problems += ['frozen ' + p for p in compare_side(expected, frozen)]
        if expected.get('reference_sync') is True:
            if frozen.get('bit_errors') != independent.get('bit_errors'):
                problems.append('frozen/independent bit_errors differ: %r != %r'
                                % (frozen.get('bit_errors'),
                                   independent.get('bit_errors')))
            for side, observed in (('independent', independent), ('frozen', frozen)):
                problem = cfo_agreement(row['params']['cfo_hz'],
                                        observed.get('cfo_hz'))
                if problem:
                    problems.append('%s %s' % (side, problem))
        rows.append({'id': row['id'], 'group': row['group'],
                     'expected': expected,
                     'independent': {k: independent.get(k) for k in
                                     ('reason', 'reference_sync', 'bit_errors',
                                      'compared_bits', 'total_symbols', 'cfo_hz',
                                      'timing_quality', 'alignment_offset')},
                     'frozen': {k: frozen.get(k) for k in
                                ('reason', 'reference_sync', 'bit_errors',
                                 'compared_bits', 'total_symbols', 'cfo_hz',
                                 'timing_quality', 'reference_offset')},
                     'ok': not problems, 'problems': problems})
    return {'schema': 'rf-bpsk-reference-vector-check-v1', 'scope': SCOPE,
            'vector_count': len(rows),
            'cross_check': 'independent model vs frozen receive_bpsk',
            'passed': all(r['ok'] for r in rows),
            'failed': [r['id'] for r in rows if not r['ok']], 'rows': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    write = sub.add_parser('write')
    write.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('check')
    check.add_argument('--directory', type=Path, required=True)
    check.add_argument('--json', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == 'write':
        document = write_set(args.output)
        print(json.dumps({'schema': document['schema'],
                          'vector_count': document['vector_count'],
                          'set_digest': document['set_digest']}, indent=2))
        return 0
    report = check_expected(args.directory)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'failed': report['failed']},
                     indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
