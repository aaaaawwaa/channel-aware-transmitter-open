#!/usr/bin/env python3
"""Independent QPSK/BPSK TX truth for the 6-frame real-demod smoke.

This module does NOT import the product worker, product receiver or the
independent offline receiver.  It re-implements the frozen TX facts from the
approved waveform specification: PRBS7 (bit6 output, bit6^bit5 feedback),
first bit -> I / second bit -> Q, 0 -> +amp / 1 -> -amp, RECT 20 samples/symbol,
component-peak amplitude 60, 12-bit ties-to-even quantization, 127 symbols
(2540 samples) and a 4096-sample reference formed by repeating the payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(r'<LOCAL_PATH>')
EV = ROOT / 'evidence/rf-teaching/s2-46db-qpsk-real-rx-2026-10-24'
AMPLITUDE = 60
FS_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
SPS = 20
SYMBOLS = 127
PAYLOAD_SAMPLES = SYMBOLS * SPS       # 2540
REFERENCE_SAMPLES = 4096
REFERENCE_BYTES = REFERENCE_SAMPLES * 4
MAPPING = {'prbs': 'PRBS7 seed 1..127; output bit6; feedback bit6^bit5',
           'qpsk': 'first serial bit -> I, second -> Q; 0 -> +1, 1 -> -1',
           'bpsk': 'serial bit -> I; 0 -> +1, 1 -> -1; Q=0',
           'shaping': 'RECT, 20 samples/symbol',
           'amplitude': 'component peak normalized to 60, ties-to-even quantized',
           'reference': 'payload repeated and truncated to 4096 samples'}
ROWS = [
    # index, label, mode, seed, classification
    (1, 'qpsk-39', 'QPSK', 39),
    (2, 'qpsk-40', 'QPSK', 40),
    (3, 'qpsk-41', 'QPSK', 41),
    (4, 'bpsk-6', 'BPSK', 6),
    (5, 'off-1', 'OFF', None),
    (6, 'off-2', 'OFF', None),
]
KNOWN = {
    39: {
        'payload_sha256': '402227619e55b41fb7d29e57c163df9387e7acdf699d1d1d56be5718341d3247',
        'reference_sha256': '5667f936d0a147d1f092a61d9d446c9850cd5adfab9c16a7cc1941b0a10b4649',
    },
}


def sha(b):
    return hashlib.sha256(bytes(b)).hexdigest()


def prbs7_bits(seed, count):
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError('PRBS7 seed must be 1..127')
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def build_payload(mode, seed):
    bps = 2 if mode == 'QPSK' else 1
    bits = prbs7_bits(seed, SYMBOLS * bps)
    syms = np.empty((SYMBOLS, 2), dtype=float)
    if mode == 'QPSK':
        for i in range(SYMBOLS):
            syms[i] = (1.0 if bits[2 * i] == 0 else -1.0,
                       1.0 if bits[2 * i + 1] == 0 else -1.0)
    elif mode == 'BPSK':
        for i in range(SYMBOLS):
            syms[i] = (1.0 if bits[i] == 0 else -1.0, 0.0)
    else:
        raise ValueError('no payload for mode ' + mode)
    iq = np.repeat(syms, SPS, axis=0)
    peak = float(np.max(np.abs(iq))) if iq.size else 0.0
    if peak > 0.0:
        iq = iq * (AMPLITUDE / peak)
    q = np.rint(iq).astype('<i2')
    payload = q.tobytes()
    if len(payload) != PAYLOAD_SAMPLES * 4:
        raise AssertionError('payload length')
    reference = (payload + payload)[:REFERENCE_BYTES]
    if len(reference) != REFERENCE_BYTES:
        raise AssertionError('reference length')
    return bits, payload, reference


def build_truth():
    rows = []
    artifacts = {}
    for index, label, mode, seed in ROWS:
        row = {
            'frame_index': index, 'label': label, 'mode': mode, 'truth_class': mode,
            'seed': seed, 'amplitude': AMPLITUDE, 'sample_rate_hz': FS_HZ,
            'symbol_rate_hz': SYMBOL_RATE_HZ, 'shaping': 'RECT',
            'samples': REFERENCE_SAMPLES, 'sps': SPS,
            'payload_samples': None, 'payload_bytes': None,
            'payload_sha256': None, 'reference_samples': None,
            'reference_bytes': None, 'reference_sha256': None,
            'payload_file': None, 'reference_file': None,
            'qpsk_valid_symbols': None, 'qpsk_compared_bits': None,
            'truth_source': 'independent reimplementation of frozen TX specification; '
                            'no product worker/receiver was used',
        }
        if mode != 'OFF':
            bits, payload, reference = build_payload(mode, seed)
            stem = 'truth/%s' % label
            row.update({
                'payload_samples': PAYLOAD_SAMPLES, 'payload_bytes': len(payload),
                'payload_sha256': sha(payload), 'payload_file': stem + '.payload.iq',
                'reference_samples': REFERENCE_SAMPLES, 'reference_bytes': len(reference),
                'reference_sha256': sha(reference), 'reference_file': stem + '.reference4096.iq',
                'prbs_bits': len(bits),
            })
            if mode == 'QPSK':
                row.update({'qpsk_valid_symbols': 127, 'qpsk_compared_bits': 128,
                            'alignment_symbols': 63, 'alignment_bits': 126})
            artifacts[label] = (payload, reference)
        rows.append(row)
    return rows, artifacts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=EV)
    ap.add_argument('--no-write-iq', action='store_true')
    a = ap.parse_args()
    out = a.out
    (out / 'truth').mkdir(parents=True, exist_ok=True)
    rows, artifacts = build_truth()
    if not a.no_write_iq:
        for label, (payload, reference) in artifacts.items():
            (out / 'truth' / (label + '.payload.iq')).write_bytes(payload)
            (out / 'truth' / (label + '.reference4096.iq')).write_bytes(reference)
    checks = {}
    for label, (payload, reference) in artifacts.items():
        checks[label] = {'payload_bytes': len(payload), 'reference_bytes': len(reference),
                         'payload_sha256': sha(payload), 'reference_sha256': sha(reference)}
    if 39 in KNOWN:
        assert checks['qpsk-39']['payload_sha256'] == KNOWN[39]['payload_sha256']
        assert checks['qpsk-39']['reference_sha256'] == KNOWN[39]['reference_sha256']
        checks['qpsk-39']['known_prior_truth_match'] = True
    truth = {
        'schema': 's2-qpsk-real-rx-independent-truth-v1',
        'dataset_id': 's2-46db-qpsk-real-rx-2026-10-24',
        'training_use': False,
        'workpoint': {'amplitude': AMPLITUDE, 'sample_rate_hz': FS_HZ,
                      'symbol_rate_hz': SYMBOL_RATE_HZ, 'shaping': 'RECT',
                      'samples': REFERENCE_SAMPLES, 'sps': SPS},
        'mapping': MAPPING,
        'rows': rows,
        'checks': checks,
        'independent_generator': 'scripts/qpsk_real_truth.py; no product/offline receiver import',
        'claims': {'low_ber': 'NOT_CLAIMED', 'overall_accuracy': 'NOT_CLAIMED',
                   'development_smoke': True},
    }
    (out / 'truth.json').write_text(json.dumps(truth, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'rows': len(rows), 'checks': checks}, indent=2))


if __name__ == '__main__':
    main()
