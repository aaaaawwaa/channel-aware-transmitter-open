"""Pre-processing contract `dc-complex-rms-v1` (identical semantics to the board runtime).

The board inference path in ``pynq_overlay/sw/rf_cnn_runtime.py:preprocess`` is the
frozen reference: per-component DC removal, shared complex-RMS normalisation to
[2, 4096] float32, with clipping / no-energy quality rejects.  This module is a
self-contained copy of that contract; ``ml_training/tests/test_preprocess.py``
asserts element-wise equality against the product module.
"""
from __future__ import annotations

import numpy as np

PREPROCESSING = "dc-complex-rms-v1"
SAMPLES = 4096
MIN_CODE = -2048
MAX_CODE = 2047


def preprocess_iq(iq):
    """Return (x float32 [2, 4096] | None, gate dict).

    gate = {"ok": bool, "reason": "" | "clipping" | "no_energy", "rms": float}.
    ``rms`` is the raw-code complex RMS measured after per-component DC removal
    (same meaning as the board runtime's gate rms).
    """
    values = np.asarray(iq)
    if values.shape != (SAMPLES, 2) or values.dtype.kind not in "ifu":
        raise ValueError("IQ must be 4096 numeric signed-12-bit I/Q pairs")
    if (not np.isfinite(values).all() or np.any(values < MIN_CODE)
            or np.any(values > MAX_CODE) or np.any(values != np.floor(values))):
        raise ValueError("invalid signed-12-bit IQ values")
    if np.any((values == MIN_CODE) | (values == MAX_CODE)):
        return None, {"ok": False, "reason": "clipping", "rms": 0.0}
    x = values.astype(np.float32)
    x -= x.mean(axis=0, keepdims=True)
    rms = float(np.sqrt(np.mean(np.sum(x.astype(np.float64) ** 2, axis=1))))
    if rms <= 1e-6:
        return None, {"ok": False, "reason": "no_energy", "rms": 0.0}
    return np.ascontiguousarray((x / rms).T, dtype=np.float32), {"ok": True, "reason": "", "rms": rms}


def complex_rms(iq):
    """Raw-code complex RMS after DC removal, without the clipping gate."""
    values = np.asarray(iq, dtype=np.float64)
    if values.shape != (SAMPLES, 2):
        raise ValueError("IQ must be 4096 I/Q pairs")
    centered = values - values.mean(axis=0, keepdims=True)
    return float(np.sqrt(np.mean(np.sum(centered ** 2, axis=1))))
