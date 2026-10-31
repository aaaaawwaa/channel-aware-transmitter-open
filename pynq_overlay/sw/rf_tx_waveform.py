"""Explicit I/Q waveforms for the RF binary load contract.

Supports the original 1024-point common waveforms plus the finite-frame AD9361
digital modulation path.  An optional `impairment` dict is applied to every
waveform before final 12-bit signed packing.
"""
import math
import random
import struct

import numpy as np

import rf_iq_signal as iq

SAMPLES = 1024


def _finish_with_count(payload, samples):
    return {"samples": samples, "bytes": samples * 4, "format": "s16_iq_le",
            "direction": "tx", "wrap": True}, payload


def _finish(payload):
    return _finish_with_count(payload, SAMPLES)


def _check_amp(amplitude):
    if type(amplitude) is not int or not 0 <= amplitude <= 2047:
        raise ValueError("amplitude must be an integer 0..2047")


def _check_cycles(cycles):
    if type(cycles) is not int or not 1 <= cycles <= 511:
        raise ValueError("cycles must be an integer 1..511")

def _check_duty(duty_pct):
    if type(duty_pct) is not int or not 0 <= duty_pct <= 100:
        raise ValueError("duty_pct must be an integer 0..100")

def cycles_for_frequency(freq_hz, sample_rate_hz, samples=SAMPLES):
    """Return (cycles, actual_hz) for a baseband frequency at the applied Fs.

    Rejects frequencies below one Fs/samples step, at/above Nyquist, and any
    frequency that rounds to an unrepresentable cycle count (1..511).
    """
    if type(freq_hz) is not int or freq_hz <= 0:
        raise ValueError("freq_hz must be a positive integer")
    if type(sample_rate_hz) is not int or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive integer")
    step = sample_rate_hz / float(samples)
    if freq_hz < step:
        raise ValueError("frequency is below one Fs/%d step" % samples)
    if freq_hz >= sample_rate_hz / 2.0:
        raise ValueError("frequency is at or above Nyquist")
    cycles = int(round(freq_hz / step))
    if not 1 <= cycles <= 511:
        raise ValueError("cycles must be an integer 1..511")
    return cycles, cycles * step

def build_cw(args):
    amplitude = args["amplitude"]
    _check_amp(amplitude)
    payload = b"".join(struct.pack("<hh", amplitude, 0) for _ in range(SAMPLES))
    return _finish(payload)


def build_sine(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    _check_cycles(cycles); _check_amp(amplitude)
    payload = b"".join(struct.pack("<hh", round(amplitude * math.sin(
        2 * math.pi * cycles * i / SAMPLES)), 0) for i in range(SAMPLES))
    return _finish(payload)


def build_square(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    _check_cycles(cycles); _check_amp(amplitude)
    # Per-cycle internal phase, not whole-frame block parity.  This keeps cycles=1
    # as one 50% duty square period and makes 17/32 cycles land on the expected
    # FFT fundamentals instead of DC/block artifacts.
    payload = b"".join(struct.pack(
        "<hh", amplitude if ((cycles * i) % SAMPLES) < (SAMPLES // 2) else -amplitude, 0)
        for i in range(SAMPLES))
    return _finish(payload)


def build_triangle(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    _check_cycles(cycles); _check_amp(amplitude)
    payload = bytearray()
    for i in range(SAMPLES):
        ph = (cycles * i) % SAMPLES
        if ph < SAMPLES // 2:
            v = -amplitude + 4 * amplitude * ph // SAMPLES
        else:
            v = amplitude - 4 * amplitude * (ph - SAMPLES // 2) // SAMPLES
        payload += struct.pack("<hh", v, 0)
    return _finish(bytes(payload))


def build_sawtooth(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    _check_cycles(cycles); _check_amp(amplitude)
    payload = b"".join(struct.pack("<hh", -amplitude + 2 * amplitude * ((cycles * i) % SAMPLES) // SAMPLES, 0) for i in range(SAMPLES))
    return _finish(payload)

def build_pulse(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    duty_pct = args.get("duty_pct", 50)
    _check_cycles(cycles); _check_amp(amplitude); _check_duty(duty_pct)
    threshold = int(round(SAMPLES * duty_pct / 100.0))
    payload = b"".join(struct.pack(
        "<hh", amplitude if ((cycles * i) % SAMPLES) < threshold else -amplitude, 0)
        for i in range(SAMPLES))
    return _finish(payload)

def build_multitone(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    cycles2 = args["tone2_cycles"]
    amp2 = args["tone2_amplitude"]
    _check_cycles(cycles); _check_amp(amplitude)
    _check_cycles(cycles2); _check_amp(amp2)
    if amplitude + amp2 > 2047:
        raise ValueError("multitone amplitude sum must not exceed 2047")
    payload = b"".join(struct.pack("<hh", round(amplitude * math.sin(
        2 * math.pi * cycles * i / SAMPLES) + amp2 * math.sin(
        2 * math.pi * cycles2 * i / SAMPLES)), 0) for i in range(SAMPLES))
    return _finish(payload)


def build_chirp(args):
    cycles, amplitude = args["cycles"], args["amplitude"]
    f1 = args["chirp_f1_cycles"]
    _check_cycles(cycles); _check_amp(amplitude); _check_cycles(f1)
    payload = b"".join(struct.pack("<hh", round(amplitude * math.sin(
        2 * math.pi * (cycles * i + (f1 - cycles) * i * i / (2 * SAMPLES)) / SAMPLES)), 0) for i in range(SAMPLES))
    return _finish(payload)


def build_noise(args):
    amplitude = args["amplitude"]
    seed = args.get("seed", 1)
    _check_amp(amplitude)
    rng = random.Random(seed)
    payload = b"".join(struct.pack("<hh", rng.randint(-amplitude, amplitude), 0) for _ in range(SAMPLES))
    return _finish(payload)


BUILDERS = {
    "cw": build_cw,
    "sine": build_sine,
    "square": build_square,
    "triangle": build_triangle,
    "sawtooth": build_sawtooth,
    "pulse": build_pulse,
    "multitone": build_multitone,
    "chirp": build_chirp,
    "noise": build_noise,
}


def _apply_impairment(metadata, payload, impairment):
    """Apply the shared channel impairment to an already-built IQ payload."""
    if impairment is None:
        return metadata, payload
    values = np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(float)
    values = iq.apply_channel_impairment(values, impairment)
    payload = iq.pack_iq(values)
    return _finish_with_count(payload, metadata["samples"])


def build_tx_waveform(args):
    """Return daemon metadata and immutable little-endian int16 I/Q bytes."""
    if not isinstance(args, dict):
        raise ValueError("waveform args must be a dict")
    wf = args.get("waveform", "sine")
    if wf == "digital":
        metadata, payload = iq.generate_digital_frame(args)
        return metadata, payload
    if wf not in BUILDERS:
        raise ValueError("unknown waveform type: %r" % (wf,))
    try:
        metadata, payload = BUILDERS[wf](args)
    except KeyError as exc:
        raise ValueError("missing waveform parameter: %s" % exc)
    return _apply_impairment(metadata, payload, args.get("impairment"))
