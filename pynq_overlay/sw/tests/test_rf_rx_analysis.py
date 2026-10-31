"""Regressions for the pure RX analysis and the two data-freshness windows (plan Task 3).

Written RED FIRST: rf_rx_analysis.py does not exist yet, so every criterion here fails
on the missing module (ModuleNotFoundError / FileNotFoundError) rather than on a wrong
assertion.

The口径 decisions this file pins down are the ones the plan, the task brief and the
controller's rulings fix:

* component dBFS = 20*log10(sqrt(mean(component**2))/2048) -- the RAW mean square, with
  NO DC removal, computed per component and labelled as such;
* the COMPLEX RMS is reported separately against the complex full scale 2048*sqrt(2),
  because the two component powers ADD; a single-component full scale is never used for
  the complex value (and the identity between them is asserted);
* zero energy is an explicit no_energy status and never NaN/inf/-inf in telemetry;
* the magnitude spectrum is normalised by the coherent gain of the ACTUAL window
  (sum of the Hann coefficients), never by N and never by an assumed window;
* DC is removed from the complex samples before windowing (dc_removed=True), the DC bin
  is still PRESENT in the returned spectrum, and it is excluded from the peak search and
  from the 99%-power occupied-bandwidth sum (dc_bin_excluded=True);
* clipping is a CODE-RAIL event only (I or Q reaching -2048 or 2047) and the field is
  named output_clipped so it can never be read as "the RF front end was linear";
* 99% occupied bandwidth = the width of the NARROWEST CONTIGUOUS frequency span that
  holds 99% of the AC power (the standard 占用带宽 meaning), tested against a brick-wall
  signal of a known width AND against a floor-limited signal on which the pre-fix top-bin
  span and the contiguous span differ by more than 1.5x;
* a 30 s IQ-snapshot freshness window from the LOCAL monotonic completion time and an
  INDEPENDENT 2 s hardware-telemetry window, whose value must stay equal to the two
  pre-existing hardware-telemetry declarations (pynq_status.RF_STALE_AFTER and the status
  worker's stale_after default) so the three can never drift apart.
"""
import ast
import math
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rf_rx_types import (RX_IQ_MAX, RX_IQ_MIN, RX_MAX_SAMPLES, RX_NORMAL_OP,
                         RxAnalysis, RxIqFrame, RxReadback)

FS = 60_000_000
N = 4096
BIN_HZ = FS / N
TONE_HZ = 128 * BIN_HZ  # exactly 1.875 MHz: an exact FFT bin, no scalloping loss
MODULE_PATH = Path(__file__).resolve().parents[1] / "rf_rx_analysis.py"


def _repo_root():
    """The repository root, found by walking up rather than by counting parents.

    These criteria must also run from a scratch harness that only copies the modules under
    test (that is how the pre-fix RED is recorded), so the archived evidence cannot be
    located with a fixed number of ``parents[]``.
    """
    for parent in Path(__file__).resolve().parents:
        if ((parent / "pynq_overlay" / "sw" / "tests").is_dir()
                and (parent / "evidence").is_dir()):
            return parent
    return Path(__file__).resolve().parents[3]


PREFLIGHT = (_repo_root() / "evidence" / "2026-10-01-rx-smart-preflight-loopback"
             / "raw-iq.json")


def analysis_module():
    """Imported at call time so each criterion reports its OWN missing-module RED."""
    import rf_rx_analysis
    return rf_rx_analysis


def iq_bytes(i, q):
    pairs = np.stack([np.asarray(i), np.asarray(q)], axis=1)
    return pairs.astype("<i2").tobytes()


def frame(i, q, ratio=0, fs=FS, capture_id="cap-1", completed=1234.5):
    return RxIqFrame(capture_id=capture_id, payload=iq_bytes(i, q),
                     samples=int(np.size(i)), sample_rate_hz=fs, ratio=ratio,
                     daemon_timestamp_ns=0, connection_generation=1,
                     config_revision=0, request_id=1, release_id="rel", op=RX_NORMAL_OP,
                     physical=False, pre_readback=RxReadback(io_ok=True),
                     post_readback=RxReadback(io_ok=True),
                     completed_at_monotonic=completed)


def complex_tone(n, hz, amp, fs=FS):
    """A complex exponential of amplitude amp: i = amp*cos, q = amp*sin."""
    x = amp * np.exp(1j * 2 * np.pi * hz * np.arange(n) / fs)
    return np.rint(x.real).astype(int), np.rint(x.imag).astype(int)


def real_tone(n, hz, amp, phase=0.0, fs=FS):
    return np.rint(amp * np.cos(2 * np.pi * hz * np.arange(n) / fs + phase)).astype(int)


