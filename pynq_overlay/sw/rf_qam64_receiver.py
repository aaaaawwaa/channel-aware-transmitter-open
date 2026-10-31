"""Finite QAM64/RECT snapshot receiver (no model, IO or TX state access).

Supported snapshot: 4096 signed-12 I/Q samples, 60 MSPS, 3 MSym/s, QAM64, RECT.
The TX mapping is exactly ``rf_iq_signal.map_bits_to_symbol``:

    I bits b0 b1 b2 -> levels (7, 5, 1, 3, -7, -5, -1, -3)
    Q bits b3 b4 b5 -> the same table
    MSB-first serial PRBS7, first bit is b0.

The wire interleave order is explicit (``Qam64Config.iq_order``, default IQ;
real captures in this project use QI).  The first 63 symbols (378 bits) are used
only for timing/CFO/reference/gain/phase alignment.  BER starts at symbol 63 and
covers the remaining 64 symbols (384 bits), never reusing/re-aligning alignment.
No reference, low energy, clipping, domain mismatch or failed reference search
never fabricate a BER or denominator.  EVM is not implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from rf_iq_signal import map_bits_to_symbol, prbs7_bits
from rf_modem_result import MAX_BIT_PREVIEW, MAX_SYMBOL_PREVIEW, ModemResult
from rf_rx_types import RxIqFrame

VERSION = "qam64-rect-snapshot-v2"
QAM64_VERSION = VERSION
SPS = 20
SYMBOL_RATE_HZ = 3_000_000
SAMPLE_RATE_HZ = 60_000_000
SAMPLES = 4096
PRBS_SYMBOLS = 127
PAYLOAD_SAMPLES = PRBS_SYMBOLS * SPS
BITS_PER_SYMBOL = 6
ALIGNMENT_SYMBOLS = 63
ALIGNMENT_BITS = BITS_PER_SYMBOL * ALIGNMENT_SYMBOLS
COMPARISON_START_SYMBOL = ALIGNMENT_SYMBOLS
COMPARED_SYMBOLS = PRBS_SYMBOLS - ALIGNMENT_SYMBOLS
COMPARED_BITS = BITS_PER_SYMBOL * COMPARED_SYMBOLS
MAX_CFO_HZ = 100_000
CFO_COARSE_STEP_HZ = 10_000
CFO_FINE_STEP_HZ = 500
CFO_FINE_SPAN_HZ = 10_000
SYNC_CORRELATION_MIN = 0.80
# Alignment-only model selection.  No BER or comparison symbol enters these
# thresholds.  Fine CFO is only accepted when it materially lowers alignment
# RMSE; otherwise the zero-CFO model is retained.
AFFINE_CONDITION_MAX = 2.0
AFFINE_LINEAR_MAX = 8.0
AFFINE_DC_MAX = 1024.0
AFFINE_RMSE_IMPROVEMENT = 0.01
CFO_RMSE_IMPROVEMENT = 0.01
FINE_CFO_STEP_HZ = 25.0
FINE_CFO_SPAN_HZ = 2_000.0
CLIP_MIN = -2048
CLIP_MAX = 2047
LEVELS = (7.0, 5.0, 1.0, 3.0, -7.0, -5.0, -1.0, -3.0)


@dataclass(frozen=True)
class Qam64Config:
    reference_prbs7_seed: Optional[int] = None
    mode: str = "QAM64"
    shaping: str = "RECT"
    symbol_rate_hz: int = SYMBOL_RATE_HZ
    iq_order: str = "IQ"


def _identity(frame):
    return dict(capture_id=frame.capture_id, release_id=frame.release_id,
                config_revision=frame.config_revision,
                connection_generation=frame.connection_generation,
                completed_at_monotonic=frame.completed_at_monotonic,
                mode="QAM64", shaping="RECT",
                sample_rate_hz=frame.sample_rate_hz,
                symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples)


def _refusal(frame, reason, **updates):
    return ModemResult.unavailable(reason=reason, algorithm_version=VERSION,
                                   **_identity(frame), **updates)


def _reference_symbols(seed):
    bits = prbs7_bits(seed, BITS_PER_SYMBOL * PRBS_SYMBOLS)
    return np.array([
        complex(*map_bits_to_symbol("QAM64", tuple(
            bits[BITS_PER_SYMBOL * i:BITS_PER_SYMBOL * (i + 1)])))
        for i in range(PRBS_SYMBOLS)
    ], dtype=complex)


def _axis_bits(value):
    """Nearest QAM64 axis level -> its three TX bits (MSB first)."""
    v = float(value)
    if v > 6.0:
        return (0, 0, 0)       # +7
    if v > 4.0:
        return (0, 0, 1)       # +5
    if v > 2.0:
        return (0, 1, 1)       # +3
    if v > 0.0:
        return (0, 1, 0)       # +1
    if v > -2.0:
        return (1, 1, 0)       # -1
    if v > -4.0:
        return (1, 1, 1)       # -3
    if v > -6.0:
        return (1, 0, 1)       # -5
    return (1, 0, 0)           # -7


def _bits_from_symbols(symbols):
    z = np.asarray(symbols, dtype=complex)
    bits = np.empty(BITS_PER_SYMBOL * len(z), dtype=int)
    for i, v in enumerate(z):
        bits[BITS_PER_SYMBOL * i:BITS_PER_SYMBOL * (i + 1)] = (
            _axis_bits(v.real) + _axis_bits(v.imag))
    return bits


def _decision_axis(value):
    """Quantize a normalized axis to one of the eight levels."""
    v = float(value)
    if v > 6.0:
        return 7.0
    if v > 4.0:
        return 5.0
    if v > 2.0:
        return 3.0
    if v > 0.0:
        return 1.0
    if v > -2.0:
        return -1.0
    if v > -4.0:
        return -3.0
    if v > -6.0:
        return -5.0
    return -7.0


def _reference_bank(ref):
    """All 127 cyclic reference alignments and their alignment norms."""
    bank = np.empty((PRBS_SYMBOLS, ALIGNMENT_SYMBOLS), dtype=complex)
    for shift in range(PRBS_SYMBOLS):
        bank[shift] = ref[(shift + np.arange(ALIGNMENT_SYMBOLS)) % PRBS_SYMBOLS]
    norms = np.linalg.norm(bank, axis=1)
    return bank, norms


def _fit_alignment_affine(soft, expected):
    """Least-squares I/Q 2x2 affine + DC from alignment symbols only."""
    z = np.asarray(soft, dtype=complex)
    ref = np.asarray(expected, dtype=complex)
    design = np.column_stack([z.real, z.imag, np.ones(len(z), dtype=float)])
    target = np.column_stack([ref.real, ref.imag])
    mat, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    pred = design @ mat
    rmse = float(np.sqrt(np.mean(np.sum((pred - target) ** 2, axis=1))))
    linear = mat[:2, :2]
    try:
        cond = float(np.linalg.cond(linear))
    except np.linalg.LinAlgError:
        cond = float("inf")
    finite = bool(np.all(np.isfinite(mat)))
    scalar_gain = np.vdot(ref, z) / np.vdot(ref, ref)
    scalar = z / scalar_gain if abs(scalar_gain) > 1e-12 else z
    scalar_rmse = float(np.sqrt(np.mean(np.abs(scalar - ref) ** 2)))
    return {"affine": mat, "affine_rmse": rmse, "scalar_rmse": scalar_rmse,
            "scalar_gain": complex(scalar_gain), "condition": cond,
            "rank": int(rank), "finite": finite, "pred": pred}


def _select_alignment_model(found, expected):
    """Choose CFO/model from alignment RMSE only, with fixed predeclared rules.

    The comparison region [63,127) is not passed to this function at all.
    """
    base = found["base"]
    symbol_t = np.arange(PRBS_SYMBOLS) * SPS / SAMPLE_RATE_HZ
    coarse = float(found["cfo_hz"])
    candidates = {0.0}
    fine = coarse + np.arange(-FINE_CFO_SPAN_HZ, FINE_CFO_SPAN_HZ + 0.1,
                              FINE_CFO_STEP_HZ)
    candidates.update(float(v) for v in fine)
    best = None  # (rmse, cfo, model)
    zero = None
    for cfo in sorted(candidates):
        soft = base * np.exp(-2j * np.pi * cfo * symbol_t)
        model = _fit_alignment_affine(soft[:ALIGNMENT_SYMBOLS - 1],
                                      expected[:ALIGNMENT_SYMBOLS - 1])
        if cfo == 0.0:
            zero = model
        if best is None or model["affine_rmse"] < best[0]:
            best = (model["affine_rmse"], cfo, model)
    if zero is None:
        zero = _fit_alignment_affine(base[:ALIGNMENT_SYMBOLS - 1],
                                     expected[:ALIGNMENT_SYMBOLS - 1])
    # Keep zero CFO unless a nonzero candidate gives a material alignment
    # improvement; this prevents a noise-induced 500 Hz branch from winning.
    if best[1] != 0.0 and zero["affine_rmse"] > 0.0:
        gain = (zero["affine_rmse"] - best[0]) / zero["affine_rmse"]
        if gain < CFO_RMSE_IMPROVEMENT:
            best = (zero["affine_rmse"], 0.0, zero)
    rmse, cfo, model = best
    soft_all = base * np.exp(-2j * np.pi * cfo * symbol_t)
    # Affine is selected only on a material alignment-RMSE improvement and
    # only if its 2x2 I/Q matrix is well-conditioned and numerically sane.
    use_affine = (model["scalar_rmse"] > 0.0 and
                  model["affine_rmse"] <= model["scalar_rmse"] *
                  (1.0 - AFFINE_RMSE_IMPROVEMENT))
    if use_affine:
        linear = model["affine"][:2, :2]
        dc = model["affine"][2, :]
        if (not model["finite"] or model["condition"] > AFFINE_CONDITION_MAX
                or np.max(np.abs(linear)) > AFFINE_LINEAR_MAX
                or np.linalg.norm(dc) > AFFINE_DC_MAX):
            return None, "ILL_CONDITIONED"
        design = np.column_stack([soft_all.real, soft_all.imag,
                                  np.ones(PRBS_SYMBOLS)])
        pred = design @ model["affine"]
        corrected = pred[:, 0] + 1j * pred[:, 1]
        kind = "affine"
        phase = int(round(np.angle(model["scalar_gain"]) / (np.pi / 2.0))) % 4
    else:
        scalar_gain = model["scalar_gain"]
        if not np.isfinite(scalar_gain) or abs(scalar_gain) < 1e-9:
            return None, "REFERENCE_NOT_FOUND"
        corrected = soft_all / scalar_gain
        kind = "scalar"
        phase = int(round(np.angle(scalar_gain) / (np.pi / 2.0))) % 4
    return {"corrected": corrected, "kind": kind, "cfo_hz": float(cfo),
            "rmse": float(rmse), "condition": model["condition"],
            "scalar_rmse": model["scalar_rmse"], "affine_rmse": model["affine_rmse"],
            "linear": model["affine"][:2, :2].tolist(),
            "dc": model["affine"][2, :].tolist(),
            "phase_ambiguity": phase}, ""


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
            "symbols": normalized, "raw_symbols": soft, "base": base,
            "expected": expected}


def _no_reference(frame, z):
    return _refusal(frame, "NO_REFERENCE")


def receive_qam64(frame, config) -> ModemResult:
    if not isinstance(frame, RxIqFrame) or not isinstance(config, Qam64Config):
        raise TypeError("expected RxIqFrame and Qam64Config")
    if (config.mode != "QAM64" or config.shaping != "RECT"
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
    # Do NOT subtract the mean of the whole frame: the comparison region is a
    # measurement-only region and a damaged comparison symbol must not move the
    # alignment/reference search or CFO.  The fixed contract has no DC term and
    # the alignment/reference search estimates its own carrier phase and gain;
    # leave the raw samples untouched here rather than reading any comparison
    # sample for a frame-wide mean.
    # Only the alignment region is allowed to gate energy; comparison samples
    # are measured afterwards and never influence synchronization.
    power = float(np.mean(np.abs(z[:ALIGNMENT_SYMBOLS * SPS]) ** 2))
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
    selected, reason = _select_alignment_model(found, found["expected"])
    if selected is None:
        return _refusal(frame, reason, cfo_hz=found["cfo_hz"],
                        timing_offset_samples=found["timing_offset"])
    soft = selected["corrected"]
    expected = found["expected"]
    decisions = _bits_from_symbols(soft)
    expected_bits = _bits_from_symbols(expected)
    start = BITS_PER_SYMBOL * COMPARISON_START_SYMBOL
    measured = decisions[start:]
    expected_final = expected_bits[start:]
    errors = int(np.count_nonzero(measured != expected_final))
    compared = int(len(measured))
    preview = tuple((float(v.real), float(v.imag))
                    for v in soft[:MAX_SYMBOL_PREVIEW])
    return ModemResult(**_identity(frame), cfo_hz=selected["cfo_hz"],
                       timing_offset_samples=found["timing_offset"],
                       phase_ambiguity=selected["phase_ambiguity"],
                       carrier_sync=True, timing_sync=True, reference_sync=True,
                       valid_symbols=PRBS_SYMBOLS, symbol_preview=preview,
                       bit_preview=tuple(int(v) for v in decisions[:MAX_BIT_PREVIEW]),
                       alignment_bits=ALIGNMENT_BITS,
                       comparison_start_symbol=COMPARISON_START_SYMBOL,
                       bit_errors=errors, compared_bits=compared,
                       evm_available=False, evm_rms=None,
                       algorithm_version=VERSION)
