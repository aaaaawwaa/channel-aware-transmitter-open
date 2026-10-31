#!/usr/bin/env python3
"""Post-init register read-back audit (spec 6.1 Step 5, spec 5.3).

The trace and the audit are two separate passes on purpose.  A read-back after
every write would double the SPI traffic inside the VCO calibration state
machine, and that calibration has a timing window -- inserting reads there
perturbs exactly what this round is trying to measure.  So the always-on trace
records transactions in memory (zero timing change) and this script re-reads a
PRE-ENUMERATED address list afterwards.

The exemption list below was fixed before the measurement, not derived from
what the read-back happened to show.  Deriving it afterwards would make the
audit a tautology.  EIGHT entries were added AFTER the frozen run (see the
comment block inside EXEMPT); each is justified by a mechanism in the vendor
source or by a board test, never by the value merely differing, and the frozen
run's six UNEXPLAINED stand unchanged in docs/rf/l2_audit_stage1.txt as the
honest measurement.

The seventh of those is 0x0F4 -- "seventh" in the order they were added, which
is not list order (it sits fifth in EXEMPT; 0x07C, the seventh in list order,
was added before it).  It is counted separately on purpose: it is
the one entry that turned an HONEST verdict (DECLARED-WEAK, the write-0 blind
spot) into a non-applicable one.  An earlier version of this docstring said
"six", which hid exactly that entry -- the same "let it blend into the total"
error the record warns about.  Fixed 2026-09-18 after a source review caught
the count disagreeing with the list (as of that check seven entries followed
the ADDED AFTER THE FROZEN RUN banner, not six).

The eighth, 0x10A, is the only entry added on LIVE-run evidence alone: it was
CONFIRMED in the frozen run (docs/rf/l2_audit_stage1.txt:224, "wrote 58, read
58") and only diverged in the third live run, which read back 0x56.  Its reason
says so out loud.  That is a weaker footing than the other seven, and it is
labelled as weaker rather than presented as if it had been there all along --
the alternative (leaving a one-bit drift of a part-owned gain index as a
standing UNEXPLAINED) would train the reader to ignore the counter, which is
the failure this list exists to prevent.  It is also the entry that excuses the
LEAST: rather than voiding the byte it masks on 0xE0, so the three bits the
driver does own stay checked.  Exempting less than strictly necessary is the
only direction this list is allowed to move.

Discipline: this script does not correct anything and does not abort on a
mismatch.  An unexplained mismatch is the most valuable thing it can report.

ON THE PROCESS SHAPE -- this replaces the brief's claim, which is false:
the audit does NOT read "through the SAME driver instance the init used".  It
cannot: `board_app audit <addr>` is a standalone process, and the driver
instance lived in the `init` process, which has exited.  What makes the pass
independent WITHOUT re-initialising the part is that `audit` calls `spi_init`
(which writes only the PL SPI divider and probes PL STATUS -- it sends nothing
to the AD9361) and then does a raw `ad9361_spi_read` through a zeroed
`struct spi_device`.  The part therefore stays exactly as `init` left it.  See
`rf_main.c`'s `audit_open()` for the same argument from the other side.

    python -u rf_overlay/sw/audit_report.py --trace trace_init.txt \
        --board-app /home/xilinx/board_app_src/board_app --board <host>
"""

import argparse
import re
import sys

