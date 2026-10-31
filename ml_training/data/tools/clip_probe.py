"""Clip-probability probe for the synthetic receive-simulation model (data-gen tool).

Purpose
-------
This is a small *diagnostic* utility (not part of the dataset contract and not
imported by the generator or the reference checker).  It measures how often the
12-bit ADC model would saturate for each modulation / SNR combination under the
currently frozen configuration, using the same building blocks as
``ml_training/data/generate.py`` (bit seeding, constellation mapping, RECT
shaping, frame peak normalisation, receiver phase/CFO rotation and AWGN).

It exists because the clipping headroom interacts with the peak-normalisation
convention: the frozen contract uses **complex-envelope peak** normalisation
(``max|I+jQ| == amplitude_codes``) precisely so that the receiver phase/CFO
rotation applied afterwards cannot push a component above ``amplitude_codes``.
Run it after changing the config / headroom to confirm the expected clipped rate
for the formal dataset:

    python ml_training/data/tools/clip_probe.py

The formal acceptance evidence remains ``ml_training/data/reports/reference-check.json``
(actual clipped counts of the generated dataset); this probe is only a design aid.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DATA_DIR))

import generate  # noqa: E402

SNRS = [20.0, 18.0, 15.0, 12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 0.0, -3.0, -6.0]
TRIALS = 300


def probe_frame(rng, mode: str, snr_db: float, amplitude: int, waveform: dict) -> bool:
    sps = int(waveform["sps"])
    samples = int(waveform["samples"])
    n_symbols = int(waveform["n_symbols"])
    fs_hz = float(waveform["fs_hz"])
    symbol_seed = int(rng.integers(0, 2 ** 63))
    noise_seed = int(rng.integers(0, 2 ** 63))
    bits = generate.bits_from_seed(symbol_seed, mode, n_symbols)
    symbols = generate.symbols_from_bits(mode, bits, n_symbols)
    wave = generate.rect_waveform(symbols, sps)
    wave, _peak = generate.peak_normalise(wave, amplitude)
    offset = int(rng.integers(0, 20))
    seg = wave[offset:offset + samples]
    phase = float(np.float32(rng.uniform(0.0, 2.0 * np.pi)))
    cfo = float(np.float32(rng.uniform(-20000.0, 20000.0)))
    n = np.arange(samples, dtype=np.float64)
    clean = (seg[:, 0] + 1j * seg[:, 1]) * np.exp(1j * phase)
    clean = clean * np.exp(1j * (2.0 * np.pi * cfo * n / fs_hz))
    p_sig = float(np.mean(clean.real * clean.real + clean.imag * clean.imag))
    sigma = math.sqrt(p_sig / (2.0 * generate.snr_linear(snr_db)))
    noise = rng.standard_normal((samples, 2)) * sigma
    rx = np.stack([clean.real + noise[:, 0], clean.imag + noise[:, 1]], axis=1)
    _X, clipped = generate.quantize_with_clip(rx)
    return bool(clipped)


def main() -> int:
    config_path = REPO_ROOT / "ml_training" / "config" / "first_round.json"
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    waveform = config["dataset"]["waveform"]
    impairment = config["dataset"]["impairment"]
    headroom = float(impairment["clipping_headroom_sigma"])
    amp_low, amp_high = impairment["amplitude_codes_uniform"]
    rng = np.random.default_rng(20261020)

    print("config:", config_path.as_posix())
    print("headroom_sigma =", headroom, "| peak normalisation =", generate.PEAK_NORMALISATION)
    print("c table =", generate.SAFETY_C)
    header = "%-6s %s" % ("mode", " ".join("%6.0f" % snr for snr in SNRS))
    print(header)
    for mode in generate.CLASS_ORDER:
        rates = []
        for snr_db in SNRS:
            cap = generate.safe_amplitude_cap(generate.SAFETY_C[mode], snr_db, headroom)
            clipped = 0
            for _ in range(TRIALS):
                amplitude = min(int(rng.integers(int(amp_low), int(amp_high) + 1)), cap)
                clipped += probe_frame(rng, mode, snr_db, amplitude, waveform)
            rates.append(100.0 * clipped / TRIALS)
        print("%-6s %s" % (mode, " ".join("%5.2f%%" % rate for rate in rates)))
    print("SNR      " + " ".join("%6.0f" % snr for snr in SNRS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