def top_bin_span(spectrum, freqs, fraction=0.99):
    """The PRE-FIX definition, kept here so both can be computed on ONE spectrum.

    It is max(f) - min(f) over the smallest set of STRONGEST bins that reaches `fraction`
    of the power: an upper bound on the occupied bandwidth, not a contiguous containment.
    Comparing the module's answer against this is what makes the two definitions
    distinguishable.
    """
    power = np.asarray(spectrum, dtype=float) ** 2
    power[0] = 0.0
    total = float(power.sum())
    order = np.argsort(power)[::-1]
    cumulative = np.cumsum(power[order])
    index = min(int(np.searchsorted(cumulative, fraction * total, side="left")),
                power.size - 1)
    band = freqs[order[:index + 1]]
    return float(band.max() - band.min())


def contiguous_span(spectrum, freqs, fraction=0.99):
    """The DEFINITION, written from the sentence in the module docstring.

    The narrowest CONTIGUOUS span of ascending frequency that holds `fraction` of the AC
    power, found with the standard two-pointer sweep: every power is non-negative, so the
    left edge of the window only ever moves forward and each right edge is settled in one
    look.  Written as its own algorithm (not the module's cumulative-sum form), so the
    module is checked against the definition rather than against itself.
    """
    power = np.asarray(spectrum, dtype=float) ** 2
    power[0] = 0.0
    order = np.argsort(freqs)
    ordered_freqs = freqs[order]
    ordered_power = power[order]
    target = fraction * float(power.sum())
    best = None
    low = 0
    running = 0.0
    for high in range(ordered_power.size):
        running += ordered_power[high]
        while running - ordered_power[low] >= target:
            running -= ordered_power[low]
            low += 1
        if running >= target:
            width = ordered_freqs[high] - ordered_freqs[low]
            if best is None or width < best:
                best = width
    return best


def brute_force_span(spectrum, freqs, fraction=0.99):
    """O(N^2) exhaustive containment, used to prove THAT two-pointer reference right.

    Only ever called on the short synthetic record (N = 512): the point is to check the
    reference against the definition itself, not to analyse 4096-point real frames.
    """
    power = np.asarray(spectrum, dtype=float) ** 2
    power[0] = 0.0
    order = np.argsort(freqs)
    ordered_freqs = freqs[order]
    ordered_power = power[order]
    target = fraction * float(power.sum())
    best = None
    for low in range(ordered_power.size):
        running = 0.0
        for high in range(low, ordered_power.size):
            running += ordered_power[high]
            if running >= target:
                width = ordered_freqs[high] - ordered_freqs[low]
                if best is None or width < best:
                    best = width
                break
    return best


# --- time domain: the planned dBFS口径 ------------------------------------------------

def test_a_single_complex_tone_is_resolved_at_its_own_frequency():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    assert isinstance(result, RxAnalysis)
    assert result.capture_id == "cap-1"
    assert result.samples == N
    assert result.completed_at_monotonic == 1234.5
    assert result.valid is True and result.no_energy is False
    assert result.peak_hz is not None
    assert abs(result.peak_hz - TONE_HZ) < BIN_HZ / 2
    assert result.spectrum_window == "hann"
    assert abs(result.coherent_gain - float(np.hanning(N).sum())) < 1e-9
    assert len(result.spectrum) == N
    assert result.decimation == 1
    assert result.input_fs_hz == FS
    assert abs(result.analysis_fs_hz - FS) < 1e-6
    assert result.clipping_count == 0 and result.output_clipped is False


def test_a_known_amplitude_gives_the_planned_component_dbfs():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    expected = 20 * math.log10((1000.0 / math.sqrt(2.0)) / 2048.0)
    assert result.i_rms == pytest.approx(1000.0 / math.sqrt(2.0), rel=1e-3)
    assert result.q_rms == pytest.approx(1000.0 / math.sqrt(2.0), rel=1e-3)
    assert result.component_dbfs[0] == pytest.approx(expected, abs=0.05)
    assert result.component_dbfs[1] == pytest.approx(expected, abs=0.05)


def test_the_complex_rms_adds_the_two_component_powers():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    assert result.complex_rms == pytest.approx(1000.0, rel=1e-3)
    assert result.complex_rms ** 2 == pytest.approx(result.i_rms ** 2 + result.q_rms ** 2,
                                                   rel=1e-9)
    expected = 20 * math.log10(1000.0 / (2048.0 * math.sqrt(2.0)))
    assert result.complex_dbfs == pytest.approx(expected, abs=0.05)
    # the complex dBFS is exactly the mean of the two component POWERS
    combined = 10 * math.log10((10 ** (result.component_dbfs[0] / 10.0)
                                + 10 ** (result.component_dbfs[1] / 10.0)) / 2.0)
    assert result.complex_dbfs == pytest.approx(combined, abs=1e-9)


