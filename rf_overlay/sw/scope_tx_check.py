#!/usr/bin/env python3
"""AD9361 TX bring-up: the B/C-layer scope criteria (spec 6.1 Step 4).

Run this with the SYSTEM python -- it imports pyvisa:

    python -u rf_overlay/sw/scope_tx_check.py \
        --board-app /home/xilinx/board_app_src/board_app

    python -u rf_overlay/sw/scope_tx_check.py --channel CHAN2 \
        --board-app /home/xilinx/board_app_src/board_app      # TX2

    python -u rf_overlay/sw/scope_tx_check.py --selftest   # no board, no scope

--channel selects the SCOPE channel to measure, and it is not cosmetic.  CHAN1
(default) carries TX1, which has the DDS tone, and is the configuration stage 1
was accepted on.  CHAN2 carries TX2, whose DAC data is forced to zero
(rf_tx_source.v:103-104, mode 0), so all it emits is LO leakage.  On CHAN2 the
criteria that need a tone -- the offset df (4.2), the fs it implies (4.3), the
attenuator difference (4.5) and C1's symmetric pair -- have nothing to measure
and print NOT APPLICABLE; a criterion with no measurement must never print
PASS.  Presence at LO (the presence half of 4.1) and translation with LO (4.4)
do survive on LO leakage and are judged normally.

The criteria below are copied from the design spec (docs/superpowers/specs/
2026-09-17-ad9361-tx-bringup-design.md section 6.1 Step 4) and were fixed
BEFORE any measurement.  If a criterion turns out to be badly chosen, the fix
is to record it as NOT RUN and revisit, never to move the threshold after
seeing the number.

Everything is judged RELATIVE -- position, difference, ratio -- never absolute
amplitude.  The 80 MHz carrier sits in the roll-off of this scope's 100 MHz
front end, whose response is not characterised here, so an absolute amplitude
threshold would be a criterion written against an unknown.

Layer B is DIAGNOSTIC and frequency-only.  Its sole signal source is LO
leakage: there is no chip-internal tone generator in this part (spec 0.2 --
ad9361.c/.h have zero DDS references), and the leakage amplitude was never
measured, so no amplitude threshold can be justified.  "Cannot resolve a line
at LO" means "instrument-limited, this diagnostic is unavailable", which is
NOT a failure.  B's value is bisection: if C fails while B passes, the analog
chain is alive and the problem is in the data path.

Layer C is the primary criterion.

Three constants below differ from the plan's transcription, each because the
transcribed value contradicts the spec or the instrument's own numbers.  Every
one is a PRE-measurement correction -- computed from the spec's arithmetic, not
from a reading -- and each is commented where it is defined.  They are called
out here so the diff is visible rather than buried:

  TIM_SCAL         6.25 us/div, not 20 ns/div.  20 ns/div gives ~400 points and
                   ~5 MHz bins, which cannot resolve a 469 kHz sideband.  The
                   spec names 62500 points / 62 us / 16 kHz.
  LO_LINE_TOL_HZ   new; the LO-line identification radius.  The plan reused the
                   2 MHz LO-shift tolerance for this, which is wider than the
                   1.875 MHz maximum sideband spacing -- it would classify the
                   sidebands as "not sidebands" and could latch onto one as the
                   carrier.
  FS_FIT_MAX       how far `df * 2^12 / phase_inc` may sit from the sampling
                   rate `init` reads back from the part.
  FS_RATIO_DOCUMENTED  the SECOND-HAND LVDS-DDR ratio (example_project_notes.md:85),
                   kept only as the C3x cross-check.

                   The ratio framing this file used to adjudicate against --
                   `FS_RATIO_CANDIDATES`, four ratios {1/2, 1, 2, 3} -- is GONE,
                   and the reason is worth keeping.  It was mode-dependent: the
                   true ratio is 60/120 = 1/2 in 1R1T but 60/240 = 1/4 in 2R2T,
                   and 1/4 was in no list, so in 2R2T C3 FAILed BY CONSTRUCTION
                   whatever the hardware did (measured 2026-09-18; the row was
                   marked NOT RUN for purpose in docs/rf/boardtest.md 9.7 rather
                   than having its threshold moved).  It also routed the verdict
                   through f_DATA_CLK, which the firmware prints after a 16-bit
                   counter wrap -- i.e. through the quantity under dispute.
                   C3 now compares the scope's df against the sampling rate the
                   part itself reports, which needs no ratio and no f_DATA_CLK
                   and holds in either mode.  See layer_c's C3 block.
"""

import argparse
import ast
import contextlib
import io
import os
import re
import sys
import time

import numpy as np

# ---------------------------------------------------------------- criteria
# Frozen before measurement.  Kept as named constants so that a change is a
# visible diff rather than a tweak buried in a comparison.

# The PL STATUS word's own provenance.  NOT used for arithmetic here: the
# decode lives in the board's C (platform_rf.h RF_STATUS_F_DATA_CLK_HZ) and
# this script only ever READS the resulting rate via `board_app status`.  Kept
# as documentation of what the register means, and deliberately not combined
# into any frequency this script computes -- re-deriving f_DATA_CLK here would
# reintroduce exactly the hardcoded-rate defect ruling R14 was about.
WIN_LOG2          = 15          # from the PL STATUS register's WIN_LOG2 field
FCLK0_HZ          = 50_000_000  # FCLK0, read from SLCR per docs/rf/boardtest.md

LO_NOMINAL_HZ     = 80e6
LO_SHIFTED_HZ     = 85e6

# 4.2: phase_inc 128 -> 256 must double the tone offset.
PHASE_INC_1       = 128
PHASE_INC_2       = 256
DF_RATIO_MIN      = 1.96        # 2.00 - 2%
DF_RATIO_MAX      = 2.04        # 2.00 + 2%

# 4.5: take the DIFFERENCE between two attenuator settings so the scope's
# front-end response cancels.
#
# SAFETY FLOOR.  TX1 is cabled to RX1 on this board: atten 0 is full TX power
# straight into the receiver's input, so no `init` here may omit `atten` (the
# board's struct default is 0) and none may go below ATTEN_FLOOR_MIN_MDB.
# Spec 6.1 Step 4's own tables were written for an attenuator in circuit, so the
# measurements move to the safe floor rather than the floor moving up to the
# measurement.  40000 mdB (40 dB) is the value used everywhere else in this
# project for the same loopback, and it is this script's DEFAULT for every init
# that is not part of C5's pair -- see ATTEN_FLOOR_MIN_MDB below for the one
# deliberate exception and why it is not a new operating point on the cable.
#
# What this costs: 40 dB less LO-leakage and sideband amplitude at the scope.
# Layer B's whole premise is that it is FREQUENCY-ONLY for exactly this reason
# (see the module docstring), and layer C judges relative position, difference
# and ratio, never absolute amplitude -- so the floor is affordable.  What it
# cannot afford is a criterion that silently stops being measurable; that is
# what the safety assertions in the selftest guard, not assume.
SAFE_ATTEN_MDB    = 40000

# The lowest attenuation ANY init in this script may carry.  This is the one
# number that may be lowered, and only deliberately: it is the bottom of C5's
# pair.  It stays 20 dB below full power, i.e. 10 dB safer than the pair the
# already-accepted stage-1 record measured (10000 -> 30000 mdB measured 19.85 dB,
# docs/rf/l2_stage1_record.md:270), so it is not a new operating point on the
# cable -- it is a step back toward one this board has already survived.
ATTEN_FLOOR_MIN_MDB = 20000

# C5: the two attenuator points, kept 20 dB apart.
#
# HISTORY, because the pair has now been moved twice and both moves were forced
# by measurement rather than preference.  Spec 6.1 Step 4 wrote the pair as
# 10000 -> 30000 mdB.  Both sit under the loopback safety floor, so the pair was
# translated up 30 dB to 40000 -> 60000.  That translation was then MEASURED to
# have destroyed the criterion: over 24 captures in three interleaved blocks
# (rf_overlay/out/diag_floor.log, 2026-09-20) the 40000 upper-point reading sat at
# 25.4 .. 26.7 dB while the 60000 point swung -5.7 .. 15.4 dB capture to capture
# -- and the capture floor, the 8-bit peak code and the waveform std were
# BIT-IDENTICAL in every block, i.e. 20 dB of attenuation did not move the
# waveform at all.  A 20 dB step that does not change the waveform cannot be
# measured by reading the waveform 20 dB down; the 60000 bin was floor
# fluctuation, and averaging it does not help (run-to-run means 7.4 vs 11.4 dB).
#
# So the pair moved DOWN to 20000 -> 40000, putting its upper point exactly at
# the safe floor and using the one 20 dB of headroom available below it.  What
# C5 compares is a DELTA -- a ratio of two readings at the same bin -- and a
# delta is invariant under translating both points together, so the criterion is
# unchanged; only its measurability was at stake.  Do not move the pair again
# without re-running diag_floor.py: the last move was made on six points of
# measurement, not on the assumption that lower is better.
ATTEN_LOW_MDB     = ATTEN_FLOOR_MIN_MDB
ATTEN_HIGH_MDB    = SAFE_ATTEN_MDB
ATTEN_DELTA_DB    = 20.0
ATTEN_TOL_DB      = 3.0

# How many captures C5 averages at EACH point.  A single capture is not a
# measurement of a signal this far above the floor, and the selftest pins that:
# MEASURED 2026-09-20 at one fixed operating point the upper point's reading
# swung 1.0 .. 15.7 dB over eight captures and the 6 dB measurability gate
# cleared 6 of 8 times.  Judging from frame 0 therefore flips a coin and then
# blames the instrument -- it reported N/A about a board it had not measured.
# Eight captures is where the interleaved blocks stopped paying (the per-block
# means agreed to 0.3 dB); the cost is ~1.6 s of scope traffic per point.
C5_CAPTURES       = 8

# Settle between repeated captures, in seconds.  See avg_capture's docstring:
# without it the DSOX1102A's acquisition wedges (POIN -> 0, every acquisition
# query hangs) and only :SYST:PRES recovers.  0.15 s is the value the working
# 32-capture probe used.  This is an instrument constraint, so it applies to any
# caller looping captures -- not to a single-capture criterion, which has its
# own board_init + sleep before it.
CAPTURE_SETTLE_S  = 0.15

# B2/4.4: the LO moves 80 -> 85 MHz; every line must move with it.  2 MHz is
# slack for a 5 MHz translation, and is used ONLY for that.  It must not be
# reused as the "is this peak at the carrier" radius: the DDS sidebands sit at
# phase_inc/2^12 * fs, i.e. fs/32 = 0.3125 / 0.625 / 1.25 / 1.875 MHz for the
# four candidate ratios, all of them INSIDE a 2 MHz radius.  A 2 MHz LO-line
# radius therefore rejects every real sideband from C1's `side` list, and lets
# find_nearest latch onto a sideband as the carrier whenever it outranks LO
# leakage -- which on a live TX it does.
LO_SHIFT_TOL_HZ   = 2e6

# The LO-line identification radius.  Sized for the PLAN's assumed fs = 30 MHz,
# where the smallest candidate sideband offset is (fs/32)/2 = 469 kHz / 2.
# MEASURED f_DATA_CLK is 20 MHz, not 30 (docs/rf/l2_stage1_record.md), so the
# smallest candidate offset is 312.5 kHz and this radius is 80% of it, not 50%.
# It still cannot capture a sideband -- the margin is 62.5 kHz, ~4 bins -- but
# the 2x margin this rationale originally claimed is now 1.25x.  Do not re-derive
# it from the plan's 30 MHz; derive it from the measured f_DATA_CLK.
LO_LINE_TOL_HZ    = 250_000

# C1: how far the two sidebands may sit from a PERFECT mirror about the LO.
# The DDS is I-only, so the lines are at lo - df and lo + df exactly, and what
# `sym` measures is only peak-position quantisation: at most one 16 kHz bin per
# sideband plus two for the LO line itself, i.e. <= 2 bins = 32 kHz.  50 kHz is
# ~3 bins, so a working board cannot fail on quantisation, while a spectrum
# that merely has a line at the LO with something above it and something below
# it misses by 4.1 / 28.2 / 37.9 MHz in the measured cases -- 80x to 570x the
# limit.  Without this bound the criterion named "carrier + symmetric pair" was
# satisfied by a single sideband plus two noise bins.
SYM_TOL_HZ        = 50_000

# C1: the furthest a real DDS sideband can sit from the carrier.  df =
# phase_inc/2^12 * fs, i.e. fs/32 at phase_inc 128 -- 1.875 MHz at ruling
# R14's 60 MSPS, 0.9375 MHz at the plan's 30 MSPS pairing.  Half the LO is 21x
# the largest candidate: no plausible f_DATA_CLK can put a real tone past it,
# while a pair of unrelated spurs half an LO apart -- which would otherwise
# satisfy "carrier + symmetric pair" and hand a fabricated df to C2/C3 -- is
# rejected.
DF1_MAX_HZ        = 0.5 * LO_NOMINAL_HZ

# Documented, SECOND-HAND: example_project_notes.md:85 flags the source XDC's
# 25 MHz constraint as "possibly conservative or not matching reality", so this
# is a cross-check to RECORD, never the basis of a verdict.  The measurement is
# the primary evidence; the document is the cross-check.
#
# MEASURED 2026-09-18, LO 80 MHz, atten 0, 12-bit accumulator, in BOTH modes:
#   phase_inc 128 -> sidebands 78.1280 / 81.8720 MHz, df1 = 1872.0 kHz
#   phase_inc 256 -> sidebands 76.2560 / 83.7440 MHz, df2 = 3744.0 kHz
#   i.e. df = phase_inc/2^12 * fs  =>  fs = 1872.0 kHz * 4096/128 = 59.90 MHz
# and 59.90 MHz is the trustworthy half of that: it agrees with the driver's own
# requested-and-read-back 60 MSPS to -0.2%, and it needs no f_DATA_CLK at all.
#
# ⚠ CORRECTED 2026-09-18.  The first version of this note went on "...against a
# measured f_DATA_CLK of 20.000 MHz, i.e. fs = 3.0 * f_DATA_CLK", and that was
# wrong twice over.  (a) 20.000 MHz is not f_DATA_CLK: the window counter is 16
# bits and wraps, so the firmware prints 1/6 of the truth (true 120 MHz in 1R1T,
# 240 MHz in 2R2T -- docs/rf/boardtest.md 9.4).  (b) So the relation it named,
# fs = 3 * f_DATA_CLK, does not exist; the true ratios are 60/120 = 1/2 in 1R1T
# and 60/240 = 1/4 in 2R2T.
#
# That is the E11 defect firing a second time and in the opposite direction --
# not a candidate omitted, but every candidate SCALED by a number read back FROM
# THE DEVICE UNDER TEST.  A criterion that scales itself by its own subject will
# name whatever relation the scale error produces, and print it as a finding.
# The "3.0 lands inside the 5% band (-0.2%)" reading above is exactly that: 3.0
# is only near 1872.0 kHz because the wrapped 20 MHz put a candidate at
# 1875.0 kHz.  Two consequences, recorded rather than papered over:
#   * stage 1's "C3 PASS" was a false identification, not a hardware result;
#   * stage 2's C3 FAIL was structural -- 1/4 is in no candidate list, so it
#     would FAIL on a perfect board.  No threshold was moved.
#
# 2026-09-20: the ratio test is GONE (see FS_FIT_MAX below), so C3 no longer
# FAILs by construction and docs/rf/boardtest.md 9.7's NOT RUN no longer applies.
# This constant survives only as C3x's RECORD: the measured fs / f_DATA_CLK the
# read-back rate and the window counter imply, printed next to the documented
# 2.0 so a disagreement is visible, never as the basis of the verdict.
FS_RATIO_DOCUMENTED = 2.0

# How far the df-derived sampling rate may lie from the rate the part itself
# reports, as a fraction of the read-back rate.  This is the FIT test.
#
# Pre-registered from the spec's arithmetic, not tuned after a reading: the
# capture is 62500 points at 1 GSa/s, i.e. 16 kHz bins (spec 6.1 Step 4).  A
# peak picks a bin centre, so df carries up to +/-16 kHz of quantisation, and
# fs = df * 2^12 / phase_inc multiplies it by 32 -- 512 kHz on a 60 MHz rate,
# i.e. 0.85%.  The band must be wider than that or a working board false-FAILs
# on quantisation alone, and far narrower than the 20% that would let a rate
# from a different profile pass.  5% is ~5.9x the quantisation floor and 4x
# inside that distance.  C2's independent gate is +/-2%.
#
# (What this replaced, and why: C3 used to look for the ratio fs / f_DATA_CLK in
# a candidate list {1/2, 1, 2, 3}.  The true ratio is 1/4 in 2R2T and 1/2 in
# 1R1T, and 1/4 is in no list -- so that form FAILed on a perfect board
# whenever the part ran 2R2T, which it does.  The comparison below needs no
# ratio and never touches f_DATA_CLK, so it is mode-independent.)
FS_FIT_MAX = 0.05

# Peak-picking: a line must stand this far above the local noise floor.
PEAK_SNR_MIN_DB   = 6.0

# C1: how far the line claimed to be the LO carrier must stand above the floor,
# ON TOP OF the generic peak gate above.  6 dB is a peak-PICKING floor -- it
# says "this is a local maximum worth listing", not "this is a signal" -- and
# once the peak list is no longer truncated (see peak_freqs) it is far too weak
# to decide C1.
#
# Measured on this board 2026-09-18, atten 0 / LO 80 MHz / phase_inc 128.  Both
# sides of the gap come from the same recorded capture (31251 bins, 16 kHz
# bins), via tmp/carrier_gate_derive.py:
#
#     real carrier at 80.0000 MHz      20.0 dB        (17.3 .. 21.2 over 6 captures)
#     strongest noise maximum that
#     landed in an equal-width window  10.4 dB        (7.6 .. 10.5 over 6 captures)
#
# so the two populations are 6.9 dB apart and empty.  14.0 dB sits in the middle
# of that gap -- 3.6 dB above the worst noise maximum ever observed and 3.3 dB
# below the worst carrier ever observed.
#
# The same capture gives the cost of getting this wrong, as a curve rather than
# an anecdote.  A false positive needs a gated peak inside the 15.6-bin window,
# and this capture's own peak population says how many survive each gate:
#
#     gate    peaks left   P(a noise peak in the window)
#     6.0 dB      1979              63%      <- the un-gated width test: a coin flip
#    10.0 dB       626              27%
#    14.0 dB       144               7%      <- CARRIER_SNR_MIN_DB
#    17.0 dB        46               2%
#
# 14.0 dB cuts the population that could stand in for a carrier by 13.7x, and
# leaves the measured carrier 6 dB of headroom.  Going higher buys little
# (17 dB is only 5 points better) and starts eating the headroom that keeps a
# real, slightly weaker leakage line from being rejected.
#
# This is a pre-registered threshold from the measurement, NOT a value tuned
# until a verdict came out green -- the verdicts above came out of the peak list
# and the carrier was never in question (it is present, symmetric and
# translates with the LO).  Without a magnitude test C1 would be a coin flip;
# with it, the criterion still asks exactly what spec 6.1 4.1 asks.  Note also
# that C1 is not decided by this gate alone: a false carrier must still be
# flanked by a symmetric pair, which the gate makes 13.7x less likely by chance.
CARRIER_SNR_MIN_DB = 14.0

# How far the carrier must ALSO stand above the strongest line in its own search
# window that is not the carrier.  CARRIER_SNR_MIN_DB alone decides "this bin is
# above the floor"; on its own it does not decide "this bin is the carrier",
# because once the carrier falls to the floor the window's strongest line IS a
# noise maximum and it clears 14 dB happily.
#
# MEASURED, from the same 2026-09-18 derivation the gate above came from: the
# two populations that must be separated are the carrier (17.3 .. 21.2 dB) and
# the strongest noise maximum in an equal-width window (7.6 .. 10.5 dB), so
# 10.5 + 6.9 = 17.4 vs 17.3 -- 6.9 dB is the separation actually observed
# between the worst carrier and the worst noise maximum, and requiring it is
# requiring the capture to be as clean as the one the gate was calibrated on.
#
# MEASURED on the real board 2026-09-20 at SAFE_ATTEN_MDB = 40000, LO 80 MHz,
# phase_inc 128, three captures: the +/-250 kHz window held 10-11 gated peaks at
# 6.8 .. 18.9 dB with no repeatable line at any bin (the top two were 3.5 dB
# apart; the bin at exactly 80.0000 MHz read 7.7 / 12.4 / 6.7 dB across the
# three).  The sidebands, by contrast, landed on the same two bins in every
# capture at 32 .. 34 dB.  So at the safety floor the carrier is BELOW the floor
# and is not identifiable AT THIS VERTICAL SCALE -- which is why C1 reports it as
# NOT APPLICABLE rather than as a FAIL, and why 6.9 dB must not be lowered to
# make it reappear.
#
# MEASURED again 2026-09-20 with the vertical scale as the variable
# (rf_overlay/out/diag_c1_scale.py, 12 captures each, same attenuation):
#
#     0.5  V/div   top line at exactly 80.0000 MHz in  1/12   median lead 0.3 dB
#     0.02 V/div   top line at exactly 80.0000 MHz in 12/12   median lead 4.3 dB
#
# so "not identifiable" is a statement about the SCALE, not about the
# transmitter: 25x more volts per division makes the LO the top line in every
# capture, at exactly the commanded frequency, with no clipping.  It does NOT
# make the carrier GATEABLE, and that is the operative point -- the top line's
# lead over the runner-up at the finer scale is 2.5 .. 5.2 dB (median 4.3), so a
# gate placed in that gap would have to sit near 2 dB, barely above the 6 dB
# peak-picking floor and far below the 6.9 dB that separates the two populations
# at the attenuation this gate was calibrated at.  Re-deriving here would
# therefore produce a WEAKER gate than the one in force, not a stronger one.
# That is why the verdict stays NOT APPLICABLE and the scale finding is recorded
# rather than converted into a threshold.
CARRIER_MARGIN_MIN_DB = 6.9

