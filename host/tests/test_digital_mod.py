import numpy as np
import pytest

from siggen.digital_mod import (
    BITS_PER_SYMBOL,
    MODES,
    PRBS7_SEED,
    bits_to_symbols,
    constellation_points,
    generate_samples,
    generate_symbols,
    payload_to_bits,
    prbs7_bits,
    user_expected_symbols,
)


def point_set(i, q):
    return set(zip(i.tolist(), q.tolist()))


def cartesian_point_set(levels):
    return {(i, q) for i in levels for q in levels}


def test_bits_per_symbol_has_complete_contract():
    assert BITS_PER_SYMBOL == {
        "BPSK": 1,
        "QPSK": 2,
        "QAM4": 2,
        "QAM16": 4,
        "QAM64": 6,
    }
    bits = prbs7_bits(254, seed=PRBS7_SEED)
    assert PRBS7_SEED == 0x7F
    assert bits[:16].tolist() == [int(x) for x in "1111111000000100"]
    assert np.array_equal(bits[:127], bits[127:254])


@pytest.mark.parametrize(
    "mode, expected_count",
    [("BPSK", 2), ("QPSK", 4), ("QAM4", 4),
     ("QAM16", 16), ("QAM64", 64)],
)
def test_constellation_has_expected_points(mode, expected_count):
    points = constellation_points(mode)
    assert points.shape == (expected_count, 2)
    assert len(point_set(points[:, 0], points[:, 1])) == expected_count
    assert np.max(np.abs(points)) <= 127


def test_qpsk_and_qam4_are_identical():
    qpsk = constellation_points("QPSK")
    qam4 = constellation_points("QAM4")
    assert np.array_equal(qpsk, qam4)


def test_constellation_levels_are_exact():
    assert point_set(*constellation_points("BPSK").T) == {
        (127, 0), (-127, 0),
    }

    qpsk_expected = {
        (127, 127), (127, -127), (-127, 127), (-127, -127),
    }
    assert point_set(*constellation_points("QPSK").T) == qpsk_expected
    assert point_set(*constellation_points("QAM4").T) == qpsk_expected

    qam16_levels = {126, 42, -126, -42}
    qam16 = constellation_points("QAM16")
    assert set(qam16[:, 0]) == qam16_levels
    assert set(qam16[:, 1]) == qam16_levels
    assert point_set(*qam16.T) == cartesian_point_set(qam16_levels)

    qam64_levels = {126, 90, 54, 18, -18, -54, -90, -126}
    qam64 = constellation_points("QAM64")
    assert set(qam64[:, 0]) == qam64_levels
    assert set(qam64[:, 1]) == qam64_levels
    assert point_set(*qam64.T) == cartesian_point_set(qam64_levels)


def test_generate_symbols_is_deterministic_and_bounded():
    for mode in MODES:
        i1, q1 = generate_symbols(mode, 127)
        i2, q2 = generate_symbols(mode, 127)
        assert np.array_equal(i1, i2)
        assert np.array_equal(q1, q2)
        assert np.max(np.abs(i1)) <= 127
        assert np.max(np.abs(q1)) <= 127
        assert point_set(i1, q1) <= point_set(*constellation_points(mode).T)


def test_modes_are_in_specified_order():
    assert MODES == ("BPSK", "QPSK", "QAM4", "QAM16", "QAM64")


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("BPSK", [(127, 0), (-127, 0)]),
        (
            "QPSK",
            [(127, 127), (127, -127), (-127, 127), (-127, -127)],
        ),
        (
            "QAM4",
            [(127, 127), (127, -127), (-127, 127), (-127, -127)],
        ),
        (
            "QAM16",
            [(126, 126), (126, 42), (126, -126), (126, -42)],
        ),
        (
            "QAM64",
            [
                (126, 126),
                (126, 90),
                (126, 18),
                (126, 54),
                (126, -126),
                (126, -90),
                (126, -18),
                (126, -54),
            ],
        ),
    ],
)

def test_constellation_points_preserve_specified_order(mode, expected):
    points = constellation_points(mode)
    assert points[:len(expected)].tolist() == [list(point) for point in expected]


@pytest.mark.parametrize(
    "mode, expected_i, expected_q",
    [
        ("BPSK", [-127, -127, -127, -127], [0, 0, 0, 0]),
        (
            "QPSK",
            [-127, -127, -127, -127],
            [-127, -127, -127, 127],
        ),
        (
            "QAM4",
            [-127, -127, -127, -127],
            [-127, -127, -127, 127],
        ),
        ("QAM16", [-42, -42, 126, 42], [-42, -126, 126, 126]),
        ("QAM64", [-54, -126, 18], [-54, 126, 126]),
    ],
)

def test_generate_symbols_uses_specified_prbs7_prefix(
    mode, expected_i, expected_q
):
    i, q = generate_symbols(mode, 4 if mode != "QAM64" else 3)
    assert i.tolist() == expected_i
    assert q.tolist() == expected_q