def test_a_single_component_tone_never_uses_the_single_component_scale_for_the_complex_value():
    ana = analysis_module()
    i = real_tone(N, TONE_HZ, 1000)
    q = np.zeros(N, dtype=int)
    result = ana.summarize(frame(i, q))
    assert result.q_rms == 0.0
    assert result.component_dbfs[1] is None  # a dead component is None, never -inf
    assert result.component_dbfs[0] == pytest.approx(
        20 * math.log10((1000.0 / math.sqrt(2.0)) / 2048.0), abs=0.05)
    assert result.complex_rms == pytest.approx(1000.0 / math.sqrt(2.0), rel=1e-3)
    # the complex scale is 2048*sqrt(2), exactly 20*log10(sqrt(2)) below the component one
    assert (result.component_dbfs[0] - result.complex_dbfs) == pytest.approx(
        20 * math.log10(math.sqrt(2.0)), abs=1e-9)


def test_the_spectrum_peak_amplitude_is_normalised_by_the_actual_windows_coherent_gain():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    # an exact-bin complex tone of amplitude 1000 lands on 1000 code units ...
    assert max(result.spectrum) == pytest.approx(1000.0, abs=1.0)
    # ... which is only true when the divisor is sum(window), not N
    assert result.coherent_gain == pytest.approx(float(np.hanning(N).sum()), abs=1e-9)
    assert abs(result.coherent_gain - N) > 1000


def test_two_tones_are_both_resolved_and_span_the_occupied_band():
    ana = analysis_module()
    f1, f2 = 100 * BIN_HZ, 233 * BIN_HZ  # 233/100 is not an integer harmonic ratio
    x = (700.0 * np.exp(1j * 2 * np.pi * f1 * np.arange(N) / FS)
         + 700.0 * np.exp(1j * 2 * np.pi * f2 * np.arange(N) / FS))
    result = ana.summarize(frame(np.rint(x.real).astype(int), np.rint(x.imag).astype(int)))
    freqs = np.fft.fftfreq(N, 1.0 / FS)
    mag = np.asarray(result.spectrum)
    k1 = int(np.argmin(np.abs(freqs - f1)))
    k2 = int(np.argmin(np.abs(freqs - f2)))
    assert mag[k1] > 300.0 and mag[k2] > 300.0
    # nothing between the two tones: this is two tones, not one wide band
    mid = int(np.argmin(np.abs(freqs - (f1 + f2) / 2.0)))
    assert mag[mid] < 0.05 * min(mag[k1], mag[k2])
    assert min(abs(result.peak_hz - f1), abs(result.peak_hz - f2)) < BIN_HZ / 2
    assert (f2 - f1) <= result.occupied_bandwidth_hz <= (f2 - f1) + 5 * BIN_HZ


# --- zero / invalid / non-finite ------------------------------------------------------

def test_zero_energy_is_an_explicit_status_and_never_nan_or_inf():
    ana = analysis_module()
    zeros = np.zeros(N, dtype=int)
    result = ana.summarize(frame(zeros, zeros))
    assert result.no_energy is True
    assert result.valid is False
    assert result.reason  # an explicit reason carries the status
    assert "energy" in result.reason.lower()
    assert result.i_rms == 0.0 and result.q_rms == 0.0 and result.complex_rms == 0.0
    # a two-element tuple of Nones, exactly like every other result: a consumer writing
    # `i_dbfs, q_dbfs = result.component_dbfs` must not hit a ValueError here
    assert result.component_dbfs == (None, None)
    i_dbfs, q_dbfs = result.component_dbfs
    assert i_dbfs is None and q_dbfs is None
    assert result.complex_dbfs is None
    assert result.peak_hz is None and result.occupied_bandwidth_hz is None
    assert result.clipping_count == 0 and result.output_clipped is False
    assert result.spectrum == ()
    assert result.dc_removed is False and result.dc_bin_excluded is False
    telemetry = result.to_dict()
    assert telemetry["no_energy"] is True
    for value in telemetry.values():
        if isinstance(value, float):
            assert math.isfinite(value), value


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_component_is_refused(bad):
    ana = analysis_module()
    i = np.zeros(N)
    i[5] = bad
    with pytest.raises(ValueError, match="finite"):
        ana.summarize_components(i, np.zeros(N), capture_id="cap-1", sample_rate_hz=FS)


@pytest.mark.parametrize("bad", [2048, -2049, 3000, -3000])
def test_a_component_outside_the_signed_12_bit_range_is_refused(bad):
    ana = analysis_module()
    i = np.zeros(N, dtype=int)
    i[7] = bad
    with pytest.raises(ValueError, match="12-bit"):
        ana.summarize_components(i, np.zeros(N, dtype=int), capture_id="cap-1",
                                 sample_rate_hz=FS)