# The line magnitudes the SELFTEST fixtures reproduce, in dB above the floor.
# They are shared constants rather than per-fixture numbers so that the two
# generators cannot drift apart again -- and they are measured, not chosen.
# The values that were there before any measurement (a carrier at 3x the floor,
# i.e. 9.5 dB) put the fixture's carrier INSIDE the noise-maximum band above; a
# criterion cannot be tested against a carrier that the instrument could not
# tell from noise, and the fixtures were therefore only measuring a guess.
# tmp/carrier_gate_derive.py reports both numbers straight off the capture.
FIX_CARRIER_SNR_DB   = 20.0     # measured 17.3 .. 21.2
FIX_SIDEBAND_SNR_DB  = 55.0     # measured 55.3 / 56.7
# The strongest noise maximum the fixtures must be able to plant at the carrier
# frequency: the NEGATIVE fixture is built at this level, so it is the boundary
# the gate is required to reject.  Measured worst over 6 captures.
FIX_MIN_CARRIER_IMPERSONATOR_DB = 10.5

# Timebase.  :WAV:POIN must NEVER be written (it pins the record length to a
# manual value that :WAV:POIN:MODE NORM cannot reset, after which every
# waveform-capture criterion in every suite silently misjudges).  A short
# timebase is what guarantees a high sample rate; 2 ms/div would push the
# sample rate to a few MSa/s and 80 MHz would vanish entirely.
#
# 6.25 us/div x 10 div = 62.5 us.  The spec's own numbers for this step are
# "62500 points => ~62 us window, 16 kHz resolution, resolving 470 kHz with room
# to spare" (section 6.1 Step 4, the B-layer visibility measures).  16 kHz is
# what a 62500-point capture at 1 GSa/s gives.  The plan transcribed 20 ns/div
# (a 200 ns window) instead, which at the DSOX1102A's 2 GSa/s ceiling holds
# ~400 points and ~5 MHz bins -- every one of the three candidate sidebands
# (0.469 / 0.938 / 1.875 MHz) would fall inside a single bin, so C1 would never
# see a symmetric pair and C2/C3 would inherit the failure.  The spec's "470 kHz"
# is the giveaway that its authors computed the ratio-1/2 candidate.
TIM_SCAL          = 6.25e-6     # 6.25 us/div, 10 div -> 62.5 us window
CHAN_SCAL         = 0.5         # relative only; see the module docstring

# The spec requires >= 500 MSa/s for these criteria.  Below it, 80 MHz is not
# representable and every FFT verdict would be an artefact of the instrument, so
# the script refuses to produce a signal verdict at all rather than manufacture
# a false FAIL -- the defect class this project keeps paying for.
SRATE_MIN_HZ      = 5e8

# ------------------------------------------------------- channel selection
# Which scope channel carries which transmitter.  `--channel` picks one; CHAN1
# is the default and the configuration stage 1 was accepted on.
DEFAULT_CHANNEL   = "CHAN1"
CHANNEL_CHOICES   = ("CHAN1", "CHAN2")

# The channels whose transmitter actually emits a DDS tone.
#
# TX2 does NOT.  rf_tx_source.v:103-104 forces dac_data_d2 / dac_data_q2 to zero
# while mode = 0 (the only mode this round configures -- nothing drives a
# pattern behind TX2 yet), so the second DAC has no baseband content at all:
# its spectrum is LO leakage and nothing else.
#
# That is not a detail of the fixture, it decides which criteria exist on
# CHAN2.  Anything DERIVED FROM THE TONE -- the offset df (4.2), the fs that
# offset implies (4.3), how the tone responds to the attenuator (4.5), and the
# symmetric sideband pair C1 is built around -- has no measurement to stand on
# there.  A criterion with nothing to measure must print NOT APPLICABLE: a
# "PASS" printed for want of a measurement is indistinguishable from a PASS
# earned by one, and that conflation is the defect class this file exists to
# avoid.  What DOES survive on CHAN2 is whatever rides on LO leakage: the
# presence of the line at LO (the presence half of 4.1) and its translation
# when LO moves (4.4).
DDS_TONE_CHANNELS = ("CHAN1",)


def _no_tone_reason(chan):
    """Why a tone-derived criterion has no verdict on `chan`.

    One string, one place, so every NOT APPLICABLE row carries the same
    specific reason instead of a bare "N/A" that reads like a bug.
    """
    return ("NOT APPLICABLE on %s: no DDS tone on this channel -- "
            "rf_tx_source.v:103-104 forces dac_data_d2/q2 to zero while mode = 0, "
            "so only LO leakage exists and this criterion has nothing to "
            "measure.  This is NOT a PASS." % chan)


def peak_freqs(freqs, spec, n=None, min_snr_db=PEAK_SNR_MIN_DB):
    """Every local maximum above the median floor by min_snr_db, strongest first.

    Returned as a list of (freq_hz, magnitude, snr_db) sorted by magnitude.
    The median is used as the floor because a spectrum with a few strong lines
    has a median dominated by the noise between them.

    `n`, when given, truncates the list, and is for DISPLAY ONLY.  The default
    is None -- no truncation -- and that default is load-bearing: the search
    callers (`find_nearest`, `symmetric_pair`) ask "which line sits at this
    frequency", and a truncation applied before the search silently deletes
    the line being asked about.

    It did.  MEASURED 2026-09-18 on this board: LO leakage at 80.0000 MHz is
    real and 20 dB above the floor, but sits ~37 dB below the DDS sidebands,
    so it ranks 24th of ~1900 gated peaks.  Under the previous default of 8
    the carrier was absent from the list, `find_nearest` returned None and C1
    reported "no line within 250 kHz of 80.0 MHz" -- a false FAIL on a
    transmitter whose sidebands were, at that moment, correct, symmetric
    (asymmetry 0.0 kHz) and translating with the LO.  Rank is not a property
    spec 6.1 4.1 asks about; whether the carrier is THERE is, and that is now
    decided by magnitude (CARRIER_SNR_MIN_DB), not by rank.
    """
    floor = np.median(spec)
    if floor <= 0:
        floor = 1e-12
    cand = []
    for k in range(1, len(spec) - 1):
        if spec[k] > spec[k - 1] and spec[k] >= spec[k + 1]:
            snr = 20.0 * np.log10(max(spec[k], 1e-12) / floor)
            if snr >= min_snr_db:
                cand.append((freqs[k], spec[k], snr))
    cand.sort(key=lambda t: -t[1])
    return cand if n is None else cand[:n]


def find_nearest(peaks, target_hz, tol_hz, min_snr_db=None):
    """The CLOSEST peak to target within tol, or None.

    Closest, not strongest.  Every caller uses this to answer "which line sits
    at this frequency" -- identify the LO leakage, identify the shifted
    position of a given line -- and strength is the wrong tie-break for that.
    The DDS sidebands are far stronger than LO leakage, and at ratio 2 they sit
    only 1.87 MHz from the carrier, i.e. INSIDE the 2 MHz LO-shift tolerance.
    A strongest-wins rule therefore returns a sideband when asked for the
    carrier, which is how C4 came to report the LO line moving 3.1 MHz instead
    of 5.0: it had matched a neighbour.  Distance is the only criterion that
    answers the question actually being asked.

    `min_snr_db`, when given, additionally rejects lines that do not stand that
    far above the floor.  Passing it is how a caller says "I expect a SIGNAL
    here, not merely a local maximum" -- see CARRIER_SNR_MIN_DB.  It defaults
    to None (rank-only) so that the existing callers, which deliberately ask
    for a line at a frequency where a weak line is still the right answer,
    are unchanged.

    Ties are broken by strength, which is the sensible secondary rule.
    """
    best = None
    for f, m, snr in peaks:
        d = abs(f - target_hz)
        if d > tol_hz:
            continue
        if min_snr_db is not None and snr < min_snr_db:
            continue
        if best is None or d < best[0] or (d == best[0] and m > best[1][1]):
            best = (d, (f, m, snr))
    return best[1] if best is not None else None


def _peaks_str(peaks, n=6):
    """The strongest peaks, for a FAIL detail.  Says so when there are none."""
    if not peaks:
        return "no peak exceeded the %.0f dB floor" % PEAK_SNR_MIN_DB
    return "peaks: %s" % ", ".join("%.1f MHz" % (f / 1e6)
                                   for f, _, _ in peaks[:n])


def find_carrier(peaks, lo_hz=LO_NOMINAL_HZ):
    """The LO leakage line, when this capture can tell it from the floor.

    Returns (line, note).  `line` is None when no line in the +/-LO_LINE_TOL_HZ
    window can be called the carrier, and `note` always says which question was
    answered -- "nothing there", "there is a line but it is too weak", and "the
    window is full of equally strong lines" have three different fixes and would
    otherwise share one word.

    TWO gates, not one.  CARRIER_SNR_MIN_DB decides a bin is above the floor;
    CARRIER_MARGIN_MIN_DB decides the strongest line in the window is a CARRIER
    rather than the loudest of a set of noise maxima.  Only the second survives a
    carrier that has fallen to the floor, and that is not hypothetical: MEASURED
    2026-09-20 at SAFE_ATTEN_MDB, the window held 10-11 gated peaks at 6.8 ..
    18.9 dB with no repeatable one among them, so the 14 dB gate was cleared by
    noise and this gate is what refuses it.

    The carrier's POSITION is deliberately not consumed by the pair search any
    more; see symmetric_pair for what using it as the symmetry centre cost.
    """
    window = sorted((p for p in peaks if abs(p[0] - lo_hz) <= LO_LINE_TOL_HZ),
                    key=lambda p: -p[2])
    if not window:
        return None, ("nothing within +/-%.0f kHz of %.1f MHz clears the %.0f dB "
                      "picking floor" % (LO_LINE_TOL_HZ / 1e3, lo_hz / 1e6,
                                         PEAK_SNR_MIN_DB))
    top = window[0]
    if top[2] < CARRIER_SNR_MIN_DB:
        return None, ("the strongest line within +/-%.0f kHz of %.1f MHz is "
                      "%.4f MHz at %.1f dB, below the %.1f dB a carrier has to "
                      "clear"
                      % (LO_LINE_TOL_HZ / 1e3, lo_hz / 1e6, top[0] / 1e6,
                         top[2], CARRIER_SNR_MIN_DB))
    if len(window) > 1:
        runner = window[1]
        gap = top[2] - runner[2]
        if gap < CARRIER_MARGIN_MIN_DB:
            return None, ("%d lines within +/-%.0f kHz of %.1f MHz, the strongest "
                          "(%.4f MHz, %.1f dB) leading the next (%.4f MHz, %.1f dB) "
                          "by only %.1f dB against the %.1f dB this gate was "
                          "calibrated on -- no one of them stands out as the "
                          "carrier, so it is NOT ESTABLISHED in this capture.  That "
                          "is a floor question, not a statement that the "
                          "transmitter has no LO leakage"
                          % (len(window), LO_LINE_TOL_HZ / 1e3, lo_hz / 1e6,
                             top[0] / 1e6, top[2], runner[0] / 1e6, runner[2],
                             gap, CARRIER_MARGIN_MIN_DB))
    return top, None


def symmetric_pair(peaks, lo_hz=LO_NOMINAL_HZ):
    """The strongest sideband pair symmetric about lo_hz within SYM_TOL_HZ.

    Returns (lower, upper, sym, reason).  lower/upper are None when the spectrum
    has no eligible pair, in which case `reason` names the nearest miss so a near
    miss is legible; otherwise sym is the residual asymmetry of the pair returned
    and reason is None.

    THE CENTRE IS THE COMMANDED LO -- from the register readback, not from a line
    found in the spectrum.  That is a correction, not a convenience.  MEASURED
    2026-09-20 on the real board at SAFE_ATTEN_MDB: LO leakage had fallen to the
    floor and the line nearest 80 MHz was a noise bin (80.224, then 79.712, then
    80.096 across three captures).  A centre error of D makes a genuinely
    symmetric pair asymmetric by 2D, so the true pair at 78.128 / 81.872 MHz was
    rejected every time -- 448 kHz of apparent asymmetry against a 50 kHz gate --
    and the search returned a coincidence of noise instead (40.880 / 119.584 MHz,
    df = 39352 kHz; on another capture 76.576 / 83.744).  The old code asked the
    spectrum where the carrier was, the spectrum could not answer at this
    attenuation, and a wrong answer is worse than none.

    The LO is known to better than a bin: `board_app init lo <Hz>` reads the
    programmed value back (criterion 2.3), and the accepted 2026-09-18 record's
    pair centred on it to 0.000 MHz.  C1's carrier clause is not weakened by
    this -- find_carrier judges it, separately and on its own terms.

    Symmetry is a FILTER, not a ranking.  The criterion is "carrier + symmetric
    pair" and the DDS is I-only, so the sidebands are at lo - df and lo + df
    exactly; a spectrum with a line at the LO and lines above and below it but no
    such pair is a different spectrum, and rejecting it is the whole point.
    Ranking by asymmetry alone accepts "whichever pair happens to be most nearly
    symmetric", which is how a single sideband plus two noise bins came to print a
    clean PASS with df = 55 MHz and hand it to C2/C3.

    Among eligible pairs the STRONGEST wins (tie-break: smaller asymmetry),
    because the DDS pair is the dominant content and two weak noise maxima must
    not outbid it -- the same primary/secondary rule find_nearest uses.  A pair
    further than DF1_MAX_HZ from the carrier is not eligible either: "carrier +
    pair" half an LO apart is two unrelated lines, not this DDS.

    A pair's members must ALSO clear CARRIER_SNR_MIN_DB, the same presence gate
    the carrier clears.  This is the half that the peak list's truncation used to
    provide by accident, and removing the truncation is what exposed it.
    MEASURED 2026-09-18 on the selftest's own single-sideband fixture, with the
    shipped ungated pair search (tmp/pair_gate_probe.py reproduces all four):

        peak list      gate          verdict
        8 strongest    none          no pair          <- correct, but only
        8 strongest    14 dB         no pair             because 7 candidates
        whole list     none          PAIR 48.304/111.648 MHz, df 31.7 MHz
        whole list     14 dB         no pair          <- correct on purpose

    With the whole ~1300-2000 peak list there are ~2e6 candidate pairs and the
    search is O(N^2) in coincidences: some pair is always within the 3-bin
    symmetry tolerance of ANY carrier, so C1 PASSed a transmitter with one
    sideband and handed C2/C3 a fabricated df (the full layer_c fixture drew a
    different coincidence, 38.0 MHz -- which is the point: the winner is
    arbitrary).  A local maximum that cannot clear the gate is not a line, and
    "carrier + symmetric pair" is a claim about LINES.  On the real capture the
    gate leaves 144 eligible peaks and the true pair outsums every coincidence by
    ~150x (562 + 683 against a noise maximum of 4), so nothing real is excluded.
    """
    side = [p for p in peaks
            if abs(p[0] - lo_hz) > LO_LINE_TOL_HZ
            and p[2] >= CARRIER_SNR_MIN_DB]
    lower = [p for p in side if p[0] < lo_hz]
    upper = [p for p in side if p[0] > lo_hz]
    best = None
    best_sym = None
    for l in lower:
        for u in upper:
            sym = abs((lo_hz - l[0]) - (u[0] - lo_hz))
            if best_sym is None or sym < best_sym:
                best_sym = sym
            if sym > SYM_TOL_HZ or (lo_hz - l[0]) > DF1_MAX_HZ:
                continue
            key = (l[1] + u[1], -sym)
            if best is None or key > best[0]:
                best = (key, l, u, sym)
    if best is None:
        # Counted so a near miss is legible: "no pair" over 100 eligible lines on
        # each side is a very different spectrum from "no pair" because the gate
        # left one line on each side.
        why = ("no symmetric pair about %.4f MHz among the %d / %d lines above "
               "%.1f dB either side of it (best asymmetry %s, limit %.0f kHz; "
               "offset limit %.1f MHz)"
               % (lo_hz / 1e6, len(lower), len(upper), CARRIER_SNR_MIN_DB,
                  "%.1f kHz" % (best_sym / 1e3) if best_sym is not None else "n/a",
                  SYM_TOL_HZ / 1e3, DF1_MAX_HZ / 1e6))
        return None, None, best_sym, why
    return best[1], best[2], best[3], None


class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, name, verdict, detail=""):
        self.rows.append((name, verdict, detail))
        print("%-6s %-46s %s" % (verdict, name, detail))

    def count(self, verdict):
        return sum(1 for r in self.rows if r[1] == verdict)

    def find(self, needle):
        """The first row whose name contains needle, or None.

        For the selftest, which judges the criteria rather than the hardware.
        """
        for r in self.rows:
            if needle in r[0]:
                return r
        return None

    def summary(self, not_applicable_note=None):
        """Print the tally and the meaning of each verdict class.

        `not_applicable_note` is an extra line printed under the standard
        NOT-APPLICABLE gloss.  It exists for the CHAN2 report, where "N/A" means
        something else -- a criterion that does not EXIST there, not one the
        instrument could not reach -- and the standard gloss would be actively
        misleading.  It defaults to None, so a CHAN1 run prints exactly what it
        always did.
        """
        p = self.count("PASS")
        f = self.count("FAIL")
        n = self.count("N/A")
        r = self.count("RECORD")
        print("\n=== %d PASS, %d FAIL, %d NOT-APPLICABLE, %d RECORD-ONLY ==="
              % (p, f, n, r))
        if n:
            print("NOT-APPLICABLE means instrument-limited, not a failure "
                  "(spec 6.1 B layer).")
            if not_applicable_note:
                print(not_applicable_note)
        if r:
            print("RECORD-ONLY entries carry no verdict by design (spec 6.1 C6).")
        return f


def layer_b(rep, wav_fft, lo_hz, label, chan=DEFAULT_CHANNEL,
            want_2r2t=False):
    """B1/B2: a resolvable line at LO, and it moves when LO moves.

    Frequency only.  No amplitude threshold -- see the module docstring.

    Valid on every channel, including one with no DDS tone: B's only signal
    source is LO leakage, which TX2 has.  `chan` is therefore threaded through
    rather than specialised.
    """
    freqs, spec = wav_fft(chan)
    peaks = peak_freqs(freqs, spec)
    hit = find_nearest(peaks, lo_hz, tol_hz=LO_SHIFT_TOL_HZ)

    if hit is None:
        rep.add("B1 (%s) line resolvable at LO" % label, "N/A",
                "strongest lines: %s" % ", ".join("%.1f MHz" % (f / 1e6)
                                                  for f, _, _ in peaks[:4]))
        return None
    rep.add("B1 (%s) line resolvable at LO" % label, "PASS",
            "%.3f MHz (target %.1f MHz, snr %.1f dB)" % (hit[0] / 1e6,
                                                         lo_hz / 1e6, hit[2]))
    return hit[0]


