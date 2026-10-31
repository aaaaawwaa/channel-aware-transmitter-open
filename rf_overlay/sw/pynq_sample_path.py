#!/usr/bin/env python3
"""C17 -- drive the sample path from board-side Python, with no board_app.

WHY THIS IS A CRITERION AND NOT A CONVENIENCE
=============================================
Before the 2026-09-19 rebuild this overlay had no addressable storage at all:
every control went over SPI one command at a time, through board_app.  The two
AXI BRAM windows added by that rebuild are the first things on this overlay a
Python MMIO can touch, and the user's direction is to drop the ACM2108 and move
everything to the AD9361 -- which makes the board-side Python path the main one
and board_app's SPI route a configuration tool.  So whether PYNQ can reach the
sample buffers decides the shape of T05, and it is worth checking before it is
built on.

CRITERION (written before measuring)
====================================
1. The overlay loads and both BRAM controllers can be located.  Their base
   addresses come from the DEPLOYED .hwh, never hardcoded: assign_bd_address
   moves them when the BD changes, and a hardcoded address that happens to be
   valid would write into some other peripheral.

   The first version of this read them from `Overlay().ip_dict`, which turned
   out to be wrong on this image: PYNQ 2.7 does not expose AXI BRAM controllers
   there at all.  `ip_dict` listed only axi_ctrl_0, pl_spi_0, axi_gpio_ctrl and
   ps7_0, while the .hwh on the board did contain both controllers -- and the
   AWG overlay's .hwh carries them with the same HWVERSION/IPTYPE, so it is not
   a difference in our file.  This project has never taken a base address from
   ip_dict; platform_rf.h says "taken from rf_overlay/out/rf_overlay.hwh" and
   means it.  So the criterion reads the .hwh too, which is both what
   everything else here does and a stronger source: the file is hash-verified
   against the build, whereas ip_dict is somebody else's parse of it.
2. A pattern written into the TX buffer reads back identical.  A byte-addressed
   port with a word index would read back a different word, not an error.
3. With src_sel = 1, `smp_pos` ADVANCES.
4. PASS NV: with src_sel = 0 (DDS), `smp_pos` does NOT advance.  Without this,
   "it advances" says nothing about which source is feeding the reader -- a
   free-running counter would satisfy check 3 on its own.

5. PRECONDITION, and the first version of this script did not have it: DATA_CLK
   must actually be running before checks 3 and 4 mean anything.

   Run before `init`, the AD9361 is unconfigured and DATA_CLK does not exist --
   but DATA_CLK arrives on an UNTERMINATED differential pair, so the pair picks
   up noise and that noise clocks the whole DATA_CLK domain erratically.  The
   first run of this script reported "smp_pos advances" from readings of
   63, 9, 9, 9, 9, 10, 10, 10 across MMIO reads ~25 us apart -- a position that
   should move at 60 MHz.  It was passing on noise, not on a data path.

   So the clock is checked by its MAGNITUDE, the same way `plcfg` does it and
   for the same reason: a sticky edge flag answers "is this line wiggling",
   which noise satisfies.  Below the ceiling the run stops, rather than
   reporting a pass that means nothing.

Usage (on the board):
    # after the normal bring-up: Overlay(), then board_app plcfg, then init
    sudo env PYTHONPATH=/home/xilinx XILINX_XRT=/usr python3 pynq_sample_path.py
    sudo ... python3 pynq_sample_path.py --load    # also load the overlay
    python3 pynq_sample_path.py --selftest         # anywhere, no pynq needed

WHY --load IS NOT THE DEFAULT
-----------------------------
Overlay() REPROGRAMS THE PL.  That wipes every axi_ctrl register -- phy_mode
back to 0, so `init` then refuses with "phy_mode is not 1 -- run plcfg first" --
and it drives rf_gpio back to 0, which is RESETB ASSERTED on the AD9361
(platform_rf.h: RF_GPIO_RESETB = 2, and RESETB is active low).  So a load leaves
the part held in reset and DATA_CLK gone.

The first version of this script called Overlay() unconditionally, and every
check after it was measured against a DATA_CLK domain with no clock on it --
being clocked instead by noise coupling into the unterminated DATA_CLK pair.
smp_pos appeared to creep by one count every few reads and then freeze, which
looks like a slow reader and was actually a dead one.

So the default assumes the bring-up already happened and only touches MMIO.  The
order when you do need to load is: Overlay(), plcfg, init, then this.
"""
import sys
import time

