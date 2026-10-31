"""Pure QPSK/RECT handoff from explicit RX settings to the frozen receiver.

This module is deliberately release-local: the worker verifies that this file,
``rf_qpsk_receiver`` and ``rf_modem_result`` all resolve inside the same verified
release before importing them.  It never reads TX state, CNN classes or a previous
frame's BER.
"""
from __future__ import annotations

from dataclasses import dataclass

from rf_modem_result import ModemResult, REASON_CODES
from rf_qpsk_receiver import QpskConfig, VERSION, receive_qpsk
from rf_rx_types import RxIqFrame

SUPPORTED_DECIMATIONS = (1, 2, 4, 8, 16)
MAX_RX_LEN = 4096
INPUT_FS_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000


@dataclass(frozen=True)
class QpskDemodConfig:
    config: QpskConfig
    revision: int
    decim: int
    rx_len: int
    input_fs_hz: int
    iq_order: str


@dataclass(frozen=True)
class QpskEnvelope:
    result: ModemResult
    submitted: QpskDemodConfig
    algorithm_version: str


def freeze_qpsk_demod_config(draft, revision, iq_order="IQ"):
    """Freeze explicit operator intent; no TX/CNN seed is consulted.

    ``iq_order`` is an explicit capture-boundary fact.  Real files in this batch
    are QI and product capture callers pass ``"QI"``; synthetic tests may pass
    ``"IQ"``.  It is never inferred from the received data.
    """
    if not isinstance(draft, dict) or type(revision) is not int or revision < 0:
        raise ValueError("explicit RX draft and nonnegative revision required")
    if iq_order not in ("IQ", "QI"):
        raise ValueError("iq_order must be explicit 'IQ' or 'QI'")
    enabled = draft.get("reference_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("reference_enabled must be boolean")
    seed = draft.get("reference_seed") if enabled else None
    if enabled and (type(seed) is not int or not 1 <= seed <= 127):
        raise ValueError("reference seed must be integer 1..127")
    mode = draft.get("expected_mode")
    shaping = draft.get("shaping", "RECT")
    rate = draft.get("symrate_hz")
    if (mode != "QPSK" or shaping != "RECT" or type(rate) is not int
            or rate != SYMBOL_RATE_HZ):
        raise ValueError("QPSK/RECT at 3 MSym/s is required")
    fields = {name: draft.get(name) for name in ("decim", "rx_len", "input_fs_hz")}
    if any(type(value) is not int or value <= 0 for value in fields.values()):
        raise ValueError("explicit integer acquisition settings required")
    if fields["decim"] not in SUPPORTED_DECIMATIONS or fields["rx_len"] > MAX_RX_LEN:
        raise ValueError("invalid acquisition settings")
    return QpskDemodConfig(
        QpskConfig(reference_prbs7_seed=seed, mode="QPSK", shaping="RECT",
                   symbol_rate_hz=SYMBOL_RATE_HZ, iq_order=iq_order),
        revision, fields["decim"], fields["rx_len"], fields["input_fs_hz"],
        iq_order)


def failed_qpsk(frame, submitted, reason="NOT_RUN"):
    """Bind one failure to its own frame; no BER/counts survive."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, QpskDemodConfig):
        raise TypeError("expected frozen frame and QpskDemodConfig")
    named = reason if reason in REASON_CODES else "NOT_RUN"
    result = ModemResult.unavailable(
        reason=named, capture_id=frame.capture_id, release_id=frame.release_id,
        config_revision=frame.config_revision,
        connection_generation=frame.connection_generation,
        completed_at_monotonic=frame.completed_at_monotonic,
        mode="QPSK", shaping="RECT", sample_rate_hz=frame.sample_rate_hz,
        symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples,
        algorithm_version=VERSION)
    return QpskEnvelope(result, submitted, VERSION)


def analyze_qpsk(frame, submitted):
    """Validate acquisition/identity, then run the pure QPSK receiver."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, QpskDemodConfig):
        raise TypeError("expected frozen frame and QpskDemodConfig")
    if (submitted.decim != frame.decimation or submitted.rx_len != frame.samples
            or submitted.input_fs_hz != frame.sample_rate_hz):
        return failed_qpsk(frame, submitted, "INPUT_DOMAIN")
    return QpskEnvelope(receive_qpsk(frame, submitted.config), submitted, VERSION)