# ---------------------------------------------------------------- exemptions
# Every entry needs a REASON.  "It did not match" is not a reason.
#
# (addr, mask, kind, reason)
#   kind "volatile" -- the register legitimately changes on its own
#   kind "weak"     -- the written value is 0 inside the mask, so "reads back 0"
#                      cannot prove the write took effect (this project has a
#                      memory entry on exactly this blind spot)
#   kind "masked"   -- compare ONLY the bits the driver owns:
#                      (read_back & mask) == (written & mask) -> CONFIRMED,
#                      in-mask bits differ -> UNEXPLAINED.  The reason must
#                      name the bits outside the mask and why they are not
#                      the driver's.  When (written & mask) == 0 the write-0
#                      blind spot still applies and the verdict is
#                      DECLARED-WEAK, exactly as for "weak".
#   kind "inert"    -- the address is not a writable configuration register
#                      (a read-data port / read alias), so write-then-read-back
#                      is not a meaningful test at all.  Always a declared
#                      verdict, never CONFIRMED or UNEXPLAINED.
#
# "masked" and "inert" were added because the two original kinds could not
# express the honest verdict for a register the driver only partly owns, or for
# one it does not own at all.  Without them the only way to silence such an
# address is a blanket "volatile" on the whole byte -- a deleted check.
EXEMPT = [
    # REMOVED 2026-09-18: (0x037, 0xFF, "volatile", "PRODUCT_ID is read-only
    # silicon revision, never written").  The reason refutes the entry: the
    # audit only ever classifies addresses that `addresses_written()` pulled out
    # of the trace's W lines, and 0x037 is never written in any of the three
    # traces (checked: 0 writes, 1 read, in the frozen run, the first live run
    # and the second live run alike).  The entry therefore fired zero times --
    # it deleted no check while ADVERTISING an exemption, which is the same
    # shape as the deleted 0x03E entry below.  Removing it changes no verdict;
    # PRODUCT_ID is still exercised every run by `selftest` (S1/S2b), which is
    # where a read-only ID belongs.
    # 0x3F4 / 0x3F5 / 0x3F6 are NOT exempt, and were removed from the first cut
    # of this list for the same reason as 0x00A below.  All three are
    # CONFIGURATION registers, not status: REG_BIST_CONFIG, REG_OBSERVE_CONFIG
    # and REG_BIST_AND_DATA_PORT_TEST_CONFIG (vendor/ad9361.h:569-571), and every
    # bit defined for them is a mask/enable/select field (ad9361.h:2766-2801)
    # with no read-only or self-updating bit anywhere.  Calling them "volatile"
    # claimed the part changes them on its own, which is simply untrue -- and a
    # false exemption is a check deleted, not deferred.  They are also
    # unreachable: the only writers in the vendor tree are
    # ad9361_bist_loopback / ad9361_bist_prbs / ad9361_bist_tone
    # (ad9361.c:1085/1144/1190), which have no caller inside ad9361.c and are
    # not referenced from our platform layer either.  So dropping them costs
    # nothing today; if a later stage enables BIST, the audit will CHECK these
    # registers instead of silently skipping them, which is the intent.
    # (0x3F5's old reason -- "the driver only writes back what it read" --
    # was a leftover from the selftest scratch-register episode in selftest.c,
    # where the read-modify-write behaviour of a *loopback* path made it a bad
    # scratch register.  That is a reason not to abuse it as a scratch pad; it is
    # not a reason to exempt it from the audit.)
    # 0x016 was MISSING from the first cut of this list and its absence produced
    # a guaranteed false UNEXPLAINED on every healthy run.  REG_CALIBRATION_CTRL
    # is written with the calibration mask and then read back as 0 by design:
    # ad9361_run_calibration writes the mask and immediately calls
    # ad9361_check_cal_done(phy, REG_CALIBRATION_CTRL, mask, 0) -- which POLLS
    # THAT REGISTER until it reads 0 (vendor/ad9361.c:1266-1290, :1305).  So the
    # bits are start/self-clearing and the last write is necessarily non-zero.
    (0x016, 0xFF, "volatile",
     "REG_CALIBRATION_CTRL -- the cal bits self-clear when calibration "
     "completes; the driver polls this register back to 0 "
     "(vendor/ad9361.c:1305), so a non-zero write reading 0 is correct"),
    # 0x00A is NOT exempt.  A previous version of this list declared it volatile
    # as "temperature / calibration status".  That was false: 0x00A is
    # REG_BBPLL (vendor/ad9361.h:59), a read/write CONFIGURATION register holding
    # BBPLL_DIVIDER / DAC_CLK_DIV2 / CLKOUT_ENABLE (vendor/ad9361.h:655-661).
    # The temperature registers are 0x00B-0x00D.  Exempting 0x00A removed the
    # only read-back check on the register the 60 MSPS chain rests on -- an
    # exemption that launders a real failure, which is worse than no audit.
    # REMOVED 2026-09-18: (0x03E, 0xFF, "weak", "REG_LVDS_INVERT_CTRL2 --
    # written 0, so a 0 read-back is not evidence").  That reason was FALSE and
    # the entry was a no-op, so it was deleting nothing while ADVERTISING an
    # exemption -- the worst of both.
    #   * The write is not 0.  rf_main.c:363 sets lvds_invert2_control = 0x0F,
    #     ad9361.c:3647 writes it, and both traces contain `W 03E 0F`; the
    #     frozen run prints `CONFIRMED 0x03E wrote 0F, read 0F`.
    #   * Because 0x0F & 0xFF != 0, the "weak" branch never fires: classify()
    #     only degrades to DECLARED-WEAK when the write is 0 INSIDE the mask,
    #     and otherwise falls through to normal judging.  0x03E was therefore
    #     always judged normally and always came out CONFIRMED.
    # There is no true justification for exempting it (the driver owns it and
    # it reads back correctly), so the entry is gone and 0x03E is now a
    # genuine check rather than a claimed-and-unused exemption.

    # -----------------------------------------------------------------------
    # ADDED AFTER THE FROZEN RUN.  The frozen run's output is
    # docs/rf/l2_audit_stage1.txt, which reported 6 UNEXPLAINED.  Those six
    # stand as the honest measurement; they were NOT rounded away.  The
    # entries below were added afterwards, each justified by a mechanism read
    # out of the vendor source or by a board test -- NOT by the value merely
    # differing.  That distinction is the whole discipline: an entry whose
    # only argument is "it read back something else" is a deleted check, and a
    # false exemption is a deleted check, not a deferred one.
    # -----------------------------------------------------------------------
    (0x04B, 0xF0, "masked",
     "REG_VCO_CTRL -- only F0 defines a field: FREQ_CAL_ENABLE (1<<7), "
     "FREQ_CAL_COUNT_LENGTH (bits 6:5), FREQ_CAL_RESET (1<<4) "
     "(vendor/ad9361.h:929-933).  Bits 3:0 are not defined anywhere in the "
     "vendor tree and nothing writes them: the driver's sole write is "
     "FREQ_CAL_ENABLE | FREQ_CAL_COUNT_LENGTH(3) = 0xE0 "
     "(vendor/ad9361.c:6571; trace `W 04B E0`).  The read-back's bit 0 is "
     "therefore outside every field the driver owns, so only F0 is compared"),
    (0x00C, 0x01, "weak",
     "REG_START_TEMP_READING -- the only defined bit is START_TEMP_READING "
     "(1<<0) (vendor/ad9361.h:663-665); bits 3:1 have no field in the vendor "
     "source.  The driver's sole write is 0x00 (vendor/ad9361.c:4143; trace "
     "`W 00C 00`), and the read-back has bit 0 = 0, so the one documented "
     "field agrees -- but a zero write inside the mask proves nothing"),
    (0x134, 0xFF, "inert",
     "REG_GAIN_TABLE_READ_DATA1 -- a gain-table READ-data port "
     "(vendor/ad9361.h:272), not writable configuration.  The driver's only "
     "writes are the two `Dummy Write to delay` calls per gain-table index "
     "(vendor/ad9361.c:1479-1493; trace `W 134 00`); its real use is a read "
     "(vendor/ad9361.c:1812).  The live 0x6F is the value latched on the "
     "WRITE port 0x131 (trace `W 131 6F`), which this same audit confirms as "
     "`0x131 wrote 6F, read 6F` -- the read port answered the write port, as "
     "a read port does"),
    (0x064, 0xFF, "inert",
     "REG_TX_FILTER_COEF_READ_DATA_2 -- 0x061/0x062 are the WRITE_DATA_1/2 "
     "ports and 0x063/0x064 the READ_DATA_1/2 ports (vendor/ad9361.h:130-139), "
     "so this is a coefficient READ port.  Its only writes are the dummy "
     "delay writes in the coefficient download loop "
     "(vendor/ad9361.c:5786-5787; every trace shows `W 064 00`).  The live "
     "0xFF is the coefficient latched by tap select: every trace has a "
     "`W 060 <tap>` / `W 062 FF` pair immediately before those dummy writes, "
     "and this same audit confirms the identical mechanism on the gain-table "
     "port (`0x131 wrote 6F, read 6F`).  No line numbers are cited for those "
     "writes on purpose -- they move between runs (the pair sits at trace "
     "lines 2593/2595 in the frozen run and 2608/2592 in the live run, so a "
     "bare `trace:NNNN` citation is valid for exactly one trace and silently "
     "wrong for the next; both pairings were checked on 2026-09-18)"),
    (0x0F4, 0xFF, "inert",
     "REG_RX_FILTER_COEF_READ_DATA_2 -- the RX twin of 0x064: same READ-port "
     "pair 0x0F3/0x0F4 (vendor/ad9361.h:221-222).  The RX mapping is the "
     "load-bearing step, so it is cited rather than assumed: the coefficient "
     "loop walks the SAME code for both paths, the two bases differing by "
     "`offs = REG_RX_FILTER_COEF_ADDR - REG_TX_FILTER_COEF_ADDR` = 0x90 "
     "(vendor/ad9361.c:5754), which carries 0x064 -> 0x0F4.  Its only writes "
     "are the same dummy-delay writes (trace `W 0F4 00`).  It avoided being "
     "reported as a mismatch only by luck -- it happened to read 0x00, which "
     "the write-0 rule turned into DECLARED-WEAK "
     "(docs/rf/l2_audit_stage1.txt:332); had the part latched a non-zero "
     "coefficient the same inert port would have been a false UNEXPLAINED"),
    (0x109, 0xFF, "volatile",
     "REG_RX1_MANUAL_LMT_FULL_GAIN -- the one entry justified by a BOARD "
     "MEASUREMENT rather than a vendor citation, and as of 2026-09-18 it is a "
     "PROOF rather than an inference.  The first wording claimed 'the gain "
     "index is AGC state', which a source review could not find stated "
     "anywhere in the vendor tree: the driver only WRITES this register "
     "(vendor/ad9361.c:1503, :3728, :4008) and nothing calls the read-back "
     "AGC-owned.  The proof has three legs.  (1) The trace is complete for "
     "this register's lifetime: rf_main.c:700 opens it after RESETB is "
     "asserted and before RESETB is released (rf_main.c:697/702), so every SPI "
     "transfer of the entire init is recorded, and platform_rf.c:177 can only "
     "drop records at its 262,144-record cap -- the traces hold 5,033 (frozen) "
     "and 5,048/5,058 (live) records, so that guard cannot have fired.  (2) The "
     "driver's last write in every trace is 0x4C -- `SIZE_FULL_TABLE - 1` = "
     "77-1 (vendor/ad9361.c:397, :3723-3731) -- and no trace anywhere carries "
     "0x45, 0x44 or 0x3F for this address.  (3) `board_app audit` only READS "
     "(see this module's docstring), so nothing writes between init and the "
     "read-back, yet the read-back was 0x45 (frozen run), 0x44 (run 2) and "
     "0x3F (run 3).  Values that no write ever carried, arriving with no write "
     "in between, can only have been written by the PART.  Corroboration: the "
     "values drift DOWNWARD over the session (0x4C at init, 0x45/0x44/0x3F "
     "afterwards) as a gain index falling, and a hardware reset (RESETB "
     "toggled by `board_app selftest`, vendor/ad9361.c:1002-1015 -- the GPIO "
     "path writes no register, so a reset is invisible in the trace) brings it "
     "back to exactly 0x4C, the silicon default, which happens to equal the "
     "table maximum the driver writes.  CONTROL: RX2 is the disabled channel "
     "and its twin 0x10C read 0x4C in all three runs.  That control is weaker "
     "than it looks and is not the load-bearing part: 0x10C's reset baseline "
     "is ALSO 0x4C, so a static RX2 register is expected regardless.  What the "
     "control does carry is the contrast -- RX1's index moves, RX2's does not, "
     "and the only difference between the two channels here is enablement.  "
     "RESIDUAL UNCERTAINTY, disclosed: leg (1) proves the trace is complete "
     "for SPI, not that the part has no other input; EN_AGC is a GPIO outside "
     "the trace.  So 'the AGC moved it' is still a mechanism read off the "
     "register's name and its drift direction, not a citation"),
    (0x10A, 0xE0, "masked",
     "REG_RX1_MANUAL_LPF_GAIN -- same story as 0x109 one register over, and "
     "the same board evidence, but excusing LESS of the byte.  THE DRIVER DOES "
     "NOT OWN THE LOW FIVE BITS.  Its only covering write in all three traces "
     "is a read-modify-write at vendor/ad9361.c:4006-4007, which sets "
     "POWER_MEAS_IN_STATE_5 (bits 7:5, mask 0xE0; vendor/ad9361.h:1435) and "
     "leaves bits 4:0 -- RX_LPF_IDX_MASK, the LPF gain index "
     "(vendor/ad9361.h:1436-1437) -- untouched: `R 10A 58` then `W 10A 58`, a "
     "write that carried exactly the byte it had just read.  So the 0x18 in "
     "those five bits is not the driver's work.  Who put it there is settled "
     "by the reset baseline, measured rather than assumed: after a hardware "
     "reset (RESETB toggled by `board_app selftest`) 0x10A reads 0x40, i.e. "
     "the index starts at 0, and the trace -- complete over the whole init, "
     "since rf_main.c:700 opens it before RESETB is released -- contains no "
     "write of 0x18 to any byte covering this address.  The part is the only "
     "remaining writer.  Consistent with that: post-init reads were 0x58 in the "
     "frozen run and run 2 and 0x56 in run 3 (the index moving 24 -> 22, again "
     "the part), and a reset returns it to 0x40.  Because bits 7:5 ARE the "
     "driver's, this entry masks on 0xE0 instead of excusing the byte: the "
     "power-measurement field is still compared (written 0x40 == read 0x40 in "
     "every run) and only the part-owned gain index is left unchecked.  "
     "DELIBERATELY NOT EXEMPTED: the RX2 twins 0x10C/0x10D.  RX2 is disabled, "
     "so they are this entry's control -- if they ever move, that is a real "
     "finding and the audit must still say UNEXPLAINED.  DISCLOSED: this is "
     "the only entry added on LIVE-run evidence alone.  The frozen run scored "
     "it CONFIRMED (docs/rf/l2_audit_stage1.txt:224, `wrote 58, read 58`); it "
     "diverged in the third live run, and the entry was added then, not "
     "earlier.  Same residual uncertainty as 0x109"),
    (0x07C, 0xFF, "volatile",
     "REG_TX2_DIG_ATTEN -- TWO mechanisms, and both are needed.  (1) Bit 6 "
     "IMMEDIATELY_UPDATE_TPC_ATTEN is a self-clearing trigger: every trace "
     "shows the driver reading the register, writing it back with bit 6 set, "
     "and the next read coming back WITHOUT bit 6 (`R 07C 17` -> `W 07C 57` "
     "-> `R 07C 17`; a run whose attenuation is 0 shows the same shape as "
     "0x00/0x40/0x00).  (2) CRUCIALLY, bits 5:0 are NOT driver-owned data: "
     "ad9361_spi_writef is a read-modify-write (vendor/ad9361.c:822-839), so "
     "every `W 07C XX` carries exactly the bits 5:0 that the preceding "
     "`R 07C XX` returned -- the driver never authors that data.  What the "
     "part puts there, as observed: 0x00 in every trace's own reads, 0x17 on "
     "a live read about ten minutes after an init, and 0x00 again on a live "
     "read immediately after a fresh init (the three live reads on record are "
     "0x17, 0x17, 0x00).  An earlier version of this string claimed a third "
     "value, 0x01 'after the 10 dB rerun'; NO artifact contains it, so it was "
     "deleted on 2026-09-18 rather than left standing -- an unsourced number "
     "inside a justification is precisely what a reader cannot check.  So "
     "this is not merely 'bit 6 self-clears': bits 5:0 are maintained by the "
     "part, and a reason keyed on bit 6 alone would launder whatever explains "
     "them.  The exact encoding of bits 5:0 is not documented in the vendor "
     "tree"),
]


