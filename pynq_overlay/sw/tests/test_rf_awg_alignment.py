"""Chunk B alignment tests: square phase, Pulse duty, 12-bit bounds, zero amplitude, frequency quantization."""
import pathlib
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from rf_tx_waveform import SAMPLES, build_tx_waveform, cycles_for_frequency


def _i_values(payload):
    """Return every I sample (even little-endian int16 slots)."""
    return struct.unpack("<%dh" % (len(payload) // 2), payload)[0::2]


def _fft_peak_bin(values):
    spectrum = np.abs(np.fft.rfft(values))
    spectrum[0] = 0.0  # ignore DC; square errors previously showed DC-only for cycles=1
    return int(np.argmax(spectrum))


def _positive_runs(values):
    runs = 0
    prev = False
    for v in values:
        now = v > 0
        if now and not prev:
            runs += 1
        prev = now
    return runs


@pytest.mark.parametrize("cycles", [1, 17, 32])
def test_square_uses_per_cycle_phase_and_fft_fundamental(cycles):
    _meta, payload = build_tx_waveform(
        {"waveform": "square", "cycles": cycles, "amplitude": 1000})
    values = _i_values(payload)
    assert _fft_peak_bin(values) == cycles
    assert _positive_runs(values) == cycles


@pytest.mark.parametrize("duty,expected", [
    (0, {-1000}),
    (100, {1000}),
    (50, {-1000, 1000}),
])
def test_pulse_duty_edge_cases(duty, expected):
    _meta, payload = build_tx_waveform(
        {"waveform": "pulse", "cycles": 4, "amplitude": 1000, "duty_pct": duty})
    values = set(_i_values(payload))
    assert values == expected


def test_pulse_duty_50_is_half_high_half_low():
    _meta, payload = build_tx_waveform(
        {"waveform": "pulse", "cycles": 4, "amplitude": 1000, "duty_pct": 50})
    values = _i_values(payload)
    assert sum(v > 0 for v in values) == len(values) // 2
    assert sum(v < 0 for v in values) == len(values) // 2


def test_zero_amplitude_yields_zero_iq_for_all_waveforms():
    cases = [
        ("sine", {"cycles": 4}),
        ("cw", {}),
        ("square", {"cycles": 4}),
        ("triangle", {"cycles": 4}),
        ("sawtooth", {"cycles": 4}),
        ("pulse", {"cycles": 4, "duty_pct": 50}),
        ("multitone", {"cycles": 4, "tone2_cycles": 2, "tone2_amplitude": 0}),
        ("chirp", {"cycles": 4, "chirp_f1_cycles": 8}),
        ("noise", {"seed": 1}),
    ]
    for wf, extra in cases:
        args = {"waveform": wf, "amplitude": 0, **extra}
        _meta, payload = build_tx_waveform(args)
        assert list(_i_values(payload)) == [0] * SAMPLES, wf
        # Q is zero in every generated waveform as well.
        q = struct.unpack("<%dh" % (len(payload) // 2), payload)[1::2]
        assert list(q) == [0] * SAMPLES, wf


def test_multitone_rejects_peak_sum_above_2047():
    with pytest.raises(ValueError):
        build_tx_waveform({"waveform": "multitone", "cycles": 16, "amplitude": 1500,
                           "tone2_cycles": 8, "tone2_amplitude": 1000})
    with pytest.raises(ValueError):
        build_tx_waveform({"waveform": "multitone", "cycles": 16, "amplitude": 2047,
                           "tone2_cycles": 8, "tone2_amplitude": 1})

    # Boundary sum exactly 2047 is allowed and stays inside signed 12-bit range.
    _meta, payload = build_tx_waveform({"waveform": "multitone", "cycles": 16,
                                        "amplitude": 1000, "tone2_cycles": 8,
                                        "tone2_amplitude": 1047})
    values = _i_values(payload)
    assert max(values) <= 2047 and min(values) >= -2047


def test_frequency_quantization_uses_applied_fs_step():
    assert cycles_for_frequency(1_000_000, 60_000_000) == (17, 996093.75)
    with pytest.raises(ValueError):
        cycles_for_frequency(10_000, 60_000_000)      # below one Fs/1024 step
    with pytest.raises(ValueError):
        cycles_for_frequency(30_000_000, 60_000_000)  # Nyquist is not representable
    with pytest.raises(ValueError):
        cycles_for_frequency(60_000_000, 60_000_000)
