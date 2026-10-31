#!/usr/bin/env python3
"""IDELAY tap sweep for the 2R2T on-chip loopback, one capture per tap.

Why this exists: on a 2R2T board nothing programs IDELAY.  `plcfg` is the only
writer of RF_REG_IDELAY_EN / RF_REG_IDELAY_TAP, and `plcfg`'s first act is
rf_reg_write(RF_REG_PHY_MODE, 1), so it cannot be run on a board that has to
stay in 2R2T.  `init` writes PHASE_INC and PHY_MODE and nothing else.  So
idelay_en stays 0, the IDELAYE2s never take a load, and the RX pins are sampled
at the primitive's power-on tap.

With idelay_en = 0x7F / tap = 15 loaded (now via `board_app idelay`, which does
the write without going anywhere near phy_mode) C11/C12 goes from "0 exact
solutions, best error 1102" to "1 exact solution", which says the missing
IDELAY load is the reason the sample-path criteria could not match.  It does
not say 15 is the RIGHT tap for 2R2T: 15 was chosen for 1R1T, and DATA_CLK's
bit period is shorter in 2R2T, so the eye centre moves.

So sweep the tap and report, per tap, the best residual and how many exact
solutions exist.  An exact solution at a whole run of taps, with errors rising
on both sides, is a sampled eye; no exact solution anywhere is a different
problem and this sweep is what tells the two apart.

Usage:
    python rf_overlay/sw/loopback_tap_sweep.py --phy-mode 2r2t [--taps 0,4,8,...]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from loopback_criteria import (IDELAY_EN, Board, SAFE_ATTEN_MDB, capture, iq,
                               init_args, solve)

# The same asymmetric pattern C11/C12 uses.  Not a tone: on a tone a phase error
# and a shift are the same observation, so a bad sampling point would be partly
# absorbed by the shift search and read as a small residual instead of as a
# wrong tap.  On this pattern the correct (mapping, shift) is unique.
ASYM = [(1000, 100), (-500, 700), (300, -900), (-1200, -400),
        (650, 250), (-150, 1100), (900, -50), (-800, 850)]


def set_tap(b, tap):
    """Load one tap and REQUIRE the readback to confirm it.

    The sweep's whole output is "which tap is best", so a tap that silently did
    not take would be scored as if it had -- and would look like an ordinary
    row of the table rather than like a failure.
    """
    out = b.run("idelay", "0x%02X" % IDELAY_EN, tap)
    if "RESULT PASS" not in out:
        raise RuntimeError("idelay tap %d did not take:\n%s" % (tap, out))
    return out



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phy-mode", default="2r2t")
    ap.add_argument("--taps", default="0,3,5,7,8,9,11,13,15,17,19,23,27,31")
    a = ap.parse_args()
    taps = [int(t) for t in a.taps.split(",")]

    b = Board()
    want_2r2t = a.phy_mode == "2r2t"

    print("--- init")
    reply = b.run(*init_args(SAFE_ATTEN_MDB, 80000000, want_2r2t))
    for line in reply.splitlines():
        if "sampling rate" in line:
            print("  %s" % line.strip())

    print("--- enabling IDELAY (idelay_en = 0x%02X, tap set per row below)"
          % IDELAY_EN)
    print("  " + set_tap(b, taps[0]).splitlines()[-1])

    b.run("samples", "zeros", 64)
    b.run("smpcfg", 64, 1)
    b.run("srcsel", 1)

    pad = ASYM + [(0, 0)] * (64 - len(ASYM))
    print("%-6s %-10s %s" % ("tap", "best", "exact solutions"))
    for tap in taps:
        set_tap(b, tap)
        w = capture(b, ("words", ASYM))
        cap = [iq(x) for x in w]
        exact, best = solve(cap, pad, 0)
        print("%-6d %-10d %r" % (tap, best[0], sorted(exact)))


if __name__ == "__main__":
    main()
