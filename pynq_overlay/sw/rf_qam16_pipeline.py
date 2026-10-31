"""Pure QAM16/RECT handoff from explicit RX settings to the frozen receiver.

Release-local, like the QPSK/RRC product handoffs: the worker verifies this file,
``rf_qam16_receiver`` and ``rf_modem_result`` all resolve inside the same verified
release before using them.  It never reads TX state, CNN classes, daemon mode or a
previous frame's BER, and never infers mode/shaping/wire order from the samples.
"""
from __future__ import annotations

from dataclasses import dataclass

from rf_modem_result import ModemResult, REASON_CODES
from rf_qam16_receiver import Qam16Config, VERSION, receive_qam16
from rf_rx_types import RxIqFrame

SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
RX_LEN = 4096
DECIM = 1
SUPPORTED_IQ_ORDERS = ("IQ", "QI")


@dataclass(frozen=True)
class Qam16DemodConfig:
    config: Qam16Config
    revision: int
    decim: int
    rx_len: int
    input_fs_hz: int

    @property
    def iq_order(self):
        return self.config.iq_order


@dataclass(frozen=True)
class Qam16Envelope:
    result: ModemResult
    submitted: Qam16DemodConfig
    algorithm_version: str


def freeze_qam16_demod_config(draft, revision, iq_order="IQ"):
    """Freeze explicit QAM16/RECT intent; reference seed is never inferred."""
    if not isinstance(draft, dict) or type(revision) is not int or revision < 0:
        raise ValueError("explicit RX draft and nonnegative revision required")
    if iq_order not in SUPPORTED_IQ_ORDERS:
        raise ValueError("iq_order must be explicit IQ or QI")
    if draft.get("expected_mode") != "QAM16" or draft.get("shaping") != "RECT":
        raise ValueError("QAM16/RECT is required")
    if draft.get("symrate_hz") != SYMBOL_RATE_HZ:
        raise ValueError("QAM16 pipeline requires 3 MSym/s")
    enabled = draft.get("reference_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("reference_enabled must be boolean")
    seed = draft.get("reference_seed") if enabled else None
    if enabled and (type(seed) is not int or not 1 <= seed <= 127):
        raise ValueError("reference seed must be integer 1..127")
    decim, rx_len, input_fs_hz = (draft.get("decim"), draft.get("rx_len"),
                                  draft.get("input_fs_hz"))
    if (type(decim) is not int or decim != DECIM
            or type(rx_len) is not int or rx_len != RX_LEN
            or type(input_fs_hz) is not int or input_fs_hz != SAMPLE_RATE_HZ):
        raise ValueError("QAM16 pipeline requires D1/4096/60 MSPS")
    return Qam16DemodConfig(
        Qam16Config(reference_prbs7_seed=seed, mode="QAM16", shaping="RECT",
                    symbol_rate_hz=SYMBOL_RATE_HZ, iq_order=iq_order),
        revision, decim, rx_len, input_fs_hz)


def failed_qam16(frame, submitted, reason="NOT_RUN"):
    """Bind one named failure to its own frame; no BER/counts survive."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, Qam16DemodConfig):
        raise TypeError("expected frozen frame and Qam16DemodConfig")
    named = reason if reason in REASON_CODES else "NOT_RUN"
    result = ModemResult.unavailable(
        reason=named, capture_id=frame.capture_id, release_id=frame.release_id,
        config_revision=frame.config_revision,
        connection_generation=frame.connection_generation,
        completed_at_monotonic=frame.completed_at_monotonic,
        mode="QAM16", shaping="RECT", sample_rate_hz=frame.sample_rate_hz,
        symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples,
        algorithm_version=VERSION)
    return Qam16Envelope(result, submitted, VERSION)


def analyze_qam16(frame, submitted):
    """Validate the explicit RX identity/domain, then run the pure QAM16 receiver."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, Qam16DemodConfig):
        raise TypeError("expected RxIqFrame and Qam16DemodConfig")
    if (submitted.decim != frame.decimation or submitted.rx_len != frame.samples
            or submitted.input_fs_hz != frame.sample_rate_hz):
        return failed_qam16(frame, submitted, "INPUT_DOMAIN")
    return Qam16Envelope(receive_qam16(frame, submitted.config), submitted, VERSION)
