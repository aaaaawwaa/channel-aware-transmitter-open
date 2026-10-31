"""Pure RRC handoff from explicit RX intent to the frozen RRC receiver.

Frozen domain only: 60 MSPS, 3 MSym/s, sps 20, alpha 0.35, 4096-sample RX,
BPSK/QPSK, explicit reference seed and explicit IQ/QI.  No TX/CNN mode is
consulted and no BER is invented for an unavailable or failed result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rf_modem_result import ModemResult
from rf_rrc_receiver import (RrcConfig, VERSION, failed_rrc as _failed_receiver,
                             receive_rrc)
from rf_rx_types import RxIqFrame

SAMPLE_RATE_HZ = 60_000_000
SYMBOL_RATE_HZ = 3_000_000
RX_LEN = 4096
DECIM = 1


@dataclass(frozen=True)
class RrcDemodConfig:
    config: RrcConfig
    revision: int
    decim: int
    rx_len: int
    input_fs_hz: int

    @property
    def iq_order(self):
        return self.config.iq_order


@dataclass(frozen=True)
class RrcEnvelope:
    result: ModemResult
    submitted: RrcDemodConfig
    algorithm_version: str
    group_delay_samples: int
    transition_symbols: int
    valid_symbol_start: int
    valid_symbol_end: int
    comparison_start_symbol: Optional[int]
    comparison_end_symbol: Optional[int]


def freeze_rrc_demod_config(draft, revision, iq_order="IQ"):
    """Freeze only the explicit RRC domain; reference seed is never inferred."""
    if not isinstance(draft, dict) or type(revision) is not int or revision < 0:
        raise ValueError("explicit RX draft and nonnegative revision required")
    if iq_order not in ("IQ", "QI"):
        raise ValueError("iq_order must be explicit IQ or QI")
    enabled = draft.get("reference_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("reference_enabled must be boolean")
    seed = draft.get("reference_seed") if enabled else None
    if enabled and (type(seed) is not int or not 1 <= seed <= 127):
        raise ValueError("reference seed must be integer 1..127")
    mode = draft.get("expected_mode")
    if mode not in ("BPSK", "QPSK"):
        raise ValueError("RRC mode must be BPSK or QPSK")
    if draft.get("shaping") != "RRC":
        raise ValueError("RRC pipeline requires shaping=RRC")
    if draft.get("symrate_hz") != SYMBOL_RATE_HZ:
        raise ValueError("RRC pipeline requires 3 MSym/s")
    decim, rx_len, input_fs_hz = (draft.get("decim"), draft.get("rx_len"),
                                  draft.get("input_fs_hz"))
    if (type(decim) is not int or decim != DECIM
            or type(rx_len) is not int or rx_len != RX_LEN
            or type(input_fs_hz) is not int or input_fs_hz != SAMPLE_RATE_HZ):
        raise ValueError("RRC pipeline requires D1/4096/60 MSPS")
    config = RrcConfig(mode=mode, reference_prbs7_seed=seed, rolloff=0.35,
                       sps=20, sample_rate_hz=SAMPLE_RATE_HZ,
                       symbol_rate_hz=SYMBOL_RATE_HZ, iq_order=iq_order)
    return RrcDemodConfig(config, revision, decim, rx_len, input_fs_hz)


def _wrap(receiver_envelope, submitted):
    return RrcEnvelope(
        result=receiver_envelope.result, submitted=submitted,
        algorithm_version=receiver_envelope.algorithm_version,
        group_delay_samples=receiver_envelope.group_delay_samples,
        transition_symbols=receiver_envelope.transition_symbols,
        valid_symbol_start=receiver_envelope.valid_symbol_start,
        valid_symbol_end=receiver_envelope.valid_symbol_end,
        comparison_start_symbol=receiver_envelope.comparison_start_symbol,
        comparison_end_symbol=receiver_envelope.comparison_end_symbol)


def failed_rrc(frame, submitted, reason="NOT_RUN"):
    """Bind one RRC failure to its own frame; no BER/counts survive."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, RrcDemodConfig):
        raise TypeError("expected RxIqFrame and RrcDemodConfig")
    return _wrap(_failed_receiver(frame, submitted.config, reason), submitted)


def analyze_rrc(frame, submitted):
    """Validate acquisition/identity, then run the actual RRC receiver."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, RrcDemodConfig):
        raise TypeError("expected RxIqFrame and RrcDemodConfig")
    if (submitted.decim != frame.decimation or submitted.rx_len != frame.samples
            or submitted.input_fs_hz != frame.sample_rate_hz):
        return failed_rrc(frame, submitted, "INPUT_DOMAIN")
    return _wrap(receive_rrc(frame, submitted.config), submitted)