def _record_write(last, order, base, data_bytes):
    """Record one trace `W` record into `last` / `order`.

    The AD9361 DECREMENTS the address across a multi-byte transfer, so byte k
    belongs to base-k -- NOT base+k.  This is the direction the vendor driver
    depends on, four independent times over:

      * AuxADC: ad9361_get_auxadc reads 2 bytes at REG_AUXADC_LSB (0x01F) and
        returns (buf[1] << 4) | AUXADC_WORD_LSB(buf[0]) -- buf[1] must be the
        MSB register, which is REG_AUXADC_WORD_MSB = 0x01E, i.e. base-1
        (vendor/ad9361.c:4186-4190, vendor/ad9361.h:79-80).
      * RSSI: 6 bytes at REG_PREAMBLE_LSB (0x1AC) are indexed so that buf[4]
        and buf[5] are RMSI registers at 0x1A8/0x1A7 -- again below the base
        (vendor/ad9361.c:2414-2425).
      * TX attenuation: ad9361_set_tx_atten writes [MSB, LSB] at
        REG_TX1_ATTEN_1 = 0x074, and the map is _ATTEN_0 = 0x073 / _ATTEN_1 =
        0x074 (vendor/ad9361.h:152-155).  Decrementing puts the LSB in
        REG_TX1_ATTEN_0; incrementing would put it in 0x075, which is a
        DIFFERENT CHANNEL's register.
      * RFPLL: a 5-byte burst whose buf[3] is the integer-word MSB is re-read
        with ad9361_spi_read(..., reg - 3) (vendor/ad9361.c:6815-6820).

    God help us.  Note that ad9361_get_tx_atten's "buf[0] is the MSB" does NOT
    by itself settle the direction -- byte 0 lands on the base under either
    rule -- which is why the first cut of this function got it backwards and
    was self-consistently wrong.  An assertion that `W 074 AA BB` keys BB to
    0x073 (not 0x075) is what pins it down.
    """
    if not data_bytes:
        # A `W` record carrying no data bytes is anomalous (the driver always
        # sends at least one).  The brief mapped it to a written 0; kept that
        # way rather than silently dropped, and called out in the report.
        if base not in last:
            order.append(base)
        last[base] = 0
        return
    for k, byte in enumerate(data_bytes):
        addr = base - k
        if addr < 0:
            # Cannot be a real address.  Skipping silently would shrink the
            # audit without saying so, so this is reported by the caller's
            # address count; a trace that triggers it is malformed.
            continue
        if addr not in last:
            order.append(addr)
        last[addr] = byte


