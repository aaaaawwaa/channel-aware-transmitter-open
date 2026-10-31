"""Independent QPSK reference receiver for P3 Task 4.2.

This module is deliberately independent of ``rf_qpsk_receiver`` / the installed
candidate: it implements the frozen TX contract directly (PRBS7 -> QPSK ->
RECT sampling) and a small offline synchroniser.  It is not used by the product
receiver, and it must not be imported to generate product answers.

TX contract used here:
* PRBS7 seed 1..127, 7-bit register, output bit6, feedback bit6 ^ bit5;
* QPSK: first bit of each pair -> I, second -> Q; 1 -> +1, 0 -> -1;
* RECT pulse, 20 samples/symbol at 60 MSPS / 3 MBd;
* one frame carries 127 QPSK symbols (2540 samples).

The first 63 symbols (126 bits) are the alignment segment.  The final BER
denominator starts at symbol 63 and never includes alignment bits.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

import numpy as np

SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
SPS = 20
FRAME_SAMPLES = 4096
PRBS_SYMBOLS = 127
ALIGNMENT_SYMBOLS = 63
ALIGNMENT_BITS = ALIGNMENT_SYMBOLS * 2
CFO_COARSE_HZ = 100_000
CFO_COARSE_STEP_HZ = 5_000
CFO_FINE_SPAN_HZ = 5_000
CFO_FINE_STEP_HZ = 500
SYNC_CORRELATION_MIN = 0.90
LOW_ENERGY_RMS = 1.0
# Real frames are not clipped by the TX amplitude, but a deliberate synthetic
# clipped fixture must be refused rather than silently scored.
CLIP_FRACTION_MAX = 0.20


class QpskReferenceError(ValueError):
    pass


def prbs7_bits(seed: int, count: int) -> list[int]:
    """Return `count` serial PRBS7 bits (RTL bit6 output, bit6^bit5 feedback)."""
    if type(seed) is not int or not 1 <= seed <= 127:
        raise QpskReferenceError("PRBS7 seed must be an integer 1..127")
    if type(count) is not int or count < 0:
        raise QpskReferenceError("count must be a non-negative integer")
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def map_qpsk_bits(bits: Sequence[int]) -> complex:
    if len(bits) != 2 or any(b not in (0, 1) for b in bits):
        raise QpskReferenceError("QPSK needs exactly two 0/1 bits")
    i = 1.0 if bits[0] else -1.0
    q = 1.0 if bits[1] else -1.0
    return complex(i, q)


def qpsk_symbols(seed: int, symbols: int = PRBS_SYMBOLS) -> np.ndarray:
    if type(symbols) is not int or symbols < 0:
        raise QpskReferenceError("symbols must be a non-negative integer")
    bits = prbs7_bits(seed, symbols * 2)
    return np.array([map_qpsk_bits(bits[2 * k:2 * k + 2]) for k in range(symbols)],
                    dtype=np.complex128)


def qpsk_bits_from_symbols(symbols: Sequence[complex]) -> np.ndarray:
    out = []
    for z in symbols:
        out.append(1 if np.real(z) >= 0 else 0)
        out.append(1 if np.imag(z) >= 0 else 0)
    return np.array(out, dtype=np.int8)


def _unavailable(reason: str, **fields) -> Dict:
    out = {
        "reason": reason, "carrier_sync": False, "timing_sync": False,
        "reference_sync": False, "cfo_hz": None, "timing_offset_samples": None,
        "reference_alignment": None, "phase_ambiguity": 0,
        "valid_symbols": 0, "alignment_bits": None,
        "comparison_start_symbol": None, "bit_errors": None,
        "compared_bits": None, "ber": None,
    }
    out.update(fields)
    return out


def _metadata_domain(metadata: Optional[Dict]) -> Optional[str]:
    if metadata is None:
        return None
    sample_rate = metadata.get("sample_rate_hz")
    symbol_rate = metadata.get("symbol_rate_hz")
    samples = metadata.get("samples")
    shaping = metadata.get("shaping")
    fmt = metadata.get("format")
    if sample_rate not in (None, SAMPLE_RATE_HZ):
        return "INPUT_DOMAIN"
    if symbol_rate not in (None, SYMBOL_RATE_HZ):
        return "INPUT_DOMAIN"
    if samples not in (None, FRAME_SAMPLES, 2540):
        return "INPUT_DOMAIN"
    if shaping not in (None, "RECT"):
        return "INPUT_DOMAIN"
    if fmt not in (None, "s16_iq_le"):
        return "INPUT_DOMAIN"
    return None


def _validate_iq(iq, metadata: Optional[Dict]):
    arr = np.asarray(iq, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < 2:
        return None, "INVALID_IQ"
    if not np.isfinite(arr).all():
        return None, "INVALID_IQ"
    domain = _metadata_domain(metadata)
    if domain:
        return None, domain
    if metadata is not None and metadata.get("samples") not in (None, FRAME_SAMPLES):
        return None, "INPUT_DOMAIN"
    if arr.shape[0] != FRAME_SAMPLES:
        return None, "INVALID_IQ"
    rms = float(np.sqrt(np.mean(arr[:, 0] ** 2 + arr[:, 1] ** 2)))
    if rms < LOW_ENERGY_RMS:
        return None, "LOW_ENERGY"
    clipped = float(np.mean(np.max(np.abs(arr), axis=1) >= 2047.0))
    if clipped > CLIP_FRACTION_MAX:
        return None, "CLIPPED"
    return arr, None


def _slice_symbols(iq: np.ndarray, tau: int, count: int) -> np.ndarray:
    block = iq[tau:tau + count * SPS].reshape(count, SPS, 2)
    return (block[:, :, 0] + 1j * block[:, :, 1]).mean(axis=1)


def _best_alignment(soft: np.ndarray, ref: np.ndarray, want_symbols: int):
    """Return (corr, ref_offset, phase, alignment_soft) on the first 63 symbols."""
    align = soft[:want_symbols]
    denom = np.linalg.norm(align)
    best = (-1.0, 0, 0.0)
    if denom == 0 or not np.isfinite(denom):
        return None
    for offset in range(len(ref)):
        expected = ref[(offset + np.arange(want_symbols)) % len(ref)]
        c = np.vdot(expected, align)
        expected_norm = np.linalg.norm(expected)
        corr = abs(c) / (denom * expected_norm) if expected_norm else 0.0
        if corr > best[0]:
            best = (float(corr), offset, float(np.angle(c)))
    return best


def _score_candidate(iq, tau, count, ref, cfo_hz):
    soft = _slice_symbols(iq, tau, count)
    t = tau + (SPS - 1) / 2.0 + SPS * np.arange(count)
    soft = soft * np.exp(-1j * 2.0 * math.pi * cfo_hz * t / SAMPLE_RATE_HZ)
    best = _best_alignment(soft, ref, ALIGNMENT_SYMBOLS)
    if best is None:
        return None
    corr, offset, phase = best
    return {'corr': corr, 'offset': offset, 'phase': phase, 'tau': tau,
            'cfo': cfo_hz, 'count': count, 'soft': soft}


def analyze_iq(iq, seed: Optional[int], metadata: Optional[Dict] = None,
               reference_seed: Optional[int] = None,
               reference_symbols: Optional[Sequence[complex]] = None) -> Dict:
    """Analyze one 4096-sample QPSK snapshot using only the independent reference.

    `seed` is the expected TX seed.  `reference_seed` selects another PRBS7
    sequence (seeds are cyclic shifts of one m-sequence, so it is kept for
    diagnostics but is not treated as a wrong-reference test).  A genuinely
    unrelated reference can be injected with `reference_symbols`; a mismatch
    must never be reported as a zero-BER success.
    """
    arr, refusal = _validate_iq(iq, metadata)
    if refusal:
        return _unavailable(refusal, samples=int(np.asarray(iq).shape[0]) if np.asarray(iq).ndim else 0)
    if seed is None:
        return _unavailable("NO_REFERENCE")
    if type(seed) is not int or not 1 <= seed <= 127:
        return _unavailable("NO_REFERENCE")
    ref_seed = seed if reference_seed is None else reference_seed
    if type(ref_seed) is not int or not 1 <= ref_seed <= 127:
        return _unavailable("NO_REFERENCE")
    ref = qpsk_symbols(ref_seed, PRBS_SYMBOLS)
    if reference_symbols is not None:
        ref = np.asarray(reference_symbols, dtype=np.complex128)
        if ref.ndim != 1 or len(ref) < 2 or not np.isfinite(ref).all():
            return _unavailable("NO_REFERENCE")
        if np.linalg.norm(ref) == 0:
            return _unavailable("NO_REFERENCE")

    candidates = []
    for tau in range(SPS):
        # The frozen TX frame carries exactly 127 QPSK symbols; a 4096-sample
        # capture has a tail after that frame and must not be counted as BER.
        count = min((FRAME_SAMPLES - tau) // SPS, PRBS_SYMBOLS)
        if count <= ALIGNMENT_SYMBOLS:
            continue
        for cfo in range(-CFO_COARSE_HZ, CFO_COARSE_HZ + 1, CFO_COARSE_STEP_HZ):
            c = _score_candidate(arr, tau, count, ref, float(cfo))
            if c is not None:
                candidates.append(c)
    if not candidates:
        return _unavailable("TIMING_NOT_SYNCED")
    coarse = max(candidates, key=lambda x: x['corr'])
    # Refine around the best coarse CFO; keep the same tau/count.
    refined = []
    lo = int(coarse['cfo'] - CFO_FINE_SPAN_HZ)
    hi = int(coarse['cfo'] + CFO_FINE_SPAN_HZ)
    for cfo in range(lo, hi + 1, CFO_FINE_STEP_HZ):
        c = _score_candidate(arr, coarse['tau'], coarse['count'], ref, float(cfo))
        if c is not None:
            refined.append(c)
    best = max(refined or [coarse], key=lambda x: x['corr'])
    corr = best['corr']
    count = best['count']
    tau = best['tau']
    offset = best['offset']
    phase = best['phase']
    soft = best['soft'] * np.exp(-1j * phase)
    if corr < SYNC_CORRELATION_MIN:
        return _unavailable("REFERENCE_NOT_FOUND", cfo_hz=float(best['cfo']),
                            timing_offset_samples=int(tau), valid_symbols=int(count),
                            reference_alignment=int(offset),
                            alignment_bits=ALIGNMENT_BITS,
                            comparison_start_symbol=ALIGNMENT_SYMBOLS,
                            alignment_correlation=float(corr))
    expected = ref[(offset + np.arange(count)) % len(ref)]
    expected_bits = qpsk_bits_from_symbols(expected)
    # Final BER only over symbols 63..count-1; alignment symbols are excluded.
    start = ALIGNMENT_SYMBOLS
    final_expected = expected_bits[2 * start:2 * count]
    final_symbols = soft[start:count]
    final_bits = qpsk_bits_from_symbols(final_symbols)
    n = min(len(final_expected), len(final_bits))
    final_expected = final_expected[:n]
    final_bits = final_bits[:n]
    bit_errors = int(np.sum(final_expected != final_bits)) if n else None
    compared_bits = int(n) if n else None
    ber = (bit_errors / compared_bits) if compared_bits else None
    return {
        "reason": "",
        "carrier_sync": True,
        "timing_sync": True,
        "reference_sync": True,
        "cfo_hz": float(best['cfo']),
        "timing_offset_samples": int(tau),
        "reference_alignment": int(offset),
        "phase_ambiguity": 0,
        "phase_rad": float(phase),
        "alignment_correlation": float(corr),
        "valid_symbols": int(count),
        "alignment_bits": ALIGNMENT_BITS,
        "comparison_start_symbol": ALIGNMENT_SYMBOLS,
        "bit_errors": bit_errors,
        "compared_bits": compared_bits,
        "ber": ber,
        "symbol_preview": [[float(np.real(z)), float(np.imag(z))] for z in soft[:16]],
        "bit_preview": [int(b) for b in final_bits[:128]],
    }


def _qpsk_waveform(seed: int, n_samples: int = FRAME_SAMPLES) -> np.ndarray:
    symbols = qpsk_symbols(seed, PRBS_SYMBOLS)
    idx = (np.arange(n_samples) // SPS) % len(symbols)
    return symbols[idx]


def make_fixture(*, seed: int = 39, reference_seed: Optional[int] = None,
                 phase_deg: float = 0.0, cfo_hz: float = 0.0,
                 timing_offset: int = 0, noise_rms: float = 0.0,
                 amplitude: float = 1000.0, clip: bool = False,
                 off: bool = False, length: int = FRAME_SAMPLES,
                 metadata_override: Optional[Dict] = None,
                 rng_seed: int = 20261023) -> tuple[np.ndarray, Optional[Dict], Optional[int]]:
    """Build a deterministic synthetic fixture; returns (s16 IQ, metadata, seed)."""
    if length != FRAME_SAMPLES:
        # Still return a malformed length; caller tests INVALID_IQ.
        z = np.zeros(length, dtype=np.complex128)
    elif off:
        rng = np.random.default_rng(rng_seed)
        z = rng.normal(0, 4.0, length) + 1j * rng.normal(0, 4.0, length)
    else:
        z = _qpsk_waveform(seed, length) * amplitude
        if timing_offset:
            z = np.concatenate([np.zeros(timing_offset, dtype=np.complex128),
                                z[:length - timing_offset]])
        z = z * np.exp(1j * (math.radians(phase_deg) +
                            2.0 * math.pi * cfo_hz * np.arange(length) / SAMPLE_RATE_HZ))
        if noise_rms:
            rng = np.random.default_rng(rng_seed)
            z = z + rng.normal(0, noise_rms, length) + 1j * rng.normal(0, noise_rms, length)
    if clip:
        z = np.clip(z, -2048 + 1j * -2048, 2047 + 1j * 2047)
    iq = np.empty((length, 2), dtype=np.int16)
    iq[:, 0] = np.clip(np.rint(np.real(z)), -2048, 2047).astype(np.int16)
    iq[:, 1] = np.clip(np.rint(np.imag(z)), -2048, 2047).astype(np.int16)
    meta = {
        "sample_rate_hz": SAMPLE_RATE_HZ, "symbol_rate_hz": SYMBOL_RATE_HZ,
        "samples": FRAME_SAMPLES, "shaping": "RECT", "format": "s16_iq_le",
        "tx_seed": seed,
    }
    if off:
        meta.update({"mode": "OFF", "tx_seed": None})
    if metadata_override:
        meta.update(metadata_override)
    return iq, meta, (None if off else (reference_seed if reference_seed is not None else seed))


def fixture_catalog() -> list[Dict]:
    """Named fixture definitions used by tests/evidence."""
    out = []
    for seed in (1, 3, 39, 127):
        for phase in (0.0, 45.0, 90.0, 180.0):
            out.append({"name": f"clean-seed{seed}-phase{int(phase)}",
                        "kind": "clean", "seed": seed, "phase_deg": phase})
    for seed in (39, 76):
        for cfo in (-80_000.0, -20_000.0, 20_000.0, 80_000.0):
            out.append({"name": f"cfo-seed{seed}-{int(cfo)}",
                        "kind": "clean", "seed": seed, "cfo_hz": cfo})
    for tau in range(20):
        out.append({"name": f"timing-{tau}", "kind": "clean", "seed": 39,
                    "timing_offset": tau})
    out += [
        {"name": "noise-clean", "kind": "clean", "seed": 39, "noise_rms": 4.0},
        {"name": "wrong-reference", "kind": "wrong_ref", "seed": 39,
         "random_reference": True},
        {"name": "off-noise", "kind": "off", "seed": 39, "off": True},
        {"name": "clipped", "kind": "clipped", "seed": 39, "clip": True,
         "amplitude": 4000.0},
        {"name": "low-energy", "kind": "low_energy", "seed": 39, "amplitude": 0.1},
        {"name": "domain-out-of-range", "kind": "domain", "seed": 39,
         "metadata_override": {"sample_rate_hz": 30_000_000}},
        {"name": "length-wrong", "kind": "length", "seed": 39, "length": 2048},
    ]
    return out
