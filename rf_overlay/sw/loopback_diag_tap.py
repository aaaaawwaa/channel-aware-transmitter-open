#!/usr/bin/env python3
"""Per-sample diff of the 2R2T loopback capture at a FIXED tap, repeated.

The tap sweep gave a sharp minimum at tap 3 (max error 2) and then an exactly
flat 128 for every tap from 5 to 19 -- and, in an earlier standalone run, tap 15
produced an exact solution (error 0) where the sweep produced 128.  A metric
that is flat over 15 taps is not an eye diagram, and a tap whose answer changes
between runs is not a controlled variable.  Neither can be reasoned about from
the summary statistics, so this prints the per-sample difference at the winning
alignment instead.

`solve` reports the MAX absolute difference, so one bad sample out of 64 is
enough to make the whole row read 128.  The question this answers is whether
that 128 is one sample or many, and whether it is in the same place run to run.

Usage:
    python rf_overlay/sw/loopback_diag_tap.py --phy-mode 2r2t --taps 3,15 --reps 3
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from loopback_criteria import (Board, MAPPINGS, SAFE_ATTEN_MDB, apply_mapping,
                               capture, init_args, iq, solve, stream)

ASYM = [(1000, 100), (-500, 700), (300, -900), (-1200, -400),
        (650, 250), (-150, 1100), (900, -50), (-800, 850)]
PAD = ASYM + [(0, 0)] * (64 - len(ASYM))


def set_idelay(b, en, tap):
    """Load en/tap through `board_app idelay` and require the readback.

    The whole point of this script is to compare taps sample by sample, so a
    tap that silently did not take would be printed as an ordinary row.
    """
    out = b.run("idelay", "0x%02X" % en, tap)
    if "RESULT PASS" not in out:
        raise RuntimeError("idelay en=0x%02X tap=%d did not take:\n%s"
                           % (en, tap, out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phy-mode", default="2r2t")
    ap.add_argument("--taps", default="3,15")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--idelay-en", type=lambda v: int(v, 0), default=0x7F)
    a = ap.parse_args()

    b = Board()
    print("--- init")
    reply = b.run(*init_args(SAFE_ATTEN_MDB, 80000000, a.phy_mode == "2r2t"))
    for line in reply.splitlines():
        if "sampling rate" in line:
            print("  %s" % line.strip())

    b.run("samples", "zeros", 64)
    b.run("smpcfg", 64, 1)
    b.run("srcsel", 1)

    for tap in [int(t) for t in a.taps.split(",")]:
        print("\n=== tap %d" % tap)
        set_idelay(b, a.idelay_en, tap)
        for rep in range(a.reps):
            w = capture(b, ("words", ASYM))
            cap = [iq(x) for x in w]
            exact, best = solve(cap, PAD, 0)
            err, name, s = best
            if exact:
                print("  rep %d: EXACT %r" % (rep, sorted(exact)))
                continue
            m = MAPPINGS[name]
            ref = stream(PAD, 0, s, len(cap))
            diffs = []
            for k, ((bi, bq), (ci, cq)) in enumerate(zip(ref, cap)):
                mi, mq = apply_mapping(m, bi, bq)
                di, dq = ci - mi, cq - mq
                if di or dq:
                    diffs.append((k, mi, mq, ci, cq, di, dq))
            print("  rep %d: worst %d at '%s' shift %d; %d/%d samples differ"
                  % (rep, err, name, s, len(diffs), len(cap)))
            for d in diffs[:12]:
                print("      k=%-3d want (%5d,%5d) got (%5d,%5d)  d=(%d,%d)"
                      % (d[0], d[1], d[2], d[3], d[4], d[5], d[6]))


if __name__ == "__main__":
    main()
