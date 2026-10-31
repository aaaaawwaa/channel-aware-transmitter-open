import numpy as np
import pytest
from pathlib import Path

from siggen.digital_mod import generate_symbols
from siggen.rrc import (
    RRC_ALPHA,
    RRC_ROLLOFFS,
    RRC_SPS,
    RRC_SPAN,
    RRC_TAPS,
    dac_scale,
    matched_filter_symbols,
    rrc_coefficients,
    rrc_filter_symbols,
    round_shift_signed,
)


def test_rrc_constants_and_coefficients_are_symmetric():
    coeff = rrc_coefficients()
    assert (RRC_ALPHA, RRC_SPS, RRC_SPAN, RRC_TAPS) == (0.35, 4, 8, 33)
    assert coeff.dtype == np.int16
    assert coeff.shape == (33,)
    assert np.array_equal(coeff, coeff[::-1])
    assert coeff[16] == 17953
    assert int(np.dot(coeff.astype(np.int64), coeff.astype(np.int64))) == 1_073_666_113
    assert int(np.max(np.abs(coeff))) == 17953
    assert coeff.tolist() == [
        33, 218, 157, -156, -417, -242, 420, 1071, 936,
        -362, -2215, -3091, -1388, 3390, 9959, 15683, 17953,
        15683, 9959, 3390, -1388, -3091, -2215, -362, 936,
        1071, 420, -242, -417, -156, 157, 218, 33,
    ]


@pytest.mark.parametrize(
    ("alpha", "center", "energy"),
    (
        (0.2, 17288, 1_073_704_914),
        (0.35, 17953, 1_073_666_113),
        (0.5, 18623, 1_073_731_519),
    ),
)
def test_rrc_coefficients_support_supported_rolloffs(alpha, center, energy):
    assert RRC_ROLLOFFS == (0.2, 0.35, 0.5)
    coeff = rrc_coefficients(alpha)
    assert coeff.dtype == np.int16
    assert coeff.shape == (33,)
    assert np.array_equal(coeff, coeff[::-1])
    assert coeff[16] == center
    assert int(np.dot(coeff.astype(np.int64), coeff.astype(np.int64))) == energy


def test_rrc_coefficients_rejects_unsupported_rolloff():
    with pytest.raises(ValueError):
        rrc_coefficients(0.1)


def test_round_shift_signed_uses_ties_away_from_zero():
    assert round_shift_signed(128, 8) == 1
    assert round_shift_signed(127, 8) == 0
    assert round_shift_signed(-128, 8) == -1
    assert round_shift_signed(-127, 8) == 0
    assert round_shift_signed(3 * 32768 + 16384, 15) == 4
    assert round_shift_signed(-(3 * 32768 + 16384), 15) == -4


def test_rrc_filter_has_four_samples_per_symbol_and_32_sample_tail():
    i, q = generate_symbols("QPSK", 3)
    fi, fq = rrc_filter_symbols(i, q)
    assert len(fi) == len(fq) == 3 * 4 + 32
    assert fi.dtype == fq.dtype == np.int16
    assert np.any(fi[:12] != 0)
    assert np.any(fi[-32:] != 0)


def test_rrc_input_scale_and_dac_scale_are_symmetric():
    i, q = generate_symbols("QPSK", 1)
    fi, fq = rrc_filter_symbols(i, q)
    assert max(abs(int(fi.min())), abs(int(fi.max()))) <= 32767
    assert dac_scale(np.array([32512, -32512], dtype=np.int16)).tolist() == [127, -127]
    assert dac_scale(np.array([32767, -32768], dtype=np.int16)).tolist() == [127, -127]


def test_qpsk_rrc_match_filter_recovers_prbs_signs_after_total_delay():
    i, q = generate_symbols("QPSK", 127)
    tx_i, tx_q = rrc_filter_symbols(i, q)
    recovered_i, recovered_q = matched_filter_symbols((tx_i, tx_q))
    valid = slice(8, -8)
    assert np.array_equal(np.sign(recovered_i[valid]), np.sign(i[valid]))
    assert np.array_equal(np.sign(recovered_q[valid]), np.sign(q[valid]))