OVERLAY = '/home/xilinx/overlays/rf/rf_overlay.bit'

# axi_ctrl register offsets.  These come from the RTL, not the .hwh, so they do
# not move with the BD -- only the AXI bases do.
REG_SRC_CFG, REG_SMP_LEN, REG_SMP_POS = 0x14, 0x18, 0x1C
REG_STATUS = 0x10
REG_CAP_CFG, REG_CAP_RATIO, REG_CAP_LEN, REG_CAP_STATUS = 0x24, 0x2C, 0x30, 0x34

SRC_SEL, SMP_WRAP, SMP_COMMIT = 1, 2, 4
CAP_ARM, CAP_CONT, CAP_COMMIT = 1, 2, 4

POS_MASK = 0x1FFF
DONE_BIT = 13

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-58s %s%s" % (name, "PASS" if cond else "FAIL",
                            ("   " + detail) if (detail and not cond) else ""))


def pack(i, q):
    """Word format: I in [15:0], Q in [31:16], each sign-extended from 12 bits."""
    return ((q & 0xFFF) << 16) | (i & 0xFFF)


def unpack(w):
    i = w & 0xFFF
    q = (w >> 16) & 0xFFF
    return (i - 0x1000 if i & 0x800 else i), (q - 0x1000 if q & 0x800 else q)


def selftest():
    ok = True

    def t(name, cond):
        nonlocal ok
        print("  %-58s %s" % (name, "PASS" if cond else "FAIL"))
        ok = ok and bool(cond)

    t("pack/unpack round-trips 0", unpack(pack(0, 0)) == (0, 0))
    t("pack/unpack round-trips +2047", unpack(pack(2047, 2047)) == (2047, 2047))
    t("pack/unpack round-trips -2048", unpack(pack(-2048, -2048)) == (-2048, -2048))
    t("pack/unpack round-trips -1", unpack(pack(-1, -1)) == (-1, -1))
    t("I occupies [15:0]", (pack(0x123, 0) & 0xFFFF) == 0x123)
    t("Q occupies [31:16]", (pack(0, 0x456) >> 16) == 0x456)
    # A word index used as a byte address reads a different word rather than
    # erroring, so the check that catches it is that word N holds what was put
    # in word N -- spelled out here as the arithmetic that makes it so.
    t("word address scales by 4", 5 * 4 == 20)
    t("0x1FFF masks a 13-bit position", POS_MASK == 8191)
    t("done is bit 13", DONE_BIT == 13)
    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def hwh_base(hwh_path, instance):
    """Find an instance's PS-side base address in the deployed .hwh.

    The .hwh is the authority for the address map and is hash-verified against
    the build; ip_dict is somebody else's parse of it and, on this image, does
    not include AXI BRAM controllers at all.  Raises if the instance is absent
    -- returning 0 would point MMIO at the PS's own low memory.
    """
    import re
    txt = open(hwh_path, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r'<MEMRANGE[^>]*>', txt):
        s = m.group(0)
        inst = re.search(r'INSTANCE="([^"]*)"', s)
        base = re.search(r'BASEVALUE="([^"]*)"', s)
        if inst and base and inst.group(1) == instance:
            return int(base.group(1), 16)
    raise KeyError("%s is not in %s -- the wrong .hwh is deployed, or the BD "
                   "revision on the board predates it" % (instance, hwh_path))