@pytest.mark.parametrize("bad", [2048, -2049])
def test_a_frame_holding_a_non_12_bit_code_is_refused_by_summarize(bad):
    # summarize() is the entry the receive pipeline calls, so the range rule cannot live
    # only on summarize_components: an s16 payload outside the 12-bit range must be
    # refused here too instead of being analysed into >0 dBFS telemetry.
    ana = analysis_module()
    i = np.zeros(N, dtype=int)
    i[3] = bad
    with pytest.raises(ValueError, match="12-bit"):
        ana.summarize(frame(i, np.zeros(N, dtype=int)))


@pytest.mark.parametrize("bad", [RX_IQ_MAX, RX_IQ_MIN])
def test_the_code_rail_endpoints_are_legal_input(bad):
    ana = analysis_module()
    i = np.zeros(N, dtype=int)
    i[0] = bad
    result = ana.summarize_components(i, np.zeros(N, dtype=int), capture_id="cap-1",
                                      sample_rate_hz=FS)
    assert result.clipping_count == 1


@pytest.mark.parametrize("i,q,match", [
    (np.zeros(10, dtype=int), np.zeros(9, dtype=int), "same length"),
    (np.zeros(0, dtype=int), np.zeros(0, dtype=int), "1..4096"),
    (np.zeros(RX_MAX_SAMPLES + 1, dtype=int), np.zeros(RX_MAX_SAMPLES + 1, dtype=int),
     "1..4096"),
    (np.zeros((4, 4), dtype=int), np.zeros((4, 4), dtype=int), "one-dimensional"),
])
def test_an_invalid_length_is_refused(i, q, match):
    ana = analysis_module()
    with pytest.raises(ValueError, match=match):
        ana.summarize_components(i, q, capture_id="cap-1", sample_rate_hz=FS)


def test_a_frame_is_the_only_way_a_payload_reaches_summarize():
    ana = analysis_module()
    # RxIqFrame already refuses a payload that is not samples*4 bytes, so summarize can
    # never be handed a ragged byte stream ...
    with pytest.raises(ValueError):
        RxIqFrame(capture_id="c", payload=b"\x00" * 6, samples=2, sample_rate_hz=FS,
                  ratio=0, daemon_timestamp_ns=0, connection_generation=1,
                  config_revision=0, request_id=1, release_id=None, op=RX_NORMAL_OP,
                  physical=False, pre_readback=RxReadback(io_ok=True),
                  post_readback=RxReadback(io_ok=True), completed_at_monotonic=0.0)
    # ... and summarize refuses anything that is not the validated frame.
    with pytest.raises(TypeError):
        ana.summarize(b"\x00" * 16)


def test_a_non_4096_length_is_analysed_honestly():
    ana = analysis_module()
    n = 100
    hz = 5 * FS / n
    i, q = complex_tone(n, hz, 1000)
    result = ana.summarize(frame(i, q))
    assert result.samples == n
    assert len(result.spectrum) == n
    assert abs(result.peak_hz - hz) < (FS / n) / 2


@pytest.mark.parametrize("n", [1, 2])
def test_a_length_below_the_window_minimum_has_no_spectrum_and_no_crash(n):
    ana = analysis_module()
    result = ana.summarize(frame(np.full(n, 7), np.full(n, -3)))
    assert result.valid is True and result.no_energy is False
    assert result.spectrum == ()
    assert result.spectrum_window == ""
    assert result.coherent_gain is None
    assert result.peak_hz is None and result.occupied_bandwidth_hz is None
    # no spectrum was built, so no DC handling ran and none may be claimed
    assert result.dc_removed is False and result.dc_bin_excluded is False


# --- DC handling ----------------------------------------------------------------------

def test_dc_is_removed_before_windowing_but_never_from_the_planned_rms():
    ana = analysis_module()
    i = np.full(N, 100, dtype=int)
    q = np.full(N, -50, dtype=int)
    result = ana.summarize(frame(i, q))
    assert result.dc_removed is True
    assert result.dc_bin_excluded is True
    assert result.i_rms == pytest.approx(100.0)
    assert result.q_rms == pytest.approx(50.0)
    assert result.component_dbfs[0] == pytest.approx(20 * math.log10(100.0 / 2048.0))
    assert result.component_dbfs[1] == pytest.approx(20 * math.log10(50.0 / 2048.0))
    # after the complex mean is removed there is no AC content at all
    assert result.spectrum[0] == 0.0
    assert max(result.spectrum) == 0.0
    assert result.peak_hz is None and result.occupied_bandwidth_hz is None
    assert result.no_energy is False


def test_a_large_dc_offset_never_wins_the_peak_search():
    ana = analysis_module()
    i = 1500 + real_tone(N, TONE_HZ, 300)
    q = 1500 + real_tone(N, TONE_HZ, 300, phase=-math.pi / 2)
    result = ana.summarize(frame(i, q))
    assert abs(result.peak_hz - TONE_HZ) < BIN_HZ / 2
    assert result.spectrum[0] < 0.05 * max(result.spectrum)


