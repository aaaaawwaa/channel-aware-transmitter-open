"""Integer IQ frame I/O (4096 complex samples, signed 12-bit, little-endian).

The on-disk frame format is identical to the real board capture format:
4096 x (I, Q) pairs of little-endian int16, i.e. exactly 16384 bytes, with the
samples constrained to the signed 12-bit range [-2048, 2047].
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

SAMPLES = 4096
FRAME_BYTES = SAMPLES * 2 * 2  # 16384
MIN_CODE = -2048
MAX_CODE = 2047


def as_frame(iq):
    """Validate/copy an IQ array into a contiguous native int16 (4096, 2) array."""
    arr = np.asarray(iq)
    if arr.shape != (SAMPLES, 2):
        raise ValueError("IQ frame must have shape (%d, 2)" % (SAMPLES,))
    if arr.dtype.kind not in "iuf":
        raise ValueError("IQ frame must be numeric")
    if (not np.isfinite(arr).all() or np.any(arr != np.floor(arr))
            or np.any(arr < MIN_CODE) or np.any(arr > MAX_CODE)):
        raise ValueError("IQ frame must contain finite integral signed 12-bit codes")
    return np.ascontiguousarray(arr, dtype=np.int16)


def frame_bytes(iq):
    """Raw little-endian int16 bytes of a validated frame."""
    return as_frame(iq).astype("<i2").tobytes()


def frame_sha256(iq):
    return hashlib.sha256(frame_bytes(iq)).hexdigest()


def read_frame(path):
    """Read one 16384-byte s16_iq_le frame and return a (4096, 2) int16 array."""
    raw = Path(path).read_bytes()
    if len(raw) != FRAME_BYTES:
        raise ValueError("IQ frame %s must be %d bytes, got %d"
                         % (path, FRAME_BYTES, len(raw)))
    return np.frombuffer(raw, dtype="<i2").reshape(SAMPLES, 2).astype(np.int16)


def write_frame(path, iq):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(frame_bytes(iq))


def clipping_detected(iq):
    arr = np.asarray(iq)
    return bool(np.any((arr == MIN_CODE) | (arr == MAX_CODE)))
