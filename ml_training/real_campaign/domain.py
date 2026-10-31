"""Input-domain constants/validation for real campaign import.

Independent of any model prediction or environment label.  The real campaign import only
accepts the frozen round-2 inference domain; an out-of-domain frame is indexed and clearly
marked ``NOT_RUN_DOMAIN`` rather than silently reshaped.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

FORMAT = "s16_iq_le"
SAMPLES = 4096
FRAME_BYTES = SAMPLES * 2 * 2
SAMPLE_RATE_HZ = 60_000_000
DECIMATION = 1
SYMBOL_RATE_HZ = 3_000_000
SHAPING = "RECT"
MIN_CODE = -2048
MAX_CODE = 2047

CONTEXT_FIELDS = ("format", "samples", "sample_rate_hz", "decimation",
                  "symbol_rate_hz", "shaping")
FROZEN_CONTEXT = {
    "format": FORMAT, "samples": SAMPLES, "sample_rate_hz": SAMPLE_RATE_HZ,
    "decimation": DECIMATION, "symbol_rate_hz": SYMBOL_RATE_HZ, "shaping": SHAPING,
}

REASON_OK = ""
REASON_INVALID_LENGTH = "invalid_iq_length"
REASON_INVALID_FORMAT = "invalid_iq_format"
REASON_INVALID_RANGE = "invalid_iq_range"
REASON_UNSUPPORTED_FORMAT = "unsupported_format"
REASON_UNSUPPORTED_LENGTH = "unsupported_length"
REASON_UNSUPPORTED_SAMPLING = "unsupported_sampling_domain"
REASON_UNSUPPORTED_SYMBOL = "unsupported_symbol_domain"
REASON_MISSING_CONTEXT = "missing_input_context"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def validate_context(context):
    """Validate a declared input context against the frozen round-2 domain."""
    if not isinstance(context, dict):
        return {"ok": False, "reason": REASON_MISSING_CONTEXT,
                "missing_fields": list(CONTEXT_FIELDS), "observed": None}
    missing = [field for field in CONTEXT_FIELDS if context.get(field) is None]
    if missing:
        return {"ok": False, "reason": REASON_MISSING_CONTEXT,
                "missing_fields": missing, "observed": context}
    observed = {field: context[field] for field in CONTEXT_FIELDS}
    if observed["format"] != FORMAT:
        return {"ok": False, "reason": REASON_UNSUPPORTED_FORMAT, "observed": observed,
                "required": {"format": FORMAT}}
    try:
        samples = int(observed["samples"])
        sample_rate = float(observed["sample_rate_hz"])
        decimation = int(observed["decimation"])
        symbol_rate = float(observed["symbol_rate_hz"])
    except (TypeError, ValueError):
        return {"ok": False, "reason": REASON_UNSUPPORTED_SAMPLING, "observed": observed,
                "error": "non-numeric context field"}
    if samples != SAMPLES:
        return {"ok": False, "reason": REASON_UNSUPPORTED_LENGTH, "observed": observed,
                "required": {"samples": SAMPLES}}
    if sample_rate != float(SAMPLE_RATE_HZ) or decimation != DECIMATION:
        return {"ok": False, "reason": REASON_UNSUPPORTED_SAMPLING, "observed": observed,
                "required": {"sample_rate_hz": SAMPLE_RATE_HZ,
                             "decimation": DECIMATION}}
    if symbol_rate != float(SYMBOL_RATE_HZ) or observed["shaping"] != SHAPING:
        return {"ok": False, "reason": REASON_UNSUPPORTED_SYMBOL, "observed": observed,
                "required": {"symbol_rate_hz": SYMBOL_RATE_HZ, "shaping": SHAPING}}
    return {"ok": True, "reason": REASON_OK, "observed": observed}


def load_iq(path):
    """Read an explicit ``s16_iq_le`` file and validate bytes/shape/range/digest."""
    path = Path(path)
    result = {"ok": False, "reason": REASON_INVALID_FORMAT, "path": str(path),
              "digest": None, "iq": None,
              "observed": {"exists": path.is_file(),
                           "bytes": path.stat().st_size if path.is_file() else None}}
    if not path.is_file():
        result["reason"] = "missing_iq_file"
        return result
    raw = path.read_bytes()
    result["observed"]["bytes"] = len(raw)
    result["digest"] = sha256_bytes(raw)
    if len(raw) != FRAME_BYTES:
        result["reason"] = REASON_INVALID_LENGTH
        return result
    values = np.frombuffer(raw, dtype="<i2").reshape(SAMPLES, 2)
    result["observed"].update({"shape": list(values.shape), "dtype": "int16",
                               "min": int(values.min()), "max": int(values.max())})
    if values.min() < MIN_CODE or values.max() > MAX_CODE:
        result["reason"] = REASON_INVALID_RANGE
        return result
    result.update(ok=True, reason=REASON_OK, iq=values)
    return result