# --- clipping -------------------------------------------------------------------------

def test_a_tone_in_the_lowest_non_dc_bin_is_never_reported_as_dc():
    ana = analysis_module()
    # A tone in bin 1 is multiplied by a Hann window whose own DC term beats its +-1
    # terms, so the windowed DC bin is the LARGEST raw bin of the record.  Only an
    # explicit exclusion keeps the reported peak off 0.0 Hz.
    i = np.rint(-1000.0 * np.cos(2 * np.pi * np.arange(N) / N)).astype(int)
    result = ana.summarize(frame(i, np.zeros(N, dtype=int)))
    assert result.dc_bin_excluded is True
    assert result.spectrum[0] > max(result.spectrum[1], result.spectrum[N - 1])
    assert result.peak_hz == pytest.approx(BIN_HZ, abs=1e-6)
    assert result.peak_hz != 0.0

def test_clipping_is_the_code_rail_and_the_endpoints_count_exactly():
    ana = analysis_module()
    i = np.zeros(N, dtype=int)
    q = np.zeros(N, dtype=int)
    i[0], i[1], q[2], q[3] = RX_IQ_MAX, RX_IQ_MIN, RX_IQ_MAX, RX_IQ_MIN
    result = ana.summarize(frame(i, q))
    assert result.clipping_count == 4
    assert result.output_clipped is True


def test_a_sample_one_code_inside_the_rail_is_not_clipping():
    ana = analysis_module()
    i = np.zeros(N, dtype=int)
    q = np.zeros(N, dtype=int)
    i[0], i[1] = RX_IQ_MAX - 1, RX_IQ_MIN + 1
    q[2], q[3] = RX_IQ_MAX - 1, RX_IQ_MIN + 1
    result = ana.summarize(frame(i, q))
    assert result.clipping_count == 0
    assert result.output_clipped is False


def test_a_hard_limited_but_in_range_signal_is_never_reported_as_clipped():
    ana = analysis_module()
    # a +/-1500 square wave is grossly distorted and still never touches the 12-bit rail:
    # the flag is a rail test, not a claim about the analog front end.
    pattern = np.where(np.arange(N) % 8 < 4, 1500, -1500)
    result = ana.summarize(frame(pattern, np.zeros(N, dtype=int)))
    assert result.clipping_count == 0
    assert result.output_clipped is False


# --- occupied bandwidth ---------------------------------------------------------------

def test_occupied_bandwidth_is_99_percent_power_containment_of_a_known_width():
    ana = analysis_module()
    rng = np.random.RandomState(20261001)
    bandwidth = 10e6
    freqs = np.fft.fftfreq(N, 1.0 / FS)
    inband = np.abs(freqs) <= bandwidth / 2.0
    spectrum = np.zeros(N, dtype=complex)
    spectrum[inband] = np.exp(1j * 2 * np.pi * rng.rand(int(inband.sum())))
    x = np.fft.ifft(spectrum)
    x = x / np.max(np.abs(x)) * 1200.0
    i = np.rint(x.real).astype(int)
    q = np.rint(x.imag).astype(int)
    result = ana.summarize(frame(i, q))
    assert result.output_clipped is False
    assert 0.9 * bandwidth <= result.occupied_bandwidth_hz <= 1.1 * bandwidth


def test_a_single_tone_occupied_bandwidth_is_the_window_mainlobe():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    # 99% of a Hann-windowed single tone sits in its three-bin mainlobe, so the estimate
    # is the window's width -- not zero and not a hand-waved "peak width".
    assert result.occupied_bandwidth_hz == pytest.approx(2 * BIN_HZ, abs=1e-6)
    assert result.occupied_bandwidth_hz < 0.05e6


def test_occupied_bandwidth_is_the_narrowest_contiguous_span_not_a_top_bin_span():
    # Two tones plus a broadband floor holding a few percent of the AC power.  The
    # strongest-bin span is dragged out towards the edge of the band by scattered floor
    # bins (it is an upper bound), while the narrowest CONTIGUOUS 99% span only has to
    # reach the floor bins nearest the tones.  Neither the single-tone nor the brick-wall
    # case can tell those two definitions apart; this one differs by more than 1.5x.
    ana = analysis_module()
    n = 512
    rng = np.random.RandomState(20261001)
    t = np.arange(n)
    tones = (700.0 * np.exp(1j * 2 * np.pi * (100 * FS / n) * t / FS)
             + 700.0 * np.exp(1j * 2 * np.pi * (233 * FS / n) * t / FS))
    x = tones + 100.0 * (rng.randn(n) + 1j * rng.randn(n))
    result = ana.summarize_components(x.real, x.imag, capture_id="cap-1",
                                      sample_rate_hz=FS)
    freqs = np.fft.fftfreq(n, 1.0 / FS)
    definition = contiguous_span(result.spectrum, freqs)
    upper_bound = top_bin_span(result.spectrum, freqs)
    # the reference itself is checked against exhaustive enumeration (cheap at N = 512)
    assert definition == pytest.approx(brute_force_span(result.spectrum, freqs),
                                       abs=1e-6)
    assert result.occupied_bandwidth_hz == pytest.approx(definition, abs=1e-6)
    assert definition < upper_bound
    assert upper_bound > 1.5 * result.occupied_bandwidth_hz
    assert (233 - 100) * (FS / n) <= result.occupied_bandwidth_hz < upper_bound


