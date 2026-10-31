"""Prove the new autoscale regression test FAILS on the old implementation.

The project's rule is that a regression test which has not been shown to fail on
the buggy code is not evidence.  This replays the selftest's stub scenario
against the OLD autoscale body (the one that reassigned `best` unconditionally
and walked up on span>200 with no SNR guard) and asserts it returns the
no-line clipped capture -- i.e. "no line" where a line was measurable.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # these live beside scope_boundary.py

import numpy as np                                          # noqa: E402
import scope_boundary as sb                                 # noqa: E402


def old_autoscale(inst, f_expect, start_scal):
    """Verbatim body of the implementation that provoked the sweep failure."""
    scal = start_scal
    best = None
    for _ in range(6):
        sb.set_scale(inst, scal)
        xinc, y = sb.capture(inst)
        span = float(y.max() - y.min())
        freqs = np.fft.rfftfreq(len(y), xinc)
        spec = sb.spectrum_of(y)
        f, snr = sb.line_near(freqs, spec, f_expect, sb.LIN_TOL_HZ)
        best = dict(xinc=xinc, n=len(y), freqs=freqs, spec=spec,
                    f=f, snr=snr, span=span, scal=scal,
                    vpp_trace=sb.meas_vpp(inst))
        if span >= sb.CLIP_COUNTS:
            i = sb.SCAL_LADDER.index(scal) if scal in sb.SCAL_LADDER else None
            if i is None or i + 1 >= len(sb.SCAL_LADDER):
                return best
            scal = sb.SCAL_LADDER[i + 1]
            continue
        i = sb.SCAL_LADDER.index(scal) if scal in sb.SCAL_LADDER else None
        if (span < sb.MIN_COUNTS and i is not None and i > 0
                and snr is not None and snr >= sb.PEAK_SNR_MIN_DB):
            scal = sb.SCAL_LADDER[i - 1]
            continue
        if span > 200 and i is not None and i + 1 < len(sb.SCAL_LADDER):
            scal = sb.SCAL_LADDER[i + 1]
            continue
        return best
    return best


RUNGS = {0.5: dict(span=210, snr=22.0),      # line visible, span>200 -> walk up
         1.0: dict(span=250, snr=None),      # clipped, nothing to see
         2.0: dict(span=250, snr=None)}


class Stub(object):
    _scal = 0.5

    def write(self, cmd):
        pass

    def query(self, cmd):
        return "%.4g" % self._scal if cmd.endswith(":SCAL?") else "0"


def run(fn):
    stub = Stub()
    saved = (sb.capture, sb.meas_vpp, sb.set_scale, sb.SCAL_LADDER,
             sb.line_near, sb.spectrum_of)
    sb.capture = lambda inst: (0.5e-9, _y(inst))
    sb.meas_vpp = lambda inst: None
    sb.set_scale = lambda inst, s: setattr(inst, "_scal", s)
    sb.SCAL_LADDER = (0.5, 1.0, 2.0)
    sb.line_near = lambda fr, sp, fc, tol, **kw: (
        (fc, RUNGS[stub._scal]["snr"]) if RUNGS[stub._scal]["snr"] is not None
        else (None, None))
    sb.spectrum_of = lambda y: np.ones(32)
    try:
        return fn(stub, 200e6, 0.5)
    finally:
        (sb.capture, sb.meas_vpp, sb.set_scale, sb.SCAL_LADDER,
         sb.line_near, sb.spectrum_of) = saved


def _y(inst):
    y = np.zeros(64)
    y[-1] = RUNGS[inst._scal]["span"]
    return y


def main():
    old = run(old_autoscale)
    new = run(sb.autoscale)
    print("OLD autoscale -> span %g, line %s"
          % (old["span"], "present" if old["f"] is not None else "NONE"))
    print("NEW autoscale -> span %g, line %s"
          % (new["span"], "present" if new["f"] is not None else "NONE"))
    ok = True
    if old["f"] is not None:
        print("FAIL the old code did NOT reproduce the bug -- the test proves "
              "nothing")
        ok = False
    else:
        print("PASS old code reports NO LINE where a line was measurable")
    if new["f"] is None:
        print("FAIL the new code still loses the line")
        ok = False
    else:
        print("PASS new code keeps it")
    print("\n%s" % ("REGRESSION TEST IS VALID" if ok else "TEST NOT VALID"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