def test_rrc_rejects_mismatched_axes_and_invalid_match_lengths():
    with pytest.raises(ValueError):
        rrc_filter_symbols(np.array([1], dtype=np.int16), np.array([], dtype=np.int16))
    samples = (np.zeros(33, dtype=np.int16), np.zeros(33, dtype=np.int16))
    with pytest.raises(ValueError):
        matched_filter_symbols(samples)
    with pytest.raises(ValueError):
        matched_filter_symbols((np.zeros(32, dtype=np.int16),
                                np.zeros(32, dtype=np.int16)))

def test_round_shift_signed_rejects_negative_shift():
    with pytest.raises(ValueError):
        round_shift_signed(1, -1)


def test_rrc_filter_rejects_non_1d_symbols_and_bad_coefficients():
    with pytest.raises(ValueError):
        rrc_filter_symbols(np.zeros((1, 1)), np.zeros(1))
    with pytest.raises(ValueError):
        rrc_filter_symbols(np.zeros(1), np.zeros(1), np.zeros(32))


def test_matched_filter_rejects_bad_coefficients_and_delay():
    samples = (np.zeros(36, dtype=np.int16), np.zeros(36, dtype=np.int16))
    with pytest.raises(ValueError):
        matched_filter_symbols(samples, coeffs=np.zeros(32))
    with pytest.raises(ValueError):
        matched_filter_symbols(samples, delay_samples=-1)


def test_dac_scale_preserves_shape_and_saturates():
    samples = np.array([[32767, -32768], [32512, -32512]], dtype=np.int16)
    scaled = dac_scale(samples)
    assert scaled.dtype == np.int8
    assert scaled.shape == samples.shape
    assert scaled.tolist() == [[127, -127], [127, -127]]


def test_generated_rrc_files_have_three_coefficient_sets_and_paired_samples():
    coeff_file = Path("src/rtl/rrc_coeffs.vh")
    vector_files = (
        Path("src/tb/rrc_expected_qpsk_a020.mem"),
        Path("src/tb/rrc_expected.mem"),
        Path("src/tb/rrc_expected_qpsk_a050.mem"),
    )
    assert coeff_file.exists()
    assert all(vector_file.exists() for vector_file in vector_files)

    coeff_text = coeff_file.read_text(encoding="ascii")
    assert coeff_text.count("RRC_COEFF_") == 99
    for suffix in ("A20", "A35", "A50"):
        assert coeff_text.count(f"RRC_COEFF_00_{suffix}") == 1
        assert sum(f"RRC_COEFF_{index:02d}_{suffix}" in coeff_text
                   for index in range(RRC_TAPS)) == RRC_TAPS

    vector_lines = []
    for vector_file in vector_files:
        lines = [line for line in vector_file.read_text(encoding="ascii").splitlines() if line]
        assert len(lines) == 16 * 4 + 32
        assert all(
            len(line) == 8 and all(char in "0123456789abcdefABCDEF" for char in line)
            for line in lines
        )
        vector_lines.append(lines)
    assert len({lines[0] for lines in vector_lines}) == len(vector_lines)


@pytest.mark.parametrize(
    ("alpha", "vector_file"),
    (
        (0.2, Path("src/tb/rrc_expected_qpsk_a020.mem")),
        (0.35, Path("src/tb/rrc_expected.mem")),
        (0.5, Path("src/tb/rrc_expected_qpsk_a050.mem")),
    ),
)
def test_generated_rrc_vectors_match_python_reference(alpha, vector_file):
    i_symbols, q_symbols = generate_symbols("QPSK", 16)
    i_samples, q_samples = rrc_filter_symbols(
        i_symbols, q_symbols, rrc_coefficients(alpha)
    )
    expected_lines = [
        f"{int(i) & 0xffff:04x}{int(q) & 0xffff:04x}"
        for i, q in zip(i_samples, q_samples)
    ]
    actual_lines = [
        line for line in vector_file.read_text(encoding="ascii").splitlines() if line
    ]
    assert actual_lines == expected_lines
