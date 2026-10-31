#!/usr/bin/env python3
"""AD9361 control-plane probe over the RF overlay's PL SPI master.

This is the *control-plane* half of the RF bring-up and nothing more.  A read-back
that matches what was written proves the SPI master, the FMC control pins and the
AD9361's SPI slave are all alive and wired correctly.  It proves nothing about the
LVDS data interface -- the plan is explicit that "SPI works" is not "I/Q is
reliable" (docs/rf/migration_plan.md section 4).

Two things it is careful about, both learned on this board:

* ``spi_probe`` must be told the base address, not guess one.  The address is in
  the overlay's .hwh (``pl_spi_0``), and a wrong base reads 0xDEADBEEF from the
  unmapped-register default rather than failing loudly.
* Reset and power-up are ordered.  ``resetb`` is released through the mode GPIO
  *before* any SPI transaction; the AD9361 ignores SPI while it is held in reset,
  which looks exactly like a dead SPI master.

Usage on the board (PYNQ image, root because of /dev/mem):

    sudo env PYTHONPATH=/home/xilinx python3 spi_probe.py \\
        --spi-base 0x43010000 --gpio-base 0x43020000

Offline self-check (no board, no /dev/mem):

    python3 spi_probe.py --selftest
"""

import argparse
import struct
import sys

# ---------------------------------------------------------------- register map
# pl_spi_master AXI-Lite, 32-bit registers, byte offsets (ADDR_LSB = 2).
SPI_CTRL = 0x00   # [7:0] n_tx, [15:8] n_rx, [16] start
SPI_INSTR = 0x04  # {RW[15], CNT[14:12], ADDR[9:0]}
SPI_TXD0 = 0x08   # data bytes 0..3 (LSB first on the wire)
SPI_TXD1 = 0x0C   # data bytes 4..7
SPI_RXD0 = 0x10   # received bytes 0..3
SPI_RXD1 = 0x14   # received bytes 4..7
SPI_STATUS = 0x18  # [0] busy, [1] done
SPI_DIV = 0x1C    # SCLK half period in aclk cycles

# axi_gpio_ctrl: one 9-bit output aperture, packed by create_bd.tcl.
GPIO_DATA = 0x00
GPIO_TRI = 0x04

# rf_gpio bit positions (see rf_overlay/bd/create_bd.tcl).
GPIO_TXNRX = 1 << 0
GPIO_ENABLE = 1 << 1
GPIO_RESETB = 1 << 2
GPIO_SYNC = 1 << 3
GPIO_EN_AGC = 1 << 4

# AD9361 SPI instruction word, taken from the vendor driver (ad9361.c
# ad9361_spi_readm / ad9361_spi_writem, ad9361.h): {RW[15], CNT[14:12], ADDR[9:0]}.
#
# The CNT field is the byte count *minus one*, not the byte count:
#
#     #define AD_CNT(x)  ((((x) - 1) & 0x7) << 12)
#
# So a one-byte transfer encodes CNT=0 and carries no count at all in bits 14:12.
# Encoding the raw count instead (CNT=1 for one byte) tells the part to expect two
# data bytes; the one-byte transfer that follows is then short and the part
# discards it.  A one-byte *read* encodes 0 under either rule, which is why a
# read-only probe looks perfectly healthy while every write silently does nothing.
AD_READ = 0x0000
AD_WRITE = 0x8000


def ad_cnt(num):
    """The vendor's AD_CNT: a byte count `num` in 1..8, encoded as ``num - 1``."""
    return ((num - 1) & 0x7) << 12

# SCLK half period in 50 MHz aclk cycles.  10 -> 2.5 MHz, the value the RTL
# testbench exercises.  The AD9361 SPI port is specified well below this.
SPI_DIV_DEFAULT = 10

