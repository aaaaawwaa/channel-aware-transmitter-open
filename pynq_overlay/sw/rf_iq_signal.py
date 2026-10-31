"""Pure numpy AD9361 finite-frame digital modulation and channel impairment.

This module intentionally has no model, register, socket or hardware dependency.
It produces finite periodic complex I/Q frames according to the C contract:
PRBS7 -> constellation -> RECT/RRC pulse shaping -> 12-bit signed quantization,
with an optional channel impairment model applied before final quantization.
"""
from __future__ import annotations

import math
import struct

import numpy as np

# QAM64 已移出本次交付支持范围（源码/历史证据保留，仅从交付模式列表移除）。
MODES = ("BPSK", "QPSK", "QAM16")
FILTERS = ("RECT", "RRC")
ROLLOFFS = ("0.2", "0.35", "0.5")
BITS_PER_SYMBOL = {"BPSK": 1, "QPSK": 2, "QAM16": 4}
PRBS_SYMBOLS = 127
SPS_MIN, SPS_MAX = 2, 32
AMPLITUDE_MIN, AMPLITUDE_MAX = 0, 2047
NOISE_PCT_MIN, NOISE_PCT_MAX = 0, 100
GAIN_Q8_MIN, GAIN_Q8_MAX = -256, 255
DELAY_SAMPLES_MIN, DELAY_SAMPLES_MAX = 0, 511
TAPS = 4


def _rolloff_float(rolloff):
    if isinstance(rolloff, str):
        return float(rolloff)
    if isinstance(rolloff, bool) or not isinstance(rolloff, (int, float)):
        raise ValueError("rolloff must be 0.2, 0.35 or 0.5")
    return float(rolloff)


def _check_rolloff(rolloff):
    value = _rolloff_float(rolloff)
    if value not in (0.2, 0.35, 0.5):
        raise ValueError("rolloff must be 0.2, 0.35 or 0.5")
    return value


def bits_per_symbol(mode):
    if mode == "QAM64":
        raise ValueError("QAM64超出本次交付支持范围，请重新选择 BPSK/QPSK/QAM16")
    try:
        return BITS_PER_SYMBOL[mode]
    except KeyError:
        raise ValueError("unknown constellation mode: %r" % (mode,))


def prbs7_bits(seed, count):
    """Return `count` serial PRBS7 bits, RTL bit6 output / bit6^bit5 feedback."""
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError("PRBS7 seed must be an integer 1..127")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a non-negative integer")
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def _map_axis(value, levels):
    if levels == 2:
        table = (1, -1)
    elif levels == 4:
        table = (3, 1, -3, -1)
    elif levels == 8:
        table = (7, 5, 1, 3, -7, -5, -1, -3)
    else:
        raise ValueError("unsupported constellation axis size")
    return table[value]


def map_bits_to_symbol(mode, bits):
    """Map an MSB-first tuple of serial bits to a (I, Q) constellation point.

    The bit ordering follows `iq_constellation_mapper.v`: first transmitted bit is
    the MSB of the packed symbol and maps to I, later bits to Q.
    """
    bits = tuple(bits)
    bps = bits_per_symbol(mode)
    if len(bits) != bps:
        raise ValueError("%s expects %d bits, got %d" % (mode, bps, len(bits)))
    if any(b not in (0, 1) for b in bits):
        raise ValueError("constellation bits must be 0 or 1")
    if mode == "BPSK":
        return (_map_axis(bits[0], 2), 0)
    if mode == "QPSK":
        return (_map_axis(bits[0], 2), _map_axis(bits[1], 2))
    if mode == "QAM16":
        i_val = (bits[0] << 1) | bits[1]
        q_val = (bits[2] << 1) | bits[3]
        return (_map_axis(i_val, 4), _map_axis(q_val, 4))
    if mode == "QAM64":
        # QAM64 超出本次交付支持范围：参数入口明确拒绝，不做静默映射。
        raise ValueError("QAM64超出本次交付支持范围，请重新选择 BPSK/QPSK/QAM16")
    raise ValueError("unknown constellation mode: %r" % (mode,))


def _rrc_value(t, alpha):
    """Root-raised-cosine impulse response with unit-energy continuous convention."""
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


