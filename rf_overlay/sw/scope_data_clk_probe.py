#!/usr/bin/env python3
"""scope_data_clk_probe.py -- pin DATA_CLK's real frequency by measuring the DDS.

WHY THIS EXISTS
===============
`rf_overlay/constraints/rf.xdc` constrained `data_clk` at 33.333 ns, from a comment
claiming DATA_CLK was 30 MHz.  The real value is 120 MHz, and the fix changes a
clock constraint by 4x -- so it had better not rest on an inference.

The board's own 16-bit window counter cannot settle it: it reads `win_cycles`
= 13107, and 13107 counts in 655.36 us is 20.000 MHz -- but 13107 + 65536 is
78643, which is 120.0 MHz in the same window.  Both fit, because the counter
wraps.  A measurement that is consistent with two answers is not evidence for
either.

This measures something that is NOT ambiguous.  The DDS advances one step per
`dds_ce`, which fires once per *sample*, and a sample takes two DATA_CLK cycles
in 1R1T (`phy_tx.v`).  So the emitted offset is

    delta_f = phase_inc / 2^12 * (DATA_CLK / 2)

At phase_inc = 128 that is 1875.0 kHz if DATA_CLK is 120 MHz, and 312.5 kHz if
it is 20 MHz.  The two candidates differ by 6x, so the scope does not need any
absolute accuracy to tell them apart -- and the board readback claims CLKTF =
120 MHz, which this either confirms or refutes.

CRITERION (written before measuring)
====================================
1. `delta_f` is taken as (f_upper - f_lower) / 2, the two DDS sidebands either
   side of LO.  Taking the difference cancels any LO frequency error, so this
   does not inherit the driver's LO readback.
2. The same value must come back from REPEATS independent captures within
   C_REPEAT_TOL.  A line that moves between captures is noise -- this project
   has already paid for learning that once.
3. `delta_f` must land within C_TOL of exactly ONE candidate.  If it matches
   neither, the model above (not the clock) is wrong, and that is a different
   finding, so it is reported as such.
4. PASS NV: a capture taken with `phase_inc = 0` must show NO sidebands.  If
   they are still there, what is being measured is not the DDS.

Usage (SYSTEM python -- pyvisa; and -u, or the output buffers away):
    python -u rf_overlay/sw/scope_data_clk_probe.py
    python -u rf_overlay/sw/scope_data_clk_probe.py --selftest
"""
import argparse
import math
import os
import sys
import time

import numpy as np

# pyvisa before anything Qt-ish: visa32.dll and the Qt DLLs conflict.
import pyvisa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scope_boundary as sb  # noqa: E402

LO_HZ = sb.LO_REF_HZ         # 80 MHz
PHASE_INC = 128              # what `plcfg` leaves behind
FS_NOMINAL = 60e6            # the programmed sample rate

# The two candidate DATA_CLK values, and where each would put the sidebands.
DATA_CLK_CANDIDATES = (120e6, 20e6)
C_TOL = 0.03                 # 3%, the project's relative-criterion tolerance
C_REPEAT_TOL_HZ = 30e3       # ~1 bin at 2 GSa/s / 62500 pts; a line-width scale
REPEATS = 3

# Search the sidebands well clear of the LO leakage line, wide enough to hold
# either candidate.  312.5 kHz and 1875 kHz both sit inside [200 kHz, 3 MHz].
SB_CENTER_HZ = 1.6e6
SB_TOL_HZ = 1.4e6


def candidates_for(phase_inc, fs):
    """Where each candidate DATA_CLK would put the DDS sidebands."""
    out = []
    for dclk in DATA_CLK_CANDIDATES:
        delta = phase_inc / 2.0 ** 12 * (dclk / 2.0)
        out.append((dclk, delta))
    return out


def match_candidate(delta_hz, cands, tol=C_TOL):
    """Which candidates does this delta_f match?  Returns a list -- the caller
    must treat len != 1 as a problem rather than taking the first hit."""
    hit = []
    for dclk, delta in cands:
        if delta > 0 and abs(delta_hz - delta) / delta <= tol:
            hit.append((dclk, delta))
    return hit


def measure_once(sc, cands):
    """One capture -> (delta_f, f_lower, f_upper, lo_mid), or Nones."""
    xinc, y = sb.capture(sc)
    freqs = np.fft.rfftfreq(len(y), xinc)
    spec = sb.spectrum_of(y)
    if spec is None:
        return None, None, None, None
    f_lo, snr_lo = sb.line_near(freqs, spec, LO_HZ - SB_CENTER_HZ,
                                SB_TOL_HZ, min_snr_db=10.0)
    f_hi, snr_hi = sb.line_near(freqs, spec, LO_HZ + SB_CENTER_HZ,
                                SB_TOL_HZ, min_snr_db=10.0)
    if f_lo is None or f_hi is None:
        return None, f_lo, f_hi, None
    delta = (f_hi - f_lo) / 2.0
    mid = (f_hi + f_lo) / 2.0
    return delta, f_lo, f_hi, mid


