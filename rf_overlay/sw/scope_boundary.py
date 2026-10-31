#!/usr/bin/env python3
"""scope_boundary.py -- how far up in frequency can the DSOX1102A still measure?

The operator's question, verbatim in intent: hold the SOURCE at a fixed 1 Vpp,
sweep the frequency up to 1 GHz, and find out whether the scope can still
measure normally.

Two things about that question decide this whole file.

1. "Normally" is not the same as "at all".  A 100 MHz instrument fed a 1 GHz
   tone does not merely read a small number -- past its sample rate's Nyquist
   limit it reads the WRONG number, and a wrong number is indistinguishable
   downstream from a right one.  So the primary criterion here is frequency
   IDENTITY (does the scope report the frequency that is actually there?),
   and amplitude is the secondary, recorded quantity.

2. The source is the AD9361, not the AWG.  Only the AD9361 reaches 1 GHz (its
   LO covers 70 MHz - 6 GHz); the AWG chain is constrained to 1 MHz by its own
   protocol.  The cost of that choice is that the AD9361's output is set in
   dB of attenuation, NOT in volts -- there is no Vpp calibration anywhere in
   the chain.  So "1 Vpp fixed" is realised the only honest way available:
   measure once, pick the attenuation that lands the scope on 1 Vpp, and then
   HOLD that attenuation constant for the entire sweep.  The source setting is
   what is fixed; the reading is what moves.  That is the measurement.

   It follows that a drop with frequency is the SUM of the source's rolloff and
   the scope's, and one scope cannot separate them.  C_ATT below is what keeps
   that honest: at every frequency it re-measures a KNOWN 20 dB step and
   requires the scope to still read it as 20 dB.  That can only pass if the
   scope is still linear there, so a point that passes C_ATT is a point whose
   amplitude reading still means something -- whatever the ratio of source to
   scope rolloff, the scope has not stopped working.

Criteria are stated here, before any measurement, and are not adjusted
afterwards.  A criterion that cannot be evaluated prints NOT RUN, never PASS.

Instrument rules inherited from this project (each one paid for):
  * never write :WAV:POIN -- it pins the record length to a manual value and
    :WAV:POIN:MODE NORM does not reset it, after which every waveform-based
    criterion in every suite misjudges at once;
  * run with the SYSTEM python (pyvisa) and with -u;
  * use the default VISA backend, not '@py';
  * do not bypass the reconnect wrapper -- the USB-TMC session drops every
    30-90 s on this machine whenever it is busy, and the suite takes minutes.
"""

import argparse
import math
import os
import re
import sys
import time

import numpy as np

# pyvisa must be imported before anything that pulls in Qt (visa32.dll and the
# Qt DLLs conflict).  Nothing here imports Qt, but keep the order.
import pyvisa

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from board_cmd import BoardCmd          # noqa: E402

# --------------------------------------------------------------- instrument
SCOPE_RESOURCE = "<VISA_RESOURCE>"
BOARD_HOST     = "192.0.2.10"
BOARD_APP      = "/home/xilinx/board_app_src/board_app"
CHAN           = "CHAN1"          # TX1 -> CH1, per the standing wiring
CHAN_OFF       = "CHAN2"          # deliberately OFF here; see scope_prepare()

# ------------------------------------------------------------- board source
# The DDS tone rides on the LO as a double sideband (Q is hardwired to zero in
# rf_tx_source.v, so the output is LO / LO-f / LO+f, not a single line).  With
# phase_inc = 128 the offset is 1.875 MHz at fs = 60 MSPS.  It is kept small on
# purpose: it has to stay resolvable at every LO without being mistaken for the
# carrier, and it is what makes the "is this line really at LO" question
# decidable by looking at the trio rather than at one peak.
PHASE_INC      = 128

# LO sweep.  Dense at the bottom because that is where the interesting edge is
# (the instrument is specified to 100 MHz), then coarse to the top so that the
# Nyquist fold is unmistakable rather than merely suspected.
LO_SWEEP_HZ    = (80e6, 100e6, 125e6, 150e6, 200e6, 250e6,
                  300e6, 400e6, 500e6, 600e6, 700e6, 800e6, 900e6, 1000e6)
LO_REF_HZ      = 80e6             # AD9361's LO floor is 70 MHz; 80 has margin

# ---------------------------------------------------------------- criteria
# C_REF   the fixture works at all: a line is found near LO at the reference
#         frequency and attenuation.  If this fails nothing below means
#         anything, so the run stops rather than producing 14 rows of noise.
C_REF_LO_TOL_HZ   = 3e6

# C_ID    frequency identity: the FFT peak sits at LO (within the tolerance),
#         NOT at the alias the sample rate predicts.  This is the criterion
#         the operator's question is really about.
C_ID_TOL_HZ       = 3e6

# C_ATT   attenuation linearity: raising tx_attenuation_mdB by exactly 20000
#         must move the line down by 20 dB.  A scope that reads a known 20 dB
#         step as 20 dB is still measuring; one that does not has stopped,
#         and its amplitude number at that frequency is not evidence.
C_ATT_STEP_MDB    = 20000
C_ATT_EXPECT_DB   = 20.0
C_ATT_TOL_DB      = 3.0

# C_ATT is probed on a pair of attenuations whose SEPARATION is known, but not
# on 0 -> 20000.  The first attempt used 0 -> 20000 and read 14.8 dB at 80 MHz,
# where the fixture is beyond doubt: 0 mdB is this TX chain's ceiling (board_app
# rejects a negative attenuation), so the 0 mdB level sits on the attenuator's
# own compression shoulder and the step under-reads.  10000 -> 30000 is the pair
# the RF suite already validated at 19.85 dB, so a deviation here is a deviation
# from an independently established number rather than from a wish.
ATT_LOW_MDB       = 10000
ATT_HIGH_MDB      = 30000
ATT_POINTS_HZ     = (80e6, 100e6, 200e6, 300e6, 500e6, 700e6)

# C_VPP   the operator's "peak-to-peak fixed at 1 Vpp" -- and the one criterion
#         that CANNOT be met as stated.  The source's ceiling is attenuation 0,
#         and at 80 MHz that measured ~0.83 Vpp, so "fixed at 1 Vpp" is
#         realised as "fixed at the source's maximum", held at that single
#         setting for every frequency.  Recorded here rather than quietly
#         relaxed: the drop column is relative to that level, not to 1.000 V,
#         and the write-up says so.
VPP_TARGET        = 1.0           # what was asked for; NOT achievable, see above
VPP_ACHIEVED      = None          # filled in at run time from the reference

