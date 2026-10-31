"""C-contract tests for the pure numpy RF IQ signal module."""
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rf_iq_signal import (
    apply_channel_impairment,
    bits_per_symbol,
    generate_digital_frame,
    map_bits_to_symbol,
    pack_iq,
    prbs7_bits,
    quantize_iq,
    rrc_taps,
)
import ui_model as M


def test_prbs7_known_prefix_and_period():
    assert prbs7_bits(1, 20) == [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0]
    # A 127-bit LFSR period repeats exactly after 127 bits.
    p = prbs7_bits(127, 254)
    assert p[:127] == p[127:]
    for bad in (0, 128, 1.0, True):
        with pytest.raises(ValueError):
            prbs7_bits(bad, 8)


def test_bits_per_symbol():
    assert bits_per_symbol("BPSK") == 1
    assert bits_per_symbol("QPSK") == 2
    assert bits_per_symbol("QAM16") == 4
    # QAM64 已移出本次交付支持范围：交付入口必须显式拒绝，而不是返回 6。
    # QAM64 映射源码与历史证据保留，不删除。
    with pytest.raises(ValueError):
        bits_per_symbol("QAM64")
    with pytest.raises(ValueError):
        bits_per_symbol("8PSK")


def test_constellation_maps_exact_rtl_order():
    # BPSK: bit0 -> +1, bit1 -> -1.
    assert map_bits_to_symbol("BPSK", (0,)) == (1, 0)
    assert map_bits_to_symbol("BPSK", (1,)) == (-1, 0)
    # QPSK: independent I/Q, same per-axis rule, MSB first.
    assert map_bits_to_symbol("QPSK", (0, 0)) == (1, 1)
    assert map_bits_to_symbol("QPSK", (0, 1)) == (1, -1)
    assert map_bits_to_symbol("QPSK", (1, 0)) == (-1, 1)
    assert map_bits_to_symbol("QPSK", (1, 1)) == (-1, -1)
    # QAM16: 00->+3, 01->+1, 10->-3, 11->-1 per axis.
    assert map_bits_to_symbol("QAM16", (0, 0, 0, 0)) == (3, 3)
    assert map_bits_to_symbol("QAM16", (0, 0, 0, 1)) == (3, 1)
    assert map_bits_to_symbol("QAM16", (0, 1, 1, 0)) == (1, -3)
    assert map_bits_to_symbol("QAM16", (1, 0, 1, 0)) == (-3, -3)
    assert map_bits_to_symbol("QAM16", (1, 1, 1, 1)) == (-1, -1)
    # QAM64 已移出本次交付支持范围：交付入口显式 ValueError；QAM64 映射表源码与
    # 历史证据保留，不删除、不改称其他模式，但也绝不纳入交付通过。
    with pytest.raises(ValueError):
        map_bits_to_symbol("QAM64", (0, 0, 0, 0, 0, 0))
    with pytest.raises(ValueError):
        map_bits_to_symbol("QAM64", (1, 1, 1, 1, 1, 1))


def test_prbs_to_symbol_msb_order_known_prefix():
    # seed=1 QPSK first two symbols: continuous bits 0,0 then 0,0 then 0,1...
    syms = []
    bits = prbs7_bits(1, 8)
    for i in range(4):
        syms.append(map_bits_to_symbol("QPSK", tuple(bits[2 * i:2 * i + 2])))
    assert syms == [(1, 1), (1, 1), (1, 1), (-1, 1)]


def test_rrc_taps_length_energy_and_singular_values():
    sps = 4
    alpha = 0.5
    taps = rrc_taps(sps, alpha)
    assert len(taps) == 8 * sps + 1
    assert np.isfinite(taps).all()
    assert np.sum(taps ** 2) == pytest.approx(sps, rel=0.02)
    center = len(taps) // 2
    h0 = 1 - alpha + 4 * alpha / math.pi
    h_half = (alpha / math.sqrt(2)) * (
        (1 + 2 / math.pi) * math.sin(math.pi / (4 * alpha))
        + (1 - 2 / math.pi) * math.cos(math.pi / (4 * alpha)))
    assert taps[center] == pytest.approx(h0, rel=1e-6)
    # For alpha=0.5, t=+/-1/(4*alpha) = +/-0.5 symbol periods.
    assert taps[center - 2] == pytest.approx(h_half, rel=1e-5)
    assert taps[center + 2] == pytest.approx(h_half, rel=1e-5)


def test_digital_frame_metadata_and_long_frame_rejection():
    args = {
        "mode": "QPSK", "filter": "RRC", "rolloff": "0.35", "seed": 1,
        "symbol_rate_hz": 2_000_000, "sample_rate_hz": 60_000_000,
        "amplitude": 1500,
    }
    meta, payload = generate_digital_frame(args)
    assert meta["samples"] == 3810
    assert meta["bytes"] == 3810 * 4
    assert meta["format"] == "s16_iq_le"
    assert len(payload) == 3810 * 4
    # Low symbol rate: 1 MSym/s at 60 MSPS would be sps=60 (7620 samples), reject.
    bad = dict(args, symbol_rate_hz=1_000_000)
    with pytest.raises(ValueError):
        generate_digital_frame(bad)
    # Non-integer sps reject.
    bad = dict(args, symbol_rate_hz=3_000_001)
    with pytest.raises(ValueError):
        generate_digital_frame(bad)


def test_digital_frame_zero_amplitude_is_zero_and_packed_12bit():
    args = {
        "mode": "QPSK", "filter": "RECT", "rolloff": "0.35", "seed": 1,
        "symbol_rate_hz": 2_000_000, "sample_rate_hz": 60_000_000,
        "amplitude": 0,
    }
    meta, payload = generate_digital_frame(args)
    values = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
    assert (values == 0).all()
    assert meta["samples"] == 3810