# Registers that identify the part and are safe to read before any calibration.
#
# There is no separate revision register.  The vendor driver (ad9361_api.c, the
# ad9361_init path) reads PRODUCT_ID once and splits that single byte:
#
#     if ((ret & PRODUCT_ID_MASK) != PRODUCT_ID_9361)   -> not an AD9361
#     rev = ret & REV_MASK;
#
# with PRODUCT_ID_MASK 0xF8, PRODUCT_ID_9361 0x08, REV_MASK 0x07.  Note 0x08 is
# an *in-place* mask constant, not a shifted field value: the test is
# `(byte & 0xF8) == 0x08`, i.e. bits 7:3 == 0b00001.  So the AD9361 answers
# 0x08..0x0F -- 0x0A (what this board reports) is the part at silicon revision 2.
# Compare against the mask, never for equality: an equality test on one specific
# byte would reject every other revision.
#
# Do not read 0x03E as a revision: the vendor header defines 0x03E as
# REG_LVDS_INVERT_CTRL2.  Reading it is harmless, but reporting it as "revision"
# would be a wrong claim.
REG_PRODUCT_ID = 0x037
AD9361_PRODUCT_ID_MASK = 0xF8
AD9361_PRODUCT_ID_9361 = 0x08
AD9361_REV_MASK = 0x07
REG_SPI_CONF = 0x000

# A register the part will hold a written value in, used to prove the write path
# works.  There is no scratch register in this part -- 0x3F5 is REG_OBSERVE_CONFIG,
# which the driver only ever writes back what it just read.  REG_CTRL's bit 0 is
# CTRL_ENABLE, which the vendor writes directly (ad9361.c:5297), so it round-trips.
REG_CTRL = 0x3DF


class Mmio(object):
    """Minimal /dev/mem accessor.  Opened lazily so --selftest never needs root."""

    def __init__(self, page_size=4096):
        self.page_size = page_size
        self._maps = {}
        self._fd = None

    def _map(self, addr):
        base = addr & ~(self.page_size - 1)
        if base not in self._maps:
            import mmap
            import os
            if self._fd is None:
                self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
            region = mmap.mmap(self._fd, self.page_size, offset=base)
            self._maps[base] = region
        return self._maps[base], addr - base

    def read32(self, addr):
        region, off = self._map(addr)
        return struct.unpack_from("<I", region, off)[0]

    def write32(self, addr, value):
        region, off = self._map(addr)
        struct.pack_into("<I", region, off, value & 0xFFFFFFFF)


class SpiMaster(object):
    """Drives pl_spi_master.  Talks to any object with read32/write32."""

    def __init__(self, mmio, base, div=SPI_DIV_DEFAULT, timeout_polls=100000):
        self.mmio = mmio
        self.base = base
        self.div = div
        self.timeout_polls = timeout_polls

    def _w(self, off, val):
        self.mmio.write32(self.base + off, val)

    def _r(self, off):
        return self.mmio.read32(self.base + off)

    def init(self):
        self._w(SPI_DIV, self.div)
        status = self._r(SPI_STATUS)
        if status == 0xDEADBEEF:
            raise IOError(
                "pl_spi_master at 0x%08X reads 0xDEADBEEF from STATUS -- the base "
                "address is wrong or the overlay is not loaded" % self.base)
        return status

    def transact(self, instr, tx_bytes=(), n_rx=0):
        """One CSN-low burst: 2 instruction bytes + tx_bytes, then n_rx bytes back."""
        if len(tx_bytes) > 8:
            raise ValueError("pl_spi_master holds at most 8 tx bytes")
        if n_rx > 8:
            raise ValueError("pl_spi_master holds at most 8 rx bytes")
        n_tx = len(tx_bytes)

        word0 = 0
        for i, b in enumerate(tx_bytes[:4]):
            word0 |= (b & 0xFF) << (8 * i)
        word1 = 0
        for i, b in enumerate(tx_bytes[4:8]):
            word1 |= (b & 0xFF) << (8 * i)
        self._w(SPI_TXD0, word0)
        self._w(SPI_TXD1, word1)
        self._w(SPI_INSTR, instr & 0xFFFF)
        self._w(SPI_CTRL, (n_rx << 8) | n_tx | (1 << 16))

        for _ in range(self.timeout_polls):
            status = self._r(SPI_STATUS)
            if status & 0x2:      # done
                break
        else:
            raise IOError("SPI burst timed out (instr=0x%04X)" % instr)

        rx = []
        if n_rx:
            full = self._r(SPI_RXD0) | (self._r(SPI_RXD1) << 32)
            for i in range(n_rx):
                rx.append((full >> (8 * i)) & 0xFF)
        return rx

    def read_reg(self, addr):
        # Instruction word is {RW[15], CNT[14:12], ADDR[9:0]}: the address is a
        # full 10-bit field, and a 1-byte read uses AD_CNT(1) == 0.  Masking the
        # address to 8 bits would silently read the wrong register (0x037 -> 0x000).
        return self.transact(AD_READ | ad_cnt(1) | (addr & 0x3FF), n_rx=1)[0]

    def write_reg(self, addr, value):
        # AD_CNT(1): one data byte follows the two instruction bytes.  Note this is
        # 0 in bits 14:12, not 1 -- see AD_CNT's definition above.
        self.transact(AD_WRITE | ad_cnt(1) | (addr & 0x3FF),
                      tx_bytes=[value & 0xFF])