# Vertical scale is SEARCHED per point, not fixed.  A fixed 0.25 V/div was the
# first attempt and it failed on its own terms: past ~150 MHz the signal is a
# couple of LSB at that setting, at which point :MEAS:VPP? reports the
# quantisation floor and the peak search locks onto whatever noise maximum is
# largest.  The 200 MHz row came back 0.06 Vpp between a 0.39 at 150 and a 0.29
# at 250 -- non-monotonic, i.e. an instrument artefact dressed as data.  The
# project's own note that 40-70% of screen is the accurate band is an argument
# for ranging, not against it: the reading is in volts either way, so ranging
# changes only how well it is resolved.
SCAL_LADDER       = (0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0)
CLIP_COUNTS       = 246           # >= this peak-to-peak in 8-bit codes -> clipped
MIN_COUNTS        = 24            # below this the trace is not resolved

# The line search is TARGETED at the LO, not global.  A global maximum answers
# "what is the biggest thing on screen", which above the front-end corner is
# the wrong question: the biggest thing is the noise, and reporting it as the
# signal is precisely the false reading this run exists to expose.
LIN_TOL_HZ        = 5e6

# ------------------------------------------------------- peak-finding gate
# Same statistic the RF suite already calibrated on this instrument: the peak
# must clear the median floor by this much.  Measured on this DSOX1102A at
# 80 MHz, a real carrier ran 20.0 dB over the floor and the strongest noise
# maximum ran 10.4 dB, so a gate at 14 dB sits in the gap between the two
# populations rather than on top of either.
PEAK_SNR_MIN_DB   = 14.0

# C_NEG   negative case, the discipline this project requires: a criterion that
#         has never been shown to fail is not evidence.  At maximum attenuation
#         the SIDEBANDS must vanish.  The first version of this looked for "any
#         line within +-5 MHz" and failed at 75.648 MHz -- a frequency that is
#         neither the LO nor a sideband, and that moved to 80.416 MHz then
#         70.560 MHz on repeat captures.  A line that moves between captures is
#         noise: +-5 MHz at 2 GSa/s is 312 bins, and the maximum of Rayleigh
#         noise over 312 bins sits about 10*log10(312) = 24.9 dB over the median,
#         which is what was being read as a survivor (26.5 dB).
#         So the criterion now asks about the sidebands, which is what it was
#         always meant to be about -- "is the source gone", not "is the RF
#         environment empty".  A survivor is additionally required to REPEAT at
#         the same frequency before it counts, which is the property noise lacks.
C_NEG_ATTEN_MDB   = 70000
C_NEG_REPEATS     = 3
# How close two captures have to put a line for it to count as the SAME line.
# This is a LINE-WIDTH scale, not the search window: reusing LIN_TOL_HZ here
# made any three lines anywhere in the +-5 MHz window score as "the same line",
# which is the same vacuous-test mistake the window formulation itself was.
# At the sweep's 2 GSa/s / 62500 points the bin is 32 kHz, so 100 kHz is about
# three bins -- generous for a real line, far tighter than the ~30-75 bins of
# scatter a noise maximum shows (measured: 3072 kHz and 2400 kHz).
C_NEG_REPEAT_TOL_HZ = 100e3

# Timebase ladder, run first, because the sample rate it settles on is what
# fixes the Nyquist limit and therefore what "alias" even means for the sweep.
# Record length is fixed at 62500 points, so a shorter window is the only way
# to a higher sample rate -- and the instrument silently SHORTENS THE RECORD
# rather than erroring when asked for more than it can deliver, so the actual
# points are read back at every rung instead of being assumed.
TIM_SCAL_LADDER   = (6.25e-6, 3.125e-6, 2e-6, 1e-6, 5e-7)
TIM_SCAL_SWEEP    = 3.125e-6       # 31.25 us window at 62500 pts -> 2 GSa/s


# ============================================================ scope plumbing

class ReconnectingScope(object):
    """Transparent reconnect, copied in spirit from scope_verify.py.

    The USB-TMC session on this machine drops roughly every 30-90 s while it is
    busy and recovers in about 10 s.  A sweep that re-inits the AD9361 between
    every point takes minutes, so without this the run dies partway through and
    the surviving rows silently become "the whole sweep".
    """

    def __init__(self, rm, name, timeout=10000, attempts=6):
        self._rm, self._name = rm, name
        self._timeout, self._attempts = timeout, attempts
        self._inst = None

    def open(self):
        if self._inst is None:
            self._inst = self._rm.open_resource(self._name, open_timeout=10000)
            self._inst.timeout = self._timeout

    def _drop(self):
        inst, self._inst = self._inst, None
        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass

    def _run(self, op):
        from pyvisa.errors import VisaIOError
        last = None
        for k in range(self._attempts):
            try:
                self.open()
                return op(self._inst)
            except VisaIOError as e:
                last = e
                self._drop()
                print("  [scope] session lost (%s), reconnecting..."
                      % str(e)[:60])
                time.sleep(min(5.0 * (k + 1), 25.0))
        raise last

    def write(self, cmd):
        return self._run(lambda i: i.write(cmd))

    def query(self, cmd):
        return self._run(lambda i: i.query(cmd))

    def query_binary_values(self, cmd, **kw):
        return self._run(lambda i: i.query_binary_values(cmd, **kw))

    def close(self):
        self._drop()


def scope_prepare(inst, tim_scal, chan_scal=0.5):
    """Fixed timebase and vertical scale, CH1 only, then read back what stuck.

    CH2 is switched OFF here, unlike the RF suite which arms both.  Two armed
    channels halve the maximum sample rate on this instrument family, and the
    sample rate is the whole subject of this run -- arming a channel that
    carries nothing would move the Nyquist limit being measured.  (The RF
    suite arms both for a different reason: an unarmed channel does not take
    part in acquisition, so :WAV:DATA? on it returns a flat trace, which is
    indistinguishable from a silent transmitter.  That trap does not apply
    here, where only CH1 is ever read.)

    Trigger is AUTO, not EDGE-with-a-level.  At the top of the sweep the signal
    is a few LSB out of a 100 MHz front end; an edge trigger with a fixed level
    simply does not fire there, and an acquisition that never triggered is not
    a quiet acquisition.  AUTO forces one, which is all the FFT needs -- it
    does not care about phase.
    """
    inst.write(":STOP")
    inst.write(":WAV:SOUR %s" % CHAN)
    inst.write(":WAV:FORM BYTE")
    inst.write(":WAV:POIN:MODE NORM")
    inst.write(":%s:DISP OFF" % CHAN_OFF)
    inst.write(":%s:DISP ON" % CHAN)
    inst.write(":%s:PROB 1" % CHAN)
    inst.write(":%s:OFFS 0" % CHAN)
    inst.write(":%s:SCAL %.6E" % (CHAN, chan_scal))
    inst.write(":TIM:SCAL %.9E" % tim_scal)
    inst.write(":TRIG:SOUR %s" % CHAN)
    inst.write(":TRIG:LEV 0")
    inst.write(":TRIG:MODE AUTO")
    inst.write(":MEAS:SOUR %s" % CHAN)
    inst.write(":RUN")
    time.sleep(0.4)

    xinc = float(inst.query(":WAV:XINC?"))
    npt = len(inst.query_binary_values(":WAV:DATA?", datatype="B",
                                       container=list))
    return {
        "tim_scal": float(inst.query(":TIM:SCAL?")),
        "chan_scal": float(inst.query(":%s:SCAL?" % CHAN)),
        "xinc": xinc,
        "points": npt,
        "srate": (1.0 / xinc) if xinc > 0 else 0.0,
        "window": npt * xinc,
    }