def board_init(board, atten_mdb=SAFE_ATTEN_MDB, lo_hz=None, want_2r2t=False,
               phase_inc=None):
    """Issue an `init` carrying whichever phy-dependent knobs are wanted.

    `atten_mdb` DEFAULTS TO THE SAFE FLOOR and must never be left off.  A bare
    `init` takes rf_main.c's struct value, which is 0 -- full TX power into the
    RX loopback over this board's TX1->RX1 cable.  Making the safe value the
    default (rather than a constant each call site re-states) is what makes
    "someone forgot `atten`" impossible rather than merely unlikely.

    R7: `atten` and `lo` are keyword/value pairs on `init`, not subcommands.
    They cannot be subcommands -- `phy` is a process-local static that only
    `init` ever creates, and pynq_ssh.py has no stdin, so no session is
    possible.  A bare `init` therefore RESETS both to rf_main.c's struct values
    (atten 0, LO 80 MHz); every site that wants a non-default value has to say
    so in that same invocation.

    Each `init` costs a full ad9361_init (a VCO calibration), which is why the
    caller sleeps after it.

    `phase_inc`, when given, is issued as a SECOND command right after the init,
    and it is not decoration: `init` STOPS THE DDS AND LEAVES phase_inc = 0.
    The board app says so on every init ("phase_inc was left at 0; restore it
    with `board_app phase_inc <n>` before measuring"), the script's own usage
    text says so under `init`, and it was MEASURED here 2026-09-20 -- `phase_inc
    128` read back 128, then `init atten 40000 lo 80000000 2r2t` read back 0
    with no phase_inc command in between.  A caller that re-inits the board and
    then captures has to put the increment back or it measures a tone that is no
    longer there: with the register at 0 the sidebands collapse onto the LO and
    the bin they used to occupy holds the floor.

    That is not hypothetical, and it is the reason this parameter exists rather
    than each call site re-stating the command.  C4 and C5 both re-init -- C4 to
    shift the LO, C5 to move the attenuator -- and neither restored it, so C5
    compared two floor readings and printed a "-1.07 dB difference" as a FAIL
    about the attenuator.  `init` and the restore it needs belong in one place.

    want_2r2t MUST travel with the mode, and getting this wrong is destructive
    rather than merely wrong.  `init` drives RESETB low then high and only THEN
    checks phy_mode (the gpio block in rf_main.c's `init`, before
    ad9361_init), so a bare `init` issued while the PL
    sits in 2R2T resets the AD9361 and then aborts on the assertion -- leaving
    the part unprogrammed in the middle of a measurement, with the abort text
    pointing at the PL rather than at this call.  Passing 2r2t makes init write
    phy_mode = 0 itself and expect 0, which is the supported way to move both
    sides together.
    """
    args = ["init"]
    if atten_mdb is not None:
        args += ["atten", "%d" % int(atten_mdb)]
    if lo_hz is not None:
        args += ["lo", "%d" % int(lo_hz)]
    if want_2r2t:
        args += ["2r2t"]
    out = board.cmd(" ".join(args))
    if phase_inc is not None:
        board.cmd("phase_inc %d" % int(phase_inc))
    return out


def read_sampling_rate_hz(init_output):
    """The TX sampling rate `init` read back from the part, or 0.

    FAIL-SAFE in the same way read_f_data_clk_hz is: 0 covers "no such line", an
    unparseable line and a dead transport alike, and C3 answers FAIL for a 0.  A
    criterion that turned a failed observation into a skip would report a board
    it never measured -- the defect class this task exists to avoid.

    TX, not RX: the tone C3 measures comes out of the transmit path, so the
    transmit path's rate is the one that has to explain it.  The number is a
    register recomputation, not the requested value echoed back:
    rf_main.c calls `ad9361_get_tx_sampling_freq`, which resolves through
    clk_get_rate -> ad9361_clk_factor_recalc_rate -> ad9361_get_clk_scaler, and
    that last one does real SPI reads of the clock-tree registers.

    A line scan, not a positional parse: pynq_ssh.py prints a per-command banner
    into the same stream BoardCmd.cmd returns, and rf_main.c prints other lines
    around this one.  The banner cannot contain the key.
    """
    for line in init_output.splitlines():
        if "TX sampling rate readback" not in line:
            continue
        try:
            return float(line.split("=")[1].split("Hz")[0].strip())
        except (IndexError, ValueError):
            return 0.0
    return 0.0


def read_f_data_clk_hz(board):
    """f_DATA_CLK from the PL STATUS register, via `board_app status`.

    FAIL-SAFE, and deliberately kept that way: this returns 0 both for a
    measured "NO CLOCK" and for a "STALE" window, an unparseable reply, or a
    dead transport, and C3 answers FAIL for a 0.  A transport failure must land
    on FAIL, not on a vacuous PASS -- conflating the two is the defect class
    this whole task exists to avoid.  Do not "improve" this into a skip.

    A line scan, not a positional parse: pynq_ssh.py prints a per-command banner
    into the same stream BoardCmd.cmd returns, and rf_main.c prints other
    f_DATA_CLK-free lines around this one.  The banner cannot contain the key.
    """
    out = board.cmd("status")
    for line in out.splitlines():
        if "f_DATA_CLK" not in line:
            continue
        if "NO CLOCK" in line or "STALE" in line:
            return 0
        if "Hz" in line:
            try:
                return float(line.split("=")[1].split("Hz")[0].strip())
            except (IndexError, ValueError):
                return 0
    return 0


def layer_c(rep, board, wav_fft, chan=DEFAULT_CHANNEL, want_2r2t=False):
    """C1..C6 -- the primary criteria.  See spec 6.1 Step 4's table.

    `chan` is the scope channel to measure.  It is an ARGUMENT with a default,
    never a constant baked into a call site: the C-layer criteria are written
    for a transmitter with a tone, and on CHAN2 (TX2, no baseband data) the
    tone-derived ones have nothing to measure and say so explicitly rather than
    passing.  See DDS_TONE_CHANNELS.

    Returns the measured `fs / f_DATA_CLK` ratio, or None when neither the ratio
    nor the df was available.  The verdicts live in `rep`; this is only what the
    selftest reads to check that the ratio tracks the fixture it was given.
    """
    tone = chan in DDS_TONE_CHANNELS

    # --- measurement 1: baseline, LO = 80 MHz, safe atten, phase_inc 128 ---
    # The attenuation is the SAFE FLOOR, not 0.  Spec 6.1 Step 4 wanted the
    # maximum LO-leakage visibility, but TX1 is cabled to RX1 here, so "atten 0
    # for visibility" is full TX power into the receiver.  Layer B is
    # frequency-only and layer C judges relative quantities precisely so that
    # this trade is affordable; see SAFE_ATTEN_MDB.
    # phase_inc travels WITH the init because `init` itself stops the DDS and
    # leaves the register at 0 -- the comment that used to sit here claimed the
    # opposite ("`init` drives RESETB low/high, which hardware-resets the AD9361
    # but does NOT clear the PL phase_inc register"), and that claim is what let
    # C4's and C5's re-inits go without a restore for as long as they did.  It is
    # issued on every channel even though it only drives the DDS, so that the two
    # channels are measured from identical register state.
    # The reply is KEPT: it carries the sampling-rate readback C3 compares the
    # scope's df against.  Nothing re-inits between here and C3 (C4's and C5's
    # re-inits are downstream of it), so this is the reference that belongs to
    # the captures C2 and C3 are computed from.
    #
    # ...and it is PRINTED, for the same reason it is kept.  C3's verdict now
    # rests on a number that comes off the board, and a verdict whose one piece
    # of hardware evidence never reaches the log is a verdict the log cannot
    # support -- the failure mode this project keeps re-learning.  The other
    # inits' replies stay unprinted so the log does not carry six copies.
    baseline_init = board_init(board, lo_hz=int(LO_NOMINAL_HZ),
                               want_2r2t=want_2r2t, phase_inc=PHASE_INC_1)
    for line in baseline_init.splitlines():
        if "sampling rate" in line:
            print("  %s" % line.strip())
    time.sleep(0.3)

    freqs, spec = wav_fft(chan)
    peaks = peak_freqs(freqs, spec)

    # C1: presence, in TWO rows rather than one.  The pair and the carrier are
    # measured by different things and are not equally available at the safety
    # floor, so folding them together forces one of them to answer for the other.
    #
    # The PAIR is the criterion's substance: the DDS is I-only, so
    # dac_data_q1 is hard zero and the output is double-sideband (spec 7).  A
    # single sideband would mean something other than this DDS is producing the
    # tone -- which is why `sym`, the residual asymmetry of the pair, is a
    # BOUNDED input to the choice of pair and not merely a quantity to minimise.
    # Minimising it and then accepting whatever won is a criterion satisfied by
    # any line near LO with anything above it and anything below it, and it hands
    # a fabricated df1 to C2 and C3.  Its centre is the COMMANDED LO -- see
    # symmetric_pair for why taking it from the spectrum was a defect.
    #
    # The CARRIER is the separate half.  It is LO leakage, i.e. the suppressed
    # carrier of an I-only upconversion, and it sits ~35 dB below the sidebands;
    # at SAFE_ATTEN_MDB it is below the capture floor.  Judging that as a FAIL
    # would report the attenuator as a transmitter fault, and judging it as a
    # PASS would be reporting a measurement that was never made.  It is N/A.
    if tone:
        carrier, carrier_why = find_carrier(peaks)
        l, u, best_sym, pair_why = symmetric_pair(peaks)
    else:
        # No DDS on this channel, so "carrier + symmetric pair" is not the
        # question.  The half of 4.1 that IS askable here is presence: is LO
        # leakage resolvable at the LO frequency, clearing the same
        # CARRIER_SNR_MIN_DB gate the carrier has to clear?  The pair row is
        # printed separately as NOT APPLICABLE -- it must be visible as a
        # criterion that was NOT judged, never folded into a PASS.
        carrier = find_nearest(peaks, LO_NOMINAL_HZ, LO_LINE_TOL_HZ,
                               min_snr_db=CARRIER_SNR_MIN_DB)
        l = u = None
        best_sym = 0.0
        pair_why = None

    df1 = None
    if not tone:
        if carrier is None:
            rep.add("C1 (%s) LO leakage line present at LO" % chan, "FAIL",
                    "no line at %.1f MHz clears the %.0f dB carrier gate; %s"
                    % (LO_NOMINAL_HZ / 1e6, CARRIER_SNR_MIN_DB,
                       _peaks_str(peaks)))
        else:
            rep.add("C1 (%s) LO leakage line present at LO" % chan, "PASS",
                    "%.3f MHz (%.1f dB above the floor)"
                    % (carrier[0] / 1e6, carrier[2]))
        rep.add("C1 (%s) carrier + symmetric pair" % chan, "N/A",
                _no_tone_reason(chan))
    else:
        pair_ok = l is not None and u is not None
        if pair_ok:
            # Half the separation, not the distance from one line to the centre:
            # it averages the two bins' quantisation error instead of inheriting
            # one of them, which matters at C2's 2% tolerance (the DF1_MAX_HZ /
            # SYM_TOL_HZ gates are unchanged, and so is every amplitude gate).
            df1 = (u[0] - l[0]) / 2.0
            rep.add("C1 symmetric sideband pair about LO", "PASS",
                    "LO %.3f MHz (commanded and read back), sidebands %.3f / "
                    "%.3f MHz, df = %.1f kHz, asymmetry %.1f kHz"
                    % (LO_NOMINAL_HZ / 1e6, l[0] / 1e6, u[0] / 1e6,
                       df1 / 1e3, best_sym / 1e3))
        else:
            rep.add("C1 symmetric sideband pair about LO", "FAIL",
                    "%s; %s" % (pair_why, _peaks_str(peaks)))

        if carrier is not None:
            rep.add("C1 carrier (LO leakage) present at LO", "PASS",
                    "%.4f MHz (%.1f dB above the floor)"
                    % (carrier[0] / 1e6, carrier[2]))
        elif pair_ok:
            rep.add("C1 carrier (LO leakage) present at LO", "N/A",
                    "%s -- NOT APPLICABLE and not FAIL: the symmetric DDS pair "
                    "above is present, correct and translating, which is "
                    "independent evidence that the transmitter and its LO are "
                    "running.  What is missing is the capture floor, not the "
                    "carrier" % carrier_why)
        else:
            rep.add("C1 carrier (LO leakage) present at LO", "FAIL", carrier_why)

    # C2: df must be LINEAR in phase_inc.  This is what separates "a DDS tone"
    # from "a spurious line that happens to sit near LO".  Note this is a RATIO
    # of two offsets measured at the same fs, so it is entirely independent of
    # the absolute fs <-> DATA_CLK question that C3 adjudicates.
    df2 = None
    if not tone:
        rep.add("C2 df is linear in phase_inc", "N/A", _no_tone_reason(chan))
    elif df1 is not None and df1 > 0:
        board.cmd("phase_inc %d" % PHASE_INC_2)
        time.sleep(0.3)
        freqs2, spec2 = wav_fft(chan)
        peaks2 = peak_freqs(freqs2, spec2)
        # Same pair rule as C1, about the same commanded LO, and deliberately
        # NOT routed through the carrier.  C2 asks how df scales with phase_inc;
        # a carrier that has fallen to the floor must not be able to fabricate
        # df2 (it used to: a noise bin near the LO supplied the centre, and the
        # "lower sideband" was then whatever noise sat below it).  The carrier
        # does not move with phase_inc anyway, so it carried no information C2
        # needed -- only a way to be wrong.
        l2, u2, _, _why2 = symmetric_pair(peaks2)
        df2 = None
        if l2 is not None and u2 is not None:
            df2 = (u2[0] - l2[0]) / 2.0
        if df2 is not None and df2 > 0:
            ratio = df2 / df1
            ok = DF_RATIO_MIN <= ratio <= DF_RATIO_MAX
            rep.add("C2 df is linear in phase_inc (ratio 2.00 +/- 2%)",
                    "PASS" if ok else "FAIL",
                    "df1 = %.1f kHz, df2 = %.1f kHz, ratio = %.4f"
                    % (df1 / 1e3, df2 / 1e3, ratio))
        else:
            rep.add("C2 df is linear in phase_inc", "FAIL", "no sideband at 256")
    else:
        rep.add("C2 df is linear in phase_inc", "FAIL", "C1 gave no df1")

    # C3: does the SCOPE's df imply the sample rate the PART itself reports?
    #
    # The earlier form asked which RATIO of fs to f_DATA_CLK the df matched, out
    # of a pre-registered list.  That form was mode-dependent and wrong by
    # construction in 2R2T -- the true ratio there is 60/240 = 1/4, which was in
    # no list, so C3 printed FAIL whatever the hardware did (plan 2.5.2, and the
    # FS_RATIO_CANDIDATES comment this replaces) -- and it routed the verdict
    # through f_DATA_CLK, a number the firmware prints after a 16-bit counter
    # wrap, i.e. through the quantity under dispute.
    #
    # This form needs no ratio and no f_DATA_CLK.  `df * 2^12 / phase_inc` is the
    # fs the transmit path has to be running at for the DDS to place a tone at
    # df, and `init` reads the transmit path's ACTUAL rate back out of the part's
    # clock-tree registers.  So the comparison is between an external instrument
    # and the device's own register state, and it holds in 1R1T and 2R2T alike.
    #
    # What it does NOT establish: that the part's rate is the RIGHT one.  Both
    # sides move together if the rate is merely mis-set -- that is criterion
    # 2.5's job (requested vs read back).  C3 says the DDS arithmetic and the
    # reported rate agree; it does not audit the rate.
    #
    # The FIR is enabled, so the bare df = inc/2^12 * fs relation is not directly
    # trustworthy on AMPLITUDE.  It is used only for POSITION: C2 already
    # established df scales exactly with phase_inc, which a fixed LTI filter
    # cannot produce, so the line is the DDS tone.
    measured_ratio = None
    if not tone:
        # Nothing measured a df on this channel, so there is no relation to
        # adjudicate AND none to cross-check against the document.  Both rows
        # exist and both say NOT APPLICABLE: dropping them would let the report
        # read as though the criteria had run and come out clean.
        rep.add("C3 fs from df vs read-back fs", "N/A", _no_tone_reason(chan))
        rep.add("C3x documented expectation cross-check", "N/A",
                _no_tone_reason(chan))
    elif df1 is not None and df1 > 0:
        fs_from_df = df1 * (1 << 12) / PHASE_INC_1
        fs_readback = read_sampling_rate_hz(baseline_init)
        if fs_readback:
            rel = abs(fs_from_df - fs_readback) / fs_readback
            verdict = "PASS" if rel <= FS_FIT_MAX else "FAIL"
            rep.add("C3 fs from df vs read-back fs", verdict,
                    "df = %.1f kHz -> fs = %.3f MHz (df * 2^12 / phase_inc); "
                    "TX sampling rate read-back = %.3f MHz; off by %.2f%% "
                    "(limit %.0f%%)%s"
                    % (df1 / 1e3, fs_from_df / 1e6, fs_readback / 1e6,
                       rel * 100.0, FS_FIT_MAX * 100.0,
                       "" if verdict == "PASS" else
                       " -- THE DDS SPACING AND THE PART'S OWN RATE DISAGREE"))
        else:
            # read_sampling_rate_hz's fail-safe: no line, unparseable, or dead
            # transport all arrive here, and all of them are FAIL rather than a
            # skip.  A criterion that skipped would report a board it never
            # measured.
            rep.add("C3 fs from df vs read-back fs", "FAIL",
                    "`init` reported no TX sampling rate (or the transport "
                    "failed), so there is nothing to compare the df against; "
                    "run `board_app init` and read its criterion-2.5 line")
        # The documented expectation is SECOND-HAND and must not manufacture a
        # verdict: example_project_notes.md:85 itself flags the source XDC's
        # 25 MHz constraint as possibly wrong.  So this stays RECORD, and it is
        # now the only place the ratio is named at all -- the verdict above no
        # longer depends on f_DATA_CLK.
        f_dclk = read_f_data_clk_hz(board)
        if not f_dclk:
            rep.add("C3x documented expectation cross-check", "RECORD",
                    "f_DATA_CLK unreadable (STALE / NO CLOCK / transport), so "
                    "there is no measured ratio to cross-check against the "
                    "documented fs = %g * f_DATA_CLK" % FS_RATIO_DOCUMENTED)
        elif not fs_readback:
            rep.add("C3x documented expectation cross-check", "RECORD",
                    "no read-back rate, so there is no measured fs / f_DATA_CLK "
                    "ratio to cross-check against the documented fs = %g * "
                    "f_DATA_CLK" % FS_RATIO_DOCUMENTED)
        else:
            measured_ratio = fs_readback / f_dclk
            rep.add("C3x documented expectation cross-check", "RECORD",
                    "documented (LVDS DDR) fs = %g * f_DATA_CLK; measured "
                    "%.4g -> %s"
                    % (FS_RATIO_DOCUMENTED, measured_ratio,
                       "AGREES" if abs(measured_ratio - FS_RATIO_DOCUMENTED)
                       < 1e-9 else
                       "DISAGREES -- measurement stands, the document is the "
                       "cross-check"))
    else:
        rep.add("C3 fs from df vs read-back fs", "FAIL", "no df to adjudicate")
        rep.add("C3x documented expectation cross-check", "N/A",
                "no df, so there is no measured ratio to cross-check")

    # C4: move LO and require EVERY line to move with it.  This is what shows
    # the lines are baseband content up-converted, not artifacts of the scope
    # or of the LO itself.
    #
    # The baseline is re-captured here at PHASE_INC_1 rather than reusing C1's
    # peaks.  C2 left the DDS at PHASE_INC_2, so reusing them would compare a
    # baseline measured at one phase increment against a shifted capture at
    # another -- and the sidebands are the peaks that move, by df2 - df1 on top
    # of the 5 MHz LO translation (0.469 / 0.938 / 1.875 MHz for the three
    # candidate ratios).  That extra displacement eats up to 94% of the 2 MHz
    # tolerance, so C4 would pass only by luck at ratio 2 and false-FAIL on any
    # additional measurement error.  C4 is about the LO, so phase_inc is held
    # constant across it: re-assert, re-capture, then move the LO.
    #
    # `before` is the lines C1 identified -- the LO leakage line and its
    # symmetric sideband pair, re-identified from THIS capture, which is taken
    # at the same phase increment and LO as C1's so it must find the same
    # lines.  It is deliberately NOT the six strongest peaks.  peak_freqs gates
    # on 6 dB above the MEDIAN, and a real capture's floor is Rayleigh, whose
    # local maxima routinely run 11-14 dB above its median -- 1391 of them in a
    # noise-only 62500-point capture were measured.  "The six strongest peaks"
    # is therefore three signal lines plus three noise maxima, and requiring
    # all six to translate judges sidelobe statistics rather than the board:
    # a healthy transmitter with LO leakage and a correct pair false-FAILed
    # with "336.0->MISSING 350.0->MISSING 388.4->MISSING".  So the list is now
    # exactly the lines C4 is entitled to judge: the pair always, and the LO
    # leakage line only when find_carrier established it in this capture.  The
    # detail prints which lines were used, so "two" and "three" are told apart
    # in the report rather than assumed.  On a channel with no tone the list is
    # the leakage line alone, and printing it is the same safeguard.
    if tone:
        c4_name = "C4 all lines translate with LO (+5 MHz)"
        c4_no_base = "C4 all lines translate with LO"
    else:
        # On a channel with no tone there is exactly one line to translate,
        # the LO leakage one -- and it is still a real judgement: it is what
        # separates "this line is the LO" from "this line is something else in
        # the capture".  Row names differ by channel so the report cannot be
        # read as claiming the three-line test ran on CHAN2.
        c4_name = "C4 (%s) LO line translates with LO (+5 MHz)" % chan
        c4_no_base = "C4 (%s) LO line translates with LO" % chan
    if (l is not None and u is not None) if tone else (carrier is not None):
        board.cmd("phase_inc %d" % PHASE_INC_1)
        time.sleep(0.3)
        freqs4, spec4 = wav_fft(chan)
        peaks4 = peak_freqs(freqs4, spec4)
        if tone:
            carrier4, _ = find_carrier(peaks4)
            l4, u4, _, _ = symmetric_pair(peaks4)
            # The carrier joins the list only when it was actually established
            # in THIS capture.  C4's claim is "every line moves with the LO", and
            # a line that could not be measured cannot be one of them -- at the
            # safety floor that means two lines, not three, and the detail prints
            # which ones were used.  The pair is always measurable.
            if l4 is None or u4 is None:
                before = None
            else:
                before = ([carrier4[0]] if carrier4 is not None else []) + [l4[0], u4[0]]
        else:
            lo4 = find_nearest(peaks4, LO_NOMINAL_HZ, LO_LINE_TOL_HZ,
                               min_snr_db=CARRIER_SNR_MIN_DB)
            before = None if lo4 is None else [lo4[0]]
        if before is None:
            rep.add(c4_name, "FAIL",
                    "the baseline at LO=%.1f MHz has no %s; %s"
                    % (LO_NOMINAL_HZ / 1e6,
                       "symmetric pair" if tone else "line at LO",
                       _peaks_str(peaks4)))
        else:
            board_init(board, lo_hz=int(LO_SHIFTED_HZ), want_2r2t=want_2r2t,
                       phase_inc=PHASE_INC_1)
            time.sleep(0.3)
            freqs3, spec3 = wav_fft(chan)
            peaks3 = peak_freqs(freqs3, spec3)
            shifted_ok = True
            details = []
            for f0 in before:
                target = f0 + (LO_SHIFTED_HZ - LO_NOMINAL_HZ)
                hit = find_nearest(peaks3, target, tol_hz=LO_SHIFT_TOL_HZ)
                details.append("%.1f->%s" % (f0 / 1e6,
                                             "%.1f" % (hit[0] / 1e6) if hit else "MISSING"))
                if hit is None:
                    shifted_ok = False
            rep.add(c4_name,
                    "PASS" if shifted_ok else "FAIL",
                    "lines used %s MHz: %s"
                    % ("/".join("%.4f" % (f / 1e6) for f in before),
                       " ".join(details)))
            board_init(board, lo_hz=int(LO_NOMINAL_HZ), want_2r2t=want_2r2t,
                       phase_inc=PHASE_INC_1)
            time.sleep(0.3)
    else:
        rep.add(c4_no_base, "FAIL", "no baseline lines")

    # C5: attenuation difference.  A DIFFERENCE, so the scope's front-end
    # response cancels -- the whole point of judging relatively.
    #
    # TONE-DERIVED, so it does not exist on CHAN2: the difference is measured on
    # the strongest bin, which on a tone-less channel is LO leakage -- and LO
    # leakage is not in the attenuator's path, which is precisely what C6
    # records (measured 0.2 dB over the same 20 dB step).  Running it there
    # would produce a FAIL that says nothing about the board.
    if not tone:
        rep.add("C5 atten difference", "N/A", _no_tone_reason(chan))
        rep.add("C6 LO leakage vs attenuation", "N/A",
                "not measured on %s: this record is a by-product of C5's two "
                "captures, and C5 has nothing to measure here (no DDS tone).  "
                "NOT a PASS." % chan)
    elif l is not None:
        # Both points carry phase_inc for the same reason C4's re-inits do:
        # `init` leaves the DDS stopped at phase_inc = 0, and the bin C5 compares
        # is the SIDEBAND.  Without the restore, neither capture contains the
        # line C1 named, and the "difference" is between two floor readings.
        board_init(board, atten_mdb=ATTEN_LOW_MDB, want_2r2t=want_2r2t,
                   phase_inc=PHASE_INC_1)
        time.sleep(0.3)
        freqs_low, s_low = avg_capture(wav_fft, chan, C5_CAPTURES)
        board_init(board, atten_mdb=ATTEN_HIGH_MDB, want_2r2t=want_2r2t,
                   phase_inc=PHASE_INC_1)
        time.sleep(0.3)
        _, s_high = avg_capture(wav_fft, chan, C5_CAPTURES)

        # The bin is the DDS SIDEBAND found in C1 -- NOT the global argmax.
        # Only the sidebands travel through the attenuator; LO leakage does not
        # (that is what C6 records, ~0.2 dB over the same 20 dB step).  At a low
        # attenuation the sideband happens to be the strongest bin, so argmax
        # landed on it by coincidence and the two agreed.  At SAFE_ATTEN_MDB the
        # leakage line is the strongest, so an argmax-based C5 measures the ONE
        # line guaranteed not to respond to the attenuator: measured on this
        # fixture at the safe floor, 0.09 dB -- a PASS-shaped row describing no
        # measurement.  Naming the frequency also makes the two captures
        # comparable in the first place, which argmax never did.
        bin_hz = freqs_low[1] - freqs_low[0]
        k = int(round(l[0] / bin_hz))
        if not (0 <= k < len(s_low) and k < len(s_high)):
            rep.add("C5 atten difference", "FAIL",
                    "the C1 sideband at %.4f MHz is outside the capture"
                    % (l[0] / 1e6))
            k = None
        if k is not None:
            a_low = max(s_low[k], 1e-12)
            a_high = max(s_high[k], 1e-12)
            delta = 20.0 * np.log10(a_low / a_high)
            # The label is DERIVED, not typed: a hardcoded "10000->30000" here is
            # how the printed criterion and the command actually issued drift
            # apart, and the reader then judges a threshold the board was never
            # driven at.
            label = ("C5 atten %d->%d gives a %.0f +/- %.0f dB difference"
                     % (ATTEN_LOW_MDB, ATTEN_HIGH_MDB, ATTEN_DELTA_DB,
                        ATTEN_TOL_DB))

            # MEASURABILITY FIRST, and judged from the capture rather than
            # assumed from the fixture: a difference between two readings is
            # only a difference between the two tones while BOTH are above the
            # noise floor.  Once the upper reading has sunk into the floor it is
            # a difference between the lower tone and the floor, which reports a
            # number -- and that number is neither the attenuator's response nor
            # a hardware fault, so neither PASS nor FAIL is honest.
            #
            # This is not hypothetical, and it is why the pair is where it is:
            # at 40000 -> 60000 the upper point was MEASURED to swing -5.7 ..
            # 15.4 dB capture to capture while the waveform itself did not move
            # (rf_overlay/out/diag_floor.log).  The gate below is what turns that
            # into a truthful N/A, so it has to read an ESTIMATE -- hence
            # avg_capture.  Judging it from one frame made the verdict a coin
            # flip: measured 6 of 8 captures cleared the gate at a single fixed
            # operating point, so the same board could be reported as either
            # "PASS" or "the instrument is at fault" depending on when it was
            # sampled.
            # `median(s_high)` is the floor: it is a 31250-bin spectrum of which
            # a handful of bins are lines, so the median is the Rayleigh floor
            # and not a line.
            floor_high = max(float(np.median(s_high)), 1e-12)
            snr_high = 20.0 * np.log10(a_high / floor_high)
            if snr_high < PEAK_SNR_MIN_DB:
                rep.add(label, "N/A",
                        "the tone at %d mdB reads %.1f dB above the capture "
                        "floor, under the %.1f dB a difference needs; the upper "
                        "point is floor-limited, so this is the instrument and "
                        "not the attenuator (%d-capture estimate)"
                        % (ATTEN_HIGH_MDB, snr_high, PEAK_SNR_MIN_DB,
                           C5_CAPTURES))
            else:
                ok = abs(delta - ATTEN_DELTA_DB) <= ATTEN_TOL_DB
                rep.add(label, "PASS" if ok else "FAIL",
                        "measured difference = %.2f dB (bin %.3f MHz, upper "
                        "point %.1f dB above floor, %d-capture estimate)"
                        % (delta, freqs_low[k] / 1e6, snr_high, C5_CAPTURES))

        # C6: LO leakage vs the signal lines.  RECORD ONLY, never judge: it
        # says where the attenuator sits relative to the leakage path, which
        # is informative but not something this round set a criterion for.
        lo_peak_low = find_nearest(peak_freqs(freqs_low, s_low), LO_NOMINAL_HZ,
                                   LO_LINE_TOL_HZ)
        lo_peak_high = find_nearest(peak_freqs(freqs_low, s_high), LO_NOMINAL_HZ,
                                    LO_LINE_TOL_HZ)
        if lo_peak_low and lo_peak_high:
            rep.add("C6 LO leakage vs attenuation", "RECORD",
                    "%.2f dB change (NOT a criterion -- spec 6.1 C6)"
                    % (20.0 * np.log10(lo_peak_low[1] / max(lo_peak_high[1], 1e-12))))
        else:
            rep.add("C6 LO leakage vs attenuation", "RECORD",
                    "leakage line not resolvable in one or both captures")
    else:
        rep.add("C5 atten difference", "FAIL", "no baseline lines")
        rep.add("C6 LO leakage vs attenuation", "N/A", "no baseline lines")

    return measured_ratio