class FakeSpi(object):
    """A SpiMaster-compatible fake that answers reads from a register dict.

    Used by the selftest for the higher-level probe() logic.  It deliberately does
    *not* exercise SpiMaster.transact -- FakeMmio below does that.
    """

    def __init__(self, regs):
        self.regs = dict(regs)
        self.writes = []

    def init(self):
        return 0

    def read_reg(self, addr):
        return self.regs.get(addr, 0)

    def write_reg(self, addr, value):
        self.writes.append((addr, value))
        self.regs[addr] = value


class FakeMmio(object):
    """Emulates pl_spi_master's AXI register file, including the instruction word.

    This is what makes the selftest worth running: it exercises the real
    SpiMaster.transact, so the instruction encoding ({RW, CNT, ADDR}), the tx byte
    packing into TXD0/TXD1 and the rx unpacking from RXD0/RXD1 are all checked
    against an independent model of the RTL rather than against themselves.
    """

    def __init__(self, regs, n_rx_reply=0xFF, status=0x2):
        self.regs = dict(regs)
        self.n_rx_reply = n_rx_reply
        # Bursts complete immediately, so STATUS reads as done-not-busy.  Set this
        # to 0xDEADBEEF to model a wrong base address (the RTL's unmapped-register
        # default), which is how the selftest checks that init() catches it.
        self.status = status
        self.mem = {}
        self.bursts = []

    def read32(self, addr):
        off = addr & 0x1F
        if off == SPI_STATUS:
            return self.status
        if off == SPI_RXD0:
            return self._rx_word & 0xFFFFFFFF
        if off == SPI_RXD1:
            return (self._rx_word >> 32) & 0xFFFFFFFF
        return self.mem.get(off, 0)

    def write32(self, addr, value):
        off = addr & 0x1F
        self.mem[off] = value & 0xFFFFFFFF
        if off == SPI_CTRL and (value >> 16) & 1:
            self._run_burst()

    def _run_burst(self):
        ctrl = self.mem[SPI_CTRL]
        n_tx = ctrl & 0xFF
        n_rx = (ctrl >> 8) & 0xFF
        instr = self.mem[SPI_INSTR] & 0xFFFF
        txd = self.mem.get(SPI_TXD0, 0) | (self.mem.get(SPI_TXD1, 0) << 32)
        tx = [(txd >> (8 * i)) & 0xFF for i in range(n_tx)]

        rw = (instr >> 15) & 1
        cnt = (instr >> 12) & 0x7
        addr10 = instr & 0x3FF
        self.bursts.append({"instr": instr, "rw": rw, "cnt": cnt,
                            "addr": addr10, "tx": tx, "n_rx": n_rx})

        rx = []
        if rw == 1:                       # write
            for i, b in enumerate(tx):
                self.regs[addr10 + i] = b
        else:                             # read
            for i in range(n_rx):
                rx.append(self.regs.get(addr10 + i, self.n_rx_reply))
        word = 0
        for i, b in enumerate(rx):
            word |= (b & 0xFF) << (8 * i)
        self._rx_word = word


