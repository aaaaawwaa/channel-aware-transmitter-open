"""Finite QAM16/RECT snapshot receiver (no model, IO or TX state access).

Supported snapshot: 4096 signed-12 I/Q samples, 60 MSPS, 3 MSym/s, QAM16, RECT.
The TX mapping is exactly ``rf_iq_signal.map_bits_to_symbol``:

    I bits b0 b1 -> levels 3, 1, -3, -1
    Q bits b2 b3 -> levels 3, 1, -3, -1
    MSB-first serial PRBS7, first bit is b0.

The wire interleave order is explicit (``Qam16Config.iq_order``, default IQ;
real captures in this project use QI).  The first 63 symbols (252 bits) are used
only for timing/reference/gain/phase alignment.  BER starts at symbol 63 and
covers the remaining 64 symbols (256 bits), never reusing/re-aligning alignment.
No reference, low energy, clipping, domain mismatch or failed reference search
never fabricate a BER or denominator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from rf_iq_signal import map_bits_to_symbol, prbs7_bits
from rf_modem_result import MAX_BIT_PREVIEW, MAX_SYMBOL_PREVIEW, ModemResult
from rf_rx_types import RxIqFrame

VERSION = "qam16-rect-snapshot-v1"
QAM16_VERSION = VERSION
SPS = 20
SYMBOL_RATE_HZ = 3_000_000
SAMPLE_RATE_HZ = 60_000_000
SAMPLES = 4096
PRBS_SYMBOLS = 127
PAYLOAD_SAMPLES = PRBS_SYMBOLS * SPS
ALIGNMENT_SYMBOLS = 63
ALIGNMENT_BITS = 4 * ALIGNMENT_SYMBOLS
COMPARISON_START_SYMBOL = ALIGNMENT_SYMBOLS
COMPARED_SYMBOLS = PRBS_SYMBOLS - ALIGNMENT_SYMBOLS
COMPARED_BITS = 4 * COMPARED_SYMBOLS
MAX_CFO_HZ = 100_000
CFO_COARSE_STEP_HZ = 10_000
CFO_FINE_STEP_HZ = 500
CFO_FINE_SPAN_HZ = 10_000
SYNC_CORRELATION_MIN = 0.80
CLIP_MIN = -2048
CLIP_MAX = 2047
LEVELS = (3.0, 1.0, -3.0, -1.0)


@dataclass(frozen=True)
class Qam16Config:
    reference_prbs7_seed: Optional[int] = None
    mode: str = "QAM16"
    shaping: str = "RECT"
    symbol_rate_hz: int = SYMBOL_RATE_HZ
    iq_order: str = "IQ"


def _identity(frame):
    return dict(capture_id=frame.capture_id, release_id=frame.release_id,
                config_revision=frame.config_revision,
                connection_generation=frame.connection_generation,
                completed_at_monotonic=frame.completed_at_monotonic,
                mode="QAM16", shaping="RECT",
                sample_rate_hz=frame.sample_rate_hz,
                symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples)


def _refusal(frame, reason, **updates):
    return ModemResult.unavailable(reason=reason, algorithm_version=VERSION,
                                   **_identity(frame), **updates)


def _reference_symbols(seed):
    bits = prbs7_bits(seed, 4 * PRBS_SYMBOLS)
    return np.array([complex(*map_bits_to_symbol("QAM16", tuple(bits[4 * i:4 * i + 4])))
                     for i in range(PRBS_SYMBOLS)], dtype=complex)


def _axis_bits(value):
    """Nearest QAM16 axis level -> its two TX bits (MSB first)."""
    v = float(value)
    if v > 2.0:
        return (0, 0)       # +3
    if v > 0.0:
        return (0, 1)       # +1
    if v > -2.0:
        return (1, 1)       # -1
    return (1, 0)           # -3


def _bits_from_symbols(symbols):
    z = np.asarray(symbols, dtype=complex)
    bits = np.empty(4 * len(z), dtype=int)
    for i, v in enumerate(z):
        bits[4 * i:4 * i + 4] = _axis_bits(v.real) + _axis_bits(v.imag)
    return bits


def _decision_axis(value):
    """Quantize a normalized axis to one of +/-1,+/-3 (never a BER shortcut)."""
    v = float(value)
    if v > 2.0:
        return 3.0
    if v > 0.0:
        return 1.0
    if v > -2.0:
        return -1.0
    return -3.0


def _reference_bank(ref):
    """All 127 cyclic reference alignments and their alignment norms."""
    bank = np.empty((PRBS_SYMBOLS, ALIGNMENT_SYMBOLS), dtype=complex)
    for shift in range(PRBS_SYMBOLS):
        bank[shift] = ref[(shift + np.arange(ALIGNMENT_SYMBOLS)) % PRBS_SYMBOLS]
    norms = np.linalg.norm(bank, axis=1)
    return bank, norms


def _search_reference(z, seed, sample_rate_hz):
    """Reference-assisted timing/CFO/reference-offset search on alignment only."""
    ref = _reference_symbols(seed)
    bank, ref_norms = _reference_bank(ref)
    symbol_t = np.arange(PRBS_SYMBOLS) * SPS / sample_rate_hz
    best = None  # (score, tau, cfo, ref_offset, soft)
    for tau in range(SPS):
        if tau + PAYLOAD_SAMPLES > len(z):
            continue
        base = z[tau:tau + PAYLOAD_SAMPLES].reshape(PRBS_SYMBOLS, SPS).mean(axis=1)
        for cfo in range(-MAX_CFO_HZ, MAX_CFO_HZ + 1, CFO_COARSE_STEP_HZ):
            soft = base * np.exp(-2j * np.pi * cfo * symbol_t)
            align = soft[:ALIGNMENT_SYMBOLS]
            denom = float(np.linalg.norm(align))
            if denom <= 0.0:
                continue
            scores = np.abs(bank.conj() @ align) / (denom * ref_norms)
            idx = int(np.argmax(scores))
            score = float(scores[idx])
            if best is None or score > best[0]:
                best = (score, tau, cfo, idx, soft)
    if best is None:
        return None
    _, tau, cfo0, _, _ = best
    for cfo in range(cfo0 - CFO_FINE_SPAN_HZ, cfo0 + CFO_FINE_SPAN_HZ + 1,
                     CFO_FINE_STEP_HZ):
        base = z[tau:tau + PAYLOAD_SAMPLES].reshape(PRBS_SYMBOLS, SPS).mean(axis=1)
        soft = base * np.exp(-2j * np.pi * cfo * symbol_t)
        align = soft[:ALIGNMENT_SYMBOLS]
        denom = float(np.linalg.norm(align))
        if denom <= 0.0:
            continue
        scores = np.abs(bank.conj() @ align) / (denom * ref_norms)
        idx = int(np.argmax(scores))
        score = float(scores[idx])
        if score > best[0]:
            best = (score, tau, cfo, idx, soft)
    score, tau, cfo, offset, soft = best
    expected = ref[(offset + np.arange(PRBS_SYMBOLS)) % PRBS_SYMBOLS]
    reference = expected[:ALIGNMENT_SYMBOLS]
    gain = np.vdot(reference, soft[:ALIGNMENT_SYMBOLS]) / np.vdot(reference, reference)
    if not np.isfinite(gain) or abs(gain) < 1e-9:
        return None
    normalized = soft / gain
    return {"correlation": score, "timing_offset": tau, "cfo_hz": float(cfo),
            "reference_offset": offset, "gain": complex(gain),
            "phase_ambiguity": int(round(np.angle(gain) / (np.pi / 2.0))) % 4,
            "symbols": normalized, "expected": expected}


def _no_reference(frame, z):
    """No reference means no timing/offset/BER claim."""
    return _refusal(frame, "NO_REFERENCE")


def receive_qam16(frame, config) -> ModemResult:
    if not isinstance(frame, RxIqFrame) or not isinstance(config, Qam16Config):
        raise TypeError("expected RxIqFrame and Qam16Config")
    if (config.mode != "QAM16" or config.shaping != "RECT"
            or type(config.symbol_rate_hz) is not int
            or config.symbol_rate_hz != SYMBOL_RATE_HZ
            or config.iq_order not in ("IQ", "QI")):
        return _refusal(frame, "UNSUPPORTED")
    if (frame.samples != SAMPLES or frame.sample_rate_hz != SAMPLE_RATE_HZ
            or frame.ratio != 0 or len(frame.payload) != SAMPLES * 4):
        return _refusal(frame, "INPUT_DOMAIN")
    iq = np.frombuffer(frame.payload, dtype="<i2").reshape(-1, 2)
    if np.any(iq <= CLIP_MIN) or np.any(iq >= CLIP_MAX):
        return _refusal(frame, "CLIPPED")
    if config.iq_order == "QI":
        z = iq[:, 1].astype(float) + 1j * iq[:, 0].astype(float)
    else:
        z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
    z = z - np.mean(z)
    power = float(np.mean(np.abs(z) ** 2))
    if not np.isfinite(power) or power < 1.0:
        return _refusal(frame, "LOW_ENERGY")

    seed = config.reference_prbs7_seed
    if seed is None:
        return _no_reference(frame, z)
    if type(seed) is not int or not 1 <= seed <= 127:
        return _refusal(frame, "REFERENCE_NOT_FOUND")
    if len(z) < PAYLOAD_SAMPLES:
        return _refusal(frame, "TIMING_NOT_SYNCED")

    found = _search_reference(z, seed, frame.sample_rate_hz)
    if found is None or found["correlation"] < SYNC_CORRELATION_MIN:
        return _refusal(frame, "REFERENCE_NOT_FOUND",
                        cfo_hz=found["cfo_hz"] if found else None,
                        timing_offset_samples=found["timing_offset"] if found else None)
    soft = found["symbols"]
    expected = found["expected"]
    decisions = _bits_from_symbols(soft)
    expected_bits = _bits_from_symbols(expected)
    start = 4 * COMPARISON_START_SYMBOL
    measured = decisions[start:]
    expected_final = expected_bits[start:]
    errors = int(np.count_nonzero(measured != expected_final))
    compared = int(len(measured))
    preview = tuple((float(v.real), float(v.imag))
                    for v in soft[:MAX_SYMBOL_PREVIEW])
    return ModemResult(**_identity(frame), cfo_hz=found["cfo_hz"],
                       timing_offset_samples=found["timing_offset"],
                       phase_ambiguity=found["phase_ambiguity"],
                       carrier_sync=True, timing_sync=True, reference_sync=True,
                       valid_symbols=PRBS_SYMBOLS, symbol_preview=preview,
                       bit_preview=tuple(int(v) for v in decisions[:MAX_BIT_PREVIEW]),
                       alignment_bits=ALIGNMENT_BITS,
                       comparison_start_symbol=COMPARISON_START_SYMBOL,
                       bit_errors=errors, compared_bits=compared,
                       evm_available=False, evm_rms=None,
                       algorithm_version=VERSION)

