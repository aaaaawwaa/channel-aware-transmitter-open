"""Immutable RX handoff types: one validated IQ frame, its analysis, its model result.

The polling/telemetry channel is JSON and bounded (plan file-responsibility table:
"不可变IQ帧/分析/模型结果，避免把数组放进遥测"), so every to_dict() here returns SCALARS
ONLY.  The IQ payload, the amplitude spectrum and the per-class score vector are
reachable as attributes by the caller that draws them, but they can never leak into a
poll payload by accident.

RxIqFrame is the validated artifact.  Its constructor refuses a shape the wire cannot
have produced -- a payload that is not immutable bytes, a byte count that is not
samples * 4, a ratio outside the register selection code range -- so "only a fully
validated frame is published" is structural rather than a promise the client makes.

The wire contract these types describe was read from the daemon, not from prose:

* rf_protocol.c:rf_proto_write_iq_frame -- the one header line has exactly
  id/type/direction/format/samples/bytes/sample_rate_hz/ratio/timestamp_ns.  The
  current wire has NO config-version and NO session field.
* rf_daemon.c:op_rx_capture / op_loopback_physical_capture -- header, newline, then
  samples * 4 payload bytes, all in one write.
* rf_iq.c -- one sample is i16 I then i16 Q, little endian, each inside
  RF_IQ_MIN..RF_IQ_MAX (12-bit range).
* rf_decimate.v / rf_daemon.c -- ratio is a SELECTION CODE 0..4, so decim = 1 << ratio
  (1/2/4/8/16) and the output rate is sample_rate_hz // decim.  It is not a divisor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# The wire fixed shape (see the module docstring for where each is asserted).
RX_IQ_FORMAT = "s16_iq_le"
RX_DIRECTION = "rx"
RX_MAX_SAMPLES = 4096          # RF_BRAM_WORDS, platform_rf.h
RX_BYTES_PER_SAMPLE = 4        # i16 I + i16 Q
RX_IQ_MIN = -2048              # RF_IQ_MIN, rf_iq.h
RX_IQ_MAX = 2047               # RF_IQ_MAX, rf_iq.h
RX_MAX_RATIO = 4               # RF_REG_CAP_RATIO [2:0] select 0..4
RX_NORMAL_OP = "rx.capture"
RX_PHYSICAL_OP = "loopback.physical.capture"
RX_ANALYSIS_SCHEMA_VERSION = 2   # RxAnalysis field set (Task 3)

# The RX pair is RX1 I and Q of ONE RF channel -- never RX1/RX2, which would be two
# receivers.  RxIqFrame carries one channel's two components, not two channels.


@dataclass(frozen=True)
class RxReadback:
    """The PL register readback taken immediately before and after one capture.

    Kept as its own small type rather than reusing the status snapshot nested groups
    so a frame carries the exact evidence it was validated against.
    """

    io_ok: bool
    rx_armed: Optional[bool] = None
    rx_done: Optional[bool] = None
    rx_position_words: Optional[int] = None
    tx_source_selected: Optional[bool] = None
    sampled_at_monotonic_ns: Optional[int] = None

    def to_dict(self):
        return {"io_ok": self.io_ok, "rx_armed": self.rx_armed,
                "rx_done": self.rx_done, "rx_position_words": self.rx_position_words,
                "tx_source_selected": self.tx_source_selected,
                "sampled_at_monotonic_ns": self.sampled_at_monotonic_ns}


@dataclass(frozen=True)
class RxIqFrame:
    """One fully validated finite capture.

    payload is the raw wire bytes (i16 I, i16 Q, little endian) and is the only copy;
    it is never converted into a list, so nothing downstream can mutate it or hand a
    half-decoded array to another thread.
    """

    capture_id: str
    payload: bytes
    samples: int
    sample_rate_hz: int
    ratio: int
    daemon_timestamp_ns: int
    connection_generation: int
    config_revision: int
    request_id: int
    release_id: Optional[str]
    op: str
    physical: bool
    pre_readback: RxReadback
    post_readback: RxReadback
    completed_at_monotonic: float

    def __post_init__(self):
        if type(self.payload) is not bytes:
            raise ValueError("an IQ frame payload must be immutable bytes")
        if not isinstance(self.capture_id, str) or not self.capture_id:
            raise ValueError("an IQ frame needs a local capture id")
        if type(self.samples) is not int or not 1 <= self.samples <= RX_MAX_SAMPLES:
            raise ValueError("IQ frame samples must be 1..%d" % RX_MAX_SAMPLES)
        if type(self.ratio) is not int or not 0 <= self.ratio <= RX_MAX_RATIO:
            raise ValueError("IQ frame ratio must be the selection code 0..%d"
                             % RX_MAX_RATIO)
        if len(self.payload) != self.samples * RX_BYTES_PER_SAMPLE:
            raise ValueError("an IQ frame payload is exactly samples*%d bytes"
                             % RX_BYTES_PER_SAMPLE)

    @property
    def decimation(self) -> int:
        """The decimation the ratio selects: 1 << ratio (never the ratio itself)."""
        return 1 << self.ratio

    @property
    def output_fs_hz(self) -> int:
        """Post-decimation sample rate: sample_rate_hz // (1 << ratio)."""
        return self.sample_rate_hz // self.decimation

    @property
    def bytes(self) -> int:
        return len(self.payload)

    def to_dict(self):
        """Telemetry-safe metadata: scalars only, never the payload."""
        return {"capture_id": self.capture_id, "samples": self.samples,
                "bytes": self.bytes, "sample_rate_hz": self.sample_rate_hz,
                "ratio": self.ratio, "decimation": self.decimation,
                "output_fs_hz": self.output_fs_hz, "op": self.op,
                "physical": self.physical, "release_id": self.release_id,
                "connection_generation": self.connection_generation,
                "config_revision": self.config_revision,
                "request_id": self.request_id,
                "daemon_timestamp_ns": self.daemon_timestamp_ns,
                "completed_at_monotonic": self.completed_at_monotonic,
                "pre_io_ok": self.pre_readback.io_ok,
                "post_io_ok": self.post_readback.io_ok,
                "post_rx_armed": self.post_readback.rx_armed,
                "post_tx_source_selected": self.post_readback.tx_source_selected}