class Ad9361Slave(object):
    """Models the AD9361's *own* decode of a burst, which FakeMmio does not.

    FakeMmio above models the PL master, and the PL master never looks at the CNT
    field -- it just clocks 2 + N_TX + N_RX bytes.  So a selftest that only runs
    against FakeMmio cannot notice a wrong CNT encoding, because the CNT field
    never influences anything.  That is exactly how the real bug got through:
    writes were encoded with the raw byte count instead of the vendor's
    count-minus-one, and every read (which encodes 0 either way) still worked.

    This model closes that hole.  It applies the part's rule -- the burst must
    carry exactly ``CNT + 1`` data bytes, and the part keeps the *last* one --
    so an encoding that disagrees with AD_CNT is rejected here rather than on the
    board.  Hand it a burst from FakeMmio's ``bursts`` list.
    """

    def __init__(self, regs=None):
        self.regs = dict(regs or {})
        self.rejected = []

    def accept(self, burst):
        """Feed one burst; return True if the part would honour it."""
        n_data = len(burst["tx"])
        expected = (burst["cnt"] & 0x7) + 1
        if burst["rw"] == 1 and n_data != expected:
            self.rejected.append((burst, expected, n_data))
            return False
        if burst["rw"] == 1 and n_data:
            self.regs[burst["addr"]] = burst["tx"][-1]
        return True


def probe(spi, gpio, log=print):
    """Reset/power the part, then read its identity registers.

    Returns (ok, detail_dict).  ``ok`` is False on any failure, with the reason in
    detail so the caller never has to parse a printed string.
    """
    detail = {}

    # Order matters: put the mode pins in a known state, hold reset, release it,
    # and only then talk SPI.
    log("gpio: enable=1 resetb=0 (asserted) txnrx=0")
    gpio.write32(GPIO_DATA, GPIO_ENABLE)
    # Give the part time to see reset.  AD9361 needs only a few us, but the mode
    # pins cross into the part asynchronously; be generous rather than clever.
    import time
    time.sleep(0.01)

    log("gpio: release resetb")
    gpio.write32(GPIO_DATA, GPIO_ENABLE | GPIO_RESETB)
    # Post-reset the part runs a self-check and will NACK SPI while it does.  A
    # single read can land inside that window, so poll instead of deciding once.
    deadline = time.time() + 2.0
    last = None
    while time.time() < deadline:
        try:
            last = spi.read_reg(REG_PRODUCT_ID)
        except IOError as exc:
            last = None
            detail["spi_error"] = str(exc)
        if last is not None and (last & AD9361_PRODUCT_ID_MASK) == AD9361_PRODUCT_ID_9361:
            break
        time.sleep(0.05)

    detail["product_id"] = last
    if last is None or (last & AD9361_PRODUCT_ID_MASK) != AD9361_PRODUCT_ID_9361:
        detail["reason"] = (
            "PRODUCT_ID 0x%r: bits 7:3 are not 0x%02X, so this is not an AD9361 "
            "(an AD9361 answers 0x08..0x0F).  Check the FMC is seated, P18 is on "
            "the 2.5 V position, and the overlay is loaded."
            % (last, AD9361_PRODUCT_ID_9361))
        return False, detail

    # Silicon revision is the low 3 bits of the same PRODUCT_ID byte; there is no
    # separate revision register to read.
    detail["silicon_revision"] = last & AD9361_REV_MASK
    detail["spi_conf"] = spi.read_reg(REG_SPI_CONF)

    # Round-trip a writable register to prove writes land, not just reads.
    #
    # There is no "scratch" register in this part: the vendor header defines 0x3F5
    # as REG_OBSERVE_CONFIG, which the driver only ever writes back a value it just
    # read, so it is not a safe test target.  REG_CTRL (0x3DF) is: bit 0 is
    # CTRL_ENABLE and the vendor writes it directly (ad9361.c:5297), so both 0x00
    # and 0x01 are legal values the part will hold.  Do not write PRODUCT_ID.
    test_addr = REG_CTRL
    for pattern in (0x01, 0x00):
        spi.write_reg(test_addr, pattern)
        got = spi.read_reg(test_addr)
        if (got & 0x01) != pattern:
            detail["reason"] = ("write/read-back of 0x%03X failed: wrote 0x%02X got "
                                "0x%02X" % (test_addr, pattern, got))
            detail["roundtrip"] = False
            return False, detail
    detail["roundtrip"] = True
    return True, detail


