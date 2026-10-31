"""Second waveform: sine vs square, at one LO, same everything else.

The operator asked for measurements with different waveform types.  A square is
the useful second shape here for a reason beyond variety: it is a different
SPECTRUM, not just a different amplitude.  A 50% square at the same f_out has
odd harmonics at 3f, 5f, 7f... and no even ones, so the scope's acquisition can
be checked against a prediction the fixture cannot fake: if the shape really
changed at the DDS, the harmonic ladder appears; if only the level changed,
there are no new lines.

wave_sel is reg10[0] in the PL (board_app `wavesel`), so this also exercises
the RTL change end to end.

Judged on the RATIO of the 3rd harmonic to the fundamental at each shape, which
is independent of the fixture's frequency response only when the two lines are
close -- so 1.888 MHz and 5.664 MHz are compared against the sine's own ratio
as the baseline, not against an absolute theory value.
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
F_OFF = 1.875e6          # phase_inc 128 at fs = 60 MSPS


def capture_best(sc, lo, tries=2):
    best = None
    for _ in range(tries):
        xinc, y = sb.capture(sc)
        freqs = np.fft.rfftfreq(len(y), xinc)
        spec = sb.spectrum_of(y)
        f, snr = sb.line_near(freqs, spec, lo, sb.LIN_TOL_HZ)
        rec = (snr, f, spec, freqs, int(y.max() - y.min()))
        if best is None or (snr is not None
                            and (best[0] is None or snr > best[0])):
            best = rec
        if snr is not None and snr >= 30:
            break
        time.sleep(0.4)
    return best


def harmonic_table(spec, freqs, lo, scal, ks=(1, 2, 3, 4, 5, 6, 7)):
    rows = []
    for k in ks:
        rows.append((k, lo + k * F_OFF, lo - k * F_OFF))
    out = []
    for k, up, dn in rows:
        au = sb.line_amplitude_vpp(spec, freqs, up, scal)
        ad = sb.line_amplitude_vpp(spec, freqs, dn, scal)
        best = max([a for a in (au, ad) if a] or [0.0])
        out.append((k, up, dn, au, ad, best))
    return out


def main():
    board = sb.BoardCmd(sb.BOARD_APP, host=sb.BOARD_HOST)
    sc = sb.ReconnectingScope(pyvisa.ResourceManager(), sb.SCOPE_RESOURCE)
    sb.scope_prepare(sc, 3.125e-6, SCAL)
    sb.board_prepare_1r1t(board)
    sb.board_init(board, LO, 0)

    results = {}
    for ws in (0, 1):
        rb = board.cmd("wavesel %d" % ws)
        for line in rb.splitlines():
            if "wave_sel" in line or "square" in line or "sine" in line:
                print("  [board] %s" % line.strip())
        time.sleep(0.3)
        snr, f, spec, freqs, span = capture_best(sc, LO)
        print()
        print("=" * 70)
        print("waveform = %s   (span %d codes, line at %s, SNR %s dB)"
              % ("SQUARE" if ws else "SINE", span,
                 ("%.4f MHz" % (f / 1e6)) if f else "not found",
                 ("%.1f" % snr) if snr is not None else "n/a"))
        print("=" * 70)
        print("  %-3s %-11s %-13s %-13s %s"
              % ("k", "LO+k*1.875", "upper Vpp", "lower Vpp", "best Vpp"))
        tbl = harmonic_table(spec, freqs, LO, SCAL)
        results[ws] = tbl
        for k, up, dn, au, ad, best in tbl:
            print("  %-3d %-11.3f %-13s %-13s %s"
                  % (k, up / 1e6,
                     ("%.5f" % au) if au else "-",
                     ("%.5f" % ad) if ad else "-",
                     ("%.5f" % best) if best else "-"))
        ref = tbl[0][5]
        if ref:
            print("  ratios to the fundamental: %s"
                  % "  ".join("k%d=%.4f" % (k, b / ref)
                              for k, _, _, _, _, b in tbl[1:] if b))

    print()
    print("the sine's own harmonic ratios are the BASELINE: whatever the fixture")
    print("does to 3f and 5f relative to f, it does to both waveforms equally.")
    print("A square that is a square has to be visibly richer at odd k than the")
    print("sine is at the same k.  Comparing square-vs-sine at each k removes the")
    print("fixture's frequency response from the judgement.")
    s0, s1 = results[0], results[1]
    for k in (3, 5, 7):
        a = s0[k - 1][5]
        b = s1[k - 1][5]
        f0 = s0[0][5]
        f1 = s1[0][5]
        if a and b and f0 and f1:
            print("  k=%d: sine %.5f/%.5f = %.4f | square %.5f/%.5f = %.4f"
                  % (k, a, f0, a / f0, b, f1, b / f1))
    sc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
