#!/usr/bin/env python3
"""Independent RRC truth/reference for the P3 RRC receiver.

This module does not import the receiver under test.  It implements the frozen
mathematical RRC definition directly: alpha 0.35, SPS 20, 8-symbol span
(161 taps), unit discrete energy, circular 2540-sample frame, peak-normalized
component amplitude 60, and signed-12-bit ties-to-even quantization.

The PRBS7/QPSK/BPSK mapping is the frozen TX mapping: PRBS7 bit6 output with
bit6^bit5 feedback; first serial bit -> I, second -> Q; 0 -> +amp, 1 -> -amp.
A product-TX cross-check is available separately; it is never used as truth.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(r'<LOCAL_PATH>')
EVIDENCE = ROOT / 'evidence/rf-teaching/p3-rrc-offline-2026-10-24'
ALPHA = 0.35
SPS = 20
SPAN = 8
TAPS = SPAN * SPS + 1          # 161
SYMBOLS = 127
SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
PAYLOAD_SAMPLES = SYMBOLS * SPS  # 2540
REFERENCE_SAMPLES = 4096
REFERENCE_BYTES = REFERENCE_SAMPLES * 4
AMPLITUDE = 60
GROUP_DELAY = (TAPS - 1) // 2   # 80 samples
SUPPORTED_CFO_HZ = 50_000.0


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def prbs7_bits(seed, count):
    """Frozen PRBS7: output bit6, feedback bit6^bit5."""
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError('PRBS7 seed must be an integer 1..127')
    if type(count) is not int or count < 0:
        raise ValueError('count must be non-negative')
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def map_bits(mode, bits):
    """TX mapping: 0 -> +1, 1 -> -1; QPSK I=first, Q=second."""
    bits = tuple(bits)
    if mode == 'QPSK':
        if len(bits) != 2:
            raise ValueError('QPSK needs two bits')
        return (1.0 if bits[0] == 0 else -1.0,
                1.0 if bits[1] == 0 else -1.0)
    if mode == 'BPSK':
        if len(bits) != 1:
            raise ValueError('BPSK needs one bit')
        return (1.0 if bits[0] == 0 else -1.0, 0.0)
    raise ValueError('mode must be BPSK or QPSK')


def symbol_bits(mode, seed):
    bps = 2 if mode == 'QPSK' else 1
    bits = prbs7_bits(seed, SYMBOLS * bps)
    return bits


def symbols(mode, seed):
    bits = symbol_bits(mode, seed)
    bps = 2 if mode == 'QPSK' else 1
    return np.array([map_bits(mode, bits[i * bps:(i + 1) * bps])
                     for i in range(SYMBOLS)], dtype=float)


def rrc_value(t, alpha=ALPHA):
    """Continuous RRC impulse response, including t=0 and alpha singularities."""
    t = float(t)
    if t == 0.0:
        return 1.0 - alpha + 4.0 * alpha / math.pi
    denom_factor = 1.0 - (4.0 * alpha * t) ** 2
    if abs(denom_factor) < 1e-12:
        return (alpha / math.sqrt(2.0)) * (
            (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * alpha))
            + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * alpha)))
    numerator = (math.sin(math.pi * t * (1.0 - alpha))
                 + 4.0 * alpha * t * math.cos(math.pi * t * (1.0 + alpha)))
    return numerator / (math.pi * t * denom_factor)


def rrc_taps(alpha=ALPHA, sps=SPS, span=SPAN, unit_energy=True):
    """161 real taps, symmetric, centered at index 80, unit discrete energy."""
    if (alpha, sps, span) != (ALPHA, SPS, SPAN):
        raise ValueError('only frozen alpha=0.35/SPS=20/span=8 supported')
    length = span * sps + 1
    center = length // 2
    h = np.array([rrc_value((i - center) / sps, alpha) for i in range(length)],
                 dtype=np.float64)
    if unit_energy:
        h /= math.sqrt(float(np.dot(h, h)))
    return h


def pulse_shape(symbol_values, taps=None, sps=SPS):
    """Circular RRC filtering of a 127-symbol frame to 2540 samples."""
    if taps is None:
        taps = rrc_taps()
    taps = np.asarray(taps, dtype=np.float64)
    if taps.shape != (TAPS,):
        raise ValueError('taps must have 161 values')
    n = len(symbol_values) * sps
    if n != PAYLOAD_SAMPLES:
        raise ValueError('symbol frame must yield 2540 samples')
    x = np.zeros((n, 2), dtype=np.float64)
    x[::sps] = np.asarray(symbol_values, dtype=float)
    h = np.roll(taps, -(len(taps) // 2))
    out = np.empty_like(x)
    for c in range(2):
        out[:, c] = np.fft.ifft(np.fft.fft(x[:, c], n) * np.fft.fft(h, n)).real
    return out


def quantize_s12(values):
    """Signed-12 ties-to-even; reject out-of-range instead of wrapping."""
    arr = np.asarray(values, dtype=float)
    rounded = np.rint(arr)
    if rounded.size and (rounded.max() > 2047 or rounded.min() < -2048):
        raise ValueError('sample outside signed-12 range')
    return rounded.astype('<i2')


def make_payload(mode, seed, amplitude=AMPLITUDE):
    """Ideal 2540-sample payload and 4096-sample repeated reference."""
    sym = symbols(mode, seed)
    iq = pulse_shape(sym)
    peak = float(np.max(np.abs(iq))) if iq.size else 0.0
    if peak == 0.0:
        raise ValueError('empty waveform')
    iq *= amplitude / peak
    q = quantize_s12(iq)
    payload = q.tobytes()
    reference = (payload + payload)[:REFERENCE_BYTES]
    return {
        'bits': symbol_bits(mode, seed), 'symbols': sym.tolist(),
        'payload': payload, 'reference': reference,
        'payload_samples': PAYLOAD_SAMPLES, 'reference_samples': REFERENCE_SAMPLES,
        'payload_sha256': sha256_bytes(payload),
        'reference_sha256': sha256_bytes(reference),
        'taps_sha256': sha256_bytes(np.asarray(rrc_taps(), dtype='<f8').tobytes()),
        'amplitude': amplitude, 'group_delay_samples': GROUP_DELAY,
    }


def impair_reference(reference, *, phase_deg=0.0, cfo_hz=0.0, sample_offset=0,
                     noise_rms=0.0, rng_seed=20261024, clip=False):
    """Apply deterministic channel impairments to a 4096 reference frame."""
    z = np.frombuffer(reference, dtype='<i2').reshape(-1, 2).astype(float)
    if sample_offset:
        z = np.roll(z, int(sample_offset), axis=0)
    n = np.arange(len(z), dtype=float) / SAMPLE_RATE_HZ
    rot = np.exp(1j * (math.radians(phase_deg) + 2.0 * math.pi * cfo_hz * n))
    z = z[:, 0] + 1j * z[:, 1]
    z = z * rot
    if noise_rms:
        rng = np.random.default_rng(rng_seed)
        z = z + rng.normal(0.0, noise_rms, size=z.shape) + 1j * rng.normal(0.0, noise_rms, size=z.shape)
    iq = np.column_stack((z.real, z.imag))
    if clip:
        # Explicit over-range before clipping makes this a real clipped fixture
        # (the clean RRC payload only reaches |60|).
        iq = np.clip(iq * 50.0, -2048, 2047)
    q = quantize_s12(iq)
    return q.tobytes()


def make_fixture(mode, seed, *, kind='clean', phase_deg=0.0, cfo_hz=0.0,
                 sample_offset=0, noise_rms=0.0, iq_order='QI',
                 clip=False, sample_rate_hz=SAMPLE_RATE_HZ, frame_samples=REFERENCE_SAMPLES):
    """Return one fixed fixture dictionary with independent truth bits."""
    ideal = make_payload(mode, seed)
    reference = ideal['reference']
    actual_payload_sha = ideal['payload_sha256']
    clip = bool(clip or kind == 'clipped')
    raw = impair_reference(reference, phase_deg=phase_deg, cfo_hz=cfo_hz,
                           sample_offset=sample_offset, noise_rms=noise_rms, clip=clip)
    if kind == 'truncated':
        raw = raw[:frame_samples * 4]
    elif kind == 'insufficient':
        raw = raw[:512 * 4]
    elif kind == 'no_reference':
        rng = np.random.default_rng(20261024 + seed)
        q = quantize_s12(rng.normal(0.0, 100.0, size=(frame_samples, 2)))
        raw = q.tobytes()
    elif kind == 'wrong_config':
        # Same symbols but a RECT-shaped payload: the RRC receiver must not
        # silently accept it as an RRC frame.
        rect = np.repeat(ideal['symbols'], SPS, axis=0)
        peak = float(np.max(np.abs(rect)))
        rect = quantize_s12(rect * (AMPLITUDE / peak))
        p = rect.tobytes()
        actual_payload_sha = sha256_bytes(p)
        raw = (p + p)[:min(frame_samples * 4, len(p + p))]
    arr = np.frombuffer(raw, dtype='<i2').reshape(-1, 2)
    if iq_order == 'QI':
        wire = arr[:, ::-1]
    elif iq_order == 'IQ':
        wire = arr
    else:
        raise ValueError('iq_order must be IQ or QI')
    wire_bytes = np.asarray(wire, dtype='<i2').tobytes()
    expected = {
        'supported': kind in ('clean', 'phase', 'cfo', 'timing', 'noise', 'clipped',
                              'iq_order', 'out_of_domain_fs', 'wrong_config'),
        'kind': kind,
        'sync': kind in ('clean', 'iq_order'),
        'reason': '' if kind in ('clean', 'iq_order') else 'KNOWN_NEGATIVE',
        'valid_symbols': 119 if kind in ('clean', 'iq_order') else None,
        'compared_bits': ((112 if mode == 'QPSK' else 56)
                          if kind in ('clean', 'iq_order') else None),
        'valid_symbol_start': 4, 'valid_symbol_end': 123,
        'comparison_start_symbol': 67, 'comparison_end_symbol': 123,
        'final_bits': (ideal['bits'][134:246] if mode == 'QPSK'
                       else ideal['bits'][67:123]),
    }
    ident = 'rrc-%s-%s-seed%d' % (mode.lower(), kind, seed)
    if cfo_hz:
        ident += '-cfo%+d' % int(cfo_hz)
    if sample_offset:
        ident += '-off%d' % int(sample_offset)
    if phase_deg:
        ident += '-phase%g' % float(phase_deg)
    if noise_rms:
        ident += '-noise%g' % float(noise_rms)
    if kind == 'iq_order':
        ident += '-%s' % iq_order.lower()
    return {
        'id': ident,
        'mode': mode, 'seed': seed, 'kind': kind,
        'workpoint': {'sample_rate_hz': sample_rate_hz, 'symbol_rate_hz': SYMBOL_RATE_HZ,
                      'filter': 'RRC', 'alpha': ALPHA, 'sps': SPS},
        'iq_order': iq_order,
        'frame_samples': len(wire), 'samples': frame_samples,
        'iq_sha256': sha256_bytes(wire_bytes), 'wire': wire_bytes,
        'payload_sha256': ideal['payload_sha256'],
        'actual_payload_sha256': actual_payload_sha,
        'reference_sha256': ideal['reference_sha256'],
        'taps_sha256': ideal['taps_sha256'],
        'payload_samples': ideal['payload_samples'],
        'reference_samples': ideal['reference_samples'],
        'bits': ideal['bits'],
        'expected': expected,
        'impairment': {'phase_deg': phase_deg, 'cfo_hz': cfo_hz,
                       'sample_offset': sample_offset, 'noise_rms': noise_rms,
                       'clip': clip, 'sample_rate_hz': sample_rate_hz},
        'group_delay_samples': GROUP_DELAY,
        'truth_source': 'independent rrc_reference implementation; no receiver/A import',
    }


def fixture_catalog():
    catalog = []
    for seed in (39, 40, 41):
        catalog.append(dict(mode='QPSK', seed=seed, kind='clean'))
    catalog.append(dict(mode='BPSK', seed=6, kind='clean'))
    catalog.append(dict(mode='QPSK', seed=39, kind='phase', phase_deg=90.0))
    for offset in (1, 7, 19):
        catalog.append(dict(mode='QPSK', seed=39, kind='timing', sample_offset=offset))
    for cfo in (5_000, -5_000, 20_000, -20_000):
        catalog.append(dict(mode='QPSK', seed=39, kind='cfo', cfo_hz=cfo))
    catalog.append(dict(mode='QPSK', seed=39, kind='noise', noise_rms=8.0))
    catalog.append(dict(mode='BPSK', seed=6, kind='noise', noise_rms=8.0))
    catalog.append(dict(mode='QPSK', seed=39, kind='clipped', clip=True))
    catalog.append(dict(mode='QPSK', seed=39, kind='wrong_config'))
    catalog.append(dict(mode='QPSK', seed=39, kind='truncated', frame_samples=2048))
    catalog.append(dict(mode='QPSK', seed=39, kind='insufficient', frame_samples=512))
    catalog.append(dict(mode='QPSK', seed=39, kind='no_reference'))
    catalog.append(dict(mode='QPSK', seed=39, kind='out_of_domain_fs',
                         sample_rate_hz=30_000_000))
    catalog.append(dict(mode='QPSK', seed=39, kind='iq_order', iq_order='IQ'))
    return catalog


def write_fixtures(directory, write_iq=True):
    """Write fixed fixtures and index; no receiver/product worker is invoked."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in fixture_catalog():
        fixt = make_fixture(**spec)
        path = out / (fixt['id'] + '.iq')
        if write_iq:
            path.write_bytes(fixt['wire'])
        meta = {k: v for k, v in fixt.items() if k != 'wire'}
        meta['iq_file'] = path.name
        (out / (fixt['id'] + '.json')).write_text(json.dumps(meta, indent=2) + '\n',
                                                  encoding='utf-8')
        rows.append(meta)
    index = {'schema': 'p3-rrc-fixtures-v1', 'count': len(rows),
             'alpha': ALPHA, 'sps': SPS, 'taps': TAPS, 'group_delay': GROUP_DELAY,
             'fixtures': rows}
    (out / 'fixtures.json').write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
    return index


if __name__ == '__main__':
    idx = write_fixtures(EVIDENCE / 'fixtures')
    print(json.dumps({'count': idx['count'], 'alpha': idx['alpha'],
                      'taps': idx['taps'], 'group_delay': idx['group_delay']}, indent=2))