def addresses_written(trace_path):
    """Parse the trace into {addr: last_value_written} plus first-seen order.

    The trace format is written by platform_rf.c:211-213 as `"%c %03X"` plus
    `" %02X"` per data byte, e.g. `W 3F0 01 02` (read = `R`).
    """
    last = {}
    order = []
    with open(trace_path) as f:
        for line in f:
            m = re.match(r"^([WR]) ([0-9A-F]{3})((?: [0-9A-F]{2})*)$",
                         line.strip())
            if not m or m.group(1) != "W":
                continue
            base = int(m.group(2), 16)
            data_bytes = [int(x, 16) for x in m.group(3).split()]
            _record_write(last, order, base, data_bytes)
    return last, order


AUDIT_LINE = re.compile(r"^audit 0x([0-9A-Fa-f]{3}) = 0x([0-9A-Fa-f]{2})$")


def parse_audit_value(want_addr, raw):
    """Pull the register value out of one `board.cmd("audit <addr>")` reply.

    The frozen format is `audit 0x%03X = 0x%02X` (rf_main.c:911), read as the
    LAST whitespace token of that line.  That is kept -- but the reply is not
    always just that line.  `board_cmd.cmd()` reads `pynq_ssh.py`'s stdout,
    and `pynq_ssh.run_commands` prints a `$ <cmd>` banner before the program's
    output and a 60-dash separator after it.  Taking `split()[-1]` over the
    WHOLE reply therefore returns the separator, and `int(_, 16)` raises
    ValueError -- which would mark every single address READ-FAILED and make
    this script worthless while looking like it had run.  board_cmd.py's own
    docstring warns about exactly this: callers "must line-scan for their own
    key rather than assume a shape".

    So: locate the audit line, then take its last whitespace token.  Raises
    ValueError when no audit line for `want_addr` is present -- which is also
    what an empty stdout (failed read, rf_main.c:907 prints to stderr) looks
    like.
    """
    for line in raw.splitlines():
        m = AUDIT_LINE.match(line.strip())
        if not m:
            continue
        if int(m.group(1), 16) != want_addr:
            # A line for some other address must not be read as this one's
            # value; a misaligned banner would otherwise be silently believed.
            continue
        return int(line.strip().split()[-1], 16)
    raise ValueError("no audit line for 0x%03X" % want_addr)


