import math

import numpy as np


RRC_ALPHA = 0.35
RRC_ROLLOFFS = (0.2, 0.35, 0.5)
RRC_SPS = 4
RRC_SPAN = 8
RRC_TAPS = RRC_SPAN * RRC_SPS + 1
RRC_COEFF_SCALE = 32767
RRC_INPUT_SHIFT = 8
RRC_OUTPUT_SHIFT = 8
RRC_GROUP_DELAY = (RRC_TAPS - 1) // 2
RRC_MATCH_DELAY = 2 * RRC_GROUP_DELAY


def round_half_away_from_zero(value):
    value = float(value)
    return int(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))


def round_shift_signed(value, shift):
    value = int(value)
    if shift < 0:
        raise ValueError("shift must be non-negative")
    if value >= 0:
        return (value + (1 << (shift - 1))) >> shift if shift else value
    magnitude = -value
    rounded = (magnitude + (1 << (shift - 1))) >> shift if shift else magnitude
    return -rounded


def _saturate(value, low, high):
    return min(high, max(low, int(value)))


def _rrc_value(x, alpha):
    if abs(x) < 1e-15:
        return 1.0 + alpha * (4.0 / math.pi - 1.0)
    singular = 1.0 / (4.0 * alpha)
    if abs(abs(x) - singular) < 1e-12:
        return alpha / math.sqrt(2.0) * (
            (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * alpha))
            + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * alpha))
        )
    numerator = math.sin(math.pi * x * (1.0 - alpha))
    numerator += 4.0 * alpha * x * math.cos(math.pi * x * (1.0 + alpha))
    denominator = math.pi * x * (1.0 - (4.0 * alpha * x) ** 2)
    return numerator / denominator


def rrc_coefficients(alpha=RRC_ALPHA, sps=RRC_SPS, span=RRC_SPAN):
    if sps != RRC_SPS or span != RRC_SPAN:
        raise ValueError("only the frozen RRC sps/span configuration is supported")
    if alpha not in RRC_ROLLOFFS:
        raise ValueError("unsupported RRC rolloff")
    values = np.array(
        [_rrc_value((index - (RRC_TAPS - 1) / 2) / sps, alpha)
         for index in range(RRC_TAPS)],
        dtype=np.float64,
    )
    values /= math.sqrt(float(np.dot(values, values)))
    quantized = np.array(
        [round_half_away_from_zero(item * RRC_COEFF_SCALE) for item in values],
        dtype=np.int64,
    )
    quantized = np.clip(quantized, -32768, 32767).astype(np.int16)
    quantized = ((quantized.astype(np.int32) + quantized[::-1].astype(np.int32)) // 2).astype(np.int16)
    return quantized


def _validate_coefficients(coeffs):
    coeffs = np.asarray(coeffs, dtype=np.int16)
    if coeffs.shape != (RRC_TAPS,):
        raise ValueError("coeffs must contain 33 taps")
    return coeffs


def _fir_sample(samples, coeffs, index):
    acc = 0
    for tap, coeff in enumerate(coeffs):
        source_index = index - tap
        if 0 <= source_index < len(samples):
            acc += int(samples[source_index]) * int(coeff)
    return _saturate(round_shift_signed(acc, 15), -32768, 32767)


def _filter_sample_rate(samples, coeffs):
    return np.asarray(
        [_fir_sample(samples, coeffs, index) for index in range(len(samples) + len(coeffs) - 1)],
        dtype=np.int16,
    )


def rrc_filter_symbols(i_symbols, q_symbols, coeffs=None):
    i_symbols = np.asarray(i_symbols, dtype=np.int16)
    q_symbols = np.asarray(q_symbols, dtype=np.int16)
    if i_symbols.ndim != 1 or q_symbols.ndim != 1 or len(i_symbols) != len(q_symbols):
        raise ValueError("I/Q symbol arrays must be one-dimensional and equal length")
    coeffs = rrc_coefficients() if coeffs is None else _validate_coefficients(coeffs)
    i_up = np.zeros(len(i_symbols) * RRC_SPS, dtype=np.int16)
    q_up = np.zeros(len(q_symbols) * RRC_SPS, dtype=np.int16)
    i_up[::RRC_SPS] = i_symbols.astype(np.int32) * (1 << RRC_INPUT_SHIFT)
    q_up[::RRC_SPS] = q_symbols.astype(np.int32) * (1 << RRC_INPUT_SHIFT)
    return _filter_sample_rate(i_up, coeffs), _filter_sample_rate(q_up, coeffs)


def matched_filter_symbols(samples, coeffs=None, delay_samples=RRC_MATCH_DELAY):
    i_samples, q_samples = samples
    i_samples = np.asarray(i_samples, dtype=np.int16)
    q_samples = np.asarray(q_samples, dtype=np.int16)
    if i_samples.ndim != 1 or q_samples.ndim != 1 or len(i_samples) != len(q_samples):
        raise ValueError("I/Q sample arrays must be equal-length one-dimensional arrays")
    payload = len(i_samples) - (RRC_TAPS - 1)
    if payload <= 0 or payload % RRC_SPS:
        raise ValueError("I/Q samples must have length N*4+32")
    if delay_samples < 0:
        raise ValueError("delay_samples must be non-negative")
    coeffs = rrc_coefficients() if coeffs is None else _validate_coefficients(coeffs)
    i_matched = _filter_sample_rate(i_samples, coeffs)
    q_matched = _filter_sample_rate(q_samples, coeffs)
    symbol_count = payload // RRC_SPS
    indices = delay_samples + RRC_SPS * np.arange(symbol_count)
    if len(indices) and indices[-1] >= len(i_matched):
        raise ValueError("delay_samples exceeds matched-filter output")
    return i_matched[indices], q_matched[indices]


def dac_scale(samples):
    values = np.asarray(samples, dtype=np.int16)
    result = [
        _saturate(round_shift_signed(int(value), RRC_OUTPUT_SHIFT), -127, 127)
        for value in values.flat
    ]
    return np.asarray(result, dtype=np.int8).reshape(values.shape)
