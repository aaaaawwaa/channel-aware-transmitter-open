"""Interpretable, phase-invariant features for the modulation baseline.

All features are computed on the ``dc-complex-rms-v1`` pre-processed frame (unit
complex RMS), so they are amplitude-scale and slowly-varying phase independent.
They are deliberately classic and explainable:

====================  ==========================================================
feature               intuition
====================  ==========================================================
amp_kurtosis          E|z|^4 / (E|z|^2)^2: 1 for constant-modulus PSK, >1 for QAM
amp_sixth             E|z|^6 / (E|z|^2)^3: spread of the amplitude distribution
c20_abs               |E[z^2]|: 1 for BPSK, ~0 for QPSK/QAM
c40_abs               |E[z^4] - 3 E[z^2]^2|: non-zero for PSK / sub-Gaussian QAM
c42_abs               |E|z|^4 - |E[z^2]|^2 - 2 (E|z|^2)^2|: order parameter
amp_low_fraction      fraction of samples with |z| < 0.55 * RMS (QAM low rings)
amp_entropy           normalised entropy of a 6-bin amplitude histogram
phase_diff_entropy    normalised entropy of 8-bin symbol-lag phase differences
====================  ==========================================================
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES = (
    "amp_kurtosis",
    "amp_sixth",
    "c20_abs",
    "c40_abs",
    "c42_abs",
    "amp_low_fraction",
    "amp_entropy",
    "phase_diff_entropy",
)
FEATURE_SET = "modrec-features-v1"
SYMBOL_LAG = 20  # one symbol at 60 MSPS / 3 MSym/s


def _normalised_entropy(values, bins):
    indices = np.digitize(values, bins) - 1
    indices = np.clip(indices, 0, len(bins) - 2)
    counts = np.bincount(indices, minlength=len(bins) - 1)
    counts = counts[counts > 0].astype(np.float64)
    probabilities = counts / counts.sum()
    return float(-np.sum(probabilities * np.log2(probabilities)) / np.log2(len(bins) - 1))


def extract_features(x):
    """One pre-processed frame ``(2, 4096)`` float32 -> float64 feature vector (8,)."""
    x = np.asarray(x, dtype=np.float64)
    if x.shape != (2, 4096):
        raise ValueError("feature input must be (2, 4096)")
    i = x[0]
    q = x[1]
    z = i + 1j * q
    power = np.mean(np.abs(z) ** 2)
    if not np.isfinite(power) or power <= 0.0:
        raise ValueError("invalid pre-processed frame (zero power)")
    z2 = np.mean(z ** 2)
    z4 = np.mean(z ** 4)
    abs4 = np.mean(np.abs(z) ** 4)
    abs6 = np.mean(np.abs(z) ** 6)
    amplitude = np.abs(z)
    rms = np.sqrt(power)
    phase_diff = np.angle(z[SYMBOL_LAG:]) - np.angle(z[:-SYMBOL_LAG])
    features = (
        abs4 / (power ** 2),
        abs6 / (power ** 3),
        abs(z2),
        abs(z4 - 3.0 * z2 ** 2),
        abs(abs4 - abs(z2) ** 2 - 2.0 * power ** 2),
        float(np.mean(amplitude < 0.55 * rms)),
        _normalised_entropy(amplitude / rms, np.array([0.0, 0.4, 0.6, 0.8, 1.0, 1.2, np.inf])),
        _normalised_entropy(phase_diff, np.array([-np.pi, -np.pi * 0.75, -np.pi * 0.5, -np.pi * 0.25,
                                                  0.0, np.pi * 0.25, np.pi * 0.5, np.pi * 0.75, np.pi])),
    )
    vector = np.asarray(features, dtype=np.float64)
    if not np.isfinite(vector).all():
        raise ValueError("nonfinite feature vector")
    return vector


def extract_batch(frames):
    """List/array of pre-processed frames -> (N, 8) float64 feature matrix."""
    return np.stack([extract_features(frame) for frame in frames], axis=0)