@pytest.mark.skipif(not PREFLIGHT.exists(),
                    reason="the archived real loopback capture is not present")
def test_the_real_preflight_on_frames_report_the_contiguous_span_not_a_top_bin_span():
    # The real ON frames are the case the reviewer measured: the top-bin definition
    # returned 30.88/40.00/30.03 MHz, while contiguous 99% containment returns
    # 8.55/8.06/8.55 MHz of the 60 MHz analysis band.
    import json

    ana = analysis_module()
    raw = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    seen = 0
    for record in raw["records"]:
        if not record["tag"].startswith("on-"):
            continue
        header = record["header"]
        pairs = np.asarray(record["iq"], dtype="<i2")
        result = ana.summarize(frame(pairs[:, 0], pairs[:, 1],
                                     ratio=int(header["ratio"]),
                                     fs=int(header["sample_rate_hz"]),
                                     capture_id=record["tag"]))
        freqs = np.fft.fftfreq(result.samples, 1.0 / result.analysis_fs_hz)
        assert result.occupied_bandwidth_hz == pytest.approx(
            contiguous_span(result.spectrum, freqs), abs=1e-6)
        assert 7.0e6 <= result.occupied_bandwidth_hz <= 1.0e7
        assert top_bin_span(result.spectrum, freqs) > 3.0 * result.occupied_bandwidth_hz
        seen += 1
    assert seen == 3


def test_a_real_valued_input_reports_the_two_sided_span_of_both_images():
    # The axis is the TWO-SIDED complex-baseband axis -- correct for this I/Q signal and
    # required by the 10 MHz brick-wall case.  A Q=0 input is Hermitian, so its spectrum
    # carries the +f0 AND the -f0 image and 99% containment must include both: the span
    # is about 2*f0, NOT the one-sided ~2-bin mainlobe.  Pinned here so the convention is
    # stated rather than an untested accident.
    ana = analysis_module()
    result = ana.summarize(frame(real_tone(N, TONE_HZ, 1000), np.zeros(N, dtype=int)))
    assert result.occupied_bandwidth_hz == pytest.approx(2 * TONE_HZ + 2 * BIN_HZ,
                                                         abs=2 * BIN_HZ)
    assert result.occupied_bandwidth_hz > 3.0e6


# --- decimation -----------------------------------------------------------------------

