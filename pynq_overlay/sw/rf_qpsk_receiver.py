"""Finite QPSK/RECT snapshot receiver (no model, IO or TX state access).

Supported snapshot: 4096 signed-12 I/Q samples, 60 MSPS, 3 MSym/s, RECT.
The wire interleave order is explicit (``QpskConfig.iq_order``, default ``"IQ"``);
real captured files in this project use ``"QI"``.  The frozen TX frame is exactly
127 QPSK symbols (2540 samples); a 4096-sample capture can contain a non-signal
tail, which is never included in the reference timing/BER window.

The first 63 symbols (126 bits) are used only for timing/reference alignment.
The final BER starts at symbol 63 and covers the remaining 64 symbols (128 bits);
the alignment segment is never reused or re-aligned.  Missing/weak references and
unsynchronized frames never produce a fabricated BER.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from rf_iq_signal import map_bits_to_symbol, prbs7_bits
from rf_modem_result import MAX_BIT_PREVIEW, MAX_SYMBOL_PREVIEW, ModemResult
from rf_rx_types import RxIqFrame

VERSION = "qpsk-rect-snapshot-v1"
SPS = 20
SYMBOL_RATE_HZ = 3_000_000
SAMPLE_RATE_HZ = 60_000_000
SAMPLES = 4096
MAX_CFO_HZ = 100_000
CFO_COARSE_STEP_HZ = 5_000
CFO_FINE_STEP_HZ = 500
CFO_FINE_SPAN_HZ = 5_000
ALIGNMENT_SYMBOLS = 63
ALIGNMENT_BITS = 2 * ALIGNMENT_SYMBOLS
PRBS_SYMBOLS = 127
MIN_COMPARE_SYMBOLS = 16
SYNC_CORRELATION_MIN = 0.80
CLIP_MIN = -2048
CLIP_MAX = 2047


@dataclass(frozen=True)
class QpskConfig:
    reference_prbs7_seed: Optional[int] = None
    mode: str = "QPSK"
    shaping: str = "RECT"
    symbol_rate_hz: int = SYMBOL_RATE_HZ
    iq_order: str = "IQ"


def _identity(frame):
    return dict(capture_id=frame.capture_id, release_id=frame.release_id,
                config_revision=frame.config_revision,
                connection_generation=frame.connection_generation,
                completed_at_monotonic=frame.completed_at_monotonic,
                mode="QPSK", shaping="RECT",
                sample_rate_hz=frame.sample_rate_hz,
                symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples)


def _refusal(frame, reason, **updates):
    return ModemResult.unavailable(reason=reason, algorithm_version=VERSION,
                                   **_identity(frame), **updates)


def _reference_symbols(seed):
    bits = prbs7_bits(seed, 2 * PRBS_SYMBOLS)
    return np.array([complex(*map_bits_to_symbol("QPSK", bits[2 * i:2 * i + 2]))
                     for i in range(PRBS_SYMBOLS)], dtype=complex)


def _bits_from_symbols(symbols):
    """QPSK bits: first bit -> I, second -> Q; 1=positive, 0=negative."""
    z = np.asarray(symbols, dtype=complex)
    bits = np.empty(2 * len(z), dtype=int)
    bits[0::2] = (z.real >= 0.0).astype(int)
    bits[1::2] = (z.imag >= 0.0).astype(int)
    return bits


def _estimate_cfo(z, sample_rate_hz):
    """Fourth-power lag correlation CFO estimate (no data decisions)."""
    fourth = z ** 4
    lag = 5
    product = fourth[lag:] * np.conj(fourth[:-lag])
    estimate = np.angle(np.mean(product)) * sample_rate_hz / (
        2.0 * np.pi * lag * 4.0)
    return float(estimate)


def _symbol_phase(soft):
    """Common phase of a QPSK symbol vector (fourth power is -4)."""
    return float((np.angle(np.mean(np.asarray(soft) ** 4)) - np.pi) / 4.0)


def _search_reference(z, seed, sample_rate_hz):
    """Coarse/fine reference-assisted CFO + timing search over 127 symbols.

    Correlation uses the first 63 symbols only.  Candidate CFOs are the target
    ±100 kHz grid; the final BER never participates in this search.
    """
    ref = _reference_symbols(seed)
    refm = np.array([[ref[(r + i) % PRBS_SYMBOLS]
                      for i in range(ALIGNMENT_SYMBOLS)]
                     for r in range(PRBS_SYMBOLS)], dtype=complex)
    symbol_t = np.arange(ALIGNMENT_SYMBOLS) * SPS
    best = None
    for tau in range(SPS):
        base = z[tau:tau + PRBS_SYMBOLS * SPS].reshape(PRBS_SYMBOLS, SPS).mean(axis=1)
        for cfo in range(-MAX_CFO_HZ, MAX_CFO_HZ + 1, CFO_COARSE_STEP_HZ):
            soft = base[:ALIGNMENT_SYMBOLS] * np.exp(
                -2j * np.pi * cfo * symbol_t / sample_rate_hz)
            scores = np.abs(refm.conj() @ soft) / (
                np.linalg.norm(soft) * np.sqrt(2.0 * ALIGNMENT_SYMBOLS))
            index = int(np.argmax(scores))
            score = float(scores[index])
            if best is None or score > best[0]:
                best = (score, tau, int(cfo), index)
    _, tau, cfo0, index0 = best
    # Refine only the winning timing phase; keep the reference/phase selection
    # outside the final BER interval.
    lo = cfo0 - CFO_FINE_SPAN_HZ
    hi = cfo0 + CFO_FINE_SPAN_HZ
    for cfo in range(lo, hi + 1, CFO_FINE_STEP_HZ):
        base = z[tau:tau + PRBS_SYMBOLS * SPS].reshape(PRBS_SYMBOLS, SPS).mean(axis=1)
        soft = base[:ALIGNMENT_SYMBOLS] * np.exp(
            -2j * np.pi * cfo * symbol_t / sample_rate_hz)
        scores = np.abs(refm.conj() @ soft) / (
            np.linalg.norm(soft) * np.sqrt(2.0 * ALIGNMENT_SYMBOLS))
        index = int(np.argmax(scores))
        score = float(scores[index])
        if score > best[0]:
            best = (score, tau, int(cfo), index)
    corr, tau, cfo, ref_offset = best
    base = z[tau:tau + PRBS_SYMBOLS * SPS].reshape(PRBS_SYMBOLS, SPS).mean(axis=1)
    soft = base * np.exp(-2j * np.pi * cfo * np.arange(PRBS_SYMBOLS) * SPS
                         / sample_rate_hz)
    expected = ref[(ref_offset + np.arange(ALIGNMENT_SYMBOLS)) % PRBS_SYMBOLS]
    phase = float(np.angle(np.vdot(expected, soft[:ALIGNMENT_SYMBOLS])))
    soft = soft * np.exp(-1j * phase)
    return {"correlation": corr, "timing_offset": tau, "cfo_hz": float(cfo),
            "reference_offset": ref_offset, "phase_rad": phase, "symbols": soft}


def _no_reference(frame, z):
    """Recover timing/CFO but report no BER (valid_symbols may exceed 127)."""
    power = float(np.mean(np.abs(z) ** 2))
    cfo = _estimate_cfo(z, frame.sample_rate_hz)
    if not np.isfinite(cfo) or abs(cfo) > MAX_CFO_HZ + 1000.0:
        return _refusal(frame, "CARRIER_NOT_SYNCED",
                        cfo_hz=cfo if np.isfinite(cfo) else None)
    count = (len(z) - (SPS - 1)) // SPS
    if count < ALIGNMENT_SYMBOLS + MIN_COMPARE_SYMBOLS:
        return _refusal(frame, "TIMING_NOT_SYNCED", cfo_hz=cfo)
    n = np.arange(len(z), dtype=float)
    corrected = z * np.exp(-2j * np.pi * cfo * n / frame.sample_rate_hz)
    coherence = float(abs(np.mean(corrected ** 4))) / (power * power)
    if coherence < 0.30:
        return _refusal(frame, "CARRIER_NOT_SYNCED", cfo_hz=cfo)
    best_offset, best_quality, best_symbols = 0, -1.0, None
    for offset in range(SPS):
        symbols = corrected[offset:offset + count * SPS].reshape(count, SPS).mean(axis=1)
        quality = float(np.mean(np.abs(symbols[:ALIGNMENT_SYMBOLS]) ** 2)) / power
        if quality > best_quality:
            best_offset, best_quality, best_symbols = offset, quality, symbols
    if best_quality < 0.55:
        return _refusal(frame, "TIMING_NOT_SYNCED", cfo_hz=cfo,
                        timing_offset_samples=best_offset)
    symbols = best_symbols * np.exp(-1j * _symbol_phase(best_symbols[:ALIGNMENT_SYMBOLS]))
    preview = tuple((float(v.real), float(v.imag))
                    for v in symbols[:MAX_SYMBOL_PREVIEW])
    return ModemResult(**_identity(frame), cfo_hz=cfo,
                       timing_offset_samples=best_offset, carrier_sync=True,
                       timing_sync=True, reference_sync=None,
                       reason="NO_REFERENCE", valid_symbols=count,
                       symbol_preview=preview, algorithm_version=VERSION)


def receive_qpsk(frame, config) -> ModemResult:
    if not isinstance(frame, RxIqFrame) or not isinstance(config, QpskConfig):
        raise TypeError("expected RxIqFrame and QpskConfig")
    if (config.mode != "QPSK" or config.shaping != "RECT"
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
    if len(z) < PRBS_SYMBOLS * SPS:
        return _refusal(frame, "TIMING_NOT_SYNCED")

    found = _search_reference(z, seed, frame.sample_rate_hz)
    if found["correlation"] < SYNC_CORRELATION_MIN:
        return _refusal(frame, "REFERENCE_NOT_FOUND",
                        cfo_hz=found["cfo_hz"],
                        timing_offset_samples=found["timing_offset"])
    symbols = found["symbols"]
    ref = _reference_symbols(seed)
    expected = ref[(found["reference_offset"] + np.arange(PRBS_SYMBOLS))
                   % PRBS_SYMBOLS]
    decisions = _bits_from_symbols(symbols)
    expected_bits = _bits_from_symbols(expected)
    start = 2 * ALIGNMENT_SYMBOLS
    measured = decisions[start:]
    expected_final = expected_bits[start:]
    errors = int(np.count_nonzero(measured != expected_final))
    compared = int(len(measured))
    preview = tuple((float(v.real), float(v.imag))
                    for v in symbols[:MAX_SYMBOL_PREVIEW])
    phase_ambiguity = int(round((found["phase_rad"] - np.pi / 4.0)
                                / (np.pi / 2.0))) % 4
    return ModemResult(**_identity(frame), cfo_hz=found["cfo_hz"],
                       timing_offset_samples=found["timing_offset"],
                       phase_ambiguity=phase_ambiguity,
                       carrier_sync=True, timing_sync=True, reference_sync=True,
                       valid_symbols=PRBS_SYMBOLS, symbol_preview=preview,
                       bit_preview=tuple(int(v) for v in decisions[:MAX_BIT_PREVIEW]),
                       alignment_bits=ALIGNMENT_BITS,
                       comparison_start_symbol=ALIGNMENT_SYMBOLS,
                       bit_errors=errors, compared_bits=compared,
                       evm_available=False, evm_rms=None,
                       algorithm_version=VERSION)