@pytest.mark.parametrize("mode", MODES)
def test_generate_symbols_and_samples_return_int16(mode):
    i_symbols, q_symbols = generate_symbols(mode, 2)
    i_samples, q_samples = generate_samples(mode, 2, sym_ticks=2)
    assert i_symbols.dtype == np.int16
    assert q_symbols.dtype == np.int16
    assert i_samples.dtype == np.int16
    assert q_samples.dtype == np.int16


@pytest.mark.parametrize("mode", ["QAM256", "invalid"])
def test_invalid_mode_is_rejected_by_all_generation_apis(mode):
    with pytest.raises(ValueError):
        constellation_points(mode)
    with pytest.raises(ValueError):
        generate_symbols(mode, 1)
    with pytest.raises(ValueError):
        generate_samples(mode, 1)


@pytest.mark.parametrize(
    "call",
    [
        lambda: prbs7_bits(1.5),
        lambda: prbs7_bits(True),
        lambda: generate_symbols("BPSK", 1.5),
        lambda: generate_symbols("BPSK", True),
        lambda: generate_samples("BPSK", 1.5),
        lambda: generate_samples("BPSK", True),
    ],
)
def test_non_integer_count_is_rejected(call):
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize(
    "call",
    [
        lambda: prbs7_bits(1, seed=0),
        lambda: generate_symbols("BPSK", 1, seed=0),
        lambda: generate_samples("BPSK", 1, seed=0),
    ],
)
def test_zero_seed_is_rejected(call):
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("sym_ticks", [0, -1, 1.5, True])
def test_invalid_symbol_ticks_are_rejected(sym_ticks):
    with pytest.raises(ValueError):
        generate_samples("BPSK", 1, sym_ticks=sym_ticks)


def test_generate_samples_holds_each_symbol_exactly():
    i_sym, q_sym = generate_symbols("QAM16", 4)
    i, q = generate_samples("QAM16", 4, sym_ticks=5)
    assert len(i) == len(q) == 20
    assert np.array_equal(i, np.repeat(i_sym, 5))
    assert np.array_equal(q, np.repeat(q_sym, 5))


def test_rect_reference_remains_bit_exact_after_rrc_model_addition():
    i, q = generate_samples("QPSK", 7, sym_ticks=11)
    assert i.dtype == q.dtype == np.int16
    assert np.array_equal(i, np.repeat(generate_symbols("QPSK", 7)[0], 11))
    assert np.array_equal(q, np.repeat(generate_symbols("QPSK", 7)[1], 11))


# ---- USER/DDR payload -> symbol reference (firmware consumes bytes MSB-first) ----


def test_payload_to_bits_is_msb_first_per_byte():
    bits = payload_to_bits(bytes((0x80, 0x01, 0xFF)))
    assert bits.tolist() == [1, 0, 0, 0, 0, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 1,
                             1, 1, 1, 1, 1, 1, 1, 1]
    assert payload_to_bits(b"").size == 0


def test_bits_to_symbols_matches_generate_symbols_for_prbs_bits():
    for mode in MODES:
        bps = BITS_PER_SYMBOL[mode]
        bits = prbs7_bits(31 * bps, seed=PRBS7_SEED)
        i, q = bits_to_symbols(mode, bits)
        exp_i, exp_q = generate_symbols(mode, 31)
        assert np.array_equal(i, exp_i)
        assert np.array_equal(q, exp_q)


def test_bits_to_symbols_qpsk_maps_first_bit_to_i_second_to_q():
    i, q = bits_to_symbols("QPSK", np.array([0, 0, 1, 1, 0, 1, 1, 0]))
    assert i.tolist() == [127, -127, 127, -127]
    assert q.tolist() == [127, -127, -127, 127]


def test_bits_to_symbols_qam16_uses_two_bits_per_axis():
    # I codes 00/01/10/11 -> 126/42/-126/-42, Q from the next two bits.
    i, q = bits_to_symbols("QAM16", np.array([0, 0, 0, 0, 0, 1, 0, 1,
                                              1, 0, 1, 0, 1, 1, 1, 1]))
    assert i.tolist() == [126, 42, -126, -42]
    assert q.tolist() == [126, 42, -126, -42]


def test_user_expected_symbols_follows_payload_bits():
    i, q = user_expected_symbols("QPSK", bytes((0b00011011, 0b11100100)))
    assert i.tolist() == [127, 127, -127, -127, -127, -127, 127, 127]
    assert q.tolist() == [127, -127, 127, -127, -127, 127, -127, 127]


def test_bits_to_symbols_rejects_non_binary_input():
    with pytest.raises(ValueError):
        bits_to_symbols("QPSK", np.array([0, 2, 1, 0]))
    with pytest.raises(ValueError):
        bits_to_symbols("QPSK", np.array([[0, 1], [1, 0]]))
    with pytest.raises(ValueError):
        bits_to_symbols("QAM8", np.array([0, 1, 0, 1]))

