"""Minimal unified result contract for one finite RX snapshot.

The contract is deliberately small: identity, mode/sampling domain, the three
sync answers, one named refusal reason, bounded symbol/bit previews and BER
numerator/denominator.  EVM is not implemented by the BPSK/QPSK RECT receivers;
``evm_available`` is therefore False and ``evm_rms`` is None, never 0.

``from_bpsk_result`` adapts the existing BPSK result without changing that
receiver or its fields.  Unavailable results never carry a BER from another
frame: denominators are optional and the ``ber`` property returns None unless
both counts are present and consistent.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

VERSION = "modem-result-v1"
MAX_SYMBOL_PREVIEW = 64
MAX_BIT_PREVIEW = 128

# Named reasons are part of the interface; a free-form string is not accepted.
REASON_CODES = frozenset({
    "UNSUPPORTED",
    "NO_REFERENCE",
    "CARRIER_NOT_SYNCED",
    "TIMING_NOT_SYNCED",
    "REFERENCE_NOT_FOUND",
    "LOW_ENERGY",
    "CLIPPED",
    "ILL_CONDITIONED",
    "INVALID_IQ",
    "INPUT_DOMAIN",
    "IDENTITY_MISMATCH",
    "STALE_RESULT",
    "NOT_RUN",
})


def _is_int(value) -> bool:
    return type(value) is int


@dataclass(frozen=True)
class ModemResult:
    """Immutable result of one finite snapshot analysis."""

    capture_id: str
    release_id: Optional[str]
    config_revision: int
    connection_generation: int
    completed_at_monotonic: float
    mode: str
    shaping: str
    sample_rate_hz: int
    symbol_rate_hz: int
    samples: int
    carrier_sync: bool = False
    timing_sync: bool = False
    reference_sync: Optional[bool] = None
    reason: str = ""
    cfo_hz: Optional[float] = None
    timing_offset_samples: Optional[int] = None
    phase_ambiguity: Optional[int] = None
    valid_symbols: int = 0
    symbol_preview: Tuple[Tuple[float, float], ...] = ()
    bit_preview: Tuple[int, ...] = ()
    alignment_bits: Optional[int] = None
    comparison_start_symbol: Optional[int] = None
    bit_errors: Optional[int] = None
    compared_bits: Optional[int] = None
    evm_available: bool = False
    evm_rms: Optional[float] = None
    algorithm_version: str = VERSION

    def __post_init__(self):
        for name in ("capture_id", "mode", "shaping", "algorithm_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError("%s must be a non-empty string" % name)
        for name in ("config_revision", "connection_generation", "samples",
                     "valid_symbols"):
            value = getattr(self, name)
            if not _is_int(value) or value < 0:
                raise ValueError("%s must be a non-negative integer" % name)
        if self.reason and self.reason not in REASON_CODES:
            raise ValueError("reason must be one of %s" % sorted(REASON_CODES))
        if type(self.carrier_sync) is not bool or type(self.timing_sync) is not bool:
            raise ValueError("carrier_sync/timing_sync must be bool")
        if self.reference_sync is not None and type(self.reference_sync) is not bool:
            raise ValueError("reference_sync must be bool or None")
        if not (0 <= len(self.symbol_preview) <= MAX_SYMBOL_PREVIEW):
            raise ValueError("symbol_preview is bounded to %d points"
                             % MAX_SYMBOL_PREVIEW)
        if not (0 <= len(self.bit_preview) <= MAX_BIT_PREVIEW):
            raise ValueError("bit_preview is bounded to %d bits" % MAX_BIT_PREVIEW)
        if any(b not in (0, 1) for b in self.bit_preview):
            raise ValueError("bit_preview entries must be 0 or 1")
        if any(not (math.isfinite(x) and math.isfinite(y))
               for x, y in self.symbol_preview):
            raise ValueError("symbol_preview must be finite")
        if self.bit_errors is not None or self.compared_bits is not None:
            if not (_is_int(self.bit_errors) and _is_int(self.compared_bits)
                    and self.bit_errors >= 0 and self.compared_bits >= 0
                    and self.bit_errors <= self.compared_bits):
                raise ValueError("BER counts require 0 <= bit_errors <= compared_bits")
        if not self.evm_available:
            if self.evm_rms is not None:
                raise ValueError("evm_rms must be None while EVM is unavailable")
        elif self.evm_rms is None or not math.isfinite(self.evm_rms) or self.evm_rms < 0:
            raise ValueError("an available EVM needs a finite non-negative evm_rms")

    @property
    def ber(self) -> Optional[float]:
        if self.bit_errors is None or self.compared_bits is None:
            return None
        if self.compared_bits == 0:
            return None
        return self.bit_errors / self.compared_bits

    @property
    def symbols_available(self) -> bool:
        return (self.carrier_sync is True and self.timing_sync is True
                and self.valid_symbols > 0 and not self.reason)

    @property
    def ber_available(self) -> bool:
        return (self.symbols_available and self.reference_sync is True
                and self.compared_bits is not None and self.compared_bits > 0
                and self.bit_errors is not None)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ber"] = self.ber
        data["symbols_available"] = self.symbols_available
        data["ber_available"] = self.ber_available
        data["symbol_preview"] = [list(p) for p in self.symbol_preview]
        data["bit_preview"] = list(self.bit_preview)
        return data

    @classmethod
    def unavailable(cls, *, reason: str, capture_id: str = "unknown",
                    release_id: Optional[str] = None, config_revision: int = 0,
                    connection_generation: int = 0,
                    completed_at_monotonic: float = 0.0,
                    mode: str = "UNKNOWN", shaping: str = "UNKNOWN",
                    sample_rate_hz: int = 0, symbol_rate_hz: int = 0,
                    samples: int = 0, **updates) -> "ModemResult":
        """Build an explicit refusal; no BER/count survives in this object."""
        if reason not in REASON_CODES:
            raise ValueError("reason must be one of %s" % sorted(REASON_CODES))
        return cls(capture_id=capture_id, release_id=release_id,
                   config_revision=config_revision,
                   connection_generation=connection_generation,
                   completed_at_monotonic=completed_at_monotonic,
                   mode=mode, shaping=shaping,
                   sample_rate_hz=sample_rate_hz,
                   symbol_rate_hz=symbol_rate_hz, samples=samples,
                   reason=reason, reference_sync=False, **updates)

    @classmethod
    def from_bpsk_result(cls, result, frame=None) -> "ModemResult":
        """Adapt the existing BPSK result without changing its receiver."""
        return cls(
            capture_id=result.capture_id,
            release_id=result.release_id,
            config_revision=result.config_revision,
            connection_generation=result.connection_generation,
            completed_at_monotonic=result.completed_at_monotonic,
            mode="BPSK", shaping="RECT",
            sample_rate_hz=getattr(frame, "sample_rate_hz", 60000000),
            symbol_rate_hz=3000000,
            samples=getattr(frame, "samples", 4096),
            carrier_sync=bool(result.carrier_sync),
            timing_sync=bool(result.timing_sync),
            reference_sync=result.reference_sync,
            reason=result.reason or "",
            cfo_hz=result.cfo_hz,
            timing_offset_samples=result.timing_offset_samples,
            phase_ambiguity=(0 if result.polarity is None else int(result.polarity)),
            valid_symbols=int(result.total_symbols or 0),
            bit_preview=tuple(int(b) for b in result.bits_preview)[:MAX_BIT_PREVIEW],
            alignment_bits=result.alignment_bits,
            bit_errors=result.bit_errors,
            compared_bits=result.compared_bits,
            evm_available=False,
            evm_rms=None,
            algorithm_version=getattr(result, "algorithm_version", "bpsk-result"),
        )
