"""Independent QAM64/RECT reference for offline receiver tests.

This module does not import the receiver, its pipeline, or its decision/mapping
code.  It re-implements only the frozen TX contract:

    MSB-first serial PRBS7, first transmitted bit is b0.
    I bits b0 b1 b2 -> levels (7, 5, 1, 3, -7, -5, -1, -3)
    Q bits b3 b4 b5 -> the same table
    RECT, 60 MSPS, 3 MSym/s, 20 samples/symbol, 127-symbol cyclic payload
    (2540 samples), 4096-sample frame, component-peak amplitude 60,
    signed-12 ties-to-even quantization, explicit IQ or QI wire order.

The first 63 symbols align; the last 64 symbols carry the 384 comparison bits.
"""
from __future__ import annotations

import hashlib

import numpy as np

SPS = 20
SYMBOLS = 127
PAYLOAD_SAMPLES = SYMBOLS * SPS
REFERENCE_SAMPLES = 4096
BITS_PER_SYMBOL = 6
AMPLITUDE = 60
SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
LEVELS = (7, 5, 1, 3, -7, -5, -1, -3)
ALIGNMENT_SYMBOLS = 63
COMPARISON_START_SYMBOL = 63
VALID_SYMBOLS = 127
ALIGNMENT_BITS = BITS_PER_SYMBOL * ALIGNMENT_SYMBOLS  # 378
COMPARED_BITS = BITS_PER_SYMBOL * (VALID_SYMBOLS - COMPARISON_START_SYMBOL)  # 384


def prbs7_bits(seed, count):
    """Independent MSB-first serial PRBS7 (x^7+x^6+1)."""
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError("PRBS7 seed must be 1..127")
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def axis_level(bits):
    bits = tuple(int(b) for b in bits)
    if len(bits) != 3 or any(b not in (0, 1) for b in bits):
        raise ValueError("QAM64 axis needs three bits")
    return LEVELS[(bits[0] << 2) | (bits[1] << 1) | bits[2]]


def axis_bits(level):
    value = int(level)
    for index, candidate in enumerate(LEVELS):
        if candidate == value:
            return ((index >> 2) & 1, (index >> 1) & 1, index & 1)
    raise ValueError("not a QAM64 axis level: %r" % (level,))


def symbol_for_bits(bits):
    bits = tuple(int(b) for b in bits)
    if len(bits) != BITS_PER_SYMBOL or any(b not in (0, 1) for b in bits):
        raise ValueError("QAM64 needs six bits")
    return complex(axis_level(bits[:3]), axis_level(bits[3:]))


def bits_for_symbol(symbol):
    return axis_bits(int(np.real(symbol))) + axis_bits(int(np.imag(symbol)))


def reference_symbols(seed):
    bits = prbs7_bits(seed, BITS_PER_SYMBOL * SYMBOLS)
    return np.array([symbol_for_bits(bits[BITS_PER_SYMBOL * i:
                                          BITS_PER_SYMBOL * (i + 1)])
                     for i in range(SYMBOLS)], dtype=complex)


def all_64_symbols():
    out = {}
    for value in range(64):
        bits = tuple((value >> (5 - i)) & 1 for i in range(BITS_PER_SYMBOL))
        out[bits] = symbol_for_bits(bits)
    return out


def _quantize(values):
    rounded = np.rint(np.asarray(values, dtype=float))
    if rounded.size and (rounded.max() > 2047 or rounded.min() < -2048):
        raise ValueError("sample outside signed-12 range")
    return rounded.astype("<i2")


def _payload(symbols, amplitude):
    iq = np.repeat(np.column_stack((symbols.real, symbols.imag)), SPS, axis=0)
    if amplitude:
        peak = float(np.max(np.abs(iq)))
        iq = iq * (amplitude / peak)
    else:
        iq = np.zeros_like(iq)
    return iq, _quantize(iq).tobytes()