def test_channel_impairment_disabled_transparent_and_delay_fir():
    iq = np.array([[100, -100], [200, 200], [300, -300]], dtype=float)
    assert np.array_equal(apply_channel_impairment(iq, None), iq)
    imp = {"enabled": False, "noise_pct": 50, "taps": [
        {"gain_q8": 255, "delay_samples": 1},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0}], "noise_seed": 0}
    out = apply_channel_impairment(iq, imp)
    assert np.array_equal(out, iq)
    imp["noise_pct"] = 0
    imp["enabled"] = True
    out = apply_channel_impairment(iq, imp)
    # Pure delay by 1 sample with gain 256/256 and zero noise.
    expected = (255 / 256.0) * np.roll(iq, 1, axis=0)
    assert np.allclose(out, expected)


def test_channel_impairment_mixed_taps_direct_reference_and_noise_seed():
    iq = np.arange(12, dtype=float).reshape(6, 2)
    taps = [
        {"gain_q8": 128, "delay_samples": 0},
        {"gain_q8": 64, "delay_samples": 2},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
    ]
    imp = {"enabled": True, "noise_pct": 0, "taps": taps, "noise_seed": 1}
    out = apply_channel_impairment(iq, imp)
    expected = np.empty_like(iq)
    for n in range(len(iq)):
        expected[n] = (128 / 256) * iq[n] + (64 / 256) * iq[(n - 2) % len(iq)]
    assert np.allclose(out, expected)

    imp2 = dict(imp, noise_pct=20, noise_seed=123)
    a = apply_channel_impairment(iq, imp2)
    b = apply_channel_impairment(iq, imp2)
    c = apply_channel_impairment(iq, dict(imp2, noise_seed=124))
    assert np.array_equal(a, b)
    assert not np.array_equal(a[:, 0], c[:, 0])
    assert not np.array_equal(a[:, 0], a[:, 1])  # independent I/Q


def test_quantize_ties_to_even_and_rejects_out_of_range():
    values = np.array([0.5, 1.5, -0.5, -1.5, 2.4, -2.4], dtype=float)
    quantized = quantize_iq(values)
    assert quantized.tolist() == [0, 2, 0, -2, 2, -2]
    with pytest.raises(ValueError):
        quantize_iq(np.array([2048.0]))
    with pytest.raises(ValueError):
        quantize_iq(np.array([-2049.0]))


def test_impairment_out_of_range_rejected():
    iq = np.array([[2047, 0]], dtype=float)
    imp = {"enabled": True, "noise_pct": 1, "taps": [
        {"gain_q8": 255, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0},
        {"gain_q8": 0, "delay_samples": 0}], "noise_seed": 0}
    with pytest.raises(ValueError):
        quantize_iq(apply_channel_impairment(iq, imp))



def _rrc_reference(symbols, sps, rolloff):
    sample_count = len(symbols) * sps
    h = rrc_taps(sps, rolloff)
    h = np.roll(h, -(len(h) // 2))
    x = np.zeros((sample_count, 2), dtype=float)
    x[::sps] = symbols
    out = np.empty_like(x)
    for c in range(2):
        out[:, c] = np.fft.ifft(np.fft.fft(h, sample_count) * np.fft.fft(x[:, c], sample_count)).real
    return out


def test_rrc_frame_matches_periodic_convolution_reference():
    args = {
        "mode": "QPSK", "filter": "RRC", "rolloff": "0.5", "seed": 1,
        "symbol_rate_hz": 15_000_000, "sample_rate_hz": 60_000_000,
        "amplitude": 2047,
    }
    meta, payload = generate_digital_frame(args)
    sps = args["sample_rate_hz"] // args["symbol_rate_hz"]
    bits = prbs7_bits(args["seed"], 127 * bits_per_symbol(args["mode"]))
    bps = bits_per_symbol(args["mode"])
    symbols = np.array(
        [map_bits_to_symbol(args["mode"], tuple(bits[i * bps:(i + 1) * bps]))
         for i in range(127)], dtype=float)
    out = _rrc_reference(symbols, sps, args["rolloff"])
    peak = float(np.max(np.abs(out)))
    scaled = out * (args["amplitude"] / peak)
    assert payload == pack_iq(scaled)


def test_rect_frame_repeats_symbols_over_sps_period():
    args = {
        "mode": "QPSK", "filter": "RECT", "rolloff": "0.35", "seed": 7,
        "symbol_rate_hz": 15_000_000, "sample_rate_hz": 60_000_000,
        "amplitude": 1000,
    }
    meta, payload = generate_digital_frame(args)
    sps = args["sample_rate_hz"] // args["symbol_rate_hz"]
    iq = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
    assert iq.shape[0] == 127 * sps
    for start in range(0, iq.shape[0], sps):
        block = iq[start:start + sps]
        assert (block == block[0]).all(), "RECT frame must repeat each symbol for exactly sps samples"


def test_pack_iq_little_endian_payload_bytes():
    iq = np.array([[1, -2], [258, -513]], dtype=float)
    assert pack_iq(iq) == b"\x01\x00\xfe\xff\x02\x01\xff\xfd"


def test_ui_fs_dependent_symrate_range_contract():
    assert M.rf_digital_symrate_range(60_000_000) == (1_875_000, 30_000_000)
    assert M.rf_digital_symrate_range(0) == (0, 0)
    assert M.rf_digital_parameter_error("amplitude", 1000) is None
    assert M.rf_digital_parameter_error("amplitude", 2048) is not None