@dataclass(frozen=True)
class RxAnalysis:
    """A frozen display summary of one frame (plan Task 3 fills it).

    The口径 the analyser fixed, and which this type therefore has to carry honestly:

    * ``component_dbfs`` is ``(i_dbfs, q_dbfs)`` with
      20*log10(sqrt(mean(component**2))/2048), the RAW mean square (NO DC removal)
      against ONE component's 12-bit full scale.  A component with no energy is
      ``None``, never -inf.
    * ``complex_rms``/``complex_dbfs`` are the complex figures against the COMPLEX full
      scale 2048*sqrt(2), because the two component powers ADD; the single-component
      scale is never reused for them.
    * ``spectrum`` is a MAGNITUDE spectrum normalised by the coherent gain of the ACTUAL
      window (``coherent_gain``, announced in ``spectrum_window``), so a tone's bin is
      its code amplitude -- not N-normalised and not an assumed window.
    * DC is stated per field: the complex mean is removed before windowing
      (``dc_removed``) and the DC bin is still index 0 of ``spectrum`` but is excluded
      from the peak search and from the ``occupied_bandwidth_hz`` power sum
      (``dc_bin_excluded``).
    * ``occupied_bandwidth_hz`` is the width of the NARROWEST CONTIGUOUS span of the
      two-sided analysis band that holds 99% of the AC power (DC bin excluded) -- the
      standard 占用带宽 meaning.  It is NOT ``max(f) - min(f)`` over the strongest bins
      (an upper bound that over-reports) and NOT a peak width.  On a floor-limited or
      broadband-floor signal that span approaches the whole analysis bandwidth, so it is
      then a CONTAINMENT figure and not a signal bandwidth: whoever displays it must not
      label it as the signal's bandwidth.  A real-valued input (Q = 0) is Hermitian, so
      its span covers both the +f0 and the -f0 image (about 2*f0).
    * ``output_clipped``/``clipping_count`` are CODE-RAIL events only: I or Q reaching
      -2048 or 2047.  False does NOT mean the RF front end was not compressed; there is
      no calibrated absolute level here (no Vpp, dBm, SNR, EVM or BER field).
    * ``input_fs_hz``/``decimation``/``analysis_fs_hz`` record the rate the spectrum was
      really computed at, so a decimated (and possibly aliased) spectrum is never
      labelled as if it were at the full rate.
    * ``no_energy`` is the explicit zero-input status; every level is ``None`` rather
      than NaN/inf/-inf.
    * ``valid`` says the frame carries usable TIME-DOMAIN levels.  It is False only for
      the explicit zero-energy status (``no_energy``, where every level is None); it does
      NOT promise a spectrum, because an N < 3 record and a DC-only record are both
      ``valid=True`` with ``spectrum == ()`` or ``peak_hz is None``.

    Arrays (spectrum, preview) are attributes only.  component_dbfs is a TWO-tuple,
    ``(i_dbfs, q_dbfs)``, because the two components are reported separately and the
    complex figure is a separate field so the two are never confused: a consumer may
    always unpack it, and a component with no energy is ``None`` rather than -inf.  The
    dataclass default is the empty tuple, which only means "not filled in yet" -- every
    value the analyser returns has exactly two elements.
    """

    capture_id: str
    valid: bool
    samples: int
    i_rms: Optional[float] = None
    q_rms: Optional[float] = None
    complex_rms: Optional[float] = None
    component_dbfs: Tuple[Optional[float], ...] = ()
    complex_dbfs: Optional[float] = None
    peak_hz: Optional[float] = None
    occupied_bandwidth_hz: Optional[float] = None
    clipping_count: int = 0
    output_clipped: bool = False
    dc_removed: bool = False
    dc_bin_excluded: bool = False
    no_energy: bool = False
    spectrum: Tuple[float, ...] = ()
    spectrum_window: str = ""
    coherent_gain: Optional[float] = None
    input_fs_hz: Optional[int] = None
    decimation: int = 1
    analysis_fs_hz: Optional[float] = None
    preview: Tuple[int, ...] = ()
    reason: str = ""
    completed_at_monotonic: float = 0.0
    schema_version: int = RX_ANALYSIS_SCHEMA_VERSION

    def __post_init__(self):
        if not isinstance(self.capture_id, str) or not self.capture_id:
            raise ValueError("an analysis result must name the capture it came from")

    def to_dict(self):
        """Telemetry-safe summary: scalars only, never spectrum/preview."""
        return {"capture_id": self.capture_id, "valid": self.valid,
                "samples": self.samples, "schema_version": self.schema_version,
                "i_rms": self.i_rms, "q_rms": self.q_rms,
                "complex_rms": self.complex_rms,
                "i_dbfs": (self.component_dbfs[0]
                          if len(self.component_dbfs) > 0 else None),
                "q_dbfs": (self.component_dbfs[1]
                          if len(self.component_dbfs) > 1 else None),
                "complex_dbfs": self.complex_dbfs, "peak_hz": self.peak_hz,
                "occupied_bandwidth_hz": self.occupied_bandwidth_hz,
                "clipping_count": self.clipping_count,
                "output_clipped": self.output_clipped,
                "dc_removed": self.dc_removed,
                "dc_bin_excluded": self.dc_bin_excluded,
                "no_energy": self.no_energy,
                "spectrum_window": self.spectrum_window,
                "coherent_gain": self.coherent_gain,
                "input_fs_hz": self.input_fs_hz, "decimation": self.decimation,
                "analysis_fs_hz": self.analysis_fs_hz,
                "reason": self.reason,
                "completed_at_monotonic": self.completed_at_monotonic}


