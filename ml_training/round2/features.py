"""Interpretable spectral features used by the round-2 quality gate.

Computed on the frozen ``dc-complex-rms-v1`` pre-processed frame (unit complex RMS).
The two selected features have a clear physical reading:

* ``top5_frac``: fraction of total spectrum power in the five strongest bins.  A pure
  tone concentrates its power in a handful of bins; a RECT-modulated random signal has
  a sinc-like spectrum spread over hundreds of bins.
* ``band_frac_3m``: fraction of power inside |f| <= 3 MHz (the RECT main lobe of a
  3 MSym/s signal).  RECT modulation keeps ~90% of its signal power there; full-band
  AWGN only ~10%.

Both are scale invariant (power fractions), so they do not depend on the receive
amplitude or AGC state.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE_HZ = 60_000_000.0
BAND_HZ = 3_000_000.0
SPECTRAL_NAMES = ("top1_frac", "top5_frac", "top9_frac", "band_frac_3m",
                  "peak_to_median_db", "spectral_entropy")


def spectral_features(x):
    """Pre-processed ``(2, 4096)`` frame -> 6 interpretable spectral features."""
    x = np.asarray(x, dtype=np.float64)
    if x.shape != (2, 4096):
        raise ValueError("spectral input must be (2, 4096)")
    z = x[0] + 1j * x[1]
    power = np.abs(np.fft.fftshift(np.fft.fft(z))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(z), 1.0 / SAMPLE_RATE_HZ))
    total = float(power.sum()) + 1e-30
    ordered = np.sort(power)[::-1]
    values = (
        float(ordered[0] / total),
        float(ordered[:5].sum() / total),
        float(ordered[:9].sum() / total),
        float(power[np.abs(freqs) <= BAND_HZ].sum() / total),
        float(10.0 * np.log10((ordered[0] / (np.median(power) + 1e-30)))),
    )
    probabilities = power / total
    nonzero = probabilities[probabilities > 0]
    entropy = float(-(nonzero * np.log2(nonzero)).sum() / 12.0)  # normalised by log2(4096)
    values = values + (entropy,)
    return dict(zip(SPECTRAL_NAMES, values))