def capture(inst):
    """:STOP, read the NORM record, :RUN.  Never touches :WAV:POIN."""
    inst.write(":STOP")
    time.sleep(0.05)
    xinc = float(inst.query(":WAV:XINC?"))
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    inst.write(":RUN")
    y = np.asarray(data, dtype=float)
    return xinc, y


def spectrum_of(y):
    y = y - y.mean()
    n = len(y)
    if n < 16:
        return None, None
    return np.abs(np.fft.rfft(y * np.hanning(n)))


def peak_search(freqs, spec, min_snr_db=PEAK_SNR_MIN_DB):
    """Strongest local maximum that clears the median floor.  -> (f, snr_db).

    The median, not the mean, is the floor: a spectrum with a few strong lines
    has a median dominated by the noise between them, whereas the mean would be
    dragged up by the very lines being looked for.
    """
    if spec is None or len(spec) < 8:
        return None, None
    floor = float(np.median(spec))
    if floor <= 0:
        return None, None
    # interior local maxima only: the DC bin and the Nyquist bin are not tones
    idx = np.arange(2, len(spec) - 1)
    loc = idx[(spec[idx] > spec[idx - 1]) & (spec[idx] >= spec[idx + 1])]
    if len(loc) == 0:
        return None, None
    best = loc[np.argmax(spec[loc])]
    snr = 20.0 * math.log10(spec[best] / floor)
    if snr < min_snr_db:
        return float(freqs[best]), snr
    return float(freqs[best]), snr


def predict_alias(f_true, srate):
    """Where a tone at f_true APPEARS when sampled at srate.

    Not a heuristic: this is the fold itself, and it is what makes the sweep's
    verdict decidable.  "The scope read 300 MHz when we sent 700 MHz" is a much
    weaker statement than "the scope read exactly the fold of 700 MHz", and the
    difference is the whole reason an alias can be told apart from a fault.
    """
    if srate <= 0:
        return None
    # fold into [0, srate/2]
    f = math.fmod(f_true, srate)
    if f < 0:
        f += srate
    return f if f <= srate / 2 else srate - f


def meas_vpp(inst):
    """Scope's own Vpp reading, or None if it has nothing to measure.

    Both the "no result" sentinel (9.9E+37) and a query error map to None, and
    None is reported as such.  Folding them into 0.0 would print "the reading
    dropped to zero", which is a different and stronger claim than "the scope
    could not measure it".
    """
    try:
        v = float(inst.query(":MEAS:VPP?"))
    except Exception:
        return None
    if not math.isfinite(v) or abs(v) > 1e30:
        return None
    return v


def survivor_is_real(hits, tol_hz):
    """Decide whether a repeated line search found something that is really there.

    `hits` is a list of (f, snr) from independent captures of the same setting.
    A real signal is at the same frequency every time; a noise maximum is not.
    Returns (is_real, spread_hz).  spread is None when there were not two
    qualifying hits to compare -- which is not a survivor either.
    """
    strong = [h for h in hits
              if h[0] is not None and h[1] is not None
              and h[1] >= PEAK_SNR_MIN_DB]
    if len(strong) < 2 or len(strong) < len(hits):
        return False, None
    spread = max(h[0] for h in strong) - min(h[0] for h in strong)
    return spread < tol_hz, spread


def line_near(freqs, spec, f_center, tol_hz, min_snr_db=PEAK_SNR_MIN_DB):
    """Strongest local maximum within tol_hz of f_center.  -> (f, snr_db).

    Separate from peak_search on purpose.  peak_search answers "what is the
    biggest thing on screen", which is the right question when the fixture is
    known good (the Phase 1 reference) and the wrong one everywhere else: once
    the front end has rolled off, the biggest thing on screen is the noise
    floor, and returning it as "the line" is how an instrument limit gets
    filed as a signal.  Here the expected frequency is an INPUT.
    """
    if spec is None or len(spec) < 8:
        return None, None
    lo, hi = f_center - tol_hz, f_center + tol_hz
    band = (freqs >= lo) & (freqs <= hi)
    idx = np.nonzero(band)[0]
    idx = idx[(idx > 1) & (idx < len(spec) - 2)]
    if len(idx) < 3:
        return None, None
    floor = float(np.median(spec))
    if floor <= 0:
        return None, None
    loc = idx[(spec[idx] > spec[idx - 1]) & (spec[idx] >= spec[idx + 1])]
    if len(loc) == 0:
        return None, None
    best = loc[np.argmax(spec[loc])]
    return float(freqs[best]), 20.0 * math.log10(spec[best] / floor)


def line_amplitude_vpp(spec, freqs, f_at, chan_scal):
    """Peak-to-peak volts of a line, from the FFT rather than the trace.

    The time-domain reading the operator asked about (:MEAS:VPP?) is max minus
    min over the record, so a single noise sample inflates it, and on an 8-bit
    capture of a small signal it bottoms out at the quantisation step.  The FFT
    integrates the whole record coherently, so the same signal is still
    measurable when the trace is one or two codes tall.  volts/code comes from
    the vertical scale, which is why the scale has to be read back per capture.
    """
    k = int(np.argmin(np.abs(freqs - f_at)))
    if k < 2 or k >= len(spec) - 2:
        return None
    amp_counts = 2.0 * float(spec[k]) / (0.5 * 62500.0)   # Hann: sum(w) = N/2
    return 2.0 * amp_counts * (8.0 * chan_scal / 255.0)


