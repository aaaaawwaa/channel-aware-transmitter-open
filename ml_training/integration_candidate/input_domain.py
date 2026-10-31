"""Input-domain contract for the frozen round-2 adapter.

The capture metadata (sample rate, decimation, symbol rate, shaping, format) must be supplied
by the caller from the capture/configuration path.  It is **never inferred from the IQ data
or from a model prediction**.  When the context is missing or unsupported the adapter returns
an explicit ``UNAVAILABLE`` result and does not run the model.

Supported domain (current frozen limit):
* format ``s16_iq_le``: little-endian signed int16 interleaved I/Q;
* 4096 complex samples = 8192 int16 = 16,384 bytes per frame;
* sample rate 60,000,000 Hz and decimation 1 (input complex sample rate 60 MSPS);
* symbol rate 3,000,000 sym/s with RECT shaping.

Reason codes are stable strings: ``missing_input_context``, ``unsupported_format``,
``unsupported_length``, ``unsupported_sampling_domain``, ``unsupported_symbol_domain``,
``invalid_iq_format``, ``invalid_iq_length``.
"""
from __future__ import annotations

import numpy as np

FORMAT = "s16_iq_le"
SAMPLES = 4096
SAMPLE_RATE_HZ = 60_000_000
DECIMATION = 1
SYMBOL_RATE_HZ = 3_000_000
SHAPING = "RECT"
FRAME_BYTES = SAMPLES * 2 * 2
MIN_CODE = -2048
MAX_CODE = 2047

REQUIRED_FIELDS = ("format", "samples", "sample_rate_hz", "decimation",
                   "symbol_rate_hz", "shaping")

REASON_MISSING_CONTEXT = "missing_input_context"
REASON_UNSUPPORTED_FORMAT = "unsupported_format"
REASON_UNSUPPORTED_LENGTH = "unsupported_length"
REASON_UNSUPPORTED_SAMPLING = "unsupported_sampling_domain"
REASON_UNSUPPORTED_SYMBOL = "unsupported_symbol_domain"
REASON_INVALID_IQ_FORMAT = "invalid_iq_format"
REASON_INVALID_IQ_LENGTH = "invalid_iq_length"

# Declared input context of the frozen package frames.  This is capture metadata, not a
# prediction-derived value; callers must supply their own context from the capture path.
FROZEN_INPUT_CONTEXT = {
    "format": FORMAT,
    "samples": SAMPLES,
    "sample_rate_hz": SAMPLE_RATE_HZ,
    "decimation": DECIMATION,
    "symbol_rate_hz": SYMBOL_RATE_HZ,
    "shaping": SHAPING,
}

REQUIRED_DOMAIN = {
    "format": FORMAT,
    "samples": SAMPLES,
    "sample_rate_hz": SAMPLE_RATE_HZ,
    "decimation": DECIMATION,
    "symbol_rate_hz": SYMBOL_RATE_HZ,
    "shaping": SHAPING,
}


def _result(ok, reason="", **details):
    return {"ok": bool(ok), "reason": reason, "details": details}


def validate_context(context):
    """Validate caller-provided capture/configuration metadata."""
    if not isinstance(context, dict):
        return _result(False, REASON_MISSING_CONTEXT,
                       missing_fields=list(REQUIRED_FIELDS),
                       context_source="caller-provided capture metadata (not inferred)")
    missing = [field for field in REQUIRED_FIELDS if field not in context]
    if missing:
        return _result(False, REASON_MISSING_CONTEXT, missing_fields=missing,
                       context_source="caller-provided capture metadata (not inferred)")
    observed = {field: context[field] for field in REQUIRED_FIELDS}
    if observed["format"] != FORMAT:
        return _result(False, REASON_UNSUPPORTED_FORMAT,
                       observed=observed, required={"format": FORMAT})
    try:
        samples = int(observed["samples"])
        sample_rate = float(observed["sample_rate_hz"])
        decimation = int(observed["decimation"])
        symbol_rate = float(observed["symbol_rate_hz"])
    except (TypeError, ValueError):
        return _result(False, REASON_UNSUPPORTED_LENGTH, observed=observed,
                       error="non-numeric samples/sample_rate_hz/decimation/symbol_rate_hz")
    if samples != SAMPLES:
        return _result(False, REASON_UNSUPPORTED_LENGTH,
                       observed=observed, required={"samples": SAMPLES})
    if (sample_rate != float(SAMPLE_RATE_HZ)) or decimation != DECIMATION:
        return _result(False, REASON_UNSUPPORTED_SAMPLING, observed=observed,
                       required={"sample_rate_hz": SAMPLE_RATE_HZ,
                                 "decimation": DECIMATION})
    if (symbol_rate != float(SYMBOL_RATE_HZ)) or observed["shaping"] != SHAPING:
        return _result(False, REASON_UNSUPPORTED_SYMBOL, observed=observed,
                       required={"symbol_rate_hz": SYMBOL_RATE_HZ, "shaping": SHAPING})
    return _result(True, "", observed=observed)


def validate_iq(iq):
    """Validate an already-decoded IQ array: shape, little-endian signed int16, range."""
    values = np.asarray(iq)
    observed = {"shape": list(values.shape), "dtype": str(values.dtype),
                "dtype_kind": values.dtype.kind, "itemsize": int(values.dtype.itemsize),
                "byteorder": values.dtype.byteorder}
    if values.ndim != 2 or values.shape != (SAMPLES, 2):
        return _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                       required_shape=[SAMPLES, 2])
    if values.dtype.kind != "i" or values.dtype.itemsize != 2:
        return _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                       required="little-endian signed int16")
    if values.dtype.byteorder == ">":
        return _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                       required="little-endian signed int16")
    if not np.all(np.isfinite(values)):
        return _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                       error="non-finite values")
    if np.any(values < MIN_CODE) or np.any(values > MAX_CODE):
        return _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                       required_code_range=[MIN_CODE, MAX_CODE])
    return _result(True, "", observed=observed)


def decode_raw_frame(raw):
    """Decode an explicit ``s16_iq_le`` byte frame and validate its length/shape/range."""
    observed = {"type": type(raw).__name__,
                "bytes": len(raw) if isinstance(raw, (bytes, bytearray)) else None}
    if not isinstance(raw, (bytes, bytearray)):
        return None, _result(False, REASON_INVALID_IQ_FORMAT, observed=observed,
                             required="bytes of little-endian signed int16")
    if len(raw) != FRAME_BYTES:
        return None, _result(False, REASON_INVALID_IQ_LENGTH, observed=observed,
                             required_bytes=FRAME_BYTES)
    values = np.frombuffer(bytes(raw), dtype="<i2").reshape(SAMPLES, 2)
    check = validate_iq(values)
    if not check["ok"]:
        return None, check
    return values, _result(True, "", observed={"bytes": len(raw), "shape": [SAMPLES, 2]})
