"""Independent pure-software BPSK research: reference demodulator and metrics.

This module is deliberately INDEPENDENT of the frozen receiver: it re-derives the
BPSK/RECT/PRBS7 processing from the SPEC (PRBS7 LFSR + RECT mapping, CFO via the
squaring spectral line, phase via the squared moment, timing via symbol-energy
search, correlation against an independently generated PRBS7 stream).  It is used
for two things:

1.  the expected outcome of every offline reference vector (so a vector's expected
    demodulation is never produced by the frozen implementation it is checking);
2.  the "purely software receiving algorithm" research surface: candidate
    estimators/metrics (matched-filter timing, CFO estimator, polarity/alignment,
    EVM, soft LLR) evaluated on the same vectors, with numbers.

Nothing here is imported by the frozen production path, and nothing writes to it.

    python -m pynq_overlay.sw.rf_bpsk_research  # not a CLI: import/use only
"""
import math

import numpy as np

FS_HZ = 60000000
SYMRATE_HZ = 3000000
SPS = FS_HZ // SYMRATE_HZ
ALIGNMENT_BITS = 63
COMPARE_BITS = 138
MAX_CFO_HZ = 100000
COHERENCE_MIN = 0.70
TIMING_QUALITY_MIN = 0.55
REFERENCE_CORRELATION_MIN = 0.80
IQ_CLIP_SENTINELS = (-2048, 2047)


def prbs7_bits(seed, count):
    """Spec PRBS7: current bit = bit 6; next = {bits[5:0], bit6^bit5}."""
    if not isinstance(seed, int) or not 1 <= seed <= 127:
        raise ValueError('seed must be 1..127')
    state = seed & 0x7F
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) ^ (state >> 5)) & 1)
    return out


def reference_symbols(seed, count=127):
    """The spec's +-1 reference for a seed (bit 0 -> +1, bit 1 -> -1)."""
    return np.asarray([1 - 2 * bit for bit in prbs7_bits(seed, count)], dtype=float)


def iq_from_bytes(payload):
    """s16_iq_le payload -> complex array (I + jQ)."""
    pairs = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
    return pairs[:, 0].astype(float) + 1j * pairs[:, 1].astype(float)


def _result(reason, **fields):
    base = {'reason': reason, 'reference_sync': None, 'bit_errors': None,
            'compared_bits': None, 'total_symbols': 0, 'cfo_hz': None,
            'carrier_coherence': None, 'timing_quality': None,
            'timing_offset_samples': None, 'phase_mod_pi': None,
            'alignment_offset': None, 'polarity': None}
    base.update(fields)
    return base


def estimate_cfo_hz(z, fs_hz=FS_HZ):
    """CFO from the BPSK squaring line: FFT coarse bin + weighted phase-slope fine.

    The FFT grid alone cannot resolve small offsets (bin = fs/(8N) ~ 1.8 kHz here),
    so the fine part is the weighted slope of the unwrapped phase of block-averaged
    z^2 after derotating by the coarse estimate.  Block size 64 keeps the residual
    phase step well below pi for |CFO| <= 100 kHz.
    """
    squared = z * z
    n = len(squared)
    fft_len = 8 * n
    spectrum = np.abs(np.fft.fft(squared, n=fft_len))
    peak = int(np.argmax(spectrum))
    freq_per_bin = fs_hz / float(fft_len)
    signed_bin = peak if peak < fft_len // 2 else peak - fft_len
    coarse = signed_bin * freq_per_bin / 2.0          # z^2 carries 2*CFO
    if coarse == 0.0 and peak == 0:
        # DC peak: still refine, there is no reliable bin to wrap yet
        coarse = 0.0
    positions = np.arange(n, dtype=float)
    derotated = squared * np.exp(-2j * np.pi * 2.0 * coarse * positions / fs_hz)
    block = 64
    rows = len(derotated) // block
    if rows < 8:
        return float(coarse)
    blocks = derotated[:rows * block].reshape(rows, block).mean(axis=1)
    block_positions = (np.arange(rows, dtype=float) + 0.5) * block
    phase = np.unwrap(np.angle(blocks))
    weights = np.abs(blocks)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 0:
        return float(coarse)
    center = float(np.sum(weights * block_positions)) / weight_sum
    phase_center = float(np.sum(weights * phase)) / weight_sum
    denom = float(np.sum(weights * (block_positions - center) ** 2))
    if denom <= 0:
        return float(coarse)
    slope = float(np.sum(weights * (block_positions - center)
                         * (phase - phase_center))) / denom
    return float(coarse + slope * fs_hz / (4.0 * np.pi))

def fractional_delay(z, delay_samples):
    """Fractional (sub-sample) delay by an FFT phase ramp; delay > 0 is causal."""
    n = len(z)
    freqs = np.fft.fftfreq(n)
    shifted = np.fft.ifft(np.fft.fft(z) * np.exp(-2j * np.pi * freqs * delay_samples))
    return shifted


def matched_filter_symbols(z, sps=SPS):
    """Best integer timing offset by maximum symbol energy (RECT matched filter)."""
    count = (len(z) - (sps - 1)) // sps
    best = None
    for offset in range(sps):
        symbols = z[offset:offset + count * sps].reshape(count, sps).mean(axis=1)
        quality = float(np.mean(np.abs(symbols) ** 2))
        if best is None or quality > best[0]:
            best = (quality, offset, symbols)
    quality, offset, symbols = best
    return symbols[1:-1], offset, quality