def _gpio_over_mmio(mmio, base):
    """The GPIO aperture is a plain register window, not an SPI master.

    Kept as its own tiny object so a GPIO write can never be mistaken for an SPI
    transaction at a call site.
    """
    class _Gpio(object):
        def __init__(self, mmio, base):
            self.mmio = mmio
            self.base = base

        def write32(self, off, val):
            self.mmio.write32(self.base + off, val)

        def read32(self, off):
            return self.mmio.read32(self.base + off)

    return _Gpio(mmio, base)


def selftest():
    """Exercise SpiMaster against a model of the RTL.  No board required."""
    failures = []

    def check(name, cond):
        print("%s %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    # ---- SpiMaster.transact against FakeMmio: the instruction encoding ----
    # A real AD9361 reports bits 7:3 == 0b00001, so 0x0A is the AD9361 at rev 2.
    HEALTHY_ID = 0x0A
    fm = FakeMmio({REG_PRODUCT_ID: HEALTHY_ID, REG_SPI_CONF: 0x00})
    sm = SpiMaster(fm, base=0)
    sm.init()

    got = sm.read_reg(REG_PRODUCT_ID)
    check("read_reg returns the register value", got == HEALTHY_ID)
    b = fm.bursts[-1]
    check("read uses RW=0", b["rw"] == 0)
    check("read addresses 0x037", b["addr"] == REG_PRODUCT_ID)
    check("read sends no tx bytes", b["tx"] == [])

    sm.write_reg(REG_CTRL, 0x01)
    b = fm.bursts[-1]
    check("write uses RW=1", b["rw"] == 1)
    check("write addresses 0x3DF", b["addr"] == REG_CTRL)
    check("write puts the data byte on the wire", b["tx"] == [0x01])
    check("write lands in the model register file", fm.regs.get(REG_CTRL) == 0x01)

    # ---- the CNT field must be the vendor's AD_CNT (count - 1), not the count ----
    # The PL master ignores CNT entirely, so FakeMmio alone cannot catch a wrong
    # encoding.  Ad9361Slave applies the part's rule: a write must carry exactly
    # CNT+1 data bytes, and the part keeps the last one.
    slave = Ad9361Slave()
    check("one-byte write encodes CNT=0 (vendor AD_CNT(1))",
          b["cnt"] == 0 and ad_cnt(1) == 0)
    check("the part accepts a one-byte write",
          slave.accept(b) and slave.regs.get(REG_CTRL) == 0x01)
    # The bug this guards: encoding the raw count would send CNT=1, telling the
    # part to expect two data bytes.  Model exactly that and require rejection.
    wrong = dict(b, cnt=1)
    check("the part rejects a one-byte write encoded with CNT=1",
          not Ad9361Slave().accept(wrong))
    check("AD_CNT maps 8 bytes to 7", ad_cnt(8) == (7 << 12))
    check("AD_CNT masks to the 3-bit field", ad_cnt(9) == 0)

    # A multi-byte write must pack bytes little-endian into TXD0/TXD1.  Preload the
    # data registers directly and kick one burst, rather than going through
    # write_reg (which is fixed at one byte).
    fm.mem[SPI_TXD0] = 0x03020100
    fm.mem[SPI_TXD1] = 0x07060504
    fm.mem[SPI_INSTR] = AD_WRITE | ad_cnt(4) | 0x100
    fm.mem[SPI_CTRL] = 0
    fm.write32(SPI_CTRL, (0 << 8) | 4 | (1 << 16))
    b = fm.bursts[-1]
    check("multi-byte write packs TXD0 then TXD1",
          b["tx"] == [0x00, 0x01, 0x02, 0x03])
    check("a 4-byte write encodes CNT=3 and the part accepts it",
          b["cnt"] == 3 and Ad9361Slave().accept(b))

    # A 2-byte read must unpack RXD0 then RXD1.
    fm.regs[0x200] = 0xAA
    fm.regs[0x201] = 0xBB
    rx = sm.transact(AD_READ | ad_cnt(2) | 0x200, n_rx=2)
    check("multi-byte read unpacks RXD0 then RXD1", rx == [0xAA, 0xBB])

    # ---- probe() logic against FakeSpi ----
    spi = FakeSpi({REG_PRODUCT_ID: HEALTHY_ID, REG_SPI_CONF: 0x00})
    ok, detail = probe(spi, _FakeGpio(), log=lambda *a: None)
    check("probe accepts a healthy part",
          ok and detail["product_id"] == HEALTHY_ID)
    check("probe splits the silicon revision out of PRODUCT_ID",
          detail.get("silicon_revision") == (HEALTHY_ID & AD9361_REV_MASK))
    check("probe round-tripped REG_CTRL", (REG_CTRL, 0x00) in spi.writes)
    check("probe did not write PRODUCT_ID",
          REG_PRODUCT_ID not in [a for a, _ in spi.writes])

    # A silent part must fail, not pass by accident.
    ok2, detail2 = probe(FakeSpi({REG_PRODUCT_ID: 0x00}), _FakeGpio(),
                         log=lambda *a: None)
    check("probe rejects a silent part", not ok2 and "reason" in detail2)

    # A wrong product ID must fail too (wrong chip, or a wrong base address).
    ok3, _ = probe(FakeSpi({REG_PRODUCT_ID: 0x99}), _FakeGpio(),
                   log=lambda *a: None)
    check("probe rejects a wrong product id", not ok3)

    # The vendor driver compares only bits 7:3.  A part whose *revision* bits are
    # nonzero must still be accepted -- an equality test against a fixed byte
    # would wrongly reject every revision but one.
    okrev, detailrev = probe(FakeSpi({REG_PRODUCT_ID: 0x0F, REG_SPI_CONF: 0}),
                             _FakeGpio(), log=lambda *a: None)
    check("probe accepts a nonzero silicon revision",
          okrev and detailrev["silicon_revision"] == 0x7)

    # A part that answers reads but drops writes must fail the round-trip check.
    class NoWrite(FakeSpi):
        def write_reg(self, addr, value):
            self.writes.append((addr, value))   # record, but do not apply

    ok4, detail4 = probe(NoWrite({REG_PRODUCT_ID: HEALTHY_ID, REG_SPI_CONF: 2}),
                         _FakeGpio(), log=lambda *a: None)
    check("probe rejects a part that ignores writes",
          not ok4 and detail4.get("roundtrip") is False)

    # A wrong SPI base reads 0xDEADBEEF from the unmapped-register default; init()
    # must catch that rather than letting the probe read garbage.
    dead = FakeMmio({}, status=0xDEADBEEF)
    try:
        SpiMaster(dead, base=0).init()
        check("init rejects a wrong base address", False)
    except IOError:
        check("init rejects a wrong base address", True)

    print("=== %d FAIL ===" % len(failures) if failures else "=== selftest ALL PASS ===")
    return 0 if not failures else 1


class _FakeGpio(object):
    """Records mode-pin writes so probe()'s reset ordering can be inspected."""

    def __init__(self):
        self.writes = []

    def write32(self, off, val):
        self.writes.append((off, val))

    def read32(self, off):
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spi-base", type=lambda s: int(s, 0),
                    help="pl_spi_0 AXI base (from the merged .hwh, e.g. 0x43010000)")
    ap.add_argument("--gpio-base", type=lambda s: int(s, 0),
                    help="axi_gpio_ctrl AXI base (from the merged .hwh, e.g. 0x43020000)")
    ap.add_argument("--div", type=int, default=SPI_DIV_DEFAULT,
                    help="SCLK half period in aclk cycles (default %d)" % SPI_DIV_DEFAULT)
    ap.add_argument("--selftest", action="store_true",
                    help="run offline checks against a fake register file")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.spi_base is None or args.gpio_base is None:
        ap.error("--spi-base and --gpio-base are required unless --selftest")

    mmio = Mmio()
    spi = SpiMaster(mmio, args.spi_base, div=args.div)
    gpio = _gpio_over_mmio(mmio, args.gpio_base)
    spi.init()

    ok, detail = probe(spi, gpio)
    for key in sorted(detail):
        print("  %s = %r" % (key, detail[key]))
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())