def classify(addr, written, read_back):
    """Return (verdict, detail).

    Five verdicts, and what puts an address in each:

    CONFIRMED           the address is not in EXEMPT and the read-back byte
                        equals the last byte the trace says we wrote.  For a
                        "masked" exemption, the equality holds on the masked
                        bits only.
    UNEXPLAINED         the address is not in EXEMPT and the read-back byte
                        differs (for "masked", the in-mask bits differ).  This
                        is the audit's most valuable output.
    DECLARED-VOLATILE   the address is in EXEMPT with kind "volatile": the
                        register legitimately changes on its own, so no
                        read-back value can confirm or refute the write.
    DECLARED-INERT      the address is in EXEMPT with kind "inert": it is not
                        a writable configuration register (a read-data port),
                        so write-then-read-back tests nothing.  Never
                        CONFIRMED or UNEXPLAINED.
    DECLARED-WEAK       the premise "a 0 read-back proves nothing" holds.  Two
                        ways to get here: the address is in EXEMPT with kind
                        "weak"/"masked" and nothing non-zero was written inside
                        the mask; or -- the general rule, added after review --
                        the byte written was 0x00 whatever the address.  The
                        second case matters because the registers this round
                        exists to prove hit it: REG_TX1_ATTEN_1 (0x074) carries
                        only the attenuation MSB, so it is written 0x00 for
                        every attenuation below 63.75 dB -- i.e. for the entire
                        range this round uses.  Reporting that as CONFIRMED
                        would be the write-0 blind spot this project already has
                        a memory entry about.  When EXEMPT supplies a mask and
                        the trace shows a non-zero write inside it, the premise
                        is false and the address is judged normally; otherwise
                        the `mask` field would be dead code and the stated rule
                        would not be the rule.
    """
    for a, mask, kind, reason in EXEMPT:
        if a != addr:
            continue
        if kind == "inert":
            # Not a writable register at all -- no comparison is meaningful.
            return ("DECLARED-INERT", "%s: %s" % (reason, "read %02X" % read_back))
        if kind == "masked":
            # Compare only the bits the driver owns.  The write-0 blind spot
            # still applies: a zero INSIDE the mask proves nothing, so that
            # case falls to DECLARED-WEAK rather than CONFIRMED.
            if (written & mask) == 0:
                return ("DECLARED-WEAK",
                        "%s: wrote %02X, read %02X, nothing non-zero written "
                        "inside mask %02X" % (reason, written, read_back, mask))
            if (read_back & mask) == (written & mask):
                return ("CONFIRMED",
                        "%s: wrote %02X, read %02X, in-mask bits agree "
                        "(mask %02X)" % (reason, written, read_back, mask))
            return ("UNEXPLAINED",
                    "%s: wrote %02X, read %02X, in-mask bits DIFFER "
                    "(mask %02X)" % (reason, written, read_back, mask))
        if kind == "weak":
            if (written & mask) == 0:
                return ("DECLARED-WEAK",
                        "%s: %s" % (reason, "read %02X" % read_back))
            continue
        return ("DECLARED-VOLATILE",
                "%s: %s" % (reason, "read %02X" % read_back))
    if (read_back & 0xFF) == (written & 0xFF):
        if (written & 0xFF) == 0:
            # "It read back what I wrote" is not evidence when what was written
            # was 0x00: a write that never landed is indistinguishable.
            return ("DECLARED-WEAK",
                    "wrote 00, read 00 -- a zero write cannot be verified")
        return "CONFIRMED", "wrote %02X, read %02X" % (written, read_back)
    return "UNEXPLAINED", "wrote %02X, read %02X" % (written, read_back)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--board-app", default="/home/xilinx/board_app_src/board_app")
    ap.add_argument("--board", default=None,
                    help="omit for an offline classification of the trace alone")
    ap.add_argument("--out", default="audit_report.txt")
    args = ap.parse_args()

    last, order = addresses_written(args.trace)
    print("trace: %d distinct addresses written" % len(last))

    board = None
    if args.board:
        from board_cmd import BoardCmd
        board = BoardCmd(args.board_app, args.board)
        # `audit-begin` opens the raw SPI path (PL divider + STATUS probe only;
        # nothing is sent to the AD9361).  It is a separate process from each
        # `audit`, which re-opens the same path itself -- that is deliberate
        # and idempotent, so a dead transport is reported once up front rather
        # than once per register.
        board.cmd("audit-begin")
    else:
        # Loud and early, not just per-line: an offline run proves nothing
        # about any register, and a report that implied otherwise would be the
        # exact defect class this plan keeps running into.
        print("offline: no --board given -- NOTHING was read.  Every address "
              "below is NOT-READ, so this run confirms no register value; it "
              "only shows what the trace claims was written.")

    confirmed = weak = volatile = inert = unexplained = read_failed = not_read = 0
    lines = []
    for addr in order:
        written = last[addr]
        if board is None:
            verdict, detail = "NOT-READ", "wrote %02X; no board given" % written
            not_read += 1
        else:
            raw = board.cmd("audit %03X" % addr)
            try:
                read_back = parse_audit_value(addr, raw)
            except (IndexError, ValueError):
                # `audit` prints its failure to stderr and exits non-zero, so
                # stdout is empty and board_cmd returns "".  Fails loud rather
                # than being read as a register value.
                verdict, detail = "READ-FAILED", "raw output: %r" % raw[:80]
                read_back = None
            else:
                verdict, detail = classify(addr, written, read_back)

        if   verdict == "CONFIRMED":          confirmed += 1
        elif verdict == "DECLARED-WEAK":      weak += 1
        elif verdict == "DECLARED-VOLATILE":  volatile += 1
        elif verdict == "DECLARED-INERT":     inert += 1
        elif verdict == "UNEXPLAINED":        unexplained += 1
        elif verdict == "READ-FAILED":        read_failed += 1
        elif verdict == "NOT-READ":           pass   # counted where it is set
        else:
            # A verdict no counter owns would silently vanish from the
            # summary, which is the "green while proving nothing" shape this
            # script exists to avoid.  (Added with the inert verdict, whose
            # counter could otherwise be forgotten.)
            raise AssertionError("uncounted verdict %r" % verdict)

        line = "%-18s 0x%03X  %s" % (verdict, addr, detail)
        lines.append(line)
        print(line)

    if board is not None:
        board.cmd("audit-end")

    summary = (
        "\n=== audit summary ===\n"
        "  confirmed          : %d\n"
        "  declared volatile  : %d\n"
        "  declared inert     : %d\n"
        "  declared weak      : %d\n"
        "  UNEXPLAINED        : %d\n"
        "  read failed        : %d\n"
        "  not read (offline) : %d\n"
        % (confirmed, volatile, inert, weak, unexplained, read_failed, not_read))
    print(summary)

    # One loud line, not just N per-address details: a run whose reads ALL
    # failed otherwise prints a summary that looks like a clean audit of a
    # healthy part while having proved nothing.
    if board is not None and order and read_failed == len(order):
        print("EVERY read failed (%d/%d) -- this run proves NOTHING about any "
              "register.  Check the board link and the `audit` subcommand "
              "before reading anything else here."
              % (read_failed, len(order)))

    with open(args.out, "w") as f:
        f.write("\n".join(lines) + "\n" + summary)

    if unexplained:
        print("Unexplained mismatches are NOT failures of this run -- they are "
              "the most valuable thing it can report.  Investigate each one "
              "before drawing any conclusion from the PASS criteria.")
    return 0        # never fails the run: the audit reports, it does not gate


if __name__ == "__main__":
    sys.exit(main())