def evm_rms(symbols, reference):
    """EVM as RMS error / RMS reference after DC removal and one complex scale.

    DC offset is excluded (the frozen engine also removes the frame mean); what
    remains is magnitude/phase error against the reference constellation.
    """
    symbols = np.asarray(symbols, dtype=complex)
    reference = np.asarray(reference, dtype=complex)
    if len(symbols) != len(reference) or not len(symbols):
        raise ValueError('EVM needs equal, non-empty vectors')
    symbols = symbols - symbols.mean()
    reference = reference - reference.mean()
    denom = np.vdot(reference, reference)
    if denom == 0:
        raise ValueError('reference has no variance')
    scale = np.vdot(reference, symbols) / denom
    error = symbols - scale * reference
    return float(np.sqrt(np.mean(np.abs(error) ** 2)
                         / np.mean(np.abs(scale * reference) ** 2)))


def soft_llr(symbols, noise_variance):
    """BPSK soft information: 2*Re(y)/sigma^2 (the optimal LLR for AWGN)."""
    if not noise_variance > 0:
        raise ValueError('noise variance must be positive')
    return 2.0 * np.real(np.asarray(symbols, dtype=complex)) / float(noise_variance)


def independent_demod(iq, *, seed=1, sps=SPS, fs_hz=FS_HZ):
    """The independent reference demodulator (no frozen module is used).

    Returns the same observable names the frozen engine reports so the two results
    can be compared field by field.  Expected outcomes for the reference vectors
    come from this function.
    """
    z = np.asarray(iq, dtype=complex)
    if len(z) < sps * (ALIGNMENT_BITS + 2):
        return _result('UNSUPPORTED', note='frame too short')
    if np.any(z == IQ_CLIP_SENTINELS[0]) or np.any(z == IQ_CLIP_SENTINELS[1]):
        return _result('CLIPPED')
    z = z - np.mean(z)
    power = float(np.mean(np.abs(z) ** 2))
    if power < 1.0:
        return _result('NO_ENERGY')
    cfo = estimate_cfo_hz(z, fs_hz)
    if not math.isfinite(cfo):
        return _result('CFO_OUT_OF_RANGE', cfo_hz=None)
    n = np.arange(len(z), dtype=float)
    corrected = z * np.exp(-2j * np.pi * cfo * n / fs_hz)
    moment = complex(np.mean(corrected * corrected))
    coherence = abs(moment) / power
    if coherence < COHERENCE_MIN:
        return _result('CARRIER_NOT_SYNCED', cfo_hz=cfo, carrier_coherence=coherence)
    theta = float(np.angle(moment) / 2.0)
    corrected = corrected * np.exp(-1j * theta)
    symbols, offset, energy = matched_filter_symbols(corrected, sps)
    quality = energy / power
    if quality < TIMING_QUALITY_MIN or len(symbols) < ALIGNMENT_BITS + COMPARE_BITS:
        return _result('TIMING_NOT_SYNCED', cfo_hz=cfo, carrier_coherence=coherence,
                       timing_quality=quality)
    decisions = (np.real(symbols) < 0).astype(int)
    reference_bits = prbs7_bits(seed, 127)
    reference = np.asarray([1 - 2 * bit for bit in reference_bits], dtype=float)
    observed = 1 - 2 * decisions[:ALIGNMENT_BITS]
    best, best_corr = 0, 0.0
    for shift in range(127):
        window = reference[(np.arange(ALIGNMENT_BITS) + shift) % 127]
        corr = float(np.mean(observed * window))
        if abs(corr) > abs(best_corr):
            best, best_corr = shift, corr
    if abs(best_corr) < REFERENCE_CORRELATION_MIN:
        return _result('REFERENCE_NOT_SYNCED', cfo_hz=cfo,
                       carrier_coherence=coherence, timing_quality=quality)
    polarity = 1 if best_corr >= 0 else -1
    measured = (1 - 2 * decisions[ALIGNMENT_BITS:]) * polarity
    expected = reference[(np.arange(ALIGNMENT_BITS, len(decisions)) + best) % 127]
    errors = int(np.count_nonzero(measured != expected))
    compared = len(measured)
    return {'reason': '', 'reference_sync': True, 'bit_errors': errors,
            'compared_bits': compared, 'total_symbols': len(symbols),
            'cfo_hz': cfo, 'carrier_coherence': coherence,
            'timing_quality': quality, 'timing_offset_samples': offset,
            'phase_mod_pi': theta / math.pi, 'alignment_offset': best,
            'polarity': polarity,
            'evm_rms': _evm_against_reference(symbols, reference, best, polarity),
            'bits': [int(v) for v in measured]}


def _evm_against_reference(symbols, reference, alignment, polarity):
    """EVM over the compared symbols, against the independent reference."""
    length = len(symbols) - ALIGNMENT_BITS
    ref = reference[(np.arange(ALIGNMENT_BITS, ALIGNMENT_BITS + length) + alignment) % 127]
    return evm_rms(symbols[ALIGNMENT_BITS:], ref * polarity)


def analytic_awgn_ber(eb_n0_db):
    """Coherent BPSK BER = 0.5*erfc(sqrt(Eb/N0))."""
    return 0.5 * math.erfc(math.sqrt(10 ** (float(eb_n0_db) / 10.0)))


def per_sample_snr_to_eb_n0_db(snr_db, sps=SPS):
    """Per-complex-sample SNR to Eb/N0 for one BPSK bit with an sps-sample MF."""
    return float(snr_db) + 10.0 * math.log10(sps / 2.0)