def capture_fft(inst, channel):
    """Freeze, read the NORM waveform, and return (freqs, |spectrum|).

    :WAV:POIN is deliberately never written here.  Writing it pins the record
    length to a manual value and :WAV:POIN:MODE NORM does not reset it, after
    which every waveform-based criterion in every suite misjudges at once --
    this project has already lost a session to that.
    """
    inst.write(":STOP")
    inst.write(":WAV:SOUR " + channel)
    time.sleep(0.05)
    xinc = float(inst.query(":WAV:XINC?"))
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    inst.write(":RUN")
    y = np.asarray(data, dtype=float)
    y = y - y.mean()
    n = len(y)
    spec = np.abs(np.fft.rfft(y * np.hanning(n)))
    return np.fft.rfftfreq(n, xinc), spec


def avg_capture(capture, channel, n):
    """Average `n` captures and return (freqs, RMS-amplitude spectrum).

    The combination is RMS (`sqrt(mean(s_i**2))`), NOT a mean of magnitudes, and
    the choice matters because every caller puts this through `20*log10`:

      * a coherent line has the same magnitude in every capture, so its RMS is
        exactly that magnitude -- the reading is unchanged and comparable with a
        single-capture one;
      * the noise floor FLUCTUATES, and RMS is the estimator whose `20*log10`
        estimates the floor's power.  Averaging magnitudes would instead report
        the mean of a Rayleigh distribution (1.25x its median), i.e. it would
        move the floor by 2 dB and every dB-above-floor number in the suite
        would silently acquire that offset.

    `capture` is a parameter rather than a direct call to capture_fft so that
    the selftest can drive this with a synthetic source, the same injection point
    layer_c already uses.  Taking N captures is how a criterion stops deciding
    from one draw of a quantity that was MEASURED to swing 14 dB capture to
    capture (rf_overlay/out/diag_floor.log).

    The settle delay is NOT politeness -- without it the instrument wedges.
    MEASURED 2026-09-20, twice, reproducibly: eight captures back to back left
    the DSOX1102A with `:WAV:POIN?` reading `+0` and every acquisition-dependent
    query (`:WAV:XINC?`, `:WAV:DATA?`, `:TRIG:STAT?`) hanging until the VISA
    timeout, while `*IDN?` and the pure-settings queries kept answering -- so it
    is the ACQUISITION that is stuck, not the USB session.  `:WAV:POIN 62500`
    and `:WAV:POIN:MODE MAX` do NOT clear it; only `:SYST:PRES` does, at the cost
    of the instrument's whole setup.  The same eight-capture loop WITH this
    delay ran 32 captures in four blocks earlier the same day without wedging
    (rf_overlay/out/diag_floor.py), which is where 0.15 s comes from -- it is a
    value observed to work, not a tuned one.  `capture_fft` ends with `:RUN`, so
    the delay gives the scope time to re-arm before the next `:STOP` interrupts
    it.
    """
    if n < 1:
        raise ValueError("avg_capture needs at least one capture, got %r" % (n,))
    freqs = None
    acc = None
    for i in range(n):
        if i:
            time.sleep(CAPTURE_SETTLE_S)
        f, s = capture(channel)
        if freqs is None:
            freqs = f
            acc = np.square(np.asarray(s, dtype=float))
        else:
            acc += np.square(np.asarray(s, dtype=float))
    return freqs, np.sqrt(acc / float(n))


class InstrumentSetupError(RuntimeError):
    """The scope cannot make the requested measurement at all.

    Raised instead of returning a sample rate, so the caller REFUSES to produce
    a signal verdict.  A measurement taken through an observation channel that
    is not actually observing is not a weak verdict, it is a wrong one -- and
    the wrongness is invisible downstream, which is the whole reason this is an
    exception and not a printed warning.
    """


def read_channel_disp(inst, chan):
    """True / False if the scope says `chan` is displayed, else None.

    None means the display state could NOT BE DETERMINED -- a transport error,
    or a scope that does not answer this query.  It is deliberately not folded
    into either boolean: "off" and "cannot tell" have the same downstream
    symptom (a flat trace) but different fixes, and neither is evidence that a
    transmitter is quiet.

    Kept separate from scope_prepare so the selftest can judge the parsing on
    its own, without a scope.
    """
    try:
        r = inst.query(":%s:DISP?" % chan)
    except Exception:
        return None
    r = str(r).strip().strip("+").upper()
    if r in ("1", "ON"):
        return True
    if r in ("0", "OFF"):
        return False
    return None


def scope_prepare(inst, chan=DEFAULT_CHANNEL):
    """Fixed timebase, vertical scale and display state; then verify them.

    Auto-scale converges too slowly after a signal change and can capture an
    incomplete period, which is why the scale is fixed rather than searched.

    BOTH channels are armed with :DISP ON, and that is not cosmetic.  A scope
    channel that is not displayed does not participate in acquisition, so
    :WAV:SOUR <off channel> followed by :WAV:DATA? would return a flat trace --
    and a flat trace is exactly what a genuinely silent transmitter looks like.
    The two are indistinguishable downstream, so a criterion asked on an off
    channel would FAIL and be read as "this transmitter has no output".  On
    TX2, whose only legitimate content is LO leakage, that is a false NEGATIVE
    on a possibly working channel -- the worst direction of error for a
    bring-up, because it retires a channel that may be fine.  Arming both also
    keeps --channel from changing the measurement conditions between them.

    The state is then READ BACK for the channel being measured.  A definite "not
    displayed" is an InstrumentSetupError rather than a report full of FAILs,
    because "the probe was off" and "TX2 is dead" must not print the same thing.
    An unreadable state is reported and the run continues: refusing to start on
    an unanswerable query would take down the CHAN1 path that was already
    accepted, and silence about a guard that did not arm is how a guard comes to
    mean nothing.

    NOT BOARD-VERIFIED.  What an off channel's :WAV:DATA? actually returns on
    this DSOX1102A has not been measured; the guard is preventive, and its
    criterion is "an unarmed observation channel may not serve as evidence",
    not "a flat trace was observed".
    """
    inst.write(":TIM:SCAL %.9E" % TIM_SCAL)
    for c in CHANNEL_CHOICES:
        inst.write(":%s:SCAL %.9E" % (c, CHAN_SCAL))
        inst.write(":%s:OFFS 0" % c)
        inst.write(":%s:PROB 1" % c)
        inst.write(":%s:DISP ON" % c)
    inst.write(":RUN")
    time.sleep(0.5)

    disp = read_channel_disp(inst, chan)
    if disp is False:
        raise InstrumentSetupError(
            "the scope reports %s as NOT displayed (:%s:DISP? -> 0).  A channel "
            "that is not displayed does not take part in acquisition, so "
            ":WAV:DATA? on it returns a flat trace -- which is what a silent "
            "transmitter looks like too.  Refusing to report one as the other."
            % (chan, chan))
    if disp is None:
        print("[warn] the display state of %s could not be read back "
              "(:%s:DISP? did not answer).  The channel was sent :DISP ON, but "
              "that this took effect is UNVERIFIED -- if the trace below is "
              "flat, an off channel and a silent transmitter are "
              "indistinguishable." % (chan, chan))

    # Verify the sample rate actually reached the >= 500 MSa/s the FFT needs.
    # This is not cosmetic: below it, 80 MHz is not representable and every
    # criterion below would report an instrument artefact as a signal verdict.
    xinc = float(inst.query(":WAV:XINC?"))
    srate = 1.0 / xinc if xinc > 0 else 0.0
    n = len(inst.query_binary_values(":WAV:DATA?", datatype="B", container=list))
    res = (1.0 / (n * xinc)) if (n and xinc > 0) else 0.0
    print("[scope] %.3f GSa/s, %d points, %.1f us window, %.1f kHz resolution (%s)"
          % (srate / 1e9, n, n * xinc * 1e6, res / 1e3,
             "OK" if srate >= SRATE_MIN_HZ else "TOO LOW"))
    return srate


# ------------------------------------------------------------------ selftest
# The criteria must be falsified before they are trusted.  scope_verify.py's
# --selftest is the precedent: a criteria script that has never been shown to
# say FAIL on known-bad input is not evidence.
#
# The selftest drives layer_c itself, with a fake board and a synthetic
# spectrum, so the thing under test is the real criterion code path -- not a
# reimplementation of it.

class _FakeBoard(object):
    """A board that mirrors rf_main.c's CLI contract, with no hardware.

    The parsing is deliberately a transcription of rf_main.c's: argv[2] is a
    trace path only when it is not a keyword, `atten`/`lo` take a value, `dump`
    is bare, and a bare `init` resets both knobs to the struct defaults
    (atten 0, LO 80 MHz).  If the script ever calls the board in a shape the
    real board does not accept, this raises instead of quietly succeeding.
    """

    KEYWORDS = ("atten", "lo", "dump")

    def __init__(self, f_dclk_hz, fs_ratio, df_fn=None, atten_db_scale=True,
                 init_rate_hz="auto"):
        self.f_dclk = f_dclk_hz
        self.fs_ratio = fs_ratio
        self.df_fn = df_fn or (lambda inc: inc / float(1 << 12)
                               * self.f_dclk * self.fs_ratio)
        self.atten_db_scale = atten_db_scale
        # What `init` reports as the sampling-rate readback: "auto" = this
        # fixture's own fs (f_dclk * fs_ratio), a number = a part that reports
        # something else, None = a reply carrying no readback at all.  See
        # _init_reply.
        self.init_rate_hz = init_rate_hz
        self.atten_mdb = 0
        self.lo = int(LO_NOMINAL_HZ)
        self.phase_inc = PHASE_INC_1
        self.calls = []
        # The phase increment in force at each spectrum capture, so a criterion
        # that compares captures taken at DIFFERENT increments is detectable.
        self.fft_phase_incs = []

    def snapshot(self):
        """Called by the capture function; records what the DDS is set to."""
        self.fft_phase_incs.append(self.phase_inc)

    def _init_reply(self):
        """`init`'s reply, carrying the criterion-2.5 sampling-rate readback.

        Modelled because C3 compares the scope's df against it, and a fixture
        that omitted it would leave the fail-safe path unreachable -- the same
        shape of gap that let C4/C5 measure a stopped DDS for as long as they
        did.

        The real line is rf_main.c's, and the number in it is a genuine
        readback: `ad9361_get_rx_sampling_freq` -> `clk_get_rate` ->
        `ad9361_clk_factor_recalc_rate` -> `ad9361_get_clk_scaler`, which does
        14 SPI reads of the clock-tree registers.  It is not the requested
        value echoed back.
        """
        if self.init_rate_hz is None:
            return "RESULT PASS (init)\n"
        hz = int(round(self.f_dclk * self.fs_ratio
                       if self.init_rate_hz == "auto"
                       else float(self.init_rate_hz)))
        return ("RESULT PASS (init)\n"
                "sampling rate requested = 60000000 Hz on both paths "
                "(ref/main.c:503-504)\n"
                "RX sampling rate readback = %d Hz (criterion 2.5, requested "
                "60000000: %s)\n"
                "TX sampling rate readback = %d Hz (criterion 2.5, requested "
                "60000000: %s)\n"
                % (hz, "MATCH" if hz == 60000000 else "MISMATCH",
                   hz, "MATCH" if hz == 60000000 else "MISMATCH"))

    def cmd(self, args):
        self.calls.append(args)
        toks = args.split()
        if not toks:
            raise AssertionError("empty board command")
        if toks[0] == "init":
            i = 1
            if i < len(toks) and toks[i] not in self.KEYWORDS:
                i += 1                     # trace path
            while i < len(toks):
                if toks[i] == "dump":
                    i += 1
                elif toks[i] in ("atten", "lo"):
                    if i + 1 >= len(toks):
                        raise AssertionError("%s needs a value" % toks[i])
                    v = toks[i + 1]
                    if v.lstrip("-").isdigit() is False or v.startswith("-"):
                        raise AssertionError("malformed numeric arg %r" % v)
                    if toks[i] == "atten":
                        self.atten_mdb = int(v)
                    else:
                        self.lo = int(v)
                    i += 2
                else:
                    raise AssertionError("init: unexpected argument %r" % toks[i])
            # `init` stops the DDS and LEAVES phase_inc = 0, so any criterion
            # that captures a spectrum after one has to put the increment back.
            # Modelled here because NOT modelling it is how C4 and C5 both came
            # to re-init the board and then measure a tone that was no longer
            # there: this line is what makes the two checks below reachable.
            #
            # MEASURED on this board 2026-09-20: `phase_inc 128` then
            # `plstate` reads 128, then `init atten 40000 lo 80000000 2r2t`
            # then `plstate` reads 0 -- no phase_inc command in between.  The
            # spectrum agrees: with the increment at 0 the 78.128 MHz bin holds
            # the floor (single captures -2.2 .. 14.7 dB above it, i.e. noise)
            # while the LO line carries the collapsed tone at 30.4 dB, and
            # re-issuing `phase_inc 128` brings the sideband straight back to
            # 27.0 dB.  Repeated for both of C5's points with the increment
            # restored after each init: 45.2 dB at 20000 mdB and 26.2 dB at
            # 40000, a 19.0 dB step.
            #
            # The script's own usage text states the same thing under `init`
            # ("It stops the tone first and LEAVES phase_inc = 0"), and the
            # board app prints it on every init ("phase_inc was left at 0;
            # restore it with `board_app phase_inc <n>` before measuring").
            self.phase_inc = 0
            return self._init_reply()
        if toks[0] == "phase_inc":
            self.phase_inc = int(toks[1])
            return "phase_inc set to %d\n" % self.phase_inc
        if toks[0] == "status":
            return ("--- PL STATUS (current) ---\n"
                    "  raw        = 0x0F666657\n"
                    "  f_DATA_CLK = %d Hz  (%.3f MHz)\n"
                    % (self.f_dclk, self.f_dclk / 1e6))
        raise AssertionError("unexpected board command %r" % args)


