"""Pure, bounded IQ analysis for the RX display path (plan Task 3).

This module is PURE.  It decodes an already-validated RxIqFrame (or two component
sequences) and returns a frozen, display-only RxAnalysis.  It opens nothing, talks to
nothing, and touches no hardware or page: the caller that owns the capture session hands
it a frame and gets numbers back.

口径 (each one is fixed here, labelled in the output, and pinned by a criterion in
tests/test_rf_rx_analysis.py):

* Component dBFS is ``20*log10(sqrt(mean(component**2))/2048)`` -- the RAW mean square
  against ONE component's 12-bit full scale, with NO DC removal, exactly as the plan
  fixes it.  A component with no energy is ``None``, never -inf.
* The COMPLEX RMS is reported separately.  Its reference is the COMPLEX full scale
  ``2048*sqrt(2)``, because the two component powers ADD; the single-component full scale
  is never reused for the complex value.  The identity
  ``complex_dbfs == 10*log10((10**(i_dbfs/10) + 10**(q_dbfs/10))/2)`` therefore holds
  exactly.
* The spectrum is a MAGNITUDE spectrum normalised by the coherent gain of the ACTUAL
  window -- the sum of its coefficients (``coherent_gain``), announced in
  ``spectrum_window`` ("hann").  It is never normalised by N and never by an assumed
  window, so an exact-bin complex tone of amplitude A lands on A code units.
* DC handling is stated twice on purpose: the complex mean is removed from the samples
  before windowing (``dc_removed=True``), and the DC bin is still index 0 of the returned
  spectrum but is excluded from the peak search and from the occupied-bandwidth power sum
  (``dc_bin_excluded=True``).  Both flags are True only on the path that really built a
  spectrum.  Peak and bandwidth are therefore AC measurements.
* 99% occupied bandwidth (``occupied_bandwidth_hz``) is 占用带宽 in its standard meaning:
  the width of the NARROWEST CONTIGUOUS frequency span that holds 99% of the AC power
  (``OCCUPIED_BANDWIDTH_FRACTION``), measured on the ascending two-sided complex-baseband
  axis with the DC bin excluded.  It is deliberately NOT ``max(f) - min(f)`` over the
  strongest bins: that is only an upper bound on containment, and on the real preflight
  frames it over-reported by 3.5-4.7x (30.0-40.0 MHz against the 8.1-8.6 MHz measured
  here).  It is not "peak width" either.  It is tested against a brick-wall signal of a
  known width (10 MHz in -> ~10 MHz out), against a single tone (the window mainlobe) and
  against a floor-limited signal on which the two definitions disagree.
* The span is measured on the TWO-SIDED complex-baseband axis, which is the correct axis
  for this I/Q signal and for the brick-wall case.  A real-valued input (Q = 0) is
  therefore Hermitian and its 99% span covers BOTH the +f0 and the -f0 image -- about
  2*f0, not the one-sided mainlobe.
* Caveat, MEASURED on the existing real loopback frames, reported and not tuned away: when
  the per-bin SNR is limited and the noise occupies the whole analysis band, 99%
  containment is dominated by that broadband floor and the span approaches the whole
  analysis bandwidth.  Those two-tone frames put 98.6% of the AC power in the six tone
  bins, so the remaining 1.4% -- spread over the other 4082 bins -- still widens the 99%
  span to ~8.6 MHz of the 60 MHz band.  On a floor-limited or broadband-floor signal this
  figure is therefore NOT a signal bandwidth and must never be presented as one: the panel
  must label it as a containment figure over the analysis band.
* Clipping is a CODE-RAIL event only: I or Q reaching RX_IQ_MIN (-2048) or RX_IQ_MAX
  (2047).  The flag is named ``output_clipped`` for that reason -- False does NOT mean the
  analog front end was not compressed.  There is no calibrated absolute level here, so
  this module never emits a Vpp, dBm, SNR, EVM or BER figure, and never turns a spectral
  peak's prominence into an SNR claim.
* Decimation is recorded, never hidden: ``input_fs_hz``/``decimation``/``analysis_fs_hz``
  say which rate the frequency axis was computed at, so a decimated (and possibly
  aliased) spectrum is never labelled as if it were at the full rate.

Freshness, and where each window is really declared:

* ``IQ_SNAPSHOT_FRESHNESS_S`` = 30 s for a captured IQ snapshot, measured from the LOCAL
  monotonic completion moment (``snapshot_is_fresh``).  After that the frame is history:
  archived evidence of a past capture, never proof that RF is being received now.  This
  window is declared ONLY here.
* ``TELEMETRY_FRESHNESS_S`` = 2 s for hardware telemetry (``telemetry_is_fresh``), an
  INDEPENDENT window with its own source moment: the spec keeps hardware telemetry's
  expiry separate from the snapshot's.
* The 2 s telemetry window is NOT declared in one place, and this module does not pretend
  otherwise.  The panel's status daemon already carries its own ``RF_STALE_AFTER = 2.0``
  (which it injects into the RF status worker) and the worker class defaults
  ``stale_after=2.0``: the same rule, for the same hardware telemetry.  Both are
  pre-existing and outside this module's write scope, so the three values MUST AGREE:
  Task 4 must import ``TELEMETRY_FRESHNESS_S`` from here instead of writing 2.0 a third
  time, and a criterion asserts that all three are equal so drift is caught rather than
  assumed away.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

import numpy as np

from rf_rx_types import (RX_ANALYSIS_SCHEMA_VERSION, RX_IQ_MAX, RX_IQ_MIN,
                         RX_MAX_RATIO, RX_MAX_SAMPLES, RxAnalysis, RxIqFrame)

# --- this module's named expiry windows (see the freshness note in the docstring) -----
# The IQ window has exactly one home.  The 2 s telemetry value does NOT: the panel's
# status daemon and the RF status worker each already declare it, so this constant must
# stay equal to both (a criterion asserts that) and Task 4 imports it from here.
IQ_SNAPSHOT_FRESHNESS_S = 30.0   # a captured IQ snapshot, from its local completion time
TELEMETRY_FRESHNESS_S = 2.0      # hardware telemetry, from its own sample time

# --- the analysis口径 -----------------------------------------------------------------
SPECTRUM_WINDOW = "hann"         # the window actually applied, named in the output
PREVIEW_COMPLEX_SAMPLES = 64     # bounded preview: 64 complex samples = 128 ints
OCCUPIED_BANDWIDTH_FRACTION = 0.99
FULL_SCALE_CODE = 2048.0                       # one component's 12-bit full scale
COMPLEX_FULL_SCALE_CODE = 2048.0 * math.sqrt(2.0)   # both components at full scale
# np.hanning(2) is [0, 0], so its coherent gain is 0 and no honest spectrum exists below
# this length; the time-domain figures are still returned.
MIN_SPECTRUM_SAMPLES = 3
NO_ENERGY_REASON = "no energy: every I and Q sample is zero"


def snapshot_is_fresh(completed_at_monotonic: Optional[float],
                      now_monotonic: Optional[float],
                      limit: float = IQ_SNAPSHOT_FRESHNESS_S) -> bool:
    """Is a captured IQ snapshot still CURRENT reception (default 30 s)?

    Age is measured from the LOCAL monotonic completion moment of the capture.  An absent
    moment, an age beyond the limit, or a moment in the future is not fresh: an old frame
    stays archived history and is never evidence that RF is being received now.
    """
    return _age_is_fresh(completed_at_monotonic, now_monotonic, limit)


def telemetry_is_fresh(sampled_at_monotonic: Optional[float],
                       now_monotonic: Optional[float],
                       limit: float = TELEMETRY_FRESHNESS_S) -> bool:
    """Is hardware telemetry still current (default 2 s)?  Independent of the IQ window."""
    return _age_is_fresh(sampled_at_monotonic, now_monotonic, limit)


def _age_is_fresh(moment: Optional[float], now: Optional[float], limit: float) -> bool:
    if moment is None or now is None:
        return False
    age = float(now) - float(moment)
    return 0.0 <= age <= float(limit)


def summarize(frame: RxIqFrame) -> RxAnalysis:
    """Summarise one validated RxIqFrame into a bounded, display-only RxAnalysis.

    The payload is the only place the samples come from, so a shape the wire cannot have
    produced cannot be analysed.  Anything that is not the validated frame is refused
    rather than duck-typed.
    """
    if not isinstance(frame, RxIqFrame):
        raise TypeError("summarize takes a validated RxIqFrame, not %s"
                        % type(frame).__name__)
    decimation = _analysis_decimation(frame.sample_rate_hz, frame.ratio)
    values = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
    return _summarize(values[:, 0].astype(np.float64),
                      values[:, 1].astype(np.float64),
                      capture_id=frame.capture_id, input_fs_hz=frame.sample_rate_hz,
                      decimation=decimation,
                      completed_at_monotonic=frame.completed_at_monotonic)


def summarize_components(i: Sequence[float], q: Sequence[float], *,
                         capture_id: str, sample_rate_hz: int, ratio: int = 0,
                         completed_at_monotonic: float = 0.0) -> RxAnalysis:
    """The numeric core of summarize(), for the two components of one RX channel.

    ``i``/``q`` are signed 12-bit codes (or exact floats of them).  This entry exists so
    the length, range and finiteness rules can be exercised without fabricating a frame
    the capture path could never publish, and so a numeric caller need not build bytes.
    """
    i = _checked_component(i, "I")
    q = _checked_component(q, "Q")
    if i.size != q.size:
        raise ValueError("I and Q must have the same length")
    if not 1 <= i.size <= RX_MAX_SAMPLES:
        raise ValueError("an analysis length must be 1..%d samples" % RX_MAX_SAMPLES)
    decimation = _analysis_decimation(sample_rate_hz, ratio)
    return _summarize(i, q, capture_id=capture_id, input_fs_hz=sample_rate_hz,
                      decimation=decimation,
                      completed_at_monotonic=completed_at_monotonic)


def _is_integer(value) -> bool:
    """int or a numpy integer scalar -- but never a bool and never a float.

    ``isinstance`` rather than ``type(x) is int`` so a rate that arrived from a numpy
    array is not refused as if it were a float; ``bool`` and ``float`` are still refused,
    because a silently truncated rate would mislabel every frequency on the axis.
    """
    return isinstance(value, (int, np.integer)) and not isinstance(value, bool)


def _analysis_decimation(input_fs_hz: int, ratio: int) -> int:
    """The one place the analysis decimation is derived, so its label can never lie.

    It returns the DECIMATION (``1 << ratio``), not a rate: the name says so because its
    call sites bind the result to ``decimation`` and the analysis rate is
    ``input_fs_hz // decimation``.  A rate the decimation cannot divide exactly is refused
    rather than floored: a floored axis would quietly mislabel every frequency in the
    spectrum.  Every refusal is a ValueError -- the parameter's VALUE is wrong, which is
    not a Python type error.
    """
    if not _is_integer(ratio) or not 0 <= ratio <= RX_MAX_RATIO:
        raise ValueError("ratio must be the selection code 0..%d" % RX_MAX_RATIO)
    if not _is_integer(input_fs_hz) or input_fs_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive integer")
    decimation = 1 << int(ratio)
    if int(input_fs_hz) % decimation:
        raise ValueError("sample_rate_hz must divide exactly by the decimation %d"
                         % decimation)
    return decimation


def _check_code_range(array: np.ndarray, name: str) -> None:
    """The signed-12-bit range rule, in ONE place for every entry point.

    ``summarize`` (the production entry) decodes the payload and calls ``_summarize``
    directly, so the rule cannot live only on ``summarize_components``.
    """
    if array.size and (array.min() < RX_IQ_MIN or array.max() > RX_IQ_MAX):
        raise ValueError("every %s sample must be inside the signed 12-bit range %d..%d"
                         % (name, RX_IQ_MIN, RX_IQ_MAX))


def _checked_component(value: Sequence[float], name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("%s must be a one-dimensional sequence of codes" % name)
    # Finiteness first: a NaN would slip through every range comparison below.
    if not np.all(np.isfinite(array)):
        raise ValueError("every %s sample must be finite" % name)
    _check_code_range(array, name)
    return array


def _summarize(i: np.ndarray, q: np.ndarray, capture_id: str, input_fs_hz: int,
               decimation: int, completed_at_monotonic: float) -> RxAnalysis:
    # The signed-12-bit range rule is enforced on EVERY path, including the payload
    # summarize() decodes: the production entry must refuse a code the wire cannot carry
    # instead of quietly reporting >0 dBFS for it.  (Finiteness is structural for a
    # decoded s16 payload; summarize_components checks it before arriving here.)
    _check_code_range(i, "I")
    _check_code_range(q, "Q")
    samples = int(i.size)
    input_fs_hz = int(input_fs_hz)
    analysis_fs_hz = float(input_fs_hz // decimation)

    i_rms = float(np.sqrt(np.mean(i * i)))
    q_rms = float(np.sqrt(np.mean(q * q)))
    complex_rms = float(np.sqrt(np.mean(i * i + q * q)))
    clipping_count = int(np.count_nonzero((i <= RX_IQ_MIN) | (i >= RX_IQ_MAX)
                                          | (q <= RX_IQ_MIN) | (q >= RX_IQ_MAX)))
    # dc_removed/dc_bin_excluded are set ONLY on the path that really builds a spectrum:
    # an input with no spectrum must not claim DC handling that never ran.
    common = dict(capture_id=capture_id, samples=samples, i_rms=i_rms, q_rms=q_rms,
                  complex_rms=complex_rms, clipping_count=clipping_count,
                  output_clipped=clipping_count > 0,
                  input_fs_hz=input_fs_hz, decimation=decimation,
                  analysis_fs_hz=analysis_fs_hz, preview=_preview(i, q),
                  completed_at_monotonic=completed_at_monotonic,
                  schema_version=RX_ANALYSIS_SCHEMA_VERSION)

    if complex_rms == 0.0:
        # The explicit zero-input status: no level is invented and no log is evaluated.
        return RxAnalysis(valid=False, component_dbfs=(None, None), complex_dbfs=None,
                          no_energy=True, spectrum=(), spectrum_window="",
                          coherent_gain=None, dc_removed=False, dc_bin_excluded=False,
                          reason=NO_ENERGY_REASON, **common)

    component_dbfs = (_dbfs(i_rms, FULL_SCALE_CODE), _dbfs(q_rms, FULL_SCALE_CODE))
    complex_dbfs = _dbfs(complex_rms, COMPLEX_FULL_SCALE_CODE)

    if samples < MIN_SPECTRUM_SAMPLES:
        return RxAnalysis(valid=True, component_dbfs=component_dbfs,
                          complex_dbfs=complex_dbfs, spectrum=(),
                          spectrum_window="", coherent_gain=None, peak_hz=None,
                          occupied_bandwidth_hz=None, dc_removed=False,
                          dc_bin_excluded=False, **common)

    window = np.hanning(samples)
    coherent_gain = float(window.sum())
    ac = (i - i.mean()) + 1j * (q - q.mean())
    spectrum = np.abs(np.fft.fft(ac * window)) / coherent_gain
    power = spectrum * spectrum
    power[0] = 0.0                      # the DC bin never wins the peak or the bandwidth
    freqs = np.fft.fftfreq(samples, 1.0 / analysis_fs_hz)
    total = float(power.sum())
    if total > 0.0:
        peak_hz = float(freqs[int(np.argmax(power))])
        occupied_bandwidth_hz = _occupied_bandwidth(freqs, power)
    else:
        # A DC-only record has no AC peak at all; saying so is better than naming a bin.
        peak_hz = None
        occupied_bandwidth_hz = None

    return RxAnalysis(valid=True, component_dbfs=component_dbfs,
                      complex_dbfs=complex_dbfs, peak_hz=peak_hz,
                      occupied_bandwidth_hz=occupied_bandwidth_hz, no_energy=False,
                      dc_removed=True, dc_bin_excluded=True,
                      spectrum=tuple(float(value) for value in spectrum),
                      spectrum_window=SPECTRUM_WINDOW, coherent_gain=coherent_gain,
                      **common)


def _occupied_bandwidth(freqs: np.ndarray, power: np.ndarray,
                        fraction: float = OCCUPIED_BANDWIDTH_FRACTION) -> Optional[float]:
    """The width of the NARROWEST CONTIGUOUS span holding ``fraction`` of the power.

    This is the standard 占用带宽 meaning.  ``max(f) - min(f)`` over the strongest bins is
    only an UPPER BOUND on containment: a broadband floor that holds the last 1% of the
    power drags that span out towards the edge of the band, which over-reported 30-40 MHz
    against 8.1-8.6 MHz on the real preflight frames.  Here the window must be contiguous
    in REAL FREQUENCY -- the two-sided ``fftfreq`` axis sorted ascending -- so the answer
    is the span of an actually occupied sub-band.

    With non-negative powers the smallest window ending at bin ``high`` starts at the
    largest ``low`` whose cumulative sum still reaches the target, so a single
    ``searchsorted`` on the cumulative sum finds that start for every ``high`` at once;
    the minimum of those widths is the narrowest contiguous span.  Returns ``None`` when
    there is no AC power at all (the caller reports a DC-only record instead).
    """
    total = float(power.sum())
    if total <= 0.0:
        return None
    order = np.argsort(freqs)
    ordered_freqs = freqs[order]
    prefix = np.concatenate(([0.0], np.cumsum(power[order])))
    starts = np.searchsorted(prefix, prefix[1:] - fraction * total, side="right") - 1
    reachable = starts >= 0
    if not np.any(reachable):
        return None
    widths = ordered_freqs[reachable] - ordered_freqs[starts[reachable]]
    return float(widths.min())


def _dbfs(rms: float, full_scale: float) -> Optional[float]:
    """Guarded dB: zero energy returns None so -inf can never reach telemetry."""
    if not rms > 0.0:
        return None
    return 20.0 * math.log10(rms / full_scale)


def _preview(i: np.ndarray, q: np.ndarray) -> Tuple[int, ...]:
    count = min(int(i.size), PREVIEW_COMPLEX_SAMPLES)
    pairs = np.stack([i[:count], q[:count]], axis=1)
    return tuple(int(value) for value in pairs.reshape(-1))
