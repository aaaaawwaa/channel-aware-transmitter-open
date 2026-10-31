"""Pure QAM64/RECT handoff from explicit RX settings to the frozen receiver.

Release-local, like the QPSK/RRC/QAM16 product handoffs: the caller supplies
explicit mode/shaping/symbol rate/seed/IQ order and the frame; no TX state, CNN
class or previous BER is consulted.  It never infers mode/shaping/wire order
from the samples.
"""
from __future__ import annotations

from dataclasses import dataclass

from rf_modem_result import ModemResult, REASON_CODES
from rf_qam64_receiver import Qam64Config, VERSION, receive_qam64
from rf_rx_types import RxIqFrame

SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
RX_LEN = 4096
DECIM = 1
SUPPORTED_IQ_ORDERS = ("IQ", "QI")


@dataclass(frozen=True)
class Qam64DemodConfig:
    config: Qam64Config
    revision: int
    decim: int
    rx_len: int
    input_fs_hz: int

    @property
    def iq_order(self):
        return self.config.iq_order


@dataclass(frozen=True)
class Qam64Envelope:
    result: ModemResult
    submitted: Qam64DemodConfig
    algorithm_version: str


def freeze_qam64_demod_config(draft, revision, iq_order="IQ"):
    """Freeze explicit QAM64/RECT intent; reference seed is never inferred."""
    if not isinstance(draft, dict) or type(revision) is not int or revision < 0:
        raise ValueError("explicit RX draft and nonnegative revision required")
    if iq_order not in SUPPORTED_IQ_ORDERS:
        raise ValueError("iq_order must be explicit IQ or QI")
    if draft.get("expected_mode") != "QAM64" or draft.get("shaping") != "RECT":
        raise ValueError("QAM64/RECT is required")
    if draft.get("symrate_hz") != SYMBOL_RATE_HZ:
        raise ValueError("QAM64 pipeline requires 3 MSym/s")
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
        raise ValueError("QAM64 pipeline requires D1/4096/60 MSPS")
    return Qam64DemodConfig(
        Qam64Config(reference_prbs7_seed=seed, mode="QAM64", shaping="RECT",
                    symbol_rate_hz=SYMBOL_RATE_HZ, iq_order=iq_order),
        revision, decim, rx_len, input_fs_hz)


def failed_qam64(frame, submitted, reason="NOT_RUN"):
    """Bind one named failure to its own frame; no BER/counts survive."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, Qam64DemodConfig):
        raise TypeError("expected frozen frame and Qam64DemodConfig")
    named = reason if reason in REASON_CODES else "NOT_RUN"
    result = ModemResult.unavailable(
        reason=named, capture_id=frame.capture_id, release_id=frame.release_id,
        config_revision=frame.config_revision,
        connection_generation=frame.connection_generation,
        completed_at_monotonic=frame.completed_at_monotonic,
        mode="QAM64", shaping="RECT", sample_rate_hz=frame.sample_rate_hz,
        symbol_rate_hz=SYMBOL_RATE_HZ, samples=frame.samples,
        algorithm_version=VERSION)
    return Qam64Envelope(result, submitted, VERSION)


def analyze_qam64(frame, submitted):
    """Validate the explicit RX identity/domain, then run the pure QAM64 receiver."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, Qam64DemodConfig):
        raise TypeError("expected RxIqFrame and Qam64DemodConfig")
    if (submitted.decim != frame.decimation or submitted.rx_len != frame.samples
            or submitted.input_fs_hz != frame.sample_rate_hz):
        return failed_qam64(frame, submitted, "INPUT_DOMAIN")
    return Qam64Envelope(receive_qam64(frame, submitted.config), submitted, VERSION)