def _synth_fft(board, rng, n=62500, xinc=1e-9,
               lo_amp=10.0 ** (FIX_CARRIER_SNR_DB / 20.0),
               sb_amp=10.0 ** (FIX_SIDEBAND_SNR_DB / 20.0)):
    """A synthetic spectrum matching what the fake board would produce.

    Signal lines scale with the board's attenuation (C5's model); LO leakage
    does not move with it (which is what C6 exists to record).  The noise floor
    is uniform, so the median is a faithful floor for peak_freqs.

    The default line amplitudes are the MEASURED ones (see FIX_CARRIER_SNR_DB):
    `_planted_spectrum` normalises by the floor median, so an amplitude of 10
    IS 20 dB above the floor.  A uniform floor can only ever produce peaks up to
    1.5x its median, i.e. 3.5 dB, well under PEAK_SNR_MIN_DB -- so on this floor
    peak_freqs returns the planted lines and nothing else, which is what makes
    these cases tests of the criteria's logic rather than of the picker.
    """
    board.snapshot()
    freqs = np.fft.rfftfreq(n, xinc)
    spec = 0.5 + rng.random(len(freqs))
    atten_db = board.atten_mdb / 1000.0
    sb = sb_amp * (10.0 ** (-atten_db / 20.0)) if board.atten_db_scale else sb_amp
    df = board.df_fn(board.phase_inc)
    for f, amp in ((board.lo, lo_amp), (board.lo - df, sb), (board.lo + df, sb)):
        k = int(round(f / (freqs[1] - freqs[0])))
        if 0 <= k < len(spec):
            spec[k] += amp
            if k > 0:
                spec[k - 1] += amp * 0.25
            if k + 1 < len(spec):
                spec[k + 1] += amp * 0.25
    return freqs, spec


def _planted_spectrum(rng, lines, n=62500, xinc=1e-9, exact=False):
    """A capture with a REALISTIC floor and exactly the given lines.

    lines is a sequence of (freq_hz, amplitude), the amplitude in units of the
    floor's median (so PEAK_SNR_MIN_DB's 6 dB gate is 2.0).

    The floor is the |rfft| of Hann-windowed Gaussian noise, i.e. Rayleigh --
    and that is the whole point.  `_synth_fft` uses a bounded uniform floor,
    whose local maxima can never exceed 1.5x its median (3.5 dB, below the
    6 dB gate), so `peak_freqs` there can only ever return the planted lines.
    A real capture is not like that: in a noise-only 62500-point capture, 1391
    local maxima cleared the 6 dB gate and the strongest ran 13.7 dB above the
    median.  A selftest on the uniform floor therefore cannot see any defect
    that depends on what fills the slots below the real lines -- which is
    exactly how C4's "six strongest peaks" defect survived.

    `exact` makes a planted line's magnitude deterministic at `amplitude` by
    REPLACING the floor sample already in that bin rather than adding to it, and
    capping its two neighbours below it so the bin is still a local maximum.
    A single Rayleigh bin spans ~12 dB, so an added line's achieved snr is a
    random variable drawn from that spread -- fine when the fixture only has to
    be "clearly a signal", useless when the fixture is placed AT a gate and its
    job is to fail on the wrong side of it by a known margin.

    The neighbour cap is 0.1, i.e. a deterministic 20 dB LEAD over the line, and
    it is a cap for a reason that has nothing to do with that number: a planted
    line has to remain a local maximum or peak_freqs will not return it.  0.1 is
    used rather than the 0.5 that also satisfies that requirement because 0.5 is
    exactly a 6.02 dB lead -- INSIDE the band where C1's CARRIER_MARGIN_MIN_DB
    (6.9 dB) decides.  A fixture planting one line to test the SNR gate would
    then have had its verdict co-decided by the margin gate and by whether the
    floor happened to draw low at that line's neighbours: measured 2026-09-20,
    the "C1 accepts a carrier 1 dB above CARRIER_SNR_MIN_DB" check PASSed only
    while a lucky draw held the neighbours under the cap, and flipped to N/A the
    moment an unrelated change shifted the RNG stream.  The margin gate has its
    own fixture that builds its runner explicitly; this cap must not reach into
    that gate's decision region.
    """
    spec = np.abs(np.fft.rfft(rng.normal(size=n) * np.hanning(n)))
    spec = spec / max(np.median(spec), 1e-12)
    freqs = np.fft.rfftfreq(n, xinc)
    bin_hz = freqs[1] - freqs[0]
    for f, amp in lines:
        k = int(round(f / bin_hz))
        if 0 <= k < len(spec):
            if exact:
                spec[k] = amp
                for kk in (k - 1, k + 1):
                    if 0 <= kk < len(spec):
                        spec[kk] = min(spec[kk], amp * 0.1)
            else:
                spec[k] += amp
    return freqs, spec


def _tx_capture(rng, board, sb_amp=10.0 ** (FIX_SIDEBAND_SNR_DB / 20.0),
                lo_amp=10.0 ** (FIX_CARRIER_SNR_DB / 20.0), extra=(),
                lo_override_db=None, exact=False):
    """A healthy TX on a Rayleigh floor: LO leakage plus a symmetric pair.

    The pair follows the board's own LO and phase_inc, and scales with its
    attenuation, so the same generator is valid for every capture in layer_c.
    `extra` are absolute-frequency lines (spurs that do not follow the LO).

    `lo_override_db`, when given, replaces the carrier's magnitude in dB above
    the floor.  That is how the negative fixtures are built: same spectrum, same
    symmetric pair, carrier demoted to a magnitude noise actually produces.
    Pass `exact` with it (see _planted_spectrum) or the demoted level is only
    nominal.
    """
    board.snapshot()
    df = board.df_fn(board.phase_inc)
    atten_db = board.atten_mdb / 1000.0
    sb = sb_amp * (10.0 ** (-atten_db / 20.0))
    lo = (lo_amp if lo_override_db is None
          else 10.0 ** (lo_override_db / 20.0))
    lines = [(board.lo, lo), (board.lo - df, sb), (board.lo + df, sb)]
    return _planted_spectrum(rng, list(lines) + list(extra), exact=exact)


def _leakage_only_capture(rng, board,
                          lo_amp=10.0 ** (FIX_CARRIER_SNR_DB / 20.0), extra=()):
    """TX2 as it actually is: LO leakage and nothing else.

    rf_tx_source.v:103-104 forces dac_data_d2 / dac_data_q2 to zero while mode = 0,
    so the second DAC has no baseband content -- no carrier, no pair, no df.
    This plants exactly one line at the board's LO and follows board.lo, so the
    translation C4 asks about is visible in it.

    LO leakage does NOT scale with the attenuator (that is what C6 records:
    measured 0.2 dB over a 20 dB step), so unlike _tx_capture this ignores
    board.atten_mdb.  That asymmetry is the fixture's whole point on a channel
    with no tone -- it is why C5 cannot be judged here.
    """
    board.snapshot()
    lines = [(board.lo, lo_amp)] + list(extra)
    return _planted_spectrum(rng, lines)