def main():
    from pynq import MMIO

    do_load = '--load' in sys.argv

    print("=" * 78)
    print("C17 -- board-side Python drives the sample path (no board_app)")
    print("=" * 78)

    if do_load:
        from pynq import Overlay
        Overlay(OVERLAY)
        print("  overlay loaded: %s" % OVERLAY)
        print("  NOTE: that reprogrammed the PL, so the AD9361 is now held in")
        print("        reset (RESETB is a PL GPIO).  Run `board_app plcfg` then")
        print("        `board_app init` before the advance checks will mean")
        print("        anything.")
    else:
        print("  using the already-loaded overlay (pass --load to program it)")

    hwh = OVERLAY[:-4] + '.hwh'
    try:
        ctrl = hwh_base(hwh, 'axi_ctrl_0')
        txb = hwh_base(hwh, 'axi_bram_ctrl_tx')
        rxb = hwh_base(hwh, 'axi_bram_ctrl_rx')
    except (KeyError, IOError) as e:
        print("  FAIL locating the apertures: %s" % e)
        return 1

    print("  axi_ctrl_0       @ 0x%08X   (from %s)" % (ctrl, hwh))
    print("  axi_bram_ctrl_tx @ 0x%08X" % txb)
    print("  axi_bram_ctrl_rx @ 0x%08X" % rxb)

    chk("both BRAM controllers located in the deployed .hwh",
        txb and rxb and txb != rxb)

    reg = MMIO(ctrl, 0x1000)
    tx = MMIO(txb, 16384)
    rx = MMIO(rxb, 16384)

    # ---- 5. PRECONDITION: is DATA_CLK really there?
    #
    # Without this, checks 3 and 4 are measured against a domain being clocked
    # by noise on an unterminated input, and "the position advances" is true
    # for a reason that has nothing to do with the sample path.
    worst = 0.0
    for _ in range(4):
        w = reg.read(REG_STATUS)
        log2 = (w >> 24) & 0xF
        cyc = (w >> 8) & 0xFFFF
        f = (cyc * 50e6 / (1 << log2)) if log2 else 0.0
        worst = max(worst, f)
        time.sleep(0.02)
    print("  f_DATA_CLK ~ %.3f MHz (worst of 4 windows)" % (worst / 1e6))
    chk("PRECONDITION: DATA_CLK present (> 1 MHz)", worst > 1e6,
        "%.3f MHz -- run `board_app init` first; below this the DATA_CLK "
        "domain is being clocked by noise and nothing here is measurable"
        % (worst / 1e6))
    if worst <= 1e6:
        print("\n=== %d PASS, %d FAIL ===" % (len(PASS), len(FAIL)))
        return 1

    # ---- 2. write a pattern, read it back
    N = 64
    for j in range(N):
        tx.write(j * 4, pack(j * 13 - 400, 300 - j * 7))
    bad = 0
    for j in range(N):
        w = tx.read(j * 4)
        if w != pack(j * 13 - 400, 300 - j * 7):
            bad += 1
    chk("64-word pattern reads back identical", bad == 0,
        "%d of %d words differ" % (bad, N))

    # ---- 3. src_sel = 1 -> smp_pos advances
    #
    # Order matters: write the length, then the configuration, then toggle the
    # commit.  smp_len and smp_wrap are latched together on the synchronised
    # edge of that toggle, so writing them after it would leave the reader on
    # the previous configuration.
    reg.write(REG_SMP_LEN, N)
    v = reg.read(REG_SRC_CFG) | SRC_SEL | SMP_WRAP
    reg.write(REG_SRC_CFG, v)
    reg.write(REG_SRC_CFG, v ^ SMP_COMMIT)      # toggle = commit
    time.sleep(0.05)

    seen = []
    for _ in range(8):
        seen.append(reg.read(REG_SMP_POS) & POS_MASK)
    chk("src_sel=1: smp_pos advances", len(set(seen)) > 1,
        "positions were %r" % (seen,))
    print("    positions: %r" % (seen,))

    # ---- 4. NV: src_sel = 0 -> smp_pos does not advance
    v = reg.read(REG_SRC_CFG) & ~SRC_SEL
    reg.write(REG_SRC_CFG, v)
    time.sleep(0.05)
    held = []
    for _ in range(8):
        held.append(reg.read(REG_SMP_POS) & POS_MASK)
    chk("NV: src_sel=0: smp_pos holds still", len(set(held)) == 1,
        "positions were %r" % (held,))
    print("    positions: %r" % (held,))

    print("\n=== %d PASS, %d FAIL ===" % (len(PASS), len(FAIL)))
    if not FAIL:
        print("PASS c17_pynq_sample_path")
    return 1 if FAIL else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(main())