def test_decimation_is_recorded_and_the_spectrum_uses_the_decimated_rate():
    ana = analysis_module()
    x = 1000.0 * np.exp(1j * 2 * np.pi * TONE_HZ * np.arange(N) / FS)
    decimated = x[::2]
    result = ana.summarize(frame(np.rint(decimated.real).astype(int),
                                 np.rint(decimated.imag).astype(int), ratio=1))
    assert result.decimation == 2
    assert result.input_fs_hz == FS
    assert result.analysis_fs_hz == FS // 2
    assert result.samples == N // 2
    assert len(result.spectrum) == N // 2
    # a tone below the decimated Nyquist keeps its physical frequency
    assert abs(result.peak_hz - TONE_HZ) < (FS // 2) / (N // 2) / 2


def test_a_ratio_2_spectrum_is_labelled_at_the_decimated_rate_and_shows_the_alias():
    ana = analysis_module()
    above_nyquist = 20e6
    x = 1000.0 * np.exp(1j * 2 * np.pi * above_nyquist * np.arange(N) / FS)
    decimated = x[::4]
    result = ana.summarize(frame(np.rint(decimated.real).astype(int),
                                 np.rint(decimated.imag).astype(int), ratio=2))
    assert result.decimation == 4
    assert result.analysis_fs_hz == FS // 4  # 15 MHz, never labelled as 60 MHz
    assert result.input_fs_hz == FS
    assert abs(result.peak_hz) <= result.analysis_fs_hz / 2
    assert abs(result.peak_hz - 5e6) < BIN_HZ  # folded, not 20 MHz
    assert abs(result.peak_hz - above_nyquist) > 1e6


def test_a_sample_rate_the_decimation_cannot_divide_exactly_is_refused():
    ana = analysis_module()
    with pytest.raises(ValueError, match="divide exactly"):
        ana.summarize_components(np.zeros(16, dtype=int), np.zeros(16, dtype=int),
                                 capture_id="cap-1", sample_rate_hz=FS + 1, ratio=1)


def test_a_frame_whose_rate_the_decimation_cannot_divide_is_refused_too():
    ana = analysis_module()
    i, q = complex_tone(16, FS / 16.0, 1000)
    with pytest.raises(ValueError, match="divide exactly"):
        ana.summarize(frame(i, q, ratio=1, fs=FS + 1))


def test_a_ratio_outside_the_selection_code_range_is_refused():
    ana = analysis_module()
    with pytest.raises(ValueError, match="selection code"):
        ana.summarize_components(np.zeros(16, dtype=int), np.zeros(16, dtype=int),
                                 capture_id="cap-1", sample_rate_hz=FS, ratio=5)


def test_the_decimation_helper_is_named_for_what_it_returns():
    # It returns 1 << ratio -- a decimation -- and its own call site already binds the
    # result to `decimation`.  A name that says "rate" invites reading it as the analysis
    # sample rate, which is the one thing it is not.
    ana = analysis_module()
    assert not hasattr(ana, "_analysis_rate")
    assert ana._analysis_decimation(FS, 1) == 2


def test_a_numpy_integer_rate_and_ratio_are_accepted_like_plain_integers():
    # isinstance, not `type(x) is int`: a rate that arrived from a numpy array must not
    # be refused as if it were a float.  The stored rate must still be a plain int so the
    # telemetry payload stays JSON-serialisable.
    import json

    ana = analysis_module()
    result = ana.summarize_components(np.zeros(16, dtype=int), np.zeros(16, dtype=int),
                                      capture_id="cap-1", sample_rate_hz=np.int64(FS),
                                      ratio=np.int64(1))
    assert result.decimation == 2
    assert isinstance(result.input_fs_hz, int)
    json.dumps(result.to_dict())


def test_a_float_or_bool_rate_is_still_refused_instead_of_being_truncated():
    ana = analysis_module()
    for bad in (FS + 0.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            ana.summarize_components(np.zeros(16, dtype=int), np.zeros(16, dtype=int),
                                     capture_id="cap-1", sample_rate_hz=bad)


# --- bounded display output -----------------------------------------------------------

def test_the_preview_is_bounded_and_in_wire_order():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    assert len(result.preview) <= 2 * 64
    assert all(isinstance(value, int) for value in result.preview)
    assert result.preview[0] == int(i[0]) and result.preview[1] == int(q[0])
    assert result.preview[2] == int(i[1]) and result.preview[3] == int(q[1])


def test_the_telemetry_dict_is_scalars_only_and_carries_every_label():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    telemetry = result.to_dict()
    assert "spectrum" not in telemetry
    assert "preview" not in telemetry
    assert all(isinstance(value, (str, int, float, bool)) or value is None
               for value in telemetry.values())
    for key in ("i_rms", "q_rms", "complex_rms", "i_dbfs", "q_dbfs", "complex_dbfs",
                "spectrum_window", "coherent_gain", "dc_removed", "dc_bin_excluded",
                "input_fs_hz", "decimation", "analysis_fs_hz", "output_clipped",
                "clipping_count", "peak_hz", "occupied_bandwidth_hz", "no_energy",
                "valid", "samples", "capture_id"):
        assert key in telemetry, key


def test_the_analysis_is_frozen():
    ana = analysis_module()
    i, q = complex_tone(N, TONE_HZ, 1000)
    result = ana.summarize(frame(i, q))
    with pytest.raises(FrozenInstanceError):
        result.valid = False


def test_no_output_number_is_nan_or_inf():
    ana = analysis_module()
    rng = np.random.RandomState(7)
    i = rng.randint(RX_IQ_MIN, RX_IQ_MAX + 1, N)
    q = rng.randint(RX_IQ_MIN, RX_IQ_MAX + 1, N)
    result = ana.summarize(frame(i, q))
    assert all(math.isfinite(value) for value in result.spectrum)
    assert all(value is None or math.isfinite(value) for value in result.component_dbfs)
    assert math.isfinite(result.complex_dbfs)
    assert math.isfinite(result.peak_hz)
    assert math.isfinite(result.occupied_bandwidth_hz)
    for value in result.to_dict().values():
        if isinstance(value, float):
            assert math.isfinite(value), value


# --- what the summary must never pretend to know --------------------------------------

def test_the_clipping_flag_is_named_for_the_code_rails_not_for_rf_compression():
    fields = set(RxAnalysis.__dataclass_fields__)
    assert "output_clipped" in fields
    assert "clipping_count" in fields
    assert "compressed" not in fields


def test_the_summary_has_no_snr_vpp_or_dbm_field():
    ana = analysis_module()
    fields = set(RxAnalysis.__dataclass_fields__)
    i, q = complex_tone(N, TONE_HZ, 1000)
    telemetry = ana.summarize(frame(i, q)).to_dict()
    for banned in ("snr", "snr_db", "snr_estimate", "vpp", "vpp_mv", "dbm",
                   "evm", "ber", "crc", "prominence", "prominence_db"):
        assert banned not in fields, banned
        assert banned not in telemetry, banned


# --- freshness: 30 s for the IQ snapshot, an INDEPENDENT 2 s for telemetry ------------

def test_the_two_freshness_windows_are_named_constants():
    ana = analysis_module()
    assert ana.IQ_SNAPSHOT_FRESHNESS_S == 30.0
    assert ana.TELEMETRY_FRESHNESS_S == 2.0
    assert ana.IQ_SNAPSHOT_FRESHNESS_S != ana.TELEMETRY_FRESHNESS_S


def test_the_telemetry_expiry_agrees_with_the_two_existing_declarations():
    # The 2 s hardware-telemetry rule is NOT declared once: pynq_status carries its own
    # RF_STALE_AFTER and injects it into the worker, whose constructor has the same
    # default.  The spec keeps that expiry independent of the IQ window, so this module
    # names its own constant -- and this criterion is what catches the three drifting.
    import inspect
    import sys

    ana = analysis_module()
    # Appended, not inserted: the scratch RED harness carries only the two analysis modules,
    # so the daemon modules are resolved from the real tree without shadowing them.
    sw = str(_repo_root() / "pynq_overlay" / "sw")
    if sw not in sys.path:
        sys.path.append(sw)
    import pynq_status
    import rf_status_worker

    worker = rf_status_worker.RfStatusWorker(client=object())
    injected = inspect.signature(
        pynq_status._start_rf_status_worker).parameters["stale_after"].default
    assert pynq_status.RF_STALE_AFTER == ana.TELEMETRY_FRESHNESS_S
    assert injected == ana.TELEMETRY_FRESHNESS_S
    assert worker.stale_after == ana.TELEMETRY_FRESHNESS_S
    assert ana.IQ_SNAPSHOT_FRESHNESS_S == 30.0


def test_the_module_does_not_claim_the_telemetry_expiry_has_one_home():
    # The old "Freshness has exactly one home, here" claim was false.  The module must
    # state the fact instead -- hardware telemetry keeps its OWN 2 s expiry and these two
    # pre-existing declarations are the same rule -- so the false claim cannot come back.
    source = MODULE_PATH.read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source)) or ""
    assert "exactly one home" not in doc
    assert "RF_STALE_AFTER" in doc
    assert "stale_after" in doc
    assert "independent" in doc.lower()