def _selftest():
    """Judge the criteria on known inputs.  No board, no scope, no pyvisa."""
    failures = []
    checks = [0]

    def check(name, cond, detail=""):
        checks[0] += 1
        print("%-6s %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
        if not cond:
            failures.append(name)

    rng = np.random.default_rng(20260917)

    # --- case 1: a healthy TX at the documented ratio, fs = 2 * f_DATA_CLK ---
    # f_DATA_CLK 30 MHz at 60 MSPS is the documented LVDS DDR pairing, and 30e6
    # is a reading the fake returns, not a constant the script holds.
    b = _FakeBoard(30e6, 2.0)
    rep = Report()
    ratio = layer_c(rep, b, lambda ch: _synth_fft(b, rng))

    c1 = rep.find("C1 symmetric sideband pair about LO")
    c1c = rep.find("C1 carrier")
    c2 = rep.find("C2 df is linear")
    c3 = rep.find("C3 fs from df vs read-back fs")
    check("C1 PASSes on a symmetric pair about the commanded LO",
          c1 and c1[1] == "PASS", c1[2] if c1 else "row missing")
    check("C1 PASSes its separate carrier row when the carrier is measurable",
          c1c and c1c[1] == "PASS", c1c[2] if c1c else "row missing")
    check("C2 PASSes when df scales with phase_inc", c2[1] == "PASS",
          c2[2] if c2 else "row missing")
    check("C3 identifies fs = 2 * f_DATA_CLK", ratio == 2.0,
          "identified %s" % ratio)
    check("C3 PASSes on a clean measurement", c3[1] == "PASS",
          c3[2] if c3 else "row missing")
    check("C3 prints both rates, so a disagreement names its two sides",
          c3 is not None and "df * 2^12 / phase_inc" in c3[2]
          and "read-back" in c3[2],
          c3[2] if c3 else "row missing")
    c3x = rep.find("C3x documented expectation")
    check("C3x cross-check is RECORD, not a verdict",
          c3x and c3x[1] == "RECORD" and "AGREES" in c3x[2],
          c3x[2] if c3x else "row missing")
    # C5 at the shipped pair.  MEASURED on this board 2026-09-20: at atten
    # 40000 mdB the 78.128 MHz sideband reads 25.4 .. 26.7 dB above the capture
    # floor over 24 captures in three interleaved blocks, and the floor's own
    # median is stable to +/-0.1 dB over 16.  So the pair's UPPER point is
    # measurable and C5 judges.
    c5 = rep.find("C5 atten")
    check("C5 judges at the shipped pair, where the upper point clears the floor",
          c5 is not None and c5[1] == "PASS", c5[2] if c5 else "row missing")

    # The negative control, without which the PASS above cannot be told from a
    # criterion that never refuses.  Same generator, same board, the pair moved
    # to the one that IS floor-limited: measured 2026-09-20, at 40000 -> 60000
    # the upper point reads 7.4 / 11.4 dB across runs and swings -5.7 .. 15.7 dB
    # capture to capture, which is the instrument and not the attenuator.
    _saved = (globals()["ATTEN_LOW_MDB"], globals()["ATTEN_HIGH_MDB"])
    try:
        globals()["ATTEN_LOW_MDB"] = 40000
        globals()["ATTEN_HIGH_MDB"] = 60000
        b_lim = _FakeBoard(30e6, 2.0)
        rep_lim = Report()
        layer_c(rep_lim, b_lim, lambda ch: _synth_fft(b_lim, rng))
        c5n = rep_lim.find("C5 atten")
        check("C5 reports N/A where the upper point is under the capture floor",
              c5n is not None and c5n[1] == "N/A" and "floor" in c5n[2],
              c5n[2] if c5n else "row missing")
    finally:
        globals()["ATTEN_LOW_MDB"], globals()["ATTEN_HIGH_MDB"] = _saved

    # The measurability gate must NOT be decided from one capture.  MEASURED
    # 2026-09-20 at a fixed operating point: the upper point's reading swung
    # 1.0 .. 15.7 dB over eight captures and the 6 dB gate cleared 6 of 8 times,
    # so a one-capture gate flips a coin and then reports "the instrument" about
    # a board it never measured.  This fixture makes the FIRST capture at the
    # upper point a bad draw and every later one a good one, so the two gates
    # give different verdicts on the same operating point: reading frame 0 says
    # N/A, estimating across the frames judges.
    def _flicker(board, first_amp, rest_amp):
        """A capture source whose first upper-point capture is a bad draw.

        "Upper point" is detected as the first capture after the commanded
        attenuation INCREASES -- C5 drives low -> high, so this finds it without
        the fixture having to know how many captures the criterion takes.
        """
        local = np.random.default_rng(20260920)
        state = {"prev": None, "bad_done": False}

        def cap(ch):
            a = board.atten_mdb
            first_high = (state["prev"] is not None and a > state["prev"]
                          and not state["bad_done"])
            if first_high:
                state["bad_done"] = True
            state["prev"] = a
            return _synth_fft(board, local, sb_amp=first_amp if first_high
                              else rest_amp)
        return cap

    b_fl = _FakeBoard(30e6, 2.0)
    rep_fl = Report()
    layer_c(rep_fl, b_fl, _flicker(b_fl, 60.0,
                                   10.0 ** (FIX_SIDEBAND_SNR_DB / 20.0)))
    c5f = rep_fl.find("C5 atten")
    check("C5 judges when only the FIRST upper-point capture is a bad draw",
          c5f is not None and c5f[1] == "PASS",
          c5f[2] if c5f else "row missing")
    c4 = rep.find("C4 all lines translate")
    check("C4 PASSes when every line follows the LO", c4[1] == "PASS",
          c4[2] if c4 else "row missing")
    c6 = rep.find("C6 LO leakage")
    check("C6 carries no verdict by design", c6[1] == "RECORD",
          c6[2] if c6 else "row missing")
    # The board must have been driven in the documented shape.  The baseline
    # attenuation is the SAFE floor, not 0 -- spec 6.1's "atten 0" was written
    # for an uncabled attenuator, and obeying it literally would be full TX
    # power into the RX loopback.
    #
    # Parsed rather than string-matched.  Why: since atten defaults to the safe
    # floor, EVERY init now reads `init atten N ...`, so the old literal
    # comparisons for "init lo 85000000" / "init lo 80000000" could never match
    # -- the assertion would have gone red for a shape change that is exactly
    # the one it exists to require.  A check that names the arguments
    # positionally breaks every time an argument is added anywhere; judging the
    # parsed values does not, and still fails if a value is wrong or missing.
    def _init_args(calls):
        out = []
        for c in calls:
            toks = c.split()
            if not toks or toks[0] != "init":
                continue
            d = {"atten": None, "lo": None, "2r2t": False}
            i = 1
            while i < len(toks):
                if toks[i] == "atten" and i + 1 < len(toks):
                    d["atten"] = int(toks[i + 1]); i += 2
                elif toks[i] == "lo" and i + 1 < len(toks):
                    d["lo"] = int(toks[i + 1]); i += 2
                elif toks[i] == "2r2t":
                    d["2r2t"] = True; i += 1
                else:
                    i += 1
            out.append(d)
        return out

    inits = _init_args(b.calls)
    attens = sorted({d["atten"] for d in inits})
    los    = sorted({d["lo"] for d in inits if d["lo"] is not None})
    check("board was driven with keyword/value inits",
          los == [LO_NOMINAL_HZ, LO_SHIFTED_HZ]
          and attens == sorted({SAFE_ATTEN_MDB, ATTEN_LOW_MDB, ATTEN_HIGH_MDB}),
          "attens=%s los=%s (%d calls)" % (attens, los, len(b.calls)))
    check("phase_inc issued after init, not before",
          b.calls.index("phase_inc %d" % PHASE_INC_1) > 0
          and b.calls[0].startswith("init "),
          b.calls[0])
    # SAFETY, not signal processing: TX1 is cabled to RX1, so a hardware `init`
    # that carries atten 0 is full TX power into the RX input.  Every init this
    # script issues must therefore name an attenuation at or above the floor
    # below which this project has never driven this cable, and none may omit
    # `atten` and inherit rf_main.c's struct default (which is 0).  Asserted on
    # the CALLS, because a scenario model cannot see what was sent to the board.
    #
    # The bound is ATTEN_FLOOR_MIN_MDB, not SAFE_ATTEN_MDB: C5's pair was moved
    # down on 2026-09-20 (see the constants block) so that its upper point could
    # be the measurable one, and that makes the pair's LOW point the only init in
    # the script below the 40000 default.  Restating the guard as "nothing below
    # ATTEN_FLOOR_MIN_MDB" keeps it a real guard over the new pair instead of
    # being quietly widened to whatever the script happens to send -- which is
    # what deleting the assertion would have done.
    def _init_attens(calls):
        out = []
        for c in calls:
            toks = c.split()
            if toks[0] != "init":
                continue
            if "atten" in toks:
                out.append(int(toks[toks.index("atten") + 1]))
            else:
                out.append(None)
        return out

    attens = _init_attens(b.calls)
    check("every hardware init carries an explicit attenuation",
          bool(attens) and all(a is not None for a in attens),
          "attens seen: %s" % attens)
    check("no hardware init drives the loopback below the cable's floor",
          bool(attens) and all(a is not None and a >= ATTEN_FLOOR_MIN_MDB
                               for a in attens),
          "floor %d mdB; attens seen: %s" % (ATTEN_FLOOR_MIN_MDB, attens))
    # ...and the floor itself has to be a deliberate number, not a value that
    # drifted down until the criterion went green.
    check("the cable floor is the reviewed one, not whatever C5 needed",
          ATTEN_FLOOR_MIN_MDB == 20000 and SAFE_ATTEN_MDB == 40000
          and ATTEN_LOW_MDB == ATTEN_FLOOR_MIN_MDB
          and ATTEN_HIGH_MDB == SAFE_ATTEN_MDB,
          "floor=%d safe=%d pair=%d->%d"
          % (ATTEN_FLOOR_MIN_MDB, SAFE_ATTEN_MDB, ATTEN_LOW_MDB,
             ATTEN_HIGH_MDB))
    # C4 must hold phase_inc constant across its own two captures.  The fake
    # records the increment each capture was taken at, so this catches a C4
    # that reuses C1's peaks (measured at 128) against a shifted capture taken
    # while C2 had left the DDS at 256 -- the sidebands then move by an extra
    # df2-df1, which at ratio 2 is 1.875 MHz against a 2 MHz tolerance.
    check("C4 holds phase_inc constant across its baseline and shifted captures",
          b.fft_phase_incs.count(PHASE_INC_2) == 1
          and b.fft_phase_incs.index(PHASE_INC_2) == 1
          and all(v == PHASE_INC_1 for v in b.fft_phase_incs[2:]),
          "phase_inc at each capture: %s (only C2's capture may use %d)"
          % (b.fft_phase_incs, PHASE_INC_2))

    # --- case 2: the SAME spectrum, but df does not scale with phase_inc ---
    # This is the C2 falsification: a spurious line sitting near LO produces a
    # carrier and a sideband, so C1 still passes.  Only the ratio test catches
    # it, which is the whole reason C2 exists.
    b2 = _FakeBoard(30e6, 2.0, df_fn=lambda inc: 1.0e6)
    rep2 = Report()
    layer_c(rep2, b2, lambda ch: _synth_fft(b2, rng))
    c1b = rep2.find("C1 symmetric sideband pair about LO")
    c2b = rep2.find("C2 df is linear")
    check("C1 still PASSes on a non-scaling tone", c1b[1] == "PASS",
          c1b[2] if c1b else "row missing")
    check("C2 FAILs when df does not scale with phase_inc", c2b[1] == "FAIL",
          c2b[2] if c2b else "row missing")

    # --- case 3: no signal at all (TX silent / nothing connected) ---
    b3 = _FakeBoard(30e6, 2.0)
    rep3 = Report()
    layer_c(rep3, b3, lambda ch: (np.fft.rfftfreq(62500, 1e-9),
                                  0.5 + rng.random(31251)))
    c1c = rep3.find("C1 symmetric sideband pair about LO")
    check("C1 FAILs with no lines", c1c[1] == "FAIL",
          c1c[2] if c1c else "row missing")
    check("C3 FAILs when C1 found nothing",
          rep3.find("C3 fs from df vs read-back fs")[1] == "FAIL",
          "a channel with a DDS and no sideband has no df; that is a FAIL, "
          "not a skip")
    c3x3 = rep3.find("C3x documented expectation")
    check("C3x says N/A when there is no df to cross-check",
          c3x3 and c3x3[1] == "N/A", c3x3[2] if c3x3 else "row missing")

    # --- case 4: the ratio is the OTHER one (fs == f_DATA_CLK/2) ---
    # The identified ratio must track the input, or C3x is not adjudicating.
    b4 = _FakeBoard(30e6, 0.5)
    rep4 = Report()
    ratio4 = layer_c(rep4, b4, lambda ch: _synth_fft(b4, rng))
    check("C3 identifies fs = f_DATA_CLK / 2 when that is the truth",
          ratio4 == 0.5, "identified %s" % ratio4)
    c3x4 = rep4.find("C3x documented expectation")
    check("C3x records the DISAGREEMENT without failing it",
          c3x4 and c3x4[1] == "RECORD" and "DISAGREES" in c3x4[2],
          c3x4[2] if c3x4 else "row missing")
    check("C3 itself PASSes at that same point, since the two rates agree",
          rep4.find("C3 fs from df vs read-back fs")[1] == "PASS",
          "C3x's DISAGREES is a cross-check against a second-hand document; it "
          "must not be able to decide C3")

    # --- case 5: f_DATA_CLK unavailable (STALE) -> C3 no longer cares ---
    # The verdict used to route through f_DATA_CLK, so a dead window counter
    # took C3 down with it.  It does not any more: f_DATA_CLK survives only in
    # the RECORD row.  What must still hold is that the dead channel is REPORTED
    # -- a silently empty cross-check would read as agreement.
    class _StaleBoard(_FakeBoard):
        def cmd(self, args):
            if args.split()[0] == "status":
                self.calls.append(args)
                return ("--- PL STATUS (current) ---\n"
                        "  STALE (window never advanced): ...\n"
                        "  f_DATA_CLK = STALE\n")
            return _FakeBoard.cmd(self, args)

    b5 = _StaleBoard(30e6, 2.0)
    rep5 = Report()
    layer_c(rep5, b5, lambda ch: _synth_fft(b5, rng))
    c3e = rep5.find("C3 fs from df vs read-back fs")
    check("C3 PASSes off the part's own read-back even with f_DATA_CLK dead",
          c3e[1] == "PASS", c3e[2] if c3e else "row missing")
    c3x5 = rep5.find("C3x documented expectation")
    check("C3x still RECORDs that the cross-check could not run",
          c3x5 and c3x5[1] == "RECORD" and "unreadable" in c3x5[2],
          c3x5[2] if c3x5 else "row missing")
    check("read_f_data_clk_hz resolves STALE to 0",
          read_f_data_clk_hz(b5) == 0.0)
    check("read_f_data_clk_hz resolves NO CLOCK to 0",
          read_f_data_clk_hz(_NoClockBoard(30e6, 2.0)) == 0.0)

    # --- case 6: the peak-picker itself ---
    # Lines are planted ON the bin grid (1/xinc = 1 GHz, 62500 points -> 16 kHz
    # bins), because that is what the real capture produces and off-grid lines
    # would test the quantiser rather than the picker.
    freqs = np.fft.rfftfreq(62500, 1e-9)
    bin_hz = freqs[1] - freqs[0]
    spec = 0.5 + rng.random(len(freqs))
    planted = [80e6, 80e6 - 30 * bin_hz, 80e6 + 30 * bin_hz]
    for f in planted:
        k = int(round(f / bin_hz))
        spec[k] += 100.0
    pk = peak_freqs(freqs, spec)
    found = [f for f, _, _ in pk]
    check("peak_freqs resolves three planted lines 480 kHz apart",
          all(any(abs(g - want) <= bin_hz for g in found) for want in planted)
          and len(pk) >= 3,
          "found %s" % ", ".join("%.4f MHz" % (f / 1e6) for f in found[:3]))
    # The point of the separate LO-line radius: asked for the carrier it must
    # return the CARRIER, not the 480 kHz sideband that is 100x stronger in a
    # real capture.  And the LO-shift radius must still be wide enough to find a
    # translated line.
    at_lo = find_nearest(pk, 80e6, LO_LINE_TOL_HZ)
    check("the LO-line lookup returns the carrier, not a sideband",
          at_lo is not None and abs(at_lo[0] - 80e6) <= bin_hz,
          "got %.4f MHz" % (at_lo[0] / 1e6 if at_lo else float("nan")))
    check("the LO-shift radius still finds a translated line",
          find_nearest(pk, 80e6 - 30 * bin_hz, LO_SHIFT_TOL_HZ) is not None
          and find_nearest(pk, 85e6, LO_SHIFT_TOL_HZ) is None,
          "admits a real line, rejects one 5 MHz away")
    # Closest-wins, not strongest-wins.  The DDS sidebands are ~100x stronger
    # than LO leakage and, at ratio 2, sit 1.87 MHz from the carrier -- inside
    # the 2 MHz LO-shift tolerance.  A strongest-wins rule answers "where is
    # the carrier" with a sideband, which is what made C4 report the LO line
    # moving 3.1 MHz instead of 5.0.  This is the assertion that catches it.
    neighbours = [(80.9375e6, 300.0, 30.0), (79.0625e6, 300.0, 30.0),
                  (80.0e6, 3.0, 10.0)]
    got = find_nearest(neighbours, 80e6, LO_SHIFT_TOL_HZ)
    check("find_nearest returns the CLOSEST line, not the strongest",
          got is not None and abs(got[0] - 80e6) < 1e3,
          "asked for 80.000 MHz, got %.4f MHz (a strongest-wins rule returns "
          "the 80.9375 MHz sideband)" % (got[0] / 1e6 if got else float("nan")))
    check("the LO-line radius is under the smallest sideband spacing",
          LO_LINE_TOL_HZ < PHASE_INC_1 / float(1 << 12) * 30e6 * 0.5,
          "%.0f kHz vs %.0f kHz" % (LO_LINE_TOL_HZ / 1e3, 468.75))
    # Recorded, not asserted: C2's gate is +/-2% and the bin grid is 16 kHz, so
    # at the SMALLEST candidate (df1 = 469 kHz = 29.3 bins) one bin of
    # quantisation is already 1.7% of df1.  A working board at ratio 1/2 can
    # therefore land close to the gate on measurement error alone.  The gate is
    # frozen by spec 4.2 and is NOT widened here; this prints the margin so a
    # FAIL can be told apart from a quantisation artefact.
    for r in (0.5, 1.0, 2.0):
        df1 = PHASE_INC_1 / float(1 << 12) * 30e6 * r
        print("       [note] ratio %g: df1 = %.1f kHz = %.1f bins -> one bin of "
              "quantisation is %.2f%% of the +/-2%% C2 gate"
              % (r, df1 / 1e3, df1 / bin_hz, 100.0 * bin_hz / df1))

    # --- case 7: C1 is a SYMMETRY test, not a "most nearly symmetric pair" test
    # Every spectrum below has a resolvable line at 80.000 MHz and a realistic
    # Rayleigh floor.  The shipped C1 minimised `sym` and then accepted whatever
    # pair won, with no bound at all, so it PASSed every one of them and handed
    # a fabricated df1 (55.3 MHz, 2.1 MHz, 53.9 MHz) to C2 and C3.
    def _c1_row(lines):
        bb = _FakeBoard(30e6, 2.0)
        rr = Report()
        layer_c(rr, bb, lambda ch: _planted_spectrum(rng, lines))
        return rr.find("C1 symmetric sideband pair about LO")

    one_sb = _c1_row([(80.0e6, 30.0), (78.125e6, 200.0)])
    check("C1 FAILs on a single sideband with no mirror",
          one_sb[1] == "FAIL", one_sb[2] if one_sb else "row missing")
    two_spurs = _c1_row([(80.0e6, 30.0), (77.9e6, 200.0), (120.0e6, 200.0)])
    check("C1 FAILs on two unrelated spurs either side of LO",
          two_spurs[1] == "FAIL", two_spurs[2] if two_spurs else "row missing")
    far_pair = _c1_row([(80.0e6, 30.0), (35.0e6, 200.0), (125.0e6, 200.0)])
    check("C1 FAILs on a symmetric pair over DF1_MAX_HZ from the carrier",
          far_pair[1] == "FAIL", far_pair[2] if far_pair else "row missing")
    # The bound must not cost a real tone: an asymmetry inside SYM_TOL_HZ is a
    # working board (bin quantisation alone produces up to ~2 bins).
    asym_ok = _c1_row([(80.0e6, 30.0), (78.125e6, 200.0), (81.875e6, 200.0)])
    check("C1 PASSes on a real pair whose asymmetry is inside the limit",
          asym_ok[1] == "PASS", asym_ok[2] if asym_ok else "row missing")
    check("C1 prints the measured asymmetry",
          asym_ok is not None and "asymmetry" in asym_ok[2],
          asym_ok[2] if asym_ok else "row missing")
    asym_bad = _c1_row([(80.0e6, 30.0), (78.125e6, 200.0), (82.375e6, 200.0)])
    check("C1 FAILs when the pair is 500 kHz from symmetric",
          asym_bad[1] == "FAIL", asym_bad[2] if asym_bad else "row missing")

    # --- case 8: a healthy TX over a REALISTIC (Rayleigh) noise floor ---
    # The shipped C4 built `before` from the six strongest peaks.  With a
    # Rayleigh floor those are three signal lines plus three noise maxima, so a
    # transmitter whose every real line translates perfectly with the LO was
    # reported as FAIL, on bins that were never part of the signal.
    b8 = _FakeBoard(30e6, 2.0)
    rep8 = Report()
    layer_c(rep8, b8, lambda ch: _tx_capture(rng, b8))
    c1_8 = rep8.find("C1 symmetric sideband pair about LO")
    c4_8 = rep8.find("C4 all lines translate")
    c3_8 = rep8.find("C3 fs from df vs read-back fs")
    check("C1 PASSes on a healthy TX over a Rayleigh floor",
          c1_8[1] == "PASS", c1_8[2] if c1_8 else "row missing")
    check("C4 PASSes on a healthy TX over a Rayleigh floor",
          c4_8[1] == "PASS", c4_8[2] if c4_8 else "row missing")
    check("C4 tests exactly the carrier + pair, not the top peaks",
          c4_8 is not None and c4_8[2].count("->") == 3,
          c4_8[2] if c4_8 else "row missing")
    check("C3 PASSes on a healthy TX over a Rayleigh floor",
          c3_8[1] == "PASS", c3_8[2] if c3_8 else "row missing")
    # With a Rayleigh floor the floor itself is a source of local maxima -- the
    # property whose absence let this defect through.  Assert the fixture
    # really has one, so case 8 cannot silently degenerate into case 1.
    _f, _s = _planted_spectrum(rng, [(80.0e6, 30.0)])
    _floor_peaks = peak_freqs(_f, _s, n=64)
    check("the Rayleigh fixture does put noise maxima in the peak list",
          len(_floor_peaks) > 8,
          "%d local maxima cleared the %.0f dB gate on one planted line"
          % (len(_floor_peaks), PEAK_SNR_MIN_DB))

    # --- case 8b: the carrier GATE -- C1's negative half ---
    # Widening the peak list (see peak_freqs) stopped C1 asking for a line that
    # the list had already deleted, but on its own it left C1 decidable by a
    # noise maximum: a capture of this size puts ~1 gated peak inside the
    # 250 kHz window by chance, 63% of the time.  So C1 also needs a MAGNITUDE
    # test on the carrier, and this is the fixture that proves the test is
    # really there and really at CARRIER_SNR_MIN_DB.
    #
    # The fixtures that were here before could not prove it.  Their carrier was
    # planted at 3x the floor, i.e. 9.5 dB -- INSIDE the 7.6 .. 10.5 dB band
    # that noise maxima occupy -- so they modelled a carrier no instrument could
    # tell from noise, and a criterion that gates noise out of C1 necessarily
    # failed them.  The levels here are the MEASURED ones instead (20 dB for the
    # carrier, see FIX_CARRIER_SNR_DB), set EXACTLY rather than by addition so
    # that the demoted negative fixture is at a known distance from the gate.
    #
    # Three points: at the worst noise-maximum magnitude ever measured on this
    # board, and one dB either side of the gate.  The pair either side is what
    # makes this a test of the GATE and not merely of "some threshold exists":
    # moving CARRIER_SNR_MIN_DB by more than a dB in either direction now fails
    # here instead of quietly changing what C1 means on the real board.
    _f, _s = _tx_capture(rng, _FakeBoard(30e6, 2.0),
                         lo_override_db=CARRIER_SNR_MIN_DB - 1.0, exact=True)
    _c = find_nearest(peak_freqs(_f, _s), LO_NOMINAL_HZ, LO_LINE_TOL_HZ)
    check("the exact fixture places the carrier at the level it claims",
          _c is not None and abs(_c[2] - (CARRIER_SNR_MIN_DB - 1.0)) < 0.2,
          "planted %.1f dB, measured %s"
          % (CARRIER_SNR_MIN_DB - 1.0,
             "%.2f dB" % _c[2] if _c else "absent"))

    # These fixtures moved from the PAIR row to the CARRIER row when C1 was
    # split in two, and the move is the point: the pair is now found about the
    # commanded LO and no longer consumes the carrier's position, so a demoted
    # carrier leaves it PASS.  Asserting otherwise -- which is what these three
    # checks used to do -- would now be asserting the defect the split removed.
    # The gate itself is unchanged and still judged here, on the row that owns it.
    def _gate_fixture(level_db, row="C1 carrier (LO leakage) present at LO"):
        """What C1 says about a healthy TX whose carrier is at level_db.

        The window floor is FLATTENED to 40 dB below the planted carrier, and
        that is not a convenience -- without it this fixture does not measure
        the gate it is named for.  C1 decides the carrier through TWO gates:
        CARRIER_SNR_MIN_DB (the line is above the floor) and
        CARRIER_MARGIN_MIN_DB (the line LEADS the window's next one).  This
        fixture is about the first; the second is the Rayleigh floor's business,
        and the floor owes it nothing.  MEASURED 2026-09-20: at level_db = gate
        + 1 the row came back N/A, because a noise maximum 224 kHz away measured
        8.8 dB against the 15.0 dB carrier -- a 6.2 dB lead, under the 6.9 dB
        margin gate.  The check therefore reported the MARGIN gate's verdict
        while claiming to test the SNR gate, and it had been passing only while
        a lucky floor draw kept the window quiet.  Flattening the window leaves
        exactly one gate able to decide, which is what the name promises.  The
        margin gate keeps its own fixture (case 8c) and is not weakened here.
        """
        bx = _FakeBoard(30e6, 2.0)
        repx = Report()

        def _cap(ch):
            f, s = _tx_capture(rng, bx, lo_override_db=level_db, exact=True)
            amp = 10.0 ** (level_db / 20.0)
            # Everything in the window except the carrier's OWN bin, so the
            # carrier keeps the magnitude under test and still leads.
            k_lo = int(round(bx.lo / (f[1] - f[0])))
            w = np.abs(f - bx.lo) <= LO_LINE_TOL_HZ
            w[k_lo] = False
            s[w] = np.minimum(s[w], amp * 0.01)
            return f, s

        layer_c(repx, bx, _cap)
        return repx.find(row)

    g_imp = _gate_fixture(FIX_MIN_CARRIER_IMPERSONATOR_DB)
    check("C1 does not accept a line at the worst noise-maximum magnitude seen",
          g_imp is not None and g_imp[1] != "PASS",
          g_imp[2] if g_imp else "row missing")
    check("C1's weak-carrier note says the line is too weak, and quotes the band",
          g_imp is not None and "below the" in g_imp[2] and "within" in g_imp[2],
          g_imp[2] if g_imp else "row missing")
    g_below = _gate_fixture(CARRIER_SNR_MIN_DB - 1.0)
    check("C1 does not accept a carrier 1 dB below CARRIER_SNR_MIN_DB",
          g_below is not None and g_below[1] != "PASS",
          g_below[2] if g_below else "row missing")
    g_above = _gate_fixture(CARRIER_SNR_MIN_DB + 1.0)
    check("C1 accepts a carrier 1 dB above CARRIER_SNR_MIN_DB",
          g_above is not None and g_above[1] == "PASS",
          g_above[2] if g_above else "row missing")
    # The verdict for a demoted carrier on a healthy TX is NOT APPLICABLE, not
    # FAIL: the pair above is present, correct and translating, which is
    # independent evidence the transmitter and its LO are running.  Only the
    # capture floor is missing.  Pin the verdict AND the pair's independence.
    check("a demoted carrier is reported NOT APPLICABLE, not FAIL",
          g_imp is not None and g_imp[1] == "N/A",
          g_imp[2] if g_imp else "row missing")
    p_imp = _gate_fixture(FIX_MIN_CARRIER_IMPERSONATOR_DB,
                          row="C1 symmetric sideband pair about LO")
    check("a demoted carrier leaves the sideband pair row PASS",
          p_imp is not None and p_imp[1] == "PASS",
          p_imp[2] if p_imp else "row missing")

    # --- case 8c: the carrier MARGIN gate ---
    # CARRIER_SNR_MIN_DB alone decides "above the floor"; it does not decide
    # "this line is the CARRIER".  Once the true carrier falls to the floor the
    # window's strongest line is a noise maximum and clears 14 dB happily, so a
    # second gate requires the strongest line to LEAD the next by
    # CARRIER_MARGIN_MIN_DB.  This fixture is the only thing that proves that
    # gate exists: three lines inside the window at 3 dB apart, all of them well
    # above the SNR gate, and none of them the carrier.
    _bx = _FakeBoard(30e6, 2.0)
    _runner = 10.0 ** ((FIX_CARRIER_SNR_DB - 3.0) / 20.0)
    _extras = [(LO_NOMINAL_HZ - 64e3, _runner), (LO_NOMINAL_HZ + 64e3, _runner)]

    def _margin_capture():
        return _tx_capture(rng, _bx, exact=True, extra=_extras)

    _rep = Report()
    layer_c(_rep, _bx, lambda ch: _margin_capture())
    m_row = _rep.find("C1 carrier (LO leakage) present at LO")
    _win = [p[2] for p in peak_freqs(*_margin_capture())
            if abs(p[0] - LO_NOMINAL_HZ) <= LO_LINE_TOL_HZ]
    check("the margin fixture really does put a strong line in the window",
          _win and max(_win) > CARRIER_SNR_MIN_DB,
          "strongest line in +/-%.0f kHz is %.1f dB (gate %.1f dB), %d lines"
          % (LO_LINE_TOL_HZ / 1e3, max(_win) if _win else 0.0,
             CARRIER_SNR_MIN_DB, len(_win)))
    check("C1 declines a carrier when no line in the window stands out",
          m_row is not None and m_row[1] == "N/A"
          and "stands out" in m_row[2],
          m_row[2] if m_row else "row missing")

    # --- case 9: C3 must FIT the measurement, not merely prefer something ---
    # df = 3x what the part's read-back rate implies, scaling correctly with
    # phase_inc so C2 still passes.  This is the DDS-side half of case 9b's
    # negative control: there the read-back lies, here the spacing does, and the
    # criterion has to catch both.  Under the old ratio form this case PASSed
    # and printed "identified fs = 2 * f_DATA_CLK; separation 100.0%" -- the
    # separation was normalised by the candidate frequencies, so an offset tens
    # of MHz from all of them still read 1.0.
    b9 = _FakeBoard(30e6, 2.0,
                    df_fn=lambda inc: inc / float(1 << 12) * 30e6 * 2.0 * 3.0)
    rep9 = Report()
    ratio9 = layer_c(rep9, b9, lambda ch: _tx_capture(rng, b9))
    c2_9 = rep9.find("C2 df is linear")
    c3_9 = rep9.find("C3 fs from df vs read-back fs")
    check("C2 still PASSes on a df that scales 3x off the part's rate",
          c2_9[1] == "PASS", c2_9[2] if c2_9 else "row missing")
    check("C3 FAILs when the df implies a rate the part does not report",
          c3_9[1] == "FAIL", c3_9[2] if c3_9 else "row missing")
    check("C3 names which two numbers disagree",
          c3_9 is not None
          and "THE DDS SPACING AND THE PART'S OWN RATE DISAGREE" in c3_9[2],
          c3_9[2] if c3_9 else "row missing")
    # The returned ratio is the CROSS-CHECK quantity (read-back / f_DATA_CLK),
    # which this fixture sets to 2.0.  It is deliberately not derived from the
    # df: C3's verdict no longer depends on it, and a return value that moved
    # with the thing under test would let case 1's assertion pass vacuously.
    check("C3's returned ratio is the part's, not the df's",
          ratio9 == 2.0, "returned %s" % ratio9)
    c3x9 = rep9.find("C3x documented expectation")
    check("C3x still records the documented cross-check it can run",
          c3x9 is not None and c3x9[1] == "RECORD" and "AGREES" in c3x9[2],
          c3x9[2] if c3x9 else "row missing")

    # The other half: a df just INSIDE the fit band must still pass, so the
    # band is not a knife edge.  Measured df1 lands on a bin, which is 0.85% on
    # fs, so 3% is a real perturbation and not quantisation.
    b10 = _FakeBoard(30e6, 1.0,
                     df_fn=lambda inc: inc / float(1 << 12) * 30e6 * 1.03)
    rep10 = Report()
    ratio10 = layer_c(rep10, b10, lambda ch: _tx_capture(rng, b10))
    c3_10 = rep10.find("C3 fs from df vs read-back fs")
    check("C3 PASSes when df is ~3% off the part's rate, inside the 5% band",
          c3_10[1] == "PASS" and ratio10 == 1.0,
          (c3_10[2] if c3_10 else "row missing"))

    # --- case 9b: C3 against the part's OWN sampling-rate readback ---------
    # The ratio framing is MODE-DEPENDENT: fs/f_DATA_CLK is 1/2 in 1R1T and 1/4
    # in 2R2T, and 1/4 was in no candidate list, so the ratio form FAILed by
    # construction in 2R2T whatever the hardware did.  C3 now compares the fs
    # implied by the SCOPE's df (`df * 2^12 / phase_inc`) against the rate
    # `init` reads back from the part -- a relation that holds in either mode
    # and touches no f_DATA_CLK at all.
    #
    # 240 MHz / 60 MSPS is the REAL 2R2T operating point: the case the old
    # criterion could not pass.
    b_r = _FakeBoard(240e6, 0.25)
    rep_r = Report()
    layer_c(rep_r, b_r, lambda ch: _tx_capture(rng, b_r))
    c3_r = rep_r.find("C3 fs from df vs read-back fs")
    check("C3 PASSes at the real 2R2T point (240 MHz / 60 MSPS)",
          c3_r is not None and c3_r[1] == "PASS",
          c3_r[2] if c3_r else "row missing")
    check("C3 shows the df-derived rate next to the read-back one",
          c3_r is not None and "60.0" in c3_r[2] and "read-back" in c3_r[2],
          c3_r[2] if c3_r else "row missing")
    # ...and the number itself reaches the LOG, not just the row text.  C3's
    # verdict rests on a readback that comes off the board; if the log carried
    # only the script's own arithmetic and the verdict that used it, the one
    # piece of hardware evidence would be unauditable from the record alone.
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        layer_c(Report(), b_r, lambda ch: _tx_capture(rng, b_r))
    check("the init reply's sampling-rate line is echoed into the log",
          "TX sampling rate readback = 60000000 Hz" in _buf.getvalue(),
          "log had no readback line to audit C3 against")

    # The negative control, without which the PASS above could come from a C3
    # that compared df against itself: same DDS spacing, a part that reports a
    # rate the spacing does not imply.
    b_mm = _FakeBoard(240e6, 0.25, init_rate_hz=48e6)
    rep_mm = Report()
    layer_c(rep_mm, b_mm, lambda ch: _tx_capture(rng, b_mm))
    c3_mm = rep_mm.find("C3 fs from df vs read-back fs")
    check("C3 FAILs when the read-back rate contradicts the df",
          c3_mm is not None and c3_mm[1] == "FAIL",
          c3_mm[2] if c3_mm else "row missing")

    # ...and the fail-safe: a reply carrying no readback must land on FAIL, not
    # on a vacuous PASS.  This is read_f_data_clk_hz's discipline, and it is the
    # half that a criterion comparing df to a CONSTANT would get wrong.
    b_nr = _FakeBoard(240e6, 0.25, init_rate_hz=None)
    rep_nr = Report()
    layer_c(rep_nr, b_nr, lambda ch: _tx_capture(rng, b_nr))
    c3_nr = rep_nr.find("C3 fs from df vs read-back fs")
    check("C3 FAILs when init carries no sampling-rate readback",
          c3_nr is not None and c3_nr[1] == "FAIL",
          c3_nr[2] if c3_nr else "row missing")

    check("read_sampling_rate_hz resolves a reply with no readback to 0",
          read_sampling_rate_hz("RESULT PASS (init)\n") == 0.0)
    check("read_sampling_rate_hz reads the TX rate, not the requested one",
          read_sampling_rate_hz(_FakeBoard(240e6, 0.25, init_rate_hz=48e6)
                                ._init_reply()) == 48e6)

    # --- case 10: BoardCmd really hands the host to the helper ---
    # pynq_ssh.py reads PYNQ_HOST from its ENVIRONMENT and carries no host
    # argument on the command line, so `--board <host>` is honoured only if it
    # is put there.  The shipped BoardCmd used `host` as a truthiness switch and
    # nothing else, so a second board or a typo'd address silently ran on the
    # helper's own default (192.0.2.10) and the operator would have attributed
    # the verdicts to the machine they named.  board_cmd.py has no selftest of
    # its own; this is where the criteria script can falsify it offline.
    import board_cmd

    seen = {}

    class _Completed(object):
        returncode = 0
        stdout = "RESULT PASS\n__BOARD_RC=0\n"
        stderr = ""

    def _fake_run(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw.get("env")
        return _Completed()

    real_run = board_cmd.subprocess.run
    board_cmd.subprocess.run = _fake_run
    try:
        board_cmd.BoardCmd("/home/xilinx/board_app_src/board_app",
                           "192.0.2.10").cmd("status")
    finally:
        board_cmd.subprocess.run = real_run
    check("BoardCmd puts --board's host into the helper's environment",
          (seen.get("env") or {}).get("PYNQ_HOST") == "192.0.2.10",
          "PYNQ_HOST=%r" % (seen.get("env") or {}).get("PYNQ_HOST"))
    check("BoardCmd still invokes the helper as `run <command>`",
          seen.get("argv", [])[1:3] == [board_cmd.HELPER, "run"],
          "%s" % (seen.get("argv"),))

    # --- case 11: --channel really selects the channel ---------------------
    # A channel argument is only worth having if it changes what the criteria
    # MEASURE, and a fixture that answers identically on both channels cannot
    # tell a wired-through argument from a hardcoded "CHAN1" left behind at one
    # call site.  So this fixture answers DIFFERENTLY per channel: CHAN1 is a
    # healthy DDS transmitter, CHAN2 is TX2 -- LO leakage only, which is what
    # rf_tx_source.v:103-104 actually produces.
    #
    # Two independent claims are asserted, and they fail for different defects:
    #
    #   (a) every capture a CHAN2 run makes asks for CHAN2.  This is the one
    #       that catches a hardcoded call site.  It asserts a property of the
    #       CALLS, not of the spectra, so it cannot be satisfied by the fixture
    #       happening to give the right answer anyway.
    #   (b) the tone-derived criteria come back NOT APPLICABLE -- not PASS, and
    #       not FAIL -- while presence and translation still get real verdicts
    #       off the leakage line.  This is the claim that a channel with no tone
    #       does not quietly report a green run.
    def _run(chan, fixt_for):
        """Run layer_c on `chan` over a per-channel spectrum source.

        `fixt_for(channel, board)` supplies each capture.  Both the report and
        the list of channels actually asked for are returned, because "the call
        site asked for the wrong channel" and "the fixture answered correctly
        for the wrong reason" are different defects and need different evidence.
        """
        bb = _FakeBoard(30e6, 2.0)
        seen_ch = []

        def _per_channel(ch):
            seen_ch.append(ch)
            return fixt_for(ch, bb)

        rr = Report()
        layer_c(rr, bb, _per_channel, chan=chan)
        return rr, seen_ch

    def _healthy(ch, bb):
        return (_tx_capture(rng, bb) if ch == "CHAN1"
                else _leakage_only_capture(rng, bb))

    rep11, seen11 = _run("CHAN2", _healthy)
    check("a CHAN2 run asks the scope for CHAN2, never CHAN1",
          bool(seen11) and set(seen11) == {"CHAN2"},
          "every capture asked for %s (%d captures)"
          % (sorted(set(seen11)), len(seen11)))
    c1_c2 = rep11.find("C1 (CHAN2) LO leakage line present at LO")
    check("CHAN2 C1 judges the leakage line it can actually see",
          c1_c2 is not None and c1_c2[1] == "PASS",
          c1_c2[2] if c1_c2 else "row missing")
    c1_pair = rep11.find("carrier + symmetric pair")
    check("CHAN2 C1's pair row is NOT APPLICABLE, not PASS",
          c1_pair is not None and c1_pair[1] == "N/A",
          c1_pair[2] if c1_pair else "row missing")
    c4_c2 = rep11.find("C4 (CHAN2)")
    check("CHAN2 C4 still judges LO translation off the leakage line",
          c4_c2 is not None and c4_c2[1] == "PASS",
          c4_c2[2] if c4_c2 else "row missing")
    for needle in ("C2 df is linear", "C3 fs from df vs read-back fs", "C3x ",
                   "C5 atten difference", "C6 LO leakage"):
        row = rep11.find(needle)
        check("CHAN2 %r is NOT APPLICABLE, not PASS" % needle.strip(),
              row is not None and row[1] == "N/A",
              row[2] if row else "row missing")
    # Belt and braces: no row on the CHAN2 report may carry a PASS for a
    # tone-derived criterion, whatever it is named.  The named checks above
    # would miss a criterion that was RENAMED on the CHAN2 path.
    tone_prefixes = ("C2", "C3", "C5")
    check("no CHAN2 row claims a tone-derived PASS",
          not [r for r in rep11.rows
               if r[1] == "PASS" and r[0].startswith(tone_prefixes)],
          "%d rows on the CHAN2 report" % len(rep11.rows))

    # ...and the fixture is not simply inert on CHAN1.  Same generator, same
    # board, only the channel differs: CHAN1 must find the tone.  Without this
    # the case above could pass because the fixture is broken, not because the
    # channel selection works.
    rep11b, seen11b = _run("CHAN1", _healthy)
    c2_11b = rep11b.find("C2 df is linear")
    check("the same fixture run as CHAN1 does find the tone",
          set(seen11b) == {"CHAN1"} and c2_11b is not None
          and c2_11b[1] == "PASS",
          "captures asked for %s; C2 = %s"
          % (sorted(set(seen11b)),
             c2_11b[1] if c2_11b else "row missing"))
    c1_pair_ch1 = rep11b.find("C1 symmetric sideband pair about LO")
    check("CHAN1's C1 is the pair test, not the leakage-only test",
          c1_pair_ch1 is not None and c1_pair_ch1[1] == "PASS"
          and "sidebands" in c1_pair_ch1[2],
          c1_pair_ch1[2] if c1_pair_ch1 else "row missing")

    # The claim above is about the CALLS, so it is worth one more that is about
    # the VERDICT.  Same generator both sides; TX2 emits nothing at all on the
    # right, a healthy DDS transmitter on the left.  A layer_c that read CHAN1's
    # spectrum while told it was on CHAN2 would print a clean PASS here -- so
    # this pair is a behavioural catch, not a bookkeeping one.  (The uniform
    # floor is deliberate: its local maxima cannot clear the 6 dB picking gate,
    # so "TX2 silent" is deterministic rather than a draw from the Rayleigh
    # tail.)
    def _silent_tx2(ch, bb):
        if ch == "CHAN1":
            return _tx_capture(rng, bb)
        bb.snapshot()
        fr = np.fft.rfftfreq(62500, 1e-9)
        return fr, 0.5 + rng.random(len(fr))

    rep11c, _ = _run("CHAN2", _silent_tx2)
    rep11d, _ = _run("CHAN1", _silent_tx2)
    c1_silent = rep11c.find("C1 (CHAN2) LO leakage line present")
    c1_loud = rep11d.find("C1 symmetric sideband pair about LO")
    check("CHAN2's verdict comes from CHAN2's spectrum, not CHAN1's",
          c1_silent is not None and c1_silent[1] == "FAIL"
          and c1_loud is not None and c1_loud[1] == "PASS",
          "same generator: CHAN2 C1 = %s, CHAN1 C1 = %s"
          % (c1_silent[1] if c1_silent else "row missing",
             c1_loud[1] if c1_loud else "row missing"))

    # --- case 12: an OFF channel must not serve as a quiet channel ----------
    # scope_prepare armed CHAN1 with :DISP ON and did NOT arm CHAN2.  A scope
    # channel that is not displayed does not take part in acquisition, so
    # :WAV:SOUR CHAN2 + :WAV:DATA? would return a flat trace -- and a flat trace
    # is EXACTLY what a silent TX2 looks like.  The criterion "TX2 has no output
    # at LO" would then be satisfied by the instrument, not by the board, and
    # it is a false NEGATIVE on a possibly working channel: the worst direction
    # of error for a bring-up, since it retires a channel that may be fine.
    #
    # The criterion asserted here is therefore "an unarmed observation channel
    # may not be used as evidence", NOT "a flat trace was observed".  What an
    # off channel's :WAV:DATA? actually returns on this DSOX1102A has never been
    # measured -- this guard is PREVENTIVE, and it is written so that the
    # unverified case cannot silently become a verdict either way.
    class _FakeScope(object):
        """SCPI traffic recorder answering just what scope_prepare asks.

        `disp` maps CHAN1/CHAN2 to what :CHANx:DISP? replies; a channel in
        `unreadable` raises instead, which is the "the guard could not arm"
        case -- transport error or a scope that does not support the query.
        """

        def __init__(self, n=62500, xinc=1e-9, disp=None, unreadable=()):
            self.writes = []
            self.queries = []
            self.n = n
            self.xinc = xinc
            self.disp = dict({"CHAN1": "1", "CHAN2": "1"} if disp is None
                             else disp)
            self.unreadable = set(unreadable)

        def write(self, s):
            self.writes.append(s)

        def query(self, s):
            self.queries.append(s)
            if s.endswith(":DISP?"):
                ch = s.split(":")[1]
                if ch in self.unreadable:
                    raise RuntimeError("scope did not answer %r" % s)
                return self.disp[ch]
            if s.endswith(":XINC?"):
                return "%.9E" % self.xinc
            raise RuntimeError("unexpected query %r" % s)

        def query_binary_values(self, s, **kw):
            return [128] * self.n

    def _prep(fake, chan="CHAN1"):
        """scope_prepare against a fake, returning (srate, stdout, error)."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                sr = scope_prepare(fake, chan=chan)
            return sr, buf.getvalue(), None
        except InstrumentSetupError as e:
            return None, buf.getvalue(), str(e)

    fs = _FakeScope()
    _prep(fs)
    check("scope_prepare arms :DISP ON for BOTH channels",
          ":CHAN1:DISP ON" in fs.writes and ":CHAN2:DISP ON" in fs.writes,
          "DISP writes: %s" % [w for w in fs.writes if "DISP" in w])
    check("scope_prepare reads BACK the selected channel's display state",
          ":CHAN1:DISP?" in fs.queries,
          "queries: %s" % fs.queries)
    fs2 = _FakeScope()
    _prep(fs2, chan="CHAN2")
    check("the read-back follows --channel, it is not hardcoded to CHAN1",
          ":CHAN2:DISP?" in fs2.queries and ":CHAN1:DISP?" not in fs2.queries,
          "queries: %s" % fs2.queries)

    # The failure this exists to prevent: a channel the scope itself reports as
    # off must stop the run, not produce a report of FAILs about "no output".
    _sr, _out, err = _prep(_FakeScope(disp={"CHAN1": "1", "CHAN2": "0"}),
                           chan="CHAN2")
    check("a channel the scope reports as OFF refuses the run",
          err is not None, err or "scope_prepare returned a sample rate")

    # ...and the guard is about the SELECTED channel: measuring CHAN2 while
    # CHAN1 happens to be off is a legitimate run, because CHAN1 is not what is
    # being judged.  Without this the guard could be "any channel off blocks
    # everything", which would be a different and much worse criterion.
    _sr, _out, err = _prep(_FakeScope(disp={"CHAN1": "0", "CHAN2": "1"}),
                           chan="CHAN2")
    check("the guard judges the selected channel, not any channel",
          err is None and _sr is not None and _sr > 0,
          err or "srate %.3g" % (_sr or 0))

    # The unreadable case must be REPORTED, never silently treated as
    # displayed: a guard that did not arm and says nothing is not a guard.
    _sr, _out, err = _prep(_FakeScope(unreadable=("CHAN2",)), chan="CHAN2")
    check("an unreadable display state is reported, not assumed displayed",
          err is None and "could not be read back" in _out,
          err or (("warned: " + _out.strip().splitlines()[0][:90])
                  if "could not be read back" in _out
                  else "no warning was printed"))
    # Recorded, not asserted: the parser must not turn an unknown reply into a
    # boolean.  "Cannot tell" is its own answer.
    for reply, want in (("1", True), ("0", False), ("OFF", False),
                        ("on", True), ("?", None), (None, None)):
        class _R(object):
            def __init__(self, r):
                self.r = r

            def query(self, s):
                if self.r is None:
                    raise RuntimeError("no answer")
                return self.r
        got = read_channel_disp(_R(reply), "CHAN2")
        check("read_channel_disp maps %r to %r" % (reply, want), got is want,
              "got %r" % (got,))

    # ------------------------------------------- parse_plstate (--phy-mode 2r2t)
    # The guard that decides whether the board really is in 2R2T.  Its failure
    # mode is FAILING OPEN on a string that was never a register dump, so the
    # negative cases below are the ones that matter -- and they are not
    # hypothetical: the loose pattern this replaced matched board_app's own
    # usage() text, which is reproduced here verbatim from rf_main.c:512-540 so
    # the fixture cannot drift into something easier than the real input.
    check("parse_plstate reads a real dump",
          parse_plstate(PLSTATE_REAL) == 0,
          "got %r" % (parse_plstate(PLSTATE_REAL),))
    check("parse_plstate reads 1R1T as 1, not as 'not 2R2T'",
          parse_plstate(PLSTATE_REAL.replace("phy_mode    = 0",
                                             "phy_mode    = 1")) == 1,
          "got %r" % (parse_plstate(PLSTATE_REAL.replace(
              "phy_mode    = 0", "phy_mode    = 1")),))

    USAGE_TEXT = (
        "usage: board_app <subcommand>\n"
        "  selftest               transport self-check (no vendor code)\n"
        "  status                 read the PL STATUS word\n"
        "  plcfg                  spec 6.1 Step 1: PL config + NEGATIVE check\n"
        "  plstate                read the PL config WITHOUT writing it\n"
        "                         (plcfg sets phy_mode = 1 before it judges\n"
        "                         anything, so it cannot be used to ASK what\n"
        "                         mode the PL is in -- only to set it)\n"
        "  init [trace.txt] [atten <mdB>] [lo <Hz>] [dump] [2r2t]\n"
        "                         2r2t starts the part in 2R2T (stage two)\n"
        "                         PL phy_mode = 0 to match.  It stops the\n"
        "                         tone first and LEAVES phase_inc = 0, so\n"
        "  phase_inc <n>          set the PL DDS phase increment (12 bit)\n")
    check("parse_plstate REJECTS board_app's usage text",
          parse_plstate(USAGE_TEXT) is None,
          "returned %r -- the guard matched a help message and would have "
          "measured an unknown mode as 2R2T" % (parse_plstate(USAGE_TEXT),))
    # The precise shape the stale binary produced: usage text whose only
    # numeric "phy_mode = 0" is indented prose.  This is the string that made
    # the previous guard print "phy_mode = 0 confirmed".
    check("parse_plstate rejects indented prose mentioning phy_mode = 0",
          parse_plstate("  PL phy_mode = 0 to match.  It stops the\n") is None,
          "returned %r" % (parse_plstate(
              "  PL phy_mode = 0 to match.  It stops the\n"),))
    # ...and the STATUS requirement is load-bearing on its own: a phy_mode line
    # without it is not a plstate dump, so a truncated or interleaved reply must
    # not be read as one.
    check("parse_plstate requires the STATUS line, not just phy_mode",
          parse_plstate("  phy_mode    = 0  (2R2T)\n") is None,
          "returned %r" % (parse_plstate("  phy_mode    = 0  (2R2T)\n"),))
    check("parse_plstate rejects an empty / failed read",
          parse_plstate(None) is None and parse_plstate("") is None,
          "got %r / %r" % (parse_plstate(None), parse_plstate("")))
    # `_plstate_line` is the "where was the board when we arrived" record.  It
    # must never quote usage prose as if it were a reading.
    check("_plstate_line quotes a real dump",
          _plstate_line(PLSTATE_REAL).startswith("phy_mode")
          and "2R2T" in _plstate_line(PLSTATE_REAL),
          "got %r" % (_plstate_line(PLSTATE_REAL),))
    check("_plstate_line says so, rather than guessing, when it cannot read",
          "could not be read back" in _plstate_line(None)
          and "could not be read back" in _plstate_line("  PL phy_mode = 0 to match.\n"),
          "got %r / %r" % (_plstate_line(None),
                           _plstate_line("  PL phy_mode = 0 to match.\n")))

    # --------------------------------------- every board_init carries the mode
    # A HALF-THREADED MODE IS THE DANGEROUS OUTCOME, not an unthreaded one: the
    # first few layer-C inits would pass 2r2t, the run would look like it was
    # measuring stage 2, and one later init that forgot the flag would
    # hard-reset the part into 1R1T mid-run (rf_main.c:588-646).  This is a
    # source-level check rather than a behavioural one for the same reason the
    # UI project checks widget registration by walking the source: a call that
    # omits the flag is not reachable by any test that calls the layer with a
    # fake board, because the fake board does not care what mode it is in.  The
    # only place the omission is visible is the source.
    _src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scope_tx_check.py")
    with open(_src_path, encoding="utf-8") as fh:
        _tree = ast.parse(fh.read())
    _unthreaded = []
    for _n in ast.walk(_tree):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                and _n.func.id == "board_init"):
            if "want_2r2t" not in {k.arg for k in _n.keywords}:
                _unthreaded.append(_n.lineno)
    check("every board_init call passes want_2r2t",
          not _unthreaded,
          "lines %s call board_init without the mode; on a 2R2T board those "
          "hard-reset the AD9361 and abort, leaving the part unprogrammed "
          "mid-run" % (_unthreaded,))

    print("\n=== selftest: %d checks, %d FAIL ===" % (checks[0], len(failures)))
    for f in failures:
        print("  FAILED: %s" % f)
    return 1 if failures else 0


class _NoClockBoard(_FakeBoard):
    def cmd(self, args):
        if args.split()[0] == "status":
            self.calls.append(args)
            return ("--- PL STATUS (current) ---\n"
                    "  f_DATA_CLK = NO CLOCK\n")
        return _FakeBoard.cmd(self, args)


# The exact text a real `plstate` prints (rf_main.c:649-655), kept verbatim so the
# pattern below is tested against the thing it will actually parse.
PLSTATE_REAL = ("  idelay_en   = 0x7F\n"
                "  idelay_tap  = 15\n"
                "  phase_inc   = 128\n"
                "  phy_mode    = 0  (2R2T)\n"
                "  STATUS      = 0x0F3333DD\n")


def parse_plstate(blob):
    """phy_mode out of a `plstate` dump, or None if this is not one.

    Line-anchored, and the STATUS marker is REQUIRED.  A bare
    re.search(r"phy_mode\\s*=\\s*(\\d)") is the obvious way to write this and it
    FAILS OPEN: `plstate` did not exist in the deployed binary, so board_app
    answered with its usage() text, which contains the line
    "PL phy_mode = 0 to match" -- the loose pattern matched that, the caller
    printed "phy_mode = 0 confirmed", and the run measured 1R1T while reporting
    stage 2.  A guard that a help message can satisfy is not a guard.  Anchoring
    kills the indented prose; requiring the STATUS line, which only the real
    subcommand prints, kills anything else that happens to mention the word.
    """
    blob = blob or ""
    m = re.search(r"^\s*phy_mode\s*=\s*([01])\b", blob, re.M)
    have_status = re.search(r"^\s*STATUS\s*=\s*0x[0-9A-Fa-f]+", blob,
                            re.M) is not None
    if not m or not have_status:
        return None
    return int(m.group(1))


def _plstate_line(blob):
    """The phy_mode line of a `plstate` dump, for quoting in a log -- or a
    plainly-worded note that there was no such line.  Never raises and never
    invents a value: this is the "where was the board when we arrived" record,
    and a record that guesses is worse than one that says it could not read."""
    for line in (blob or "").splitlines():
        if re.match(r"^\s*phy_mode\s*=", line):
            return line.strip()
    return "phy_mode could not be read back (%s)" % (
        "no reply" if not blob else "no phy_mode line in the reply")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board-app", default="/home/xilinx/board_app_src/board_app",
                    help="path to board_app on the board")
    ap.add_argument("--board", default=None,
                    help="board host; omitted => scope-only mode (no RF changes)")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL, choices=CHANNEL_CHOICES,
                    help="scope channel to measure: %s (default) or %s.  %s "
                         "carries TX2, which has no DDS tone (rf_tx_source.v:"
                         "103-104 zeroes its DAC data in mode 0), so the "
                         "tone-derived criteria report NOT APPLICABLE there "
                         "rather than passing." % (CHANNEL_CHOICES[0],
                                                   CHANNEL_CHOICES[1],
                                                   CHANNEL_CHOICES[1]))
    ap.add_argument("--skip-b", action="store_true",
                    help="skip layer B (diagnostic only)")
    ap.add_argument("--phy-mode", default="1r1t", choices=("1r1t", "2r2t"),
                    help="PL mode to measure.  1r1t (default, stage 1) runs "
                         "`plcfg`, which SETS phy_mode and asserts its own "
                         "negative criterion.  2r2t (stage 2) must not run "
                         "plcfg: its first act is to write phy_mode = 1, so on "
                         "a 2R2T board it would put the PL back into 1R1T while "
                         "the AD9361 stayed in 2R2T, and the run would measure "
                         "1R1T while reporting stage 2.  In 2r2t the script "
                         "instead issues `init 2r2t` itself -- which moves both "
                         "sides together, writing phy_mode = 0 while the part "
                         "is still held in reset, i.e. with DATA_CLK stopped -- "
                         "then reads phy_mode back off the PL and refuses to "
                         "measure unless it is 0.  Needs an explicit --board.")
    ap.add_argument("--selftest", action="store_true",
                    help="judge the criteria on synthetic spectra; "
                         "needs neither board nor scope")
    args = ap.parse_args()

    # One name for "the board is in 2R2T", derived once from the CLI and threaded
    # into every layer that re-inits the part.  It has to reach all of them: a
    # bare `init` drives RESETB low/high and only THEN reads phy_mode back
    # (rf_main.c:588-646), so an init issued without `2r2t` on a 2R2T board
    # hardware-resets the AD9361 and then aborts, leaving the part unprogrammed
    # in the middle of a measurement run.  Deriving it once here rather than at
    # each call site is what keeps a half-threaded mode from looking fine.
    want_2r2t = (args.phy_mode == "2r2t")

    if args.selftest:
        return _selftest()

    # Import pyvisa BEFORE anything Qt: visa32.dll fails to load otherwise
    # (a Qt DLL conflict this project has hit).
    import pyvisa

    rm = pyvisa.ResourceManager()
    res = [r for r in rm.list_resources() if "0x2A8D" in r]
    if not res:
        print("no DSOX1102A found in %s" % rm.list_resources())
        return 2

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    sys.path.insert(0, os.path.join(here, "..", "..", "scripts"))
    # ReconnectingScope comes from scope_verify.py; reusing it rather than
    # re-deriving it is the point -- the session-drop behaviour it encodes was
    # measured, not guessed.
    from scope_verify import ReconnectingScope
    from board_cmd import BoardCmd

    inst = ReconnectingScope(rm, res[0])
    # list_resources() enumerates PHANTOM nodes: an unplugged scope still
    # appears there, and so does one whose USBTMC session is stuck in the
    # driver (VI_ERROR_ALLOC / VI_ERROR_NCIC on open).  So a successful
    # enumeration is not evidence the instrument is there, and the open has to
    # be the thing that decides.  A traceback here would be indistinguishable
    # from a defect in the criteria, so it is reported as what it is.
    try:
        inst.open()
    except Exception as e:
        print("DSOX1102A at %s enumerated but could not be opened: %r" % (res[0], e))
        print("This is the instrument, not the board.  An unplugged scope and a "
              "stuck USBTMC session look identical here -- check with "
              "`Get-PnpDevice -PresentOnly`, and if the node is present but "
              "stuck, replug the USB cable or power-cycle the scope "
              "(killing the process does not clear it).")
        return 2
    # Refuse to start if the observation channel itself is not armed.  This is
    # checked before any board call, because a report produced through a dead
    # observation channel would be all FAILs with no hint that the FAILs are
    # about the instrument.
    try:
        srate = scope_prepare(inst, chan=args.channel)
    except InstrumentSetupError as e:
        print("INSTRUMENT SETUP FAILED: %s" % e)
        print("No signal verdict is produced -- this is the instrument, not "
              "the board.")
        return 2
    if srate < SRATE_MIN_HZ:
        # Refuse to judge.  Below the spec's 500 MSa/s the 80 MHz carrier is not
        # representable, so any FFT verdict here would be an artefact of the
        # instrument reported as a verdict about the board.
        print("INSTRUMENT SETUP FAILED: %.3f GSa/s < %.0f MSa/s required by "
              "spec 6.1 Step 4.  No signal verdict is produced."
              % (srate / 1e9, SRATE_MIN_HZ / 1e6))
        return 2

    board = BoardCmd(args.board_app, args.board) if args.board else None
    if board is None:
        print("[warn] --board not given: layer B only, no RF changes will be made")

    rep = Report()

    def wav_fft(channel):
        # The channel is an ARGUMENT and never a default.  A closure that
        # supplied one would silently undo --channel at every call site, which
        # is exactly the hardcoded-"CHAN1" failure this option exists to make
        # visible -- and it would do it without any call site looking wrong.
        return capture_fft(inst, channel)

    if args.channel != DEFAULT_CHANNEL:
        # Printed only on the non-default path, so a default run's output is
        # byte-for-byte what stage 1 was accepted on.  A CHAN2 report has a
        # different row set (several NOT APPLICABLE), and the reader has to be
        # able to tell at a glance which channel produced it.
        print("\n[channel] --channel %s: measuring scope channel %s.  TX2 "
              "carries no DDS tone (rf_tx_source.v:103-104), so the tone-derived "
              "criteria will report NOT APPLICABLE, not PASS."
              % (args.channel, args.channel))

    # R16: establish the PL operating point BEFORE any measurement, and
    # unconditionally -- NOT inside the `skip_b` branch, because layer C's `init`
    # hits the same guard and would fail too.
    #
    # `plcfg` is not optional here and is not only about phase_inc.  init writes
    # NO PL registers at all -- the only writers of RF_REG_PHASE_INC in rf_main.c
    # are `plcfg` and the `phase_inc` subcommand -- and the PL comes out of reset
    # with slv_reg3 = 0, i.e. phy_mode = 0 = PHY_MODE_2R2T
    # (axi_ctrl_v1_0_S00_AXI.v:239, :528; phy_tx.v:22-23), while rf_main.c
    # configures the AD9361 as 1R1T.  That is a frame-layout mismatch that
    # corrupts the TX datapath regardless of phase_inc, and at phase_inc = 0 the
    # DDS emits no sidebands at all.  `init` now refuses to run without it
    # ("phy_mode is not 1 -- run `plcfg` first", rf_main.c:643-646).
    #
    # Without this, layer B finds only LO leakage -- and that does NOT fail
    # loudly: find_nearest on a leakage-only spectrum still returns a line at LO,
    # so B1 prints a clean PASS and B2 judges its translation, green on a
    # MISCONFIGURED TRANSMITTER.  Same defect class as E11.
    #
    # plcfg also asserts its own negative criterion (STATUS must report no clock
    # while the BBPLL is unprogrammed), which falsifies the observation channel at
    # the top of the session for free.
    #
    # ⚠ It is NOT idempotent, and it is not free on a WARM board.  Measured
    # 2026-09-20: the negative criterion requires an *unprogrammed* BBPLL, so a
    # board left running from a previous round (DATA_CLK live, and aliasing to
    # 20.000 MHz through the 16-bit window counter) fails it every time --
    # "the STATUS channel reports 20.000 MHz of DATA_CLK while the BBPLL is
    # unprogrammed ... the observation channel is broken", rc = 1.  The
    # observation channel is not broken and the config writes themselves all
    # read back correct; what failed is the precondition, and it failed because
    # the part was already running.  So rc = 1 from `plcfg` here is expected on
    # a warm board and must not be read as "the PL is misconfigured" -- the
    # layer B/C numbers below are the evidence either way.  The step is still
    # run rather than skipped: on a cold board it is the only thing that puts
    # phy_mode = 1, and `init` refuses without it.
    #
    # ⚠ STAGE 2 INVERTS THIS, AND IT IS NOT A SUBTLE INVERSION.  `plcfg`'s first
    # act is `rf_reg_write(RF_REG_PHY_MODE, 1)` (rf_main.c:580) -- it writes
    # BEFORE it evaluates anything, and only then reads STATUS and returns 1
    # because a programmed part is of course producing DATA_CLK.  So calling it
    # on a board deliberately left in 2R2T does not merely "warn": it puts the PL
    # back into 1R1T, leaves the AD9361 in 2R2T, and hands the caller a failure
    # message ("the observation channel is broken") that names the wrong thing.
    # The measurement that follows then exercises 1R1T end to end, and its
    # numbers are indistinguishable from a healthy stage-1 run -- which is the
    # exact shape this project keeps having to catch: green, and measuring
    # nothing.  It cost this round one full pair of scope runs before it was
    # noticed, and the giveaway was `phy_mode = 1` read back off the board.
    #
    # So the mode is now the CALLER's decision, and in 2R2T this step only READS.
    # `--phy-mode 2r2t` requires an explicit --board (the board side owns 2R2T:
    # only `init 2r2t` moves the AD9361, so the script must not pretend it can
    # set up that state by itself).
    if board is not None:
        if args.phy_mode == "2r2t":
            print("\n--- PL config (2R2T: plcfg is NOT run) ---")
            print("plcfg's first act is rf_reg_write(RF_REG_PHY_MODE, 1) -- it "
                  "writes BEFORE it\nevaluates anything (rf_main.c:585).  On a "
                  "board deliberately left in 2R2T it\ntherefore puts the PL "
                  "back into 1R1T, leaves the AD9361 in 2R2T, and then\nreports "
                  "its own 'no DATA_CLK' negative criterion as failed, naming "
                  "the wrong\nthing.  The measurement that follows exercises "
                  "1R1T end to end and its numbers\nare indistinguishable from a "
                  "healthy stage-1 run -- green, and measuring nothing.\nThat "
                  "is exactly what happened on the first attempt at this stage; "
                  "the giveaway\nwas phy_mode = 1 read back off the board.")
            # Where the board was when we arrived.  EVIDENCE, not a gate: this
            # state is about to be overwritten by the init below, so gating on it
            # would be a test of the operator's memory rather than of the board.
            # (An earlier version of this block did gate on it and refused to run
            # at all unless a human had already issued `init 2r2t` -- which the
            # init right here then supersedes anyway.)
            print("  before: " + _plstate_line(board.cmd("plstate")))
            # Establish the mode, then PROVE it.  `init 2r2t` is the supported way
            # to move BOTH sides together, and its ordering is what makes it safe:
            # it drives RESETB low, releases it, waits out the part's boot, and
            # only THEN writes phase_inc = 0 and phy_mode = 0 -- i.e. with the
            # AD9361 still unprogrammed, so DATA_CLK has not appeared yet.  Only
            # after that does ad9361_init bring the clock up.  That is precisely
            # the discipline impl1_clock_groups.tcl:12-14 states ("the write must
            # happen with the AD9361 still in a state where DATA_CLK is stopped,
            # then the clock is allowed to appear"); the crossing has no
            # synchroniser, so a mid-stream write is a real CDC hazard, not a
            # style point.
            #
            # This costs one extra ad9361_init relative to letting layer B's own
            # `board_init(..., want_2r2t=True)` do it -- a VCO calibration, against
            # the 8 this script already performs.  Worth it: it makes the gate
            # verify a state this script created, instead of a precondition a
            # human was supposed to have arranged.
            board_init(board, lo_hz=int(LO_NOMINAL_HZ),
                       want_2r2t=True, phase_inc=PHASE_INC_1)
            time.sleep(0.3)
            plstate = board.cmd("plstate")
            print(plstate.rstrip() if plstate else "(plstate returned nothing)")
            pm = parse_plstate(plstate)
            if pm is None:
                print("RESULT FAIL: `plstate` did not return the expected "
                      "register dump (the\nphy_mode line and the STATUS line "
                      "must both be present), so phy_mode cannot be\nread.  "
                      "Refusing to measure an unknown mode.  If the output above "
                      "looks like\nusage text, the deployed board_app predates "
                      "the read-only `plstate`\nsubcommand -- rebuild and "
                      "re-deploy it.")
                return 2
            if pm != 0:
                print("RESULT FAIL: phy_mode still reads %d after `init 2r2t`, "
                      "which writes it to\n0 itself and confirms the readback "
                      "before programming the part (rf_main.c:776-787),\nso the "
                      "PL did not accept the mode change or something wrote it "
                      "back.  Refusing\nto measure -- a run in this state would "
                      "report stage 2 while measuring 1R1T." % pm)
                return 2
            print("phy_mode = 0 confirmed on the PL after `init 2r2t`; "
                  "measuring 2R2T.")
        else:
            print("\n--- PL config (spec 6.1 Step 1: plcfg + negative criterion) ---")
            board.cmd("plcfg")

    if not args.skip_b:
        print("\n--- Layer B (diagnostic: frequency only, no amplitude threshold) ---")
        # B needs LO leakage visible, and attenuation costs visibility -- but the
        # loopback safety floor is not negotiable: see SAFE_ATTEN_MDB.  A bare
        # `init` would reset LO to the struct's 80 MHz anyway, so state both.
        if board is not None:
            board_init(board, lo_hz=int(LO_NOMINAL_HZ), want_2r2t=want_2r2t,
                       phase_inc=PHASE_INC_1)
            time.sleep(0.3)
        b1 = layer_b(rep, wav_fft, LO_NOMINAL_HZ, "LO=80MHz",
                     chan=args.channel)
        if b1 is not None and board is not None:
            board_init(board, lo_hz=int(LO_SHIFTED_HZ), want_2r2t=want_2r2t,
                       phase_inc=PHASE_INC_1)
            time.sleep(0.3)
            b2 = layer_b(rep, wav_fft, LO_SHIFTED_HZ, "LO=85MHz",
                         chan=args.channel)
            if b2 is not None:
                moved = (b2 - b1) - (LO_SHIFTED_HZ - LO_NOMINAL_HZ)
                ok = abs(moved) <= LO_SHIFT_TOL_HZ
                rep.add("B2 line translates with LO (+5 MHz)",
                        "PASS" if ok else "FAIL",
                        "moved %.3f MHz, expected 5.000 MHz" % ((b2 - b1) / 1e6))
            board_init(board, lo_hz=int(LO_NOMINAL_HZ), want_2r2t=want_2r2t,
                       phase_inc=PHASE_INC_1)
            time.sleep(0.3)

    if board is not None:
        # The channel is named only on the non-default path: a default run's
        # stdout stays byte-for-byte what stage 1 was accepted on.
        print("\n--- Layer C (primary criteria, spec 6.1 Step 4%s) ---"
              % ("" if args.channel == DEFAULT_CHANNEL
                 else ", channel " + args.channel))
        layer_c(rep, board, wav_fft, chan=args.channel,
                want_2r2t=want_2r2t)

    rc = rep.summary(
        None if args.channel == DEFAULT_CHANNEL else
        "On %s the NOT-APPLICABLE rows are a different thing: those criteria "
        "describe a DDS tone and TX2 has none, so they were NOT JUDGED.  That "
        "is not a PASS and must not be reported as one."
        % args.channel)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())