@dataclass(frozen=True)
class RxModelResult:
    """A frozen classifier result for one frame (plan Tasks 9/10 fill it).

    scores is the bounded per-class vector and stays out of to_dict().  A score is a
    MODEL SCORE, not a calibrated probability, which is why the field is not named one.
    """

    capture_id: str
    quality: str
    label: str
    class_index: Optional[int] = None
    scores: Tuple[float, ...] = ()
    model_version: str = ""
    model_sha256: Optional[str] = None
    preprocessing_version: str = ""
    source: str = ""
    experimental: bool = True
    rejected: bool = False
    reason: str = ""
    completed_at_monotonic: float = 0.0
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.capture_id, str) or not self.capture_id:
            raise ValueError("a model result must name the capture it came from")

    def to_dict(self):
        """Telemetry-safe summary: scalars only, never the score vector."""
        return {"capture_id": self.capture_id, "quality": self.quality,
                "label": self.label, "class_index": self.class_index,
                "model_version": self.model_version,
                "model_sha256": self.model_sha256,
                "preprocessing_version": self.preprocessing_version,
                "source": self.source, "experimental": self.experimental,
                "rejected": self.rejected, "reason": self.reason,
                "completed_at_monotonic": self.completed_at_monotonic,
                "schema_version": self.schema_version}
