"""Tests for the frozen pre-processing contract (must equal the board runtime)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from ml_training.common.preprocess import complex_rms, preprocess_iq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_SW = PROJECT_ROOT / "pynq_overlay" / "sw"


def _product_runtime():
    if str(PRODUCT_SW) not in sys.path:
        sys.path.append(str(PRODUCT_SW))
    import rf_cnn_runtime  # noqa: PLC0415

    return rf_cnn_runtime


def _random_frame(rng, amplitude=800):
    real = rng.integers(-amplitude, amplitude + 1, size=4096)
    imag = rng.integers(-amplitude, amplitude + 1, size=4096)
    return np.stack([real, imag], axis=1).astype(np.int16)


def test_matches_product_runtime_elementwise():
    product = _product_runtime()
    rng = np.random.default_rng(7)
    for _ in range(5):
        iq = _random_frame(rng)
        ours, our_gate = preprocess_iq(iq)
        theirs, their_gate = product.preprocess(iq)
        assert our_gate == their_gate
        assert np.array_equal(ours, theirs)


def test_clipping_gate():
    frame = np.zeros((4096, 2), dtype=np.int16)
    frame[100, 0] = 2047
    x, gate = preprocess_iq(frame)
    assert x is None
    assert gate == {"ok": False, "reason": "clipping", "rms": 0.0}


def test_no_energy_gate():
    x, gate = preprocess_iq(np.zeros((4096, 2), dtype=np.int16))
    assert x is None
    assert gate["reason"] == "no_energy"
    assert gate["rms"] == 0.0


def test_output_shape_rms_and_dc():
    rng = np.random.default_rng(11)
    iq = _random_frame(rng)
    x, gate = preprocess_iq(iq)
    assert x is not None
    assert x.shape == (2, 4096) and x.dtype == np.float32
    assert abs(float(np.sqrt(np.mean(np.sum(x.astype(np.float64) ** 2, axis=0)))) - 1.0) < 1e-6
    assert np.allclose(x.mean(axis=1), 0.0, atol=1e-6)
    assert gate["rms"] == pytest.approx(complex_rms(iq))


def test_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        preprocess_iq(np.zeros((4095, 2), dtype=np.int16))
    with pytest.raises(ValueError):
        preprocess_iq(np.full((4096, 2), 2048, dtype=np.int16))
    with pytest.raises(ValueError):
        preprocess_iq(np.full((4096, 2), 1.5))