def _bits_and_symbols(symbols):
    bits = []
    for symbol in symbols:
        bits.extend(bits_for_symbol(symbol))
    return bits


def make_frame_bytes(seed, *, iq_order="IQ", amplitude=AMPLITUDE,
                     phase_deg=0.0, cfo_hz=0.0, timing_samples=0,
                     noise_rms=0.0, clipped=False, samples=REFERENCE_SAMPLES,
                     sample_rate_hz=SAMPLE_RATE_HZ, seed_noise=20261025,
                     corrupt_from=None, wrong_reference=False,
                     wrong_seed=20261025):
    """Return ``(wire_bytes, truth_metadata)`` for one 4096-sample QAM64 frame.

    ``corrupt_from`` flips the sign of every transmitted symbol at/after that
    symbol index.  Alignment is untouched when it is >= 63, so receiver
    synchronization must remain valid while comparison bits are genuinely wrong.
    ``wrong_reference`` builds a random 64-QAM payload while the caller still
    supplies the original seed: it is a true waveform mismatch, not a seed change.
    """
    truth_symbols = reference_symbols(seed)
    if corrupt_from is not None:
        corrupted = truth_symbols.copy()
        corrupted[int(corrupt_from):] *= -1.0
        tx_symbols = corrupted
    else:
        tx_symbols = truth_symbols
    if wrong_reference:
        rng = np.random.default_rng(wrong_seed)
        choices = np.array(list(all_64_symbols().values()), dtype=complex)
        tx_symbols = rng.choice(choices, size=SYMBOLS)
    iq, payload = _payload(tx_symbols, amplitude)
    reference = (payload + payload)[:REFERENCE_SAMPLES * 4]
    base = np.frombuffer(reference, dtype="<i2").reshape(-1, 2).astype(float)
    n = np.arange(len(base), dtype=float)
    phase = np.deg2rad(phase_deg) + 2.0 * np.pi * cfo_hz * n / sample_rate_hz
    z = (base[:, 0] + 1j * base[:, 1]) * np.exp(1j * phase)
    if timing_samples:
        z = np.roll(z, int(timing_samples))
    if noise_rms:
        rng = np.random.default_rng(seed_noise)
        z = z + noise_rms * (
            rng.normal(size=len(z)) + 1j * rng.normal(size=len(z)))
    iq_wire = np.empty((len(z), 2), dtype=np.int16)
    iq_wire[:, 0] = _quantize(z.real)
    iq_wire[:, 1] = _quantize(z.imag)
    if clipped:
        iq_wire[0, 0] = 2047
    if iq_order == "QI":
        iq_wire = iq_wire[:, ::-1].copy()
    wire = iq_wire.astype("<i2").tobytes()

    original_bits = _bits_and_symbols(truth_symbols)
    sent_bits = _bits_and_symbols(tx_symbols)
    expected_errors = sum(
        1 for a, b in zip(original_bits[COMPARISON_START_SYMBOL * BITS_PER_SYMBOL:],
                          sent_bits[COMPARISON_START_SYMBOL * BITS_PER_SYMBOL:])
        if a != b)
    if wrong_reference:
        expected_errors = None
    return wire, {
        "seed": seed, "iq_order": iq_order,
        "payload_samples": PAYLOAD_SAMPLES,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "reference_samples": REFERENCE_SAMPLES,
        "reference_bytes": len(reference),
        "reference_sha256": hashlib.sha256(reference).hexdigest(),
        "valid_symbols": VALID_SYMBOLS,
        "alignment_bits": ALIGNMENT_BITS,
        "comparison_start_symbol": COMPARISON_START_SYMBOL,
        "compared_bits": COMPARED_BITS,
        "symbols": truth_symbols,
        "tx_symbols": tx_symbols,
        "bits": original_bits,
        "tx_bits": sent_bits,
        "corrupt_from": corrupt_from,
        "wrong_reference": bool(wrong_reference),
        "expected_errors": expected_errors,
    }
