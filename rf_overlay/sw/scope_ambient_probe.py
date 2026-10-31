"""Focused: what is at 75.6 MHz, and is it the source or the environment?

The previous inventory proved the fixture picks up ambient shortwave broadcast:
a fixed comb of lines at 2.176/2.624/3.040/3.904/4.352/4.768... MHz that is
present at every LO, every attenuation, AND with the DDS silent by phase_inc 0.
So the input is an antenna, not just a load.

C_NEG failed at 75.648 MHz.  This asks whether that line is source-related:
it must vanish when (i) the DDS is silenced, and (ii) the TX attenuation goes
to maximum -- and it must NOT move when the LO moves if it is ambient.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # these live beside scope_boundary.py

import numpy as np                                          # noqa: E402
import pyvisa                                               # noqa: E402
import scope_boundary as sb                                 # noqa: E402

SCAL = 0.2
LO = 80e6
WIN = (70e6, 90e6)


def report(sc, tag, tries=3):
    """Strongest line in the 70-90 MHz window, best of `tries` captures."""
    best = None
    for _ in range(tries):
        xinc, y = sb.capture(sc)
        freqs = np.fft.rfftfreq(len(y), xinc)
        spec = sb.spectrum_of(y)
        band = (freqs >= WIN[0]) & (freqs <= WIN[1])
        idx = np.nonzero(band)[0]
        idx = idx[(idx > 1) & (idx < len(spec) - 2)]
        floor = float(np.median(spec[idx]))
        loc = idx[(spec[idx] > spec[idx - 1]) & (spec[idx] >= spec[idx + 1])]
        if len(loc) == 0:
            continue
        k = loc[np.argmax(spec[loc])]
        rec = (float(freqs[k]), 20.0 * np.log10(spec[k] / floor),
               int(y.max() - y.min()))
        if best is None or rec[1] > best[1]:
            best = rec
        time.sleep(0.3)
    if best is None:
        print("  %-34s no local maximum found" % tag)
        return
    f, s, span = best
    tag_f = ""
    if abs(f - LO) < 0.3e6:
        tag_f = "  <- LO"
    elif abs(abs(f - LO) - 1.888e6) < 0.5e6:
        tag_f = "  <- sideband"
    print("  %-34s %8.4f MHz  %+6.1f dB  (span %d)" % (tag, f / 1e6, s, span)
          + tag_f)


def main():
    board = sb.BoardCmd(sb.BOARD_APP, host=sb.BOARD_HOST)
    sc = sb.ReconnectingScope(pyvisa.ResourceManager(), sb.SCOPE_RESOURCE)
    sb.scope_prepare(sc, 3.125e-6, SCAL)
    sb.board_prepare_1r1t(board)
    sb.board_init(board, LO, 0)

    print("strongest line in %.0f-%.0f MHz:" % (WIN[0] / 1e6, WIN[1] / 1e6))
    print()
    print("  A. source ON, no attenuation (the line should BE the sideband)")
    sb.board_init(board, LO, 0)
    board.cmd("phase_inc 128")
    time.sleep(0.8)
    report(sc, "phase_inc 128, atten 0")

    print()
    print("  B. source ON, 30 dB of attenuation")
    sb.board_init(board, LO, 30000)
    time.sleep(0.8)
    report(sc, "phase_inc 128, atten 30000")

    print()
    print("  C. source ON, maximum attenuation")
    sb.board_init(board, LO, 70000)
    time.sleep(0.8)
    report(sc, "phase_inc 128, atten 70000")

    print()
    print("  D. DDS SILENCED, maximum attenuation  <-- if a line survives")
    print("     here it is not the source, whatever it is")
    board.cmd("phase_inc 0")
    time.sleep(0.8)
    report(sc, "phase_inc 0, atten 70000")

    print()
    print("  E. DDS silenced, attenuation back to 0")
    sb.board_init(board, LO, 0)
    board.cmd("phase_inc 0")
    time.sleep(0.8)
    report(sc, "phase_inc 0, atten 0")

    print()
    print("  restoring phase_inc 128, atten 0")
    board.cmd("phase_inc 128")
    sc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
