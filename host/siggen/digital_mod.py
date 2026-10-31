import numpy as np


PRBS7_SEED = 0x7F
MODES = ("BPSK", "QPSK", "QAM4", "QAM16", "QAM64")
BITS_PER_SYMBOL = {
    "BPSK": 1,
    "QPSK": 2,
    "QAM4": 2,
    "QAM16": 4,
    "QAM64": 6,
}
_QAM_AXIS_LEVELS = {
    "QAM16": (126, 42, -126, -42),
    "QAM64": (126, 90, 18, 54, -126, -90, -18, -54),
}


def _validate_mode(mode):
    if mode not in MODES:
        raise ValueError(f"unsupported modulation mode: {mode}")


def _validate_non_negative_int(value, name):
    if (
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative integer")


def _advance_prbs(state):
    return ((state & 0x3F) << 1) | (((state >> 6) ^ (state >> 5)) & 1)


def prbs7_bits(count, seed=PRBS7_SEED):
    _validate_non_negative_int(count, "count")
    if (seed & 0x7F) == 0:
        raise ValueError("count must be non-negative and seed must be non-zero")

    state = seed & 0x7F
    bits = np.empty(count, dtype=np.uint8)
    for index in range(count):
        bits[index] = (state >> 6) & 1
        state = _advance_prbs(state)
    return bits


def constellation_points(mode):
    _validate_mode(mode)

    if mode == "BPSK":
        points = ((127, 0), (-127, 0))
    elif mode in ("QPSK", "QAM4"):
        points = ((127, 127), (127, -127), (-127, 127), (-127, -127))
    else:
        levels = _QAM_AXIS_LEVELS[mode]
        points = tuple((i, q) for i in levels for q in levels)
    return np.asarray(points, dtype=np.int16)


def payload_to_bits(payload):
    """按字节 MSB-first 展开为 0/1 比特流,与固件 user_stream_source 一致。"""
    data = np.frombuffer(bytes(payload), dtype=np.uint8)
    return np.unpackbits(data)


def bits_to_symbols(mode, bits):
    """把显式比特流映射为 (I, Q) 符号。

    每符号取 BITS_PER_SYMBOL 位,符号内 MSB 先(与固件 iq_constellation_mapper
    一致);比特数不是整符号时丢弃末尾不足一符号的比特。
    """
    _validate_mode(mode)
    bits = np.asarray(bits)
    if (bits.ndim != 1 or bits.dtype.kind not in "iu"
            or not np.all((bits == 0) | (bits == 1))):
        raise ValueError("bits must be a one-dimensional 0/1 array")
    bits = bits.astype(np.uint8)
    bits_per_symbol = BITS_PER_SYMBOL[mode]
    count = bits.size // bits_per_symbol
    grouped = bits[:count * bits_per_symbol].reshape(count, bits_per_symbol)

    if mode == "BPSK":
        i = np.where(grouped[:, 0] == 0, 127, -127).astype(np.int16)
        q = np.zeros(count, dtype=np.int16)
        return i, q

    if mode in ("QPSK", "QAM4"):
        levels = np.array((127, -127), dtype=np.int16)
        return levels[grouped[:, 0]], levels[grouped[:, 1]]

    axis_levels = np.array(_QAM_AXIS_LEVELS[mode], dtype=np.int16)
    axis_bits = bits_per_symbol // 2
    weights = 2 ** np.arange(axis_bits - 1, -1, -1)
    i_codes = grouped[:, :axis_bits] @ weights
    q_codes = grouped[:, axis_bits:] @ weights
    return axis_levels[i_codes], axis_levels[q_codes]


def generate_symbols(mode, count, seed=PRBS7_SEED):
    _validate_mode(mode)
    _validate_non_negative_int(count, "count")

    bits = prbs7_bits(count * BITS_PER_SYMBOL[mode], seed)
    return bits_to_symbols(mode, bits)


def user_expected_symbols(mode, payload):
    """USER/DDR 上传 payload 的期望 (I, Q) 符号序列。"""
    return bits_to_symbols(mode, payload_to_bits(payload))


def generate_samples(mode, symbol_count, sym_ticks=5000, seed=PRBS7_SEED):
    _validate_non_negative_int(symbol_count, "symbol_count")
    if (
        isinstance(sym_ticks, (bool, np.bool_))
        or not isinstance(sym_ticks, (int, np.integer))
        or sym_ticks <= 0
    ):
        raise ValueError("symbol_count must be non-negative and sym_ticks must be positive")

    i_symbols, q_symbols = generate_symbols(mode, symbol_count, seed)
    return np.repeat(i_symbols, sym_ticks), np.repeat(q_symbols, sym_ticks)