@pytest.mark.parametrize("age,expected", [
    (0.0, True), (1.0, True), (29.999, True), (30.0, True),
    (30.001, False), (31.0, False), (3600.0, False), (-0.001, False),
])
def test_a_captured_iq_snapshot_is_fresh_for_30_seconds(age, expected):
    ana = analysis_module()
    assert ana.snapshot_is_fresh(1000.0, 1000.0 + age) is expected


@pytest.mark.parametrize("age,expected", [
    (0.0, True), (1.999, True), (2.0, True), (2.001, False), (3.0, False), (-0.001, False),
])
def test_hardware_telemetry_expires_after_2_seconds(age, expected):
    ana = analysis_module()
    assert ana.telemetry_is_fresh(1000.0, 1000.0 + age) is expected


def test_the_iq_window_and_the_telemetry_window_are_independent():
    ana = analysis_module()
    now = 1030.0
    assert ana.snapshot_is_fresh(1000.0, now) is True   # 30 s old IQ: still fresh
    assert ana.telemetry_is_fresh(1020.0, now) is False  # 10 s old telemetry: stale
    assert ana.telemetry_is_fresh(1029.0, now) is True   # 1 s old telemetry: fresh
    assert ana.snapshot_is_fresh(1031.0, now) is False   # a future moment is not fresh


def test_an_absent_moment_is_never_fresh():
    ana = analysis_module()
    assert ana.snapshot_is_fresh(None, 100.0) is False
    assert ana.telemetry_is_fresh(None, 100.0) is False
    assert ana.snapshot_is_fresh(100.0, None) is False
    assert ana.telemetry_is_fresh(100.0, None) is False


# --- purity ---------------------------------------------------------------------------

def test_the_module_is_pure_and_imports_nothing_from_the_transport_or_the_board():
    source = MODULE_PATH.read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "math", "typing", "numpy", "rf_rx_types"}, imported
    for banned in ("socket", "rf_status_client", "rf_status_worker", "pynq_status",
                   "ui_view", "ui_model", "subprocess", "paramiko"):
        assert banned not in imported
        assert banned not in source
