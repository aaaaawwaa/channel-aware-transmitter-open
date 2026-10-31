"""Independent QAM16/RECT reference for the offline receiver tests.

This module does not import the receiver under test. It re-implements the frozen
TX mapping from ``rf_iq_signal.map_bits_to_symbol``:

    I bits b0 b1 -> levels 3, 1, -3, -1
    Q bits b2 b3 -> levels 3, 1, -3, -1
    MSB-first serial PRBS7, first bit is b0.

RECT, 20 samples/symbol, 127 symbols (2540-sample cyclic payload), 4096-sample
reference by repeating the payload, component-peak amplitude 60, signed-12
ties-to-even quantization.
"""
from __future__ import annotations

import hashlib

import numpy as np

SPS = 20
SYMBOLS = 127
PAYLOAD_SAMPLES = SYMBOLS * SPS
REFERENCE_SAMPLES = 4096
AMPLITUDE = 60
SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
LEVELS = (3, 1, -3, -1)
ALIGNMENT_SYMBOLS = 63
COMPARISON_START_SYMBOL = 63
VALID_SYMBOLS = 127
ALIGNMENT_BITS = 4 * ALIGNMENT_SYMBOLS  # 252
COMPARED_BITS = 4 * (VALID_SYMBOLS - COMPARISON_START_SYMBOL)  # 256


def prbs7_bits(seed, count):
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError("PRBS7 seed must be 1..127")
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def axis_level(bits):
    return LEVELS[(int(bits[0]) << 1) | int(bits[1])]


def axis_bits(level):
    for i, v in enumerate(LEVELS):
        if v == level:
            return (i >> 1, i & 1)
    raise ValueError("not a QAM16 axis level: %r" % (level,))


def symbol_for_bits(bits):
    bits = tuple(int(b) for b in bits)
    if len(bits) != 4 or any(b not in (0, 1) for b in bits):
        raise ValueError("QAM16 needs four bits")
    return complex(axis_level(bits[:2]), axis_level(bits[2:]))


def bits_for_symbol(symbol):
    return axis_bits(int(np.real(symbol))) + axis_bits(int(np.imag(symbol)))


def reference_symbols(seed):
    bits = prbs7_bits(seed, 4 * SYMBOLS)
    return np.array([symbol_for_bits(bits[4 * i:4 * i + 4])
                     for i in range(SYMBOLS)], dtype=complex)


def _quantize(values):
    rounded = np.rint(np.asarray(values, dtype=float))
    if rounded.size and (rounded.max() > 2047 or rounded.min() < -2048):
        raise ValueError("sample outside signed-12 range")
    return rounded.astype("<i2")


def _payload(seed, amplitude=AMPLITUDE):
    syms = reference_symbols(seed)
    iq = np.repeat(np.column_stack((syms.real, syms.imag)), SPS, axis=0)
    peak = float(np.max(np.abs(iq)))
    iq = iq * (amplitude / peak)
    return iq, _quantize(iq).tobytes()


def make_frame_bytes(seed, *, iq_order="IQ", amplitude=AMPLITUDE,
                     phase_deg=0.0, cfo_hz=0.0, timing_samples=0,
                     noise_rms=0.0, clipped=False, samples=REFERENCE_SAMPLES,
                     sample_rate_hz=SAMPLE_RATE_HZ, seed_noise=20261024,
                     corrupt_from=None):
    """Return (wire bytes, truth metadata) for one 4096 sample frame."""
    iq, payload = _payload(seed, amplitude)
    reference = (payload + payload)[:REFERENCE_SAMPLES * 4]
    base = np.frombuffer(reference, dtype="<i2").reshape(-1, 2).astype(float)
    n = np.arange(len(base), dtype=float)
    phase = np.deg2rad(phase_deg) + 2.0 * np.pi * cfo_hz * n / sample_rate_hz
    z = (base[:, 0] + 1j * base[:, 1]) * np.exp(1j * phase)
    if corrupt_from is not None:
        s = reference_symbols(seed)
        idx = np.arange(len(z)) // SPS
        flip = idx >= corrupt_from
        z[flip] *= -1.0
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
        "symbols": reference_symbols(seed),
        "bits": prbs7_bits(seed, 4 * SYMBOLS),
    }


def all_16_symbols():
    out = {}
    for v in range(16):
        bits = tuple((v >> (3 - i)) & 1 for i in range(4))
        out[bits] = symbol_for_bits(bits)
    return out