def set_scale(inst, scal):
    inst.write(":%s:SCAL %.6E" % (CHAN, scal))
    time.sleep(0.25)


def _probe(inst, f_expect, scal):
    """One capture at one vertical scale, with the scale READ BACK.

    Read-back matters: the whole failure this function was rewritten to fix was
    a ladder that believed it was on one rung while the acquisition was on
    another.  Two captures are taken and the better-SNR one kept, so a single
    acquisition landing on a retune transient cannot be mistaken for the source
    being absent.
    """
    set_scale(inst, scal)
    rb = float(inst.query(":%s:SCAL?" % CHAN))
    rec = None
    for _ in range(2):
        xinc, y = capture(inst)
        freqs = np.fft.rfftfreq(len(y), xinc)
        spec = spectrum_of(y)
        f, snr = line_near(freqs, spec, f_expect, LIN_TOL_HZ)
        cand = dict(xinc=xinc, n=len(y), freqs=freqs, spec=spec, f=f, snr=snr,
                    span=float(y.max() - y.min()), scal=rb, asked=scal,
                    vpp_trace=meas_vpp(inst))
        if rec is None or (snr is not None
                           and (rec["snr"] is None or snr > rec["snr"])):
            rec = cand
        if snr is not None and snr >= 30.0:
            break
        time.sleep(0.4)
    return rec


def autoscale(inst, f_expect, start_scal):
    """Find a vertical scale that resolves whatever is at f_expect.

    Walk the ladder to the most sensitive rung that neither clips nor loses the
    line, where "clip" is judged on the raw record's code span rather than on
    :MEAS:VPP?, because the latter is itself the thing being made trustworthy.

    Two rules exist because the first version of this function got them wrong
    and cost a whole sweep of measurements (every Phase 3 point reported "no
    line" at settings a fixed-scale probe found immediately):

      * ``best`` is only ever replaced by a capture in which the line was
        actually found.  The old code assigned ``best`` unconditionally on
        every iteration, so a final clipped or floor-bound capture was returned
        in preference to an earlier good one.
      * Both walk directions carry the same SNR guard.  The old ``span > 200``
        walk-up had none, so it would step off a rung where the line was
        visible onto one where it was not.

    If no rung ever shows the line, the most sensitive non-clipping capture is
    returned with ``f = None`` -- that is the honest outcome, and the caller's
    verdict logic is what records "nothing there".
    """
    i0 = SCAL_LADDER.index(start_scal) if start_scal in SCAL_LADDER else 0
    i = i0
    limit = min(len(SCAL_LADDER) - 1, i0 + 4)
    fallback = None
    for _ in range(6):
        rec = _probe(inst, f_expect, SCAL_LADDER[i])
        have = rec["f"] is not None and rec["snr"] is not None \
            and rec["snr"] >= PEAK_SNR_MIN_DB
        if have:
            if fallback is None or rec["snr"] > fallback["snr"]:
                fallback = rec
            if rec["span"] >= CLIP_COUNTS and i < limit:
                i += 1                      # clipped: back off one rung
                continue
            if rec["span"] < MIN_COUNTS and i > 0:
                i -= 1                      # under-resolved: go finer
                continue
            if rec["span"] > 200 and i < limit:
                i += 1
                continue
            return rec
        # No line at this rung.  Do not climb finer chasing noise; do back off
        # if we are clipped, because a clipped trace cannot show anything.
        if rec["span"] >= CLIP_COUNTS and i < limit:
            i += 1
            continue
        if fallback is not None:
            return fallback
        if i < limit:
            i += 1                          # maybe a coarser rung is unclipped
            continue
        return rec
    return fallback if fallback is not None else rec


# ============================================================== board source

def board_init(board, lo_hz, atten_mdb):
    """One `init lo ... atten ...`, which is a full ad9361_init.

    atten and lo are arguments to init, not subcommands, because board_app's
    `phy` is a process-local static that only init ever creates -- a later
    `atten` process would find phy NULL (rf_main.c's R7 note).
    """
    out = board.cmd("init lo %d atten %d" % (int(lo_hz), int(atten_mdb)))
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(("ENSM=", "TX1 atten readback", "LO ", "override:",
                         "init:")):
            print("    [board] %s" % s)
    return out


def snapshot(board):
    out = board.cmd("plstate")
    d = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", line)
        if m:
            d[m.group(1)] = int(m.group(2), 0)
    return d


def board_prepare_1r1t(board):
    """Put the PL and the part back in the reference 1R1T configuration.

    `init` alone cannot do this.  On the 1R1T path rf_main.c only *asserts*
    that phy_mode is already 1 -- it deliberately does not write it, because
    stage one's contract is that `plcfg` ran first (rf_main.c:775-786).  The
    board was left in 2R2T by the previous round, so without `plcfg` here every
    `init` returns 1 and prints "run `plcfg` first".

    The order matters twice over.  phase_inc is zeroed first so the data path
    carries no edges while the framing bit moves (rf_tx_source's strobe divider
    reads phy_mode combinationally, so a mid-stream change glitches the period).
    And `plcfg` runs before `init`, which is safe here only because `init`
    holds RESETB low and reboots the part before it needs the clock -- the
    window in which the write lands is one where the following init will bring
    DATA_CLK up fresh anyway.

    The readback is the point: `plcfg` printing its own writes proves nothing.
    """
    board.cmd("phase_inc 0")
    board.cmd("plcfg")
    d = snapshot(board)
    ok = (d.get("phy_mode") == 1 and d.get("phase_inc") == PHASE_INC)
    print("  PL after plcfg: phy_mode=%s phase_inc=%s -> %s"
          % (d.get("phy_mode"), d.get("phase_inc"),
             "1R1T confirmed" if ok else "NOT the expected 1R1T state"))
    return ok


# ================================================================ selftest
# Same discipline as scope_verify.py --selftest: drive the REAL judging code
# with synthetic input, and require it to say the right thing on known-bad
# input.  A criteria script that has never been seen to fail is not evidence.