def rrc_taps(sps, rolloff):
    """Return 8*sps+1 real RRC taps centered at zero, unit-energy normalised."""
    if type(sps) is not int or not SPS_MIN <= sps <= SPS_MAX:
        raise ValueError("sps must be an integer %d..%d" % (SPS_MIN, SPS_MAX))
    alpha = _check_rolloff(rolloff)
    length = 8 * sps + 1
    center = length // 2
    taps = np.array([_rrc_value((i - center) / sps, alpha) for i in range(length)],
                    dtype=float)
    # Continuous unit-energy RRC formula already gives sum(h^2) ~= sps.  Keep the
    # exact t=0/singular values intact; finite truncation remains within tolerance.
    return taps


def _rect_pulse(symbols, sps):
    return np.repeat(symbols, sps, axis=0)


def _rrc_pulse(symbols, sps, rolloff):
    n_symbols = len(symbols)
    sample_count = n_symbols * sps
    h = rrc_taps(sps, rolloff)
    # Put the filter peak at index 0 so symbol n lands at sample n*sps, not at a
    # later filter-center offset.  The convolution is circular (FFT), so there is
    # no zero-padding seam.
    center = len(h) // 2
    h = np.roll(h, -center)
    x = np.zeros((sample_count, 2), dtype=float)
    x[::sps] = symbols
    out = np.empty_like(x)
    for c in range(2):
        out[:, c] = np.fft.ifft(np.fft.fft(h, sample_count) *
                                np.fft.fft(x[:, c], sample_count)).real
    return out


def _pulse_shape(symbols, sps, filter_name, rolloff):
    if filter_name == "RECT":
        return _rect_pulse(symbols, sps)
    if filter_name == "RRC":
        return _rrc_pulse(symbols, sps, rolloff)
    raise ValueError("filter must be RECT or RRC")


def _validate_digital_args(args):
    mode = args.get("mode")
    if mode not in MODES:
        return "mode must be one of %s" % (", ".join(MODES))
    filt = args.get("filter")
    if filt not in FILTERS:
        return "filter must be RECT or RRC"
    rolloff = args.get("rolloff")
    try:
        _check_rolloff(rolloff)
    except ValueError as exc:
        return str(exc)
    seed = args.get("seed")
    if type(seed) is not int or not 1 <= seed <= 127:
        return "PRBS7 seed must be an integer 1..127"
    sample_rate_hz = args.get("sample_rate_hz")
    symbol_rate_hz = args.get("symbol_rate_hz")
    if not isinstance(sample_rate_hz, int) or isinstance(sample_rate_hz, bool) or sample_rate_hz <= 0:
        return "sample_rate_hz must be a positive integer"
    if not isinstance(symbol_rate_hz, int) or isinstance(symbol_rate_hz, bool) or symbol_rate_hz <= 0:
        return "symbol_rate_hz must be a positive integer"
    if sample_rate_hz % symbol_rate_hz != 0:
        return "symbol_rate_hz must divide sample_rate_hz (integer samples per symbol)"
    sps = sample_rate_hz // symbol_rate_hz
    if not SPS_MIN <= sps <= SPS_MAX:
        return "symbol_rate_hz must be in sample_rate/32..sample_rate/2 (sps 2..32)"
    amplitude = args.get("amplitude", 2047)
    if type(amplitude) is not int or isinstance(amplitude, bool) or not AMPLITUDE_MIN <= amplitude <= AMPLITUDE_MAX:
        return "amplitude must be an integer 0..2047"
    return None


def validate_digital_args(args):
    """Return None if valid, otherwise a human-readable error string."""
    if not isinstance(args, dict):
        return "digital args must be a dict"
    return _validate_digital_args(args)


def _validate_impairment(imp):
    if imp is None:
        return None
    if not isinstance(imp, dict):
        return "impairment must be a dict"
    enabled = imp.get("enabled", False)
    if not isinstance(enabled, bool):
        return "impairment enabled must be a bool"
    noise_pct = imp.get("noise_pct", 0)
    if type(noise_pct) is not int or isinstance(noise_pct, bool) or not NOISE_PCT_MIN <= noise_pct <= NOISE_PCT_MAX:
        return "noise_pct must be an integer 0..100"
    taps = imp.get("taps")
    if not isinstance(taps, list) or len(taps) != TAPS:
        return "impairment requires exactly %d taps" % TAPS
    for k, tap in enumerate(taps):
        if not isinstance(tap, dict):
            return "tap %d must be a dict" % k
        gain = tap.get("gain_q8", 0)
        if type(gain) is not int or isinstance(gain, bool) or not GAIN_Q8_MIN <= gain <= GAIN_Q8_MAX:
            return "tap %d gain_q8 must be an integer %d..%d" % (k, GAIN_Q8_MIN, GAIN_Q8_MAX)
        delay = tap.get("delay_samples", 0)
        if type(delay) is not int or isinstance(delay, bool) or not DELAY_SAMPLES_MIN <= delay <= DELAY_SAMPLES_MAX:
            return "tap %d delay_samples must be an integer %d..%d" % (k, DELAY_SAMPLES_MIN, DELAY_SAMPLES_MAX)
    seed = imp.get("noise_seed", 0)
    if type(seed) is not int or isinstance(seed, bool) or seed < 0:
        return "noise_seed must be a non-negative integer"
    return None


