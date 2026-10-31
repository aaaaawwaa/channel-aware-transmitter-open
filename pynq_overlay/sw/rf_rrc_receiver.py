"""Finite BPSK/QPSK RRC (alpha=0.35, sps=20) snapshot receiver.

The TX contract is the actual ``rf_iq_signal`` one, not a textbook RRC:
``rrc_taps`` returns ``8*sps+1`` unit-energy taps centred at sample 80,
``_rrc_pulse`` circularly left-shifts them by 80 (peak at lag 0), convolves a
2540-sample cyclic frame, peak-normalises to ``amplitude`` and quantises with
round-to-nearest-even.  The matched filter below is the circular time reverse of
that shifted pulse, so the symbol peaks stay at sample ``n*sps`` (group delay 0).
The first/last half-filter span (4 symbols) is excluded as filter transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from rf_iq_signal import map_bits_to_symbol, prbs7_bits, rrc_taps
from rf_modem_result import MAX_BIT_PREVIEW, MAX_SYMBOL_PREVIEW, REASON_CODES, ModemResult
from rf_rx_types import RxIqFrame

VERSION = "rrc-alpha035-sps20-v3"
SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
SPS = 20
ROLLOFF = 0.35
TX_SYMBOLS = 127
TX_SAMPLES = TX_SYMBOLS * SPS
CAPTURE_SAMPLES = 4096
TAPS = 8 * SPS + 1
GROUP_DELAY_SAMPLES = 0
TRANSITION_SYMBOLS = (TAPS // 2) // SPS
VALID_SYMBOL_START = TRANSITION_SYMBOLS
VALID_SYMBOL_END = TX_SYMBOLS - TRANSITION_SYMBOLS
ALIGNMENT_SYMBOLS = 63
ALIGNMENT_START = VALID_SYMBOL_START
COMPARISON_START_SYMBOL = ALIGNMENT_START + ALIGNMENT_SYMBOLS
COMPARISON_END_SYMBOL = VALID_SYMBOL_END
MAX_CFO_HZ = 100_000
CFO_COARSE_STEP_HZ = 20_000
CFO_FINE_STEP_HZ = 1_000
CFO_FINE_SPAN_HZ = 20_000
SYNC_CORRELATION_MIN = 0.80


@dataclass(frozen=True)
class RrcConfig:
    mode: str = "BPSK"
    reference_prbs7_seed: Optional[int] = None
    rolloff: float = ROLLOFF
    sps: int = SPS
    sample_rate_hz: int = SAMPLE_RATE_HZ
    symbol_rate_hz: int = SYMBOL_RATE_HZ
    iq_order: str = "IQ"

    def __post_init__(self):
        if self.mode not in ("BPSK", "QPSK"):
            raise ValueError("mode must be BPSK or QPSK")
        if self.reference_prbs7_seed is not None and (
                type(self.reference_prbs7_seed) is not int
                or not 1 <= self.reference_prbs7_seed <= 127):
            raise ValueError("reference seed must be integer 1..127")
        if self.rolloff != ROLLOFF:
            raise ValueError("only RRC rolloff 0.35 is supported")
        if self.sps != SPS:
            raise ValueError("only sps 20 is supported")
        if self.sample_rate_hz != SAMPLE_RATE_HZ or self.symbol_rate_hz != SYMBOL_RATE_HZ:
            raise ValueError("only 60 MSPS / 3 MSym/s is supported")
        if self.iq_order not in ("IQ", "QI"):
            raise ValueError("iq_order must be explicit IQ or QI")


@dataclass(frozen=True)
class RrcEnvelope:
    result: ModemResult
    config: RrcConfig
    algorithm_version: str
    group_delay_samples: int
    transition_symbols: int
    valid_symbol_start: int
    valid_symbol_end: int
    comparison_start_symbol: Optional[int]
    comparison_end_symbol: Optional[int]


def _identity(frame, config):
    return dict(capture_id=frame.capture_id, release_id=frame.release_id,
                config_revision=frame.config_revision,
                connection_generation=frame.connection_generation,
                completed_at_monotonic=frame.completed_at_monotonic,
                mode=config.mode, shaping="RRC",
                sample_rate_hz=frame.sample_rate_hz,
                symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples)


def _failed(frame, config, reason):
    if reason not in REASON_CODES:
        reason = "NOT_RUN"
    result = ModemResult.unavailable(
        reason=reason, **_identity(frame, config),
        algorithm_version=VERSION)
    return RrcEnvelope(result, config, VERSION, GROUP_DELAY_SAMPLES,
                       TRANSITION_SYMBOLS, VALID_SYMBOL_START,
                       VALID_SYMBOL_END, None, None)


def failed_rrc(frame, config, reason="NOT_RUN"):
    if not isinstance(frame, RxIqFrame) or not isinstance(config, RrcConfig):
        raise TypeError("expected RxIqFrame and RrcConfig")
    return _failed(frame, config, reason)


def _reference_symbols(mode, seed):
    bps = 1 if mode == "BPSK" else 2
    bits = prbs7_bits(seed, TX_SYMBOLS * bps)
    return np.array([complex(*map_bits_to_symbol(
        mode, tuple(bits[i * bps:(i + 1) * bps]))) for i in range(TX_SYMBOLS)],
        dtype=complex)


def _bits_from_symbols(symbols):
    bits = np.empty(2 * len(symbols), dtype=int)
    bits[0::2] = (np.asarray(symbols).real >= 0.0).astype(int)
    bits[1::2] = (np.asarray(symbols).imag >= 0.0).astype(int)
    return bits


def _reference_bank(reference):
    """All 127 alignment shifts and their norms, precomputed once per seed."""
    shifts = np.empty((TX_SYMBOLS, ALIGNMENT_SYMBOLS), dtype=complex)
    for shift in range(TX_SYMBOLS):
        shifts[shift] = reference[
            (shift + np.arange(ALIGNMENT_SYMBOLS)) % TX_SYMBOLS]
    return shifts.conj(), np.linalg.norm(shifts, axis=1)


@lru_cache(maxsize=1)
def _matched_taps():
    """Actual 161-tap matched pulse, kept at its real circular indices."""
    h = rrc_taps(SPS, ROLLOFF)
    shifted = np.roll(h, -(TAPS // 2))
    hf = np.zeros(TX_SAMPLES, dtype=float)
    hf[0] = shifted[0]
    tail = shifted[1:]
    hf[-len(tail):] = tail
    idx = np.nonzero(hf)[0]
    return idx, hf[idx]


@lru_cache(maxsize=1)
def _symbol_index():
    """127 x 161 circular sample indices for symbol times n*sps."""
    taps, values = _matched_taps()
    return ((np.arange(TX_SYMBOLS)[:, None] * SPS - taps[None, :])
            % TX_SAMPLES), values


@lru_cache(maxsize=64)
def _symbol_kernel(cfo_hz):
    """Phase+matched-tap kernel for one CFO; no 2540-point FFT is used."""
    idx, values = _symbol_index()
    return np.exp(-2j * np.pi * cfo_hz * idx / SAMPLE_RATE_HZ) * values[None, :]


@lru_cache(maxsize=32)
def _kernel_stack(cfo_list):
    return np.stack([_symbol_kernel(cfo) for cfo in cfo_list])


def _soft_batch(window, cfo_list):
    """Symbol-rate direct convolution for ordered CFO candidates."""
    idx, _ = _symbol_index()
    values = window[idx]
    return np.einsum("kj,ckj->ck", values, _kernel_stack(tuple(cfo_list)))


def _score_soft(soft, bank):
    """Batch alignment correlation; argmax keeps the first shift on ties."""
    align = soft[:, ALIGNMENT_START:ALIGNMENT_START + ALIGNMENT_SYMBOLS]
    denom = np.linalg.norm(align, axis=1)
    if not np.any(denom > 0.0):
        return None
    shift_conj, ref_norms = bank
    corrs = align @ shift_conj.T
    scores = np.abs(corrs) / (denom[:, None] * ref_norms[None, :])
    scores[denom <= 0.0, :] = -np.inf
    shifts = np.argmax(scores, axis=1)
    return (scores[np.arange(len(soft)), shifts], shifts, soft, corrs)


def _candidate(window, reference, tau, cfo_hz, bank=None):
    """Compatibility wrapper for one candidate (not used by _search)."""
    soft = _soft_batch(window, [cfo_hz])[0]
    score, shifts, _, corrs = _score_soft(
        soft[None, :], bank if bank is not None else _reference_bank(reference))
    shift = int(shifts[0])
    return {"score": float(score[0]),
            "reference_offset": (shift - ALIGNMENT_START) % TX_SYMBOLS,
            "alignment_shift": shift, "phase": float(np.angle(corrs[0, shift])),
            "soft": soft, "tau": tau, "cfo_hz": cfo_hz}


def _search(z, reference):
    bank = _reference_bank(reference)
    cfos = list(range(-MAX_CFO_HZ, MAX_CFO_HZ + 1, CFO_COARSE_STEP_HZ))
    blocks, taus = [], []
    for tau in range(SPS):
        window = z[tau:tau + TX_SAMPLES]
        if len(window) != TX_SAMPLES:
            continue
        blocks.append(_soft_batch(window, cfos))
        taus.extend([tau] * len(cfos))
    if not blocks:
        return None
    soft_all = np.vstack(blocks)
    scored = _score_soft(soft_all, bank)
    if scored is None:
        return None
    scores, shifts, soft, corrs = scored
    best_i = int(np.argmax(scores))
    best = {"score": float(scores[best_i]), "tau": taus[best_i],
            "cfo_hz": cfos[best_i % len(cfos)],
            "shift": int(shifts[best_i]), "corr": corrs[best_i, shifts[best_i]],
            "soft": soft[best_i]}
    # Fine search preserves the old: same tau, ascending CFO, strict > updates.
    lo = int(best["cfo_hz"]) - CFO_FINE_SPAN_HZ
    hi = int(best["cfo_hz"]) + CFO_FINE_SPAN_HZ
    fine_cfos = list(range(lo, hi + 1, CFO_FINE_STEP_HZ))
    window = z[best["tau"]:best["tau"] + TX_SAMPLES]
    fine_soft = _soft_batch(window, fine_cfos)
    fine_scored = _score_soft(fine_soft, bank)
    if fine_scored is None:
        return None
    fine_scores, fine_shifts, fine_all, fine_corrs = fine_scored
    fine_i = int(np.argmax(fine_scores))
    if float(fine_scores[fine_i]) > best["score"]:
        best = {"score": float(fine_scores[fine_i]), "tau": best["tau"],
                "cfo_hz": fine_cfos[fine_i],
                "shift": int(fine_shifts[fine_i]),
                "corr": fine_corrs[fine_i, fine_shifts[fine_i]],
                "soft": fine_all[fine_i]}
    reference_offset = (best["shift"] - ALIGNMENT_START) % TX_SYMBOLS
    best["reference_offset"] = reference_offset
    best["alignment_shift"] = best["shift"]
    best["phase"] = float(np.angle(best.pop("corr")))
    best.pop("shift")
    return best


def receive_rrc(frame, config) -> RrcEnvelope:
    if not isinstance(frame, RxIqFrame) or not isinstance(config, RrcConfig):
        raise TypeError("expected RxIqFrame and RrcConfig")
    if (frame.samples != CAPTURE_SAMPLES
            or frame.sample_rate_hz != SAMPLE_RATE_HZ or frame.ratio != 0
            or len(frame.payload) != CAPTURE_SAMPLES * 4):
        return _failed(frame, config, "INPUT_DOMAIN")
    iq = np.frombuffer(frame.payload, dtype="<i2").reshape(-1, 2)
    if np.any(iq <= -2048) or np.any(iq >= 2047):
        return _failed(frame, config, "CLIPPED")
    if config.iq_order == "QI":
        z = iq[:, 1].astype(float) + 1j * iq[:, 0].astype(float)
    else:
        z = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
    z = z - np.mean(z)
    if not np.isfinite(np.mean(np.abs(z) ** 2)) or np.mean(np.abs(z) ** 2) < 1.0:
        return _failed(frame, config, "LOW_ENERGY")
    seed = config.reference_prbs7_seed
    if seed is None:
        return _failed(frame, config, "NO_REFERENCE")
    # Config already restricts seed range; this is only defensive.
    if type(seed) is not int or not 1 <= seed <= 127:
        return _failed(frame, config, "NO_REFERENCE")
    reference = _reference_symbols(config.mode, seed)
    found = _search(z, reference)
    if found is None:
        return _failed(frame, config, "TIMING_NOT_SYNCED")
    if found["score"] < SYNC_CORRELATION_MIN:
        return _failed(frame, config, "REFERENCE_NOT_FOUND")
    soft = found["soft"] * np.exp(-1j * found["phase"])
    # Alignment uses the first 63 valid symbols; final BER starts after that
    # alignment region and never re-aligns or re-picks a phase.
    final_soft = soft[COMPARISON_START_SYMBOL:COMPARISON_END_SYMBOL]
    expected = reference[(found["reference_offset"]
                          + np.arange(TX_SYMBOLS)) % TX_SYMBOLS]
    expected_final = expected[COMPARISON_START_SYMBOL:COMPARISON_END_SYMBOL]
    decisions = _bits_from_symbols(final_soft)
    expected_bits = _bits_from_symbols(expected_final)
    if config.mode == "BPSK":
        decisions = decisions[0::2]
        expected_bits = expected_bits[0::2]
    errors = int(np.count_nonzero(decisions != expected_bits))
    compared = int(len(expected_bits))
    bits_preview = _bits_from_symbols(soft[VALID_SYMBOL_START:VALID_SYMBOL_END])
    if config.mode == "BPSK":
        bits_preview = bits_preview[0::2]
    preview = tuple((float(v.real), float(v.imag))
                    for v in final_soft[:MAX_SYMBOL_PREVIEW])
    result = ModemResult(
        **_identity(frame, config), carrier_sync=True, timing_sync=True,
        reference_sync=True, cfo_hz=found["cfo_hz"],
        timing_offset_samples=found["tau"], phase_ambiguity=0,
        valid_symbols=VALID_SYMBOL_END - VALID_SYMBOL_START,
        symbol_preview=preview,
        bit_preview=tuple(int(v) for v in bits_preview[:MAX_BIT_PREVIEW]),
        alignment_bits=ALIGNMENT_SYMBOLS * (1 if config.mode == "BPSK" else 2),
        comparison_start_symbol=COMPARISON_START_SYMBOL,
        bit_errors=errors, compared_bits=compared,
        evm_available=False, evm_rms=None, algorithm_version=VERSION)
    return RrcEnvelope(result, config, VERSION, GROUP_DELAY_SAMPLES,
                       TRANSITION_SYMBOLS, VALID_SYMBOL_START,
                       VALID_SYMBOL_END, COMPARISON_START_SYMBOL,
                       COMPARISON_END_SYMBOL)