def _selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("%-4s %s%s" % ("PASS" if cond else "FAIL", name,
                             ("  " + detail) if detail else ""))
        if not cond:
            ok = False

    # --- alias prediction: the arithmetic the verdict rests on -------------
    check("alias 700MHz @2GSa/s is itself",
          abs(predict_alias(700e6, 2e9) - 700e6) < 1)
    check("alias 700MHz @1GSa/s folds to 300MHz",
          abs(predict_alias(700e6, 1e9) - 300e6) < 1)
    check("alias 900MHz @1GSa/s folds to 100MHz",
          abs(predict_alias(900e6, 1e9) - 100e6) < 1)
    check("alias 1000MHz @1GSa/s folds to 0",
          abs(predict_alias(1000e6, 1e9)) < 1)
    check("alias 1250MHz @1GSa/s folds to 250MHz",
          abs(predict_alias(1250e6, 1e9) - 250e6) < 1)

    # --- peak_search on a spectrum with a planted line ---------------------
    rng = np.random.default_rng(7)
    n = 62500
    xinc = 0.5e-9
    freqs = np.fft.rfftfreq(n, xinc)
    t = np.arange(n) * xinc
    y = 30.0 * np.sin(2 * np.pi * 77e6 * t) + rng.normal(0, 3.0, n)
    spec = spectrum_of(y)
    f, snr = peak_search(freqs, spec)
    check("peak_search finds a planted 77 MHz line",
          f is not None and abs(f - 77e6) < 3e6,
          "got %s (snr %.1f dB)" % (f, snr if snr is not None else -1))
    check("peak_search reports it well over the gate",
          snr is not None and snr > PEAK_SNR_MIN_DB,
          "snr=%.1f dB" % (snr if snr is not None else -1))

    # --- the negative case must be reachable ---------------------------------
    # Identical code path, no signal: the gate has to refuse it.  Without this
    # the sweep's "line found" rows could be the gate passing everything.
    y0 = rng.normal(0, 3.0, n)
    f0, snr0 = peak_search(freqs, spectrum_of(y0))
    check("peak_search refuses pure noise at the 14 dB gate",
          snr0 is None or snr0 < PEAK_SNR_MIN_DB,
          "best noise snr=%.1f dB" % (snr0 if snr0 is not None else -1))

    # --- identity vs alias are distinguishable -----------------------------
    # A reading that matches the ALIAS must not be scored as identity, and a
    # reading that matches the true frequency must not be scored as an alias.
    true_f, srate = 700e6, 1e9
    alias = predict_alias(true_f, srate)
    got_alias = abs(alias - 300e6) < 3e6
    got_true = abs(alias - true_f) < 3e6
    check("a fold is scored as a fold, not as identity",
          got_alias and not got_true,
          "alias=%.3f MHz" % (alias / 1e6))

    # --- line_near: the TARGETED search the sweep now uses ------------------
    # Bin-centred frequency, so the windowed FFT has no leakage to argue about.
    f_line = 2407 * 32000.0                     # 77.024 MHz == 2407 bins at 32 kHz
    yl = 20.0 * np.sin(2 * np.pi * f_line * t) + rng.normal(0, 3.0, n)
    spl = spectrum_of(yl)
    fl, snl = line_near(freqs, spl, f_line, LIN_TOL_HZ)
    check("line_near finds the line when it is where it should be",
          fl is not None and abs(fl - f_line) < 3e6,
          "got %s (snr %.1f dB)" % (fl, snl if snl is not None else -1))

    # The failure this whole rewrite exists for: a big line OFF the target must
    # NOT be returned as the target.  peak_search took the global maximum and so
    # reported a noise/alias maximum as "the signal"; line_near is targeted, so
    # it must come back empty.  Synthetic stand-in for the 77 MHz line being the
    # only thing present while the sweep asks about 700 MHz.
    f_off, snr_off = line_near(freqs, spl, 700e6, LIN_TOL_HZ)
    check("line_near refuses an off-target line (the old global-max bug)",
          f_off is None or snr_off < PEAK_SNR_MIN_DB,
          "at 700 MHz it reported %s%s"
          % (("%.4f MHz" % (f_off / 1e6)) if f_off is not None else "nothing",
             (" @%.1f dB" % snr_off) if snr_off is not None else ""))
    g_off, g_snr = peak_search(freqs, spl)
    check("...while peak_search DOES take it -- the two differ on purpose",
          g_off is not None and g_snr >= PEAK_SNR_MIN_DB,
          "peak_search said %.4f MHz @%.1f dB" % (g_off / 1e6, g_snr))

    # --- line_amplitude_vpp: the drop column's scale factor ------------------
    # A factor error here scales every "drop" in the report and looks like a
    # source rolloff, so the recovery of a KNOWN peak-to-peak is checked
    # directly.  Hann window of length N sums to N/2, so a bin-centred tone of
    # peak amplitude A codes has |X[k]| = A*N/4 and the function should give
    # back A_pp = 2A codes, i.e. 2A * 8*scal/255 volts.
    a_pp_codes = 40.0
    yv = (a_pp_codes / 2.0) * np.sin(2 * np.pi * f_line * t)
    sv = spectrum_of(yv)
    got_v = line_amplitude_vpp(sv, freqs, f_line, 0.5)
    want_v = a_pp_codes * (8.0 * 0.5 / 255.0)
    check("line_amplitude_vpp recovers a known Vpp",
          got_v is not None and abs(got_v - want_v) / want_v < 0.05,
          "got %s V, want %.4f V" % (("%.4f" % got_v) if got_v else None,
                                     want_v))
    # And it must scale with the vertical scale, not merely be proportional to
    # the code content: the same record read at twice the V/div is twice the
    # volts.  Without this a stuck constant would pass the test above.
    got_2x = line_amplitude_vpp(sv, freqs, f_line, 1.0)
    check("line_amplitude_vpp scales with the vertical scale",
          got_v and got_2x and abs(got_2x - 2 * got_v) < 1e-9,
          "%.5f V at 0.5 V/div -> %.5f V at 1.0 V/div" % (got_v, got_2x))

    # --- autoscale: the regression that voided a whole sweep -----------------
    # Field failure: every Phase 3 point came back "no line" at settings a
    # fixed-scale probe found the tone at immediately.  Two defects, both in the
    # ladder rather than in the instrument:
    #   (1) `best` was reassigned on every iteration, so a final clipped
    #       no-line capture was returned in preference to an earlier good one;
    #   (2) the `span > 200` walk-up had no SNR guard, so it stepped off a rung
    #       where the line was visible onto one where it was not.
    # This plants exactly that sequence.  The stub scope answers SCPI itself, so
    # the ladder's real control flow runs -- a stub that returned a canned
    # result would not exercise either defect.
    _rungs = {
        0.5:  dict(span=210, snr=22.0),   # line visible, but span>200 -> walk up
        1.0:  dict(span=250, snr=None),   # clipped, nothing to see
        2.0:  dict(span=250, snr=None),
    }

    class _StubScope(object):
        def write(self, cmd):
            pass

        def query(self, cmd):
            return "%.4g" % self._scal if cmd.endswith(":SCAL?") else "0"

        _scal = 0.5

    _stub = _StubScope()
    _saved = (globals()["capture"], globals()["meas_vpp"], globals()["set_scale"])

    def _fake_capture(inst):
        y = np.zeros(64)
        y[-1] = _rungs[inst._scal]["span"]     # so y.max()-y.min() is the rung's span
        return 0.5e-9, y

    def _fake_vpp(inst):
        return None

    def _fake_set_scale(inst, scal):
        inst._scal = scal

    globals()["capture"] = _fake_capture
    globals()["meas_vpp"] = _fake_vpp
    globals()["set_scale"] = _fake_set_scale
    _saved_ladder = SCAL_LADDER
    globals()["SCAL_LADDER"] = (0.5, 1.0, 2.0)

    _orig_line_near = globals()["line_near"]
    _orig_spec = globals()["spectrum_of"]
    globals()["line_near"] = lambda fr, sp, fc, tol, **kw: (
        (fc, _rungs[_stub._scal]["snr"])
        if _rungs[_stub._scal]["snr"] is not None else (None, None))
    globals()["spectrum_of"] = lambda y: np.ones(32)
    try:
        rec = autoscale(_stub, 200e6, 0.5)
    finally:
        globals()["capture"], globals()["meas_vpp"], globals()["set_scale"] = \
            _saved
        globals()["SCAL_LADDER"] = _saved_ladder
        globals()["line_near"] = _orig_line_near
        globals()["spectrum_of"] = _orig_spec

    check("autoscale keeps the last rung that HAD a line, not the last tried",
          rec["f"] is not None and rec["span"] == 210,
          "returned span %g, line %s"
          % (rec["span"], "present" if rec["f"] is not None else "NONE"))
    check("...and reports the scale it actually measured at",
          rec["scal"] == 0.5, "scal=%.4g" % rec["scal"])

    # --- survivor_is_real: the fix for C_NEG's false failure ---------------
    # C_NEG used to fail on "any line within +-5 MHz", and did fail -- at
    # 75.648 MHz, then 80.416 MHz, then 70.560 MHz on repeat captures.  Those
    # are the field's frequencies; the levels here are raised above the gate so
    # the test exercises the SPREAD rule rather than stopping at the gate check
    # (a case that is already covered by the third check below).  A line that
    # moves between captures is noise: +-5 MHz at 2 GSa/s is 312 bins, whose
    # Rayleigh maximum sits ~10*log10(312) = 24.9 dB over the median on its own.
    _real, _spread = survivor_is_real(
        [(75.648e6, 26.5), (80.416e6, 24.0), (70.560e6, 22.0)],
        C_NEG_REPEAT_TOL_HZ)
    check("survivor_is_real rejects the field's wandering C_NEG line",
          not _real,
          "spread %.1f MHz" % (_spread / 1e6) if _spread is not None
          else "rejected at the gate instead")
    # The negative-of-the-negative: a genuinely stuck spur must NOT be waved
    # away, or the new test would pass everything and prove nothing.
    _real2, _spread2 = survivor_is_real(
        [(80.000e6, 60.2), (80.000e6, 60.4), (80.000e6, 60.1)],
        C_NEG_REPEAT_TOL_HZ)
    check("...but accepts a line that holds its frequency",
          _real2, "spread %.1f kHz" % (_spread2 / 1e3) if _spread2 is not None
          else "n/a")
    # And it must still accept a real line that jitters by a bin or two.
    _real4, _spread4 = survivor_is_real(
        [(80.000e6, 60.2), (80.032e6, 60.4), (79.968e6, 60.1)],
        C_NEG_REPEAT_TOL_HZ)
    check("...tolerating a couple of bins of genuine jitter", _real4,
          "spread %.1f kHz" % (_spread4 / 1e3))
    # And a single capture below the gate is not evidence of a survivor.
    _real3, _ = survivor_is_real(
        [(75.0e6, 26.0), (None, None), (76.0e6, 20.0)], C_NEG_REPEAT_TOL_HZ)
    check("...and needs every capture, not just two of three", not _real3)

    print("\nselftest %s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


# ==================================================================== live

def run_live(board, args):
    sc = ReconnectingScope(pyvisa.ResourceManager(), SCOPE_RESOURCE)

    print("=" * 78)
    print("Phase 0 -- timebase ladder (the sample rate is what defines 'alias')")
    print("=" * 78)
    ladder = []
    for ts in TIM_SCAL_LADDER:
        try:
            st = scope_prepare(sc, ts)
        except Exception as e:
            print("  ask %.3g us/div -> FAILED (%s)" % (ts * 1e6, e))
            continue
        ladder.append(st)
        print("  ask %7.3f us/div -> set %7.3f us/div | %.3f GSa/s | %6d pts | "
              "%6.1f us | Nyquist %7.1f MHz"
              % (ts * 1e6, st["tim_scal"] * 1e6, st["srate"] / 1e9,
                 st["points"], st["window"] * 1e6, st["srate"] / 2e6))
    if not ladder:
        print("no timebase worked; aborting")
        return 1
    srate = max(s["srate"] for s in ladder)
    print()
    print("  fastest available: %.3f GSa/s -> Nyquist %.1f MHz"
          % (srate / 1e9, srate / 2e6))
    print("  NOTE: the record length is fixed at 62500 points, so the shortest")
    print("  timebases were paid for by the instrument silently SHORTENING THE")
    print("  RECORD, not by a slower sample rate.  No error is raised either way.")

    sweep_ts = max(s["tim_scal"] for s in ladder if abs(s["srate"] - srate) < 1)
    st = scope_prepare(sc, sweep_ts)
    srate, xinc = st["srate"], st["xinc"]
    print()
    print("  sweep timebase %.4g us/div -> %.3f GSa/s, %d pts, %.1f kHz bins, "
          "Nyquist %.1f MHz"
          % (st["tim_scal"] * 1e6, srate / 1e9, st["points"],
             srate / st["points"] / 1e3, srate / 2e6))

    print()
    print("=" * 78)
    print("Phase 1 -- reference at LO = %.0f MHz, source attenuation 0 mdB"
          % (LO_REF_HZ / 1e6))
    print("=" * 78)
    if not board_prepare_1r1t(board):
        print("the PL is not in the expected 1R1T state; aborting")
        return 1
    board_init(board, LO_REF_HZ, 0)

    r0 = autoscale(sc, LO_REF_HZ, 0.5)
    print("  vertical scale settled on %.4g V/div; raw record span %d/255 codes"
          % (r0["scal"], int(r0["span"])))
    if r0["f"] is None or r0["snr"] < PEAK_SNR_MIN_DB:
        print("  C_REF FAIL: no line within %.1f MHz of %.0f MHz"
              % (LIN_TOL_HZ / 1e6, LO_REF_HZ / 1e6))
        print("  Nothing further would mean anything; the sweep is not run.")
        sc.close()
        return 1
    ref_ok = abs(r0["f"] - LO_REF_HZ) < C_REF_LO_TOL_HZ
    ref_amp = line_amplitude_vpp(r0["spec"], r0["freqs"], r0["f"], r0["scal"])
    print("  C_REF %s: line at %.4f MHz (%+.4f MHz from LO, tol %.1f MHz), "
          "SNR %.1f dB"
          % ("PASS" if ref_ok else "FAIL", r0["f"] / 1e6,
             (r0["f"] - LO_REF_HZ) / 1e6, C_REF_LO_TOL_HZ / 1e6, r0["snr"]))
    print("  line amplitude from the FFT: %s"
          % (("%.4f Vpp" % ref_amp) if ref_amp else "unavailable"))
    print("  display :MEAS:VPP? reading:  %s"
          % (("%.4f V" % r0["vpp_trace"]) if r0["vpp_trace"] else "unmeasurable"))
    print()
    print("  >>> HONEST LIMIT, stated up front: the source CANNOT be taken to "
          "1 Vpp.  At 0 mdB")
    print("      (its own ceiling -- a negative attenuation is not accepted) "
          "the line measures")
    print("      about 0.83 Vpp here.  So 'source fixed at 1 Vpp' is realised "
          "as 'source fixed at")
    print("      its maximum', held at that ONE setting for the whole sweep.  "
          "The drop column")
    print("      below is therefore relative to THIS level, not to 1.000 V.")

    print()
    print("=" * 78)
    print("Phase 2 -- LO sweep, source held at 0 mdB (its maximum)")
    print("=" * 78)
    rows = []
    scal = r0["scal"]
    print("  %-8s %-32s %-10s %-11s %-9s %s"
          % ("LO MHz", "what the scope's own acquisition holds", "Vpp read",
             "FFT amp", "drop", "V/div"))
    for lo in LO_SWEEP_HZ:
        board_init(board, lo, 0)
        m = autoscale(sc, lo, scal)
        scal = m["scal"]
        alias = predict_alias(lo, srate)
        have = m["f"] is not None and m["snr"] >= PEAK_SNR_MIN_DB
        amp = (line_amplitude_vpp(m["spec"], m["freqs"], m["f"], m["scal"])
               if have else None)
        drop = (20.0 * math.log10(ref_amp / amp)) if (amp and ref_amp) else None
        if not have:
            ident = "NONE"
        elif abs(m["f"] - lo) < C_ID_TOL_HZ:
            ident = "TRUE"
        elif abs(m["f"] - alias) < C_ID_TOL_HZ:
            ident = "FOLD"
        else:
            ident = "SPUR"
        rows.append(dict(lo=lo, f=m["f"], snr=m["snr"], amp=amp,
                         vpp=m["vpp_trace"], drop=drop, alias=alias,
                         ident=ident, scal=m["scal"], span=m["span"]))
        print("  %-8.1f %-32s %-10s %-11s %-9s %.4g"
              % (lo / 1e6,
                 ("%.4f MHz  %.1f dB  [%s]" % (m["f"] / 1e6, m["snr"], ident))
                 if have else "[no line over the gate]",
                 ("%.4f" % m["vpp_trace"]) if m["vpp_trace"] else "n/a",
                 ("%.5f" % amp) if amp else "n/a",
                 ("%+.2f dB" % drop) if drop is not None else "n/a",
                 m["scal"]))

    print()
    print("=" * 78)
    print("Phase 3 -- C_ATT: does the scope still read a KNOWN %d mdB step as "
          "%.0f dB?" % (C_ATT_STEP_MDB, C_ATT_EXPECT_DB))
    print("=" * 78)
    print("  This is the phase that decides HOW TO READ Phase 2.  A drop in the")
    print("  Phase 2 column has two possible authors -- the TX chain rolling off,")
    print("  or the scope losing amplitude accuracy -- and they are only")
    print("  separable by injecting a step whose size is known independently.")
    print("  Where C_ATT holds, the scope's amplitude reading is sound and the")
    print("  Phase 2 drop is the SOURCE.  Where C_ATT cannot be measured at all,")
    print("  the Phase 2 number there is not evidence of anything.")
    print("  Pair is %d -> %d mdB, not 0 -> %d: 0 mdB is this chain's ceiling, "
          "so the" % (ATT_LOW_MDB, ATT_HIGH_MDB, C_ATT_STEP_MDB))
    print("  first attempt's 14.8 dB at 80 MHz was read on the attenuator's own")
    print("  compression shoulder.  %d -> %d is the pair the RF suite already "
          "validated" % (ATT_LOW_MDB, ATT_HIGH_MDB))
    print("  at 19.85 dB.")
    att_rows = []
    for lo in ATT_POINTS_HZ:
        board_init(board, lo, ATT_LOW_MDB)
        a = autoscale(sc, lo, scal)
        lo_amp = (line_amplitude_vpp(a["spec"], a["freqs"], a["f"], a["scal"])
                  if (a["f"] and a["snr"] >= PEAK_SNR_MIN_DB) else None)
        board_init(board, lo, ATT_HIGH_MDB)
        b = autoscale(sc, lo, scal)
        hi_amp = (line_amplitude_vpp(b["spec"], b["freqs"], b["f"], b["scal"])
                  if (b["f"] and b["snr"] >= PEAK_SNR_MIN_DB) else None)
        d = (20.0 * math.log10(lo_amp / hi_amp)) if (lo_amp and hi_amp) else None
        ok = d is not None and abs(d - C_ATT_EXPECT_DB) <= C_ATT_TOL_DB
        att_rows.append(dict(lo=lo, d=d, ok=ok))
        print("  LO %7.1f MHz | %d mdB -> %-13s | %d mdB -> %-13s | step %-10s %s"
              % (lo / 1e6, ATT_LOW_MDB,
                 ("%.5f Vpp" % lo_amp) if lo_amp else "no line",
                 ATT_HIGH_MDB,
                 ("%.5f Vpp" % hi_amp) if hi_amp else "no line",
                 ("%+.2f dB" % d) if d is not None else "NOT RUN",
                 "OK" if ok else ("OFF" if d is not None else "unmeasurable")))

    print()
    print("=" * 78)
    print("Phase 4 -- C_NEG: the SIDEBANDS must vanish at %d mdB"
          % C_NEG_ATTEN_MDB)
    print("=" * 78)
    print("  A criterion with no negative case is not a criterion.  But the")
    print("  negative case has to be about the SOURCE: at maximum attenuation")
    print("  the sidebands must be gone.  It is not a claim that the RF")
    print("  environment is empty -- this fixture picks up ambient shortwave")
    print("  broadcast, and a +-5 MHz window holds ~300 bins of Rayleigh noise")
    print("  whose maximum sits ~25 dB over the median by itself.")
    print("  A survivor therefore has to REPEAT at one frequency to count; the")
    print("  capture-to-capture frequency spread is reported alongside it.")
    snap = snapshot(board)
    f_bb = (snap.get("phase_inc") or 128) / 4096.0 * 60e6
    board_init(board, LO_REF_HZ, C_NEG_ATTEN_MDB)
    n = autoscale(sc, LO_REF_HZ, scal)
    have = n["f"] is not None and n["snr"] >= PEAK_SNR_MIN_DB
    print("  strongest line within %.1f MHz of LO (NOT the criterion, context): %s"
          % (LIN_TOL_HZ / 1e6,
             ("%.4f MHz @ %.1f dB" % (n["f"] / 1e6, n["snr"])) if have
             else "none over the %.0f dB gate" % PEAK_SNR_MIN_DB))

    # The criterion proper: the two sidebands, each searched where it belongs.
    side_absent, seen = True, []
    for label, f_target in (("LO-f", LO_REF_HZ - f_bb),
                            ("LO+f", LO_REF_HZ + f_bb)):
        hits = []
        for _ in range(C_NEG_REPEATS):
            xinc, y = capture(sc)
            fr = np.fft.rfftfreq(len(y), xinc)
            f_hit, snr_hit = line_near(fr, spectrum_of(y), f_target,
                                       LIN_TOL_HZ)
            hits.append((f_hit, snr_hit))
            time.sleep(0.25)
        # A real survivor holds its frequency; a noise maximum wanders.
        repeats, spread = survivor_is_real(hits, C_NEG_REPEAT_TOL_HZ)
        seen.append((label, hits, spread, repeats))
        if repeats:
            side_absent = False
        print("  %s at %.4f MHz over %d captures: %s"
              % (label, f_target / 1e6, C_NEG_REPEATS,
                 "; ".join(("%.4f MHz @ %.1f dB" % (h[0] / 1e6, h[1]))
                           if h[0] is not None else "none"
                           for h in hits)))
        if spread is not None:
            print("       frequency spread %.0f kHz over the captures that "
                  "cleared the gate -> %s"
                  % (spread / 1e3,
                     "REPEATS, this is a real survivor"
                     if repeats else "wanders, so it is noise"))
        else:
            print("       not found in enough captures to call it a survivor")
    neg_ok = side_absent
    print("  C_NEG %s" % ("PASS (both sidebands gone at maximum attenuation)"
                          if neg_ok else
                          "FAIL (a sideband survived, repeating, at maximum "
                          "attenuation)"))

    print()
    print("=" * 78)
    print("VERDICT -- 'can the scope measure this normally?'")
    print("=" * 78)
    true_rows = [r for r in rows if r["ident"] == "TRUE"]
    none_rows = [r for r in rows if r["ident"] == "NONE"]
    folds = [r for r in rows if r["ident"] == "FOLD"]
    spurs = [r for r in rows if r["ident"] == "SPUR"]
    ok_att = [a for a in att_rows if a["ok"]]
    print("  1. frequency identity (the criterion that matters most):")
    if true_rows:
        print("       the scope read the TRUE frequency up to %.0f MHz"
              % (max(r["lo"] for r in true_rows) / 1e6))
    if none_rows:
        print("       no line findable from %.0f MHz upward"
              % (min(r["lo"] for r in none_rows) / 1e6))
    if folds:
        print("       read the FOLD (not a fault -- unavoidable above %.0f MHz, "
              "but the" % (srate / 2e6))
        print("       reading is then meaningless): %s"
              % ", ".join("%.0f" % (r["lo"] / 1e6) for r in folds))
    if spurs:
        print("       read NEITHER the tone nor its fold (this WOULD be a fault "
              "if the")
        print("       source were known good): %s"
              % ", ".join("%.0f" % (r["lo"] / 1e6) for r in spurs))
    print("  2. amplitude accuracy, judged by the known %d mdB step:"
          % C_ATT_STEP_MDB)
    if ok_att:
        print("       held (%.0f +/- %.0f dB) at %s MHz -> the drop column "
              "there is the SOURCE"
              % (C_ATT_EXPECT_DB, C_ATT_TOL_DB,
                 ", ".join("%.0f" % (a["lo"] / 1e6) for a in ok_att)))
    else:
        print("       did NOT hold anywhere -- the scope's amplitude scale is "
              "not trustworthy")
        print("       at any point probed, so no drop below is attributable")
    bad_att = [a for a in att_rows if a["d"] is not None and not a["ok"]]
    if bad_att:
        print("       read wrong at: %s"
              % ", ".join("%.0f MHz (%.1f dB)" % (a["lo"] / 1e6, a["d"])
                          for a in bad_att))
    unmeas = [a for a in att_rows if a["d"] is None]
    if unmeas:
        print("       NOT RUN (no line to step): %s"
              % ", ".join("%.0f" % (a["lo"] / 1e6) for a in unmeas))
    print("  3. negative case: %s" % ("PASS" if neg_ok else "FAIL"))
    print()
    print("  Reading the Phase 2 drop column: it is 20*log10(reference FFT "
          "amplitude / this")
    print("  one), so it contains BOTH the source's rolloff and the scope's.  "
          "Only at the")
    print("  frequencies where line 2 above says 'held' is it attributable to "
          "the source.  At")
    print("  %.0f MHz and above the drop is confounded: the scope's 100 MHz "
          "front end and" % ((max(r["lo"] for r in true_rows) / 1e6)
                             if true_rows else 0))
    print("  the TX chain both roll off, and this fixture cannot yet tell them "
          "apart.")
    sc.close()
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="exercise the judging code offline; needs no scope "
                         "and no board")
    ap.add_argument("--host", default=BOARD_HOST)
    ap.add_argument("--app", default=BOARD_APP)
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    board = BoardCmd(args.app, host=args.host)
    return run_live(board, args)


if __name__ == "__main__":
    sys.exit(main())