def apply_channel_impairment(iq, impairment):
    """Apply the periodic FIR plus independent I/Q uniform noise to `iq`.

    `iq` is a float array of shape (N, 2). Returns float array (not quantized).
    The caller is responsible for final `quantize_iq` / packing.
    """
    if impairment is None:
        return np.array(iq, dtype=float, copy=True)
    why = _validate_impairment(impairment)
    if why is not None:
        raise ValueError(why)
    if not impairment.get("enabled", False):
        return np.array(iq, dtype=float, copy=True)
    iq = np.asarray(iq, dtype=float)
    n = len(iq)
    out = np.zeros_like(iq)
    taps = impairment["taps"]
    for tap in taps:
        gain = tap["gain_q8"]
        if gain == 0:
            continue
        delay = tap["delay_samples"]
        out += (gain / 256.0) * iq[(np.arange(n) - delay) % n]
    noise_pct = impairment["noise_pct"]
    if noise_pct:
        noise_peak = int(round(2047 * noise_pct / 100.0))
        seed = impairment["noise_seed"]
        rng = np.random.default_rng(seed)
        noise = rng.integers(-noise_peak, noise_peak + 1, size=(n, 2)).astype(float)
        out += noise
    return out


def quantize_iq(values):
    """Round to nearest integer with ties-to-even and enforce signed 12-bit range."""
    arr = np.asarray(values, dtype=float)
    rounded = np.rint(arr)
    if rounded.size and (rounded.max() > 2047 or rounded.min() < -2048):
        raise ValueError("IQ sample out of signed 12-bit range [-2048, 2047]")
    return rounded.astype(int)


def _pack_iq(iq):
    iq_int = quantize_iq(iq)
    payload = bytearray()
    for i_val, q_val in iq_int:
        payload += struct.pack("<hh", int(i_val), int(q_val))
    return bytes(payload)


def pack_iq(iq):
    """Quantize and pack an (N, 2) float IQ array to little-endian s16_iq_le."""
    return _pack_iq(iq)
def generate_digital_frame(args):
    """Return (metadata, little-endian s16_iq_le payload) for a digital frame.

    Args are explicit: mode, filter, rolloff, seed, symbol_rate_hz,
    sample_rate_hz, amplitude, and optional impairment.
    """
    why = _validate_digital_args(args)
    if why is not None:
        raise ValueError(why)
    mode = args["mode"]
    filt = args["filter"]
    rolloff = args["rolloff"]
    seed = args["seed"]
    sample_rate_hz = args["sample_rate_hz"]
    symbol_rate_hz = args["symbol_rate_hz"]
    amplitude = args.get("amplitude", 2047)
    sps = sample_rate_hz // symbol_rate_hz
    bps = bits_per_symbol(mode)
    bits = prbs7_bits(seed, PRBS_SYMBOLS * bps)
    symbols = np.array(
        [map_bits_to_symbol(mode, tuple(bits[i * bps:(i + 1) * bps]))
         for i in range(PRBS_SYMBOLS)], dtype=float)
    iq = _pulse_shape(symbols, sps, filt, rolloff)
    peak = float(np.max(np.abs(iq))) if iq.size else 0.0
    if peak > 0.0:
        iq = iq * (amplitude / peak)
    else:
        iq = np.zeros_like(iq)
    if args.get("impairment") is not None:
        iq = apply_channel_impairment(iq, args["impairment"])
    sample_count = PRBS_SYMBOLS * sps
    payload = _pack_iq(iq)
    metadata = {
        "samples": sample_count,
        "bytes": sample_count * 4,
        "format": "s16_iq_le",
        "direction": "tx",
        "wrap": True,
    }
    return metadata, payload
