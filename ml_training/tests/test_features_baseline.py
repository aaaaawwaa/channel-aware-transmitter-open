"""Feature-baseline tests on ideal noiseless constellations.

The ideal frames are built directly in the test from the documented constellation
tables (independent of the data generator) so the analytic feature values below
are a real cross-check of the extractor.
"""
from __future__ import annotations

import numpy as np
import pytest

from ml_training.model import baseline as baseline_mod
from ml_training.model.features import FEATURE_NAMES, extract_features

SPS = 20
SYMBOLS = 207


def _axis(table, value):
    return table[value]


AXIS2 = (1, -1)
AXIS4 = (3, 1, -3, -1)
AXIS8 = (7, 5, 1, 3, -7, -5, -1, -3)


def _combos(mode, count=SYMBOLS):
    """Deterministically cycle through every constellation point equally often."""
    if mode == "BPSK":
        points = [(i, 0.0) for i in AXIS2]
    elif mode == "QPSK":
        points = [(i, q) for i in AXIS2 for q in AXIS2]
    elif mode == "QAM16":
        points = [(i, q) for i in AXIS4 for q in AXIS4]
    elif mode == "QAM64":
        points = [(i, q) for i in AXIS8 for q in AXIS8]
    else:
        raise ValueError(mode)
    return np.asarray([points[index % len(points)] for index in range(count)], dtype=np.float64)


def _ideal_features(mode):
    symbols = _combos(mode)
    # Peak-normalise to unit complex RMS (this is what the pre-processing contract does).
    waveform = np.repeat(symbols, SPS, axis=0)[:4096]
    rms = np.sqrt(np.mean(np.sum(waveform ** 2, axis=1)))
    x = (waveform / rms).T.astype(np.float32)
    return extract_features(x)


def test_feature_names_stable():
    assert FEATURE_NAMES == ("amp_kurtosis", "amp_sixth", "c20_abs", "c40_abs",
                             "c42_abs", "amp_low_fraction", "amp_entropy",
                             "phase_diff_entropy")


def test_psk_features():
    bpsk = _ideal_features("BPSK")
    qpsk = _ideal_features("QPSK")
    assert bpsk[0] == pytest.approx(1.0, abs=1e-6)     # constant modulus
    assert qpsk[0] == pytest.approx(1.0, abs=1e-6)
    assert bpsk[2] == pytest.approx(1.0, abs=1e-6)     # |E[z^2]| = 1 for BPSK
    assert qpsk[2] == pytest.approx(0.0, abs=0.01)     # ~0 for QPSK
    assert bpsk[3] == pytest.approx(2.0, abs=1e-6)     # |C40| = 2 for BPSK
    assert qpsk[3] == pytest.approx(1.0, abs=0.01)     # |C40| = 1 for QPSK
    assert bpsk[4] == pytest.approx(2.0, abs=1e-6)     # |C42| = 2 for BPSK
    assert qpsk[4] == pytest.approx(1.0, abs=0.01)     # |C42| = 1 for QPSK
    assert bpsk[5] == 0.0 and qpsk[5] == 0.0
    assert bpsk[6] == pytest.approx(0.0, abs=1e-6)     # single amplitude bin
    assert qpsk[6] == pytest.approx(0.0, abs=1e-6)


def test_qam_features_ordered():
    qam16 = _ideal_features("QAM16")
    qam64 = _ideal_features("QAM64")
    # Ideal unit-power values: kurtosis 1.32 / 1.381, |C40| 0.68 / 0.619,
    # |C42| 0.68 / 0.619.  Tolerances cover the finite 4096-sample window (only
    # ~205 symbols, so the 64-QAM sample statistics deviate by ~0.02).
    assert qam16[0] == pytest.approx(1.32, abs=0.02)
    assert qam64[0] == pytest.approx(1.381, abs=0.02)
    assert qam64[0] > qam16[0]
    assert qam16[3] == pytest.approx(0.68, abs=0.03)
    assert qam64[3] == pytest.approx(0.619, abs=0.03)
    assert qam16[4] == pytest.approx(0.68, abs=0.03)
    assert qam64[4] == pytest.approx(0.619, abs=0.03)
    assert qam16[5] > qam64[5] > 0.0                   # more low-amplitude points in QAM16
    assert qam64[6] > qam16[6] > 0.0                   # QAM64 amplitude histogram is wider


def test_logistic_regression_learns_separable_features():
    rng = np.random.default_rng(3)
    centres = np.array([[2.0, 0.0], [0.0, 2.0], [-2.0, 0.0], [0.0, -2.0]])
    features, labels = [], []
    for label, centre in enumerate(centres):
        for _ in range(60):
            features.append(centre + rng.normal(scale=0.3, size=2))
            labels.append(label)
    features = np.stack(features)
    labels = np.asarray(labels)
    parameters = baseline_mod.train_logistic(features, labels, 4, l2=0.0, epochs=200,
                                             lr=0.2, seed=1)
    probabilities = baseline_mod._predict_matrix(parameters, features)
    accuracy = float(np.mean(probabilities.argmax(axis=1) == labels))
    assert accuracy > 0.99