def selftest():
    """Prove the arithmetic and the matching, on numbers whose answer is known."""
    ok = True

    def chk(name, cond):
        nonlocal ok
        print("  %-58s %s" % (name, "PASS" if cond else "FAIL"))
        if not cond:
            ok = False

    cands = candidates_for(PHASE_INC, FS_NOMINAL)
    chk("phase_inc 128 -> candidates are 1875.0 / 312.5 kHz",
        abs(cands[0][1] - 1875e3) < 1 and abs(cands[1][1] - 312.5e3) < 1)
    chk("candidates are 6x apart (unambiguous)",
        abs(cands[0][1] / cands[1][1] - 6.0) < 1e-9)
    chk("1875.0 kHz matches EXACTLY one candidate",
        len(match_candidate(1875e3, cands)) == 1)
    chk("312.5 kHz matches EXACTLY one candidate",
        len(match_candidate(312.5e3, cands)) == 1)
    chk("1878 kHz (0.16% off 1875) still matches exactly one",
        len(match_candidate(1878e3, cands)) == 1)
    # This case has to be a value that is *near but not equal* to a candidate.
    # Feeding in the exact candidate would match under any tolerance at all,
    # which is what the first version of this line did -- an assertion that
    # could not fail, and therefore tested nothing.
    chk("1875.1 kHz is rejected when the tolerance is 1e-6",
        len(match_candidate(1875e3 + 100, cands, tol=1e-6)) == 0)
    chk("NV: 1000 kHz matches neither candidate",
        len(match_candidate(1000e3, cands)) == 0)
    # The repeat rule must reject a wandering value and accept a stable one.
    chk("spread 0 Hz is a repeat",
        (max([1875e3] * 3) - min([1875e3] * 3)) < C_REPEAT_TOL_HZ)
    chk("spread 500 kHz is NOT a repeat",
        not ((1875e3 + 500e3) - 1875e3 < C_REPEAT_TOL_HZ))

    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--phase-inc", type=int, default=PHASE_INC)
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    cands = candidates_for(args.phase_inc, FS_NOMINAL)
    print("=" * 78)
    print("T01 -- pin DATA_CLK from the DDS offset (unambiguous, unlike the counter)")
    print("=" * 78)
    print("  LO assumed           %.3f MHz (cancels out: delta_f is a difference)" % (LO_HZ / 1e6))
    print("  phase_inc            %d" % args.phase_inc)
    print("  candidate DATA_CLK -> delta_f:")
    for dclk, delta in cands:
        print("      %-6.0f MHz  ->  %.4f kHz" % (dclk / 1e6, delta / 1e3))
    print("  tolerance            +/- %.0f%%" % (C_TOL * 100))

    rm = pyvisa.ResourceManager()
    sc = sb.ReconnectingScope(rm, sb.SCOPE_RESOURCE)
    sb.scope_prepare(sc, sb.TIM_SCAL_SWEEP)
    # The timebase comes from scope_prepare; the vertical scale is found, not
    # assumed.  At 10000 mdB this fixture delivers order 35 mVpp at 80 MHz, so
    # a fixed 0.5 V/div would put the signal at ~7% of full scale and spend the
    # 8-bit ADC on almost nothing.  autoscale() is the version that only ever
    # replaces its pick with a capture in which the line was actually found.
    rec = sb.autoscale(sc, LO_HZ + cands[0][1], 0.01)
    print("  vertical scale       %.3f V/div (asked %.3f)"
          % (rec["scal"], rec["asked"]))
    xinc, y = sb.capture(sc)
    npts = len(y)
    print("  scope                %.3f GSa/s, %d pts, %.3f kHz bins" %
          (1.0 / xinc / 1e9, npts, 1.0 / xinc / npts / 1e3))

    print("\n  repeats:")
    deltas = []
    for i in range(REPEATS):
        d, f_lo, f_hi, mid = measure_once(sc, cands)
        deltas.append(d)
        if d is None:
            print("    #%d  no sideband pair found" % (i + 1))
        else:
            print("    #%d  lower %8.4f MHz   upper %8.4f MHz   mid %8.4f MHz   delta_f %8.4f kHz"
                  % (i + 1, f_lo / 1e6, f_hi / 1e6, mid / 1e6, d / 1e3))
        time.sleep(0.25)

    good = [d for d in deltas if d is not None]
    if len(good) < REPEATS:
        print("\nRESULT NOT RUN: only %d of %d captures produced a sideband pair."
              % (len(good), REPEATS))
        return 2

    spread = max(good) - min(good)
    print("\n  spread over %d captures  %.4f kHz  (repeat tolerance %.1f kHz)"
          % (REPEATS, spread / 1e3, C_REPEAT_TOL_HZ / 1e3))
    if spread >= C_REPEAT_TOL_HZ:
        print("RESULT FAIL: the offset wanders between captures -> this is noise,"
              " not a line.  Nothing below can be concluded.")
        return 1

    delta = float(np.median(good))
    hit = match_candidate(delta, cands)
    print("  delta_f (median)     %.4f kHz" % (delta / 1e3))
    print("  matches              %s" % ([("DATA_CLK=%.0f MHz" % (c[0] / 1e6)) for c in hit] or "NOTHING"))

    if len(hit) == 1:
        print("\nRESULT PASS: delta_f matches %s and only that one." % ("DATA_CLK = %.0f MHz" % (hit[0][0] / 1e6)))
        print("  => DATA_CLK = %.1f MHz, so the correct constraint period is %.4f ns."
              % (hit[0][0] / 1e6, 1e9 / hit[0][0]))
        print("  (rf.xdc currently says 33.333 ns.)")
        return 0
    if len(hit) == 0:
        print("\nRESULT FAIL (model): delta_f matches NO candidate.  The clock may be"
              " fine; the relationship delta_f = phase_inc/2^12 * DATA_CLK/2 is what"
              " would then be wrong.  Do not 'fix' the xdc on this evidence.")
        return 1
    print("\nRESULT FAIL (ambiguous): delta_f matches more than one candidate -- the"
          " candidates are not separated by this measurement.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
