"""E3: how close can two lines be before this scope stops separating them?

Why this matters for "can the scope measure normally": the sine/square
comparison probes whether the scope gets a SPECTRUM right, but says nothing
about lines that are NEAR each other.  Resolution is set by the record, and the
record is the thing this instrument silently changes under you (see the
timebase ladder).  So the answer has to be measured, not quoted from the
100 MHz bandwidth spec.

No new waveform and no bitstream change is needed: the TX source is a
suppressed-carrier I/Q modulator with Q hardwired to zero, so the output is
already three lines -- LO-f, LO, LO+f.  f is set by the DDS phase increment:

    f = phase_inc / 4096 * 60 MSPS      =>  phase_inc 1 is 14.648 kHz

which is right at the bin width at 1 GSa/s, so the ladder walks from "obviously
separate" to "obviously merged" while touching one register.

CRITERION, written before measuring.

Two lines are RESOLVED when there is a local maximum on each side of LO,
each clearing the local floor by PEAK_SNR_MIN_DB, and the spectrum BETWEEN
those two peaks dips at least DIP_MIN_DB below the weaker of them.  Dip depth
is the standard Rayleigh-style resolution test and, unlike counting peaks, it
cannot be satisfied by noise: a single merged hump has no interior minimum to
find.

Only phase_inc values whose sidebands actually fall inside the analysis window
are evaluated.  Anything else is NOT RUN -- an earlier version of this probe
counted "3 peaks clearing the gate" anywhere in a fixed window and so scored
LO leakage plus noise maxima as a resolved triplet at exactly the increments
whose sidebands were furthest outside it.  That is the same class of mistake as
a global-peak search: the measurement has to be pointed at the lines.

Negative case: phase_inc 8 (f = 117 kHz) MUST resolve at both timebases.  If
it does not, the extraction logic is at fault rather than the instrument, and
nothing else in this probe means anything.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "sw")))

import numpy as np                                          # noqa: E402
import pyvisa                                               # noqa: E402
import scope_boundary as sb                                 # noqa: E402

LO = 80e6
FS = 60e6
SCAL = 0.2
INCS = (8, 4, 2, 1)                 # f = 117.19 / 58.59 / 29.30 / 14.648 kHz
HALF_WIN = 250e3                    # sidebands up to +-117 kHz sit well inside
DIP_MIN_DB = 3.0                    # resolution threshold, standard value
TIMEBASES = ((6.25e-6, "1 GSa/s"), (3.125e-6, "2 GSa/s"))


def sideband_pair(freqs, spec, f_lo, f_bb):
    """Peak on each side of LO, plus the depth of the dip between them.

    Each sideband is searched for in its OWN neighbourhood, 0.4*f to 1.6*f from
    LO.  Searching the whole window instead lets the routine answer about
    whatever is loudest nearby: at phase_inc 1 at 2 GSa/s it reported a "pair"
    at +-224 kHz for sidebands that should be at +-14.6 kHz.  Constraining the
    search is also what makes "both sidebands inside one bin" come out as no
    peak at all rather than as a confident wrong answer.

    Returns (f_lo_minus, f_lo_plus, snr_minus, snr_plus, dip_db) with any
    element None when the corresponding structure is not there.
    """
    band = (freqs >= f_lo - HALF_WIN) & (freqs <= f_lo + HALF_WIN)
    idx = np.nonzero(band)[0]
    idx = idx[(idx > 1) & (idx < len(spec) - 2)]
    if len(idx) < 8:
        return None, None, None, None, None
    floor = float(np.median(spec[idx]))
    if floor <= 0:
        return None, None, None, None, None
    tol = 0.6 * f_bb

    def peak_in(centre_sign):
        lo_hz = f_lo + centre_sign * 0.4 * f_bb
        hi_hz = f_lo + centre_sign * 1.6 * f_bb
        sel = idx[(freqs[idx] >= min(lo_hz, hi_hz))
                  & (freqs[idx] <= max(lo_hz, hi_hz))]
        sel = sel[(sel > 0) & (sel < len(spec) - 1)]
        m = sel[(spec[sel] > spec[sel - 1]) & (spec[sel] >= spec[sel + 1])]
        if len(m) == 0:
            return None
        return int(m[np.argmax(spec[m])])

    b, a = peak_in(-1), peak_in(+1)
    if b is None or a is None:
        return None, None, None, None, None
    snr_b = 20.0 * np.log10(spec[b] / floor)
    snr_a = 20.0 * np.log10(spec[a] / floor)
    between = spec[min(a, b):max(a, b) + 1]
    weaker = min(spec[a], spec[b])
    dip = 20.0 * np.log10(weaker / max(float(between.min()), 1e-30))
    return (float(freqs[b]), float(freqs[a]), snr_b, snr_a, dip)


def measure(sc, f_lo, f_bb):
    """Best of two captures: the one showing the deeper inter-peak dip."""
    best = None
    for _ in range(2):
        xinc, y = sb.capture(sc)
        freqs = np.fft.rfftfreq(len(y), xinc)
        spec = sb.spectrum_of(y)
        rec = sideband_pair(freqs, spec, f_lo, f_bb)
        if best is None or (rec[4] is not None and best[4] is not None
                            and rec[4] > best[4]):
            best = rec
        if best[4] is not None and best[4] > 6.0:
            break
        time.sleep(0.3)
    return best


def main():
    board = sb.BoardCmd(sb.BOARD_APP, host=sb.BOARD_HOST)
    sc = sb.ReconnectingScope(pyvisa.ResourceManager(), sb.SCOPE_RESOURCE)
    sb.board_prepare_1r1t(board)
    sb.board_init(board, LO, 0)
    board.cmd("wavesel 0")

    summary = []
    for tim_scal, label in TIMEBASES:
        st = sb.scope_prepare(sc, tim_scal, SCAL)
        bin_hz = st["srate"] / st["points"]
        print()
        print("=" * 76)
        print("%s: %.4g us/div -> %.3f GSa/s, %d pts, %.2f kHz bins; "
              "analysis window +-%.0f kHz"
              % (label, tim_scal * 1e6, st["srate"] / 1e9, st["points"],
                 bin_hz / 1e3, HALF_WIN / 1e3))
        print("=" * 76)
        print("  %-9s %-9s %-8s %-9s %-9s %-8s %s"
              % ("phase_inc", "f (kHz)", "2f/bin", "minus kHz", "plus kHz",
                 "dip dB", "verdict"))
        rows = []
        for inc in INCS:
            f_bb = inc / 4096.0 * FS
            if f_bb > HALF_WIN / 2.0:
                rows.append((inc, f_bb, None, None,
                             "sidebands outside the window"))
                print("  %-9d %-9.2f %-8.2f %s"
                      % (inc, f_bb / 1e3, 2 * f_bb / bin_hz,
                         "NOT RUN -- sidebands outside the window"))
                continue
            board.cmd("phase_inc %d" % inc)
            time.sleep(0.25)
            fm, fp, sm, sp, dip = measure(sc, LO, f_bb)
            resolved = (fm is not None and fp is not None
                        and sm >= sb.PEAK_SNR_MIN_DB
                        and sp >= sb.PEAK_SNR_MIN_DB and dip >= DIP_MIN_DB)
            # Two different ways to fail, and they must not be conflated: the
            # sidebands can be resolved-yet-shallow (a real resolution floor),
            # or absent as separate maxima entirely (both land inside one bin,
            # so there is nothing to resolve).  Only the first is a threshold.
            if fm is None or fp is None:
                note = "both sidebands inside one bin"
            elif not resolved and (sm < sb.PEAK_SNR_MIN_DB
                                   or sp < sb.PEAK_SNR_MIN_DB):
                note = "sidebands below the %.0f dB gate" % sb.PEAK_SNR_MIN_DB
            elif not resolved:
                note = "resolved but dip %.1f dB < %.0f dB" % (dip, DIP_MIN_DB)
            else:
                note = ""
            rows.append((inc, f_bb, (fm, fp, sm, sp, dip), resolved, note))
            print("  %-9d %-9.2f %-8.2f %-9s %-9s %-8s %s"
                  % (inc, f_bb / 1e3, 2 * f_bb / bin_hz,
                     ("%+.2f" % ((fm - LO) / 1e3)) if fm else "-",
                     ("%+.2f" % ((fp - LO) / 1e3)) if fp else "-",
                     ("%.1f" % dip) if dip is not None else "-",
                     ("RESOLVED" if resolved else "MERGED")
                     + (("  (%s)" % note) if note else "")))
        summary.append((label, bin_hz, rows))

    print()
    print("=" * 76)
    print("READING")
    print("=" * 76)
    ok = True
    for label, bin_hz, rows in summary:
        neg = [r for r in rows if r[0] == 8][0]
        if not neg[3]:
            print("  %-9s FAIL negative case: phase_inc 8 (f = 117 kHz, %.1f "
                  "bins apart) did not resolve -- extraction at fault, this "
                  "probe proves nothing" % (label, 2 * neg[1] / bin_hz))
            ok = False
        else:
            print("  %-9s PASS negative case: phase_inc 8 resolves (%.1f bins "
                  "apart, dip %.1f dB)"
                  % (label, 2 * neg[1] / bin_hz, neg[2][4]))
        # A "threshold" only exists if the failure mode is the dip closing.
        shallow = [r for r in rows if r[2] is not None and r[2][4] is not None
                   and not r[3]]
        resolved = [r for r in rows if r[2] is not None and r[3]]
        inbin = [r for r in rows if r[2] is not None and r[2][0] is None]
        if shallow and resolved:
            worst = max(shallow, key=lambda r: r[1])
            first = min(resolved, key=lambda r: r[1])
            print("           -> last SHALLOW-DIP at f = %.2f kHz (lines %.1f "
                  "bins apart, dip %.1f dB); first RESOLVED at f = %.2f kHz "
                  "(%.1f bins, dip %.1f dB)"
                  % (worst[1] / 1e3, 2 * worst[1] / bin_hz, worst[2][4],
                     first[1] / 1e3, 2 * first[1] / bin_hz, first[2][4]))
        elif inbin and resolved:
            finest = min(resolved, key=lambda r: r[1])
            print("           -> down to f = %.2f kHz (lines %.1f bins apart, "
                  "dip %.1f dB) the sidebands are still separate maxima; "
                  "below that they fall INSIDE one bin, so the ladder ends "
                  "here rather than at a dip threshold"
                  % (finest[1] / 1e3, 2 * finest[1] / bin_hz, finest[2][4]))
        elif shallow:
            print("           -> every step had a shallow dip; the floor is "
                  "above f = %.2f kHz"
                  % (max(r[1] for r in rows if r[2]) / 1e3))
    sc.close()
    print()
    print("probe %s" % ("OK" if ok else "INVALID -- negative case failed"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
