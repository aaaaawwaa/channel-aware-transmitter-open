#!/usr/bin/env python3
"""Minimal, read-back-verifiable AD9361 control-plane bring-up over PL SPI.

Scope -- read this before assuming more than it does
----------------------------------------------------
This is deliberately *not* a port of the vendor's ``ad9361_init``/``ad9361_setup``.
That closure is 62 functions / ~3400 lines, about 18% of which is glued to the
Linux ``clk``/``regmap``/``dev_*`` frameworks, and a faithful port is a project
in itself.  What is here instead is the smallest sequence that:

  1. powers and resets the part through the mode GPIO (same ordering as
     ``spi_probe.py``, which is a prerequisite -- run that first),
  2. writes the registers the vendor driver writes *first*, in the vendor's
     order (``ad9361_setup``: CTRL, bandgap, ref-divide reset bars, clock
     enable),
  3. writes the LVDS parallel-port configuration that the data interface
     actually depends on, computed from the vendor's ``default_init_param``
     values rather than guessed, and
  4. reads every one of them back and reports what came back.

Acceptance for this stage is *register readback agreement*, nothing more.

What this explicitly does NOT do
--------------------------------
* No BBPLL / RFPLL / sampling-rate / LO programming.  Writing REG_CLOCK_ENABLE
  does start the BBPLL, but the clock chain is left at its default and the LO is
  never tuned -- so the part will not produce a useful RF signal after this.
* No calibration: no baseband/ADC/TIA filter cal, no DC offset cal, no TX quad
  cal.  The vendor runs these as a long coupled sequence; skipping them is a
  conscious choice, not an oversight.
* No FIR filter loading, no gain control setup, no ENSM enable.

Consequently: **a PASS here is not "the AD9361 is working".**  It is "the SPI
master, the mode pins and the part's register file are all alive, and these
specific writes land and read back".  The data interface (LVDS) is *configured*
here but entirely unproven -- per the plan, "SPI works" is not "I/Q is reliable"
(docs/rf/migration_plan.md section 4).  Claiming otherwise would be exactly the
reference-chain-PASS-as-self-built-chain-PASS mistake the backend contract
forbids.

Usage on the board (PYNQ image, root because of /dev/mem):

    sudo env PYTHONPATH=/home/xilinx python3 ad9361_init.py \\
        --spi-base 0x43010000 --gpio-base 0x43020000

Offline self-check (no board, no /dev/mem):

    python3 ad9361_init.py --selftest
"""

import argparse
import sys

from spi_probe import (
    AD9361_PRODUCT_ID_9361,
    AD9361_PRODUCT_ID_MASK,
    AD9361_REV_MASK,
    GPIO_DATA,
    GPIO_ENABLE,
    GPIO_RESETB,
    Mmio,
    REG_PRODUCT_ID,
    SPI_DIV_DEFAULT,
    SpiMaster,
    _gpio_over_mmio,
)

# ---------------------------------------------------------------- register map
# Addresses and bit values are transcribed from the vendor header
# (ad9361.h) and driver (ad9361.c, ad9361_api.c).  Each is annotated with the
# symbol it came from so a future reader can re-verify rather than trust.
REG_SPI_CONF = 0x000
REG_RX_CLOCK_DATA_DELAY = 0x006
REG_TX_CLOCK_DATA_DELAY = 0x007
REG_CLOCK_ENABLE = 0x009
REG_PARALLEL_PORT_CONF_1 = 0x010
REG_PARALLEL_PORT_CONF_2 = 0x011
REG_PARALLEL_PORT_CONF_3 = 0x012
REG_LVDS_BIAS_CTRL = 0x03C
REG_LVDS_INVERT_CTRL1 = 0x03D
REG_LVDS_INVERT_CTRL2 = 0x03E
REG_BANDGAP_CONFIG0 = 0x2A6
REG_BANDGAP_CONFIG1 = 0x2A8
REG_REF_DIVIDE_CONFIG_1 = 0x2AB
REG_REF_DIVIDE_CONFIG_2 = 0x2AC
REG_CTRL = 0x3DF

# Bit fields, from ad9361.h.
CTRL_ENABLE = 1 << 0
MASTER_BIAS_TRIM_0E = 0x0E          # MASTER_BIAS_TRIM(0x0E)
BANDGAP_TEMP_TRIM_0E = 0x0E         # BANDGAP_TEMP_TRIM(0x0E)
RX_REF_RESET_BAR = 1 << 1
TX_REF_RESET_BAR = 1 << 4
DIGITAL_POWER_UP = 1 << 2
CLOCK_ENABLE_DFLT = 1 << 1
BBPLL_ENABLE = 1 << 0
LVDS_MODE = 1 << 4
HALF_DUPLEX_MODE = 1 << 3
SINGLE_DATA_RATE = 1 << 5
SINGLE_PORT_MODE = 1 << 2


def _data_clk_delay(x):
    return (x & 0xF) << 4


def _rx_data_delay(x):
    return x & 0xF


def _fb_clk_delay(x):
    return (x & 0xF) << 4


def _tx_data_delay(x):
    return x & 0xF


# --------------------------------------------------- LVDS port config (derived)
# Computed from the vendor example's default_init_param (main.c) through the
# same expressions ad9361_api.c uses, so these are derived values rather than
# magic numbers.  The relevant init_param fields and their example defaults:
#
#   pp_tx_swap_enable=1  pp_rx_swap_enable=1  rx_frame_pulse_mode_enable=1
#   tx/rx_channel_swap=0 two_t_two_r_timing=0 invert_data_bus=0 invert_data_clk=0
#   fdd_alt_word_order=0 invert_rx_frame=0    delay_rx_data=0
#   fdd_rx_rate_2tx=0 swap_ports=0 single_data_rate=0 lvds_mode_enable=1
#   half_duplex=0 single_port=0 full_port=0 full_duplex_swap_bits=0
#   rx_data_clock_delay=0 rx_data_delay=4 tx_fb_clock_delay=7 tx_data_delay=0
#   lvds_bias_mV=150 lvds_rx_onchip_termination_enable=1
#   lvds_invert1_control=0xFF lvds_invert2_control=0x0F
#
# A test asserts each of these against the expressions, so a transcription slip
# shows up as a failing assertion rather than a silently wrong register write.
PP_CONF_1 = 0xC8    # tx_swap|rx_swap|rx_frame_pulse  = 0x80|0x40|0x08
PP_CONF_2 = 0x00    # alt_word_order=0, invert_rx_frame=0, delay_rx_data=0
PP_CONF_3 = 0x10    # lvds_mode_enable only              = 1<<4
RX_CLK_DATA_DELAY_VAL = _data_clk_delay(0) | _rx_data_delay(4)   # 0x04
TX_CLK_DATA_DELAY_VAL = _fb_clk_delay(7) | _tx_data_delay(0)     # 0x70
LVDS_BIAS_CTRL_VAL = ((150 - 75) // 75) & 0x7 | (1 << 5)         # 0x21
LVDS_INVERT_CTRL1_VAL = 0xFF
LVDS_INVERT_CTRL2_VAL = 0x0F

# CLOCK_ENABLE: digital power up + the DFLT bit the vendor sets + BBPLL.
# XO_BYPASS is deliberately not set -- the example runs on the on-board XO
# (xo_disable_use_ext_refclk_enable = 0).
CLOCK_ENABLE_VAL = DIGITAL_POWER_UP | CLOCK_ENABLE_DFLT | BBPLL_ENABLE   # 0x07


def init_sequence():
    """The writes this stage performs, in the vendor driver's order.

    Each entry is (label, addr, value, mask).  ``mask`` selects the bits the
    readback check compares: a register may carry read-only or reserved bits
    that do not read back what was written, and comparing full bytes would
    report a false failure on those.  Masking to the bits actually driven keeps
    the check meaningful without pretending the whole byte is ours.

    One caveat the caller must not paper over: when a step writes an all-zero
    value inside its mask, the readback comparison is weak -- it asserts "reads
    back as zero", which a part that ignored the write entirely would also
    satisfy if its reset default is zero.  ``minimal_init`` records a
    ``verifiable`` flag per step for exactly this reason.
    """
    return [
        # --- ad9361_setup, in order ---
        ("CTRL enable", REG_CTRL, CTRL_ENABLE, 0x01),
        ("bandgap master bias", REG_BANDGAP_CONFIG0, MASTER_BIAS_TRIM_0E, 0x1F),
        ("bandgap temp trim", REG_BANDGAP_CONFIG1, BANDGAP_TEMP_TRIM_0E, 0x1F),
        ("rx ref reset released", REG_REF_DIVIDE_CONFIG_1, RX_REF_RESET_BAR, 0x02),
        ("tx ref reset released", REG_REF_DIVIDE_CONFIG_2, TX_REF_RESET_BAR, 0x10),
        ("clocks on", REG_CLOCK_ENABLE, CLOCK_ENABLE_VAL, 0x07),

        # --- ad9361_pp_port_setup: what the LVDS data interface needs ---
        ("parallel port conf 1", REG_PARALLEL_PORT_CONF_1, PP_CONF_1, 0xFF),
        ("parallel port conf 2", REG_PARALLEL_PORT_CONF_2, PP_CONF_2, 0xFF),
        ("parallel port conf 3 (LVDS)", REG_PARALLEL_PORT_CONF_3, PP_CONF_3, 0xFF),
        ("rx clk/data delay", REG_RX_CLOCK_DATA_DELAY, RX_CLK_DATA_DELAY_VAL, 0xFF),
        ("tx clk/data delay", REG_TX_CLOCK_DATA_DELAY, TX_CLK_DATA_DELAY_VAL, 0xFF),
        ("lvds bias", REG_LVDS_BIAS_CTRL, LVDS_BIAS_CTRL_VAL, 0x3F),
        ("lvds invert 1", REG_LVDS_INVERT_CTRL1, LVDS_INVERT_CTRL1_VAL, 0xFF),
        ("lvds invert 2", REG_LVDS_INVERT_CTRL2, LVDS_INVERT_CTRL2_VAL, 0xFF),
    ]


def minimal_init(spi, gpio, log=print):
    """Reset the part, apply the minimal sequence, verify every write reads back.

    Returns (ok, detail).  ``detail`` carries per-step observations so a failure
    can be read off data rather than a printed string.  Nothing is assumed to
    have worked: each step's readback is recorded whether or not it matched.
    """
    import time

    detail = {"steps": [], "mismatches": []}

    # Same reset ordering as spi_probe: mode pins known, reset asserted, reset
    # released, and only then SPI.  The AD9361 ignores SPI while held in reset,
    # which is indistinguishable from a dead SPI master if you skip this.
    log("gpio: enable=1 resetb=0 (asserted)")
    gpio.write32(GPIO_DATA, GPIO_ENABLE)
    time.sleep(0.01)
    log("gpio: release resetb")
    gpio.write32(GPIO_DATA, GPIO_ENABLE | GPIO_RESETB)
    time.sleep(0.01)

    # Confirm the part answers before writing to it.  A write to a part that is
    # not there lands nowhere and the readback would then be the only signal --
    # better to say so plainly here.
    pid = None
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            pid = spi.read_reg(REG_PRODUCT_ID)
        except IOError as exc:
            detail["spi_error"] = str(exc)
            pid = None
        if pid is not None and (pid & AD9361_PRODUCT_ID_MASK) == AD9361_PRODUCT_ID_9361:
            break
        time.sleep(0.05)

    detail["product_id"] = pid
    if pid is None or (pid & AD9361_PRODUCT_ID_MASK) != AD9361_PRODUCT_ID_9361:
        detail["reason"] = (
            "PRODUCT_ID 0x%r: bits 7:3 are not 0x%02X, so the part is not "
            "answering as an AD9361.  Run spi_probe.py first." % (pid,
                                                                 AD9361_PRODUCT_ID_9361))
        return False, detail
    detail["silicon_revision"] = pid & AD9361_REV_MASK

    for label, addr, value, mask in init_sequence():
        spi.write_reg(addr, value)
        got = spi.read_reg(addr)
        entry = {"label": label, "addr": addr, "wrote": value,
                 "read": got, "mask": mask,
                 "match": (got & mask) == (value & mask),
                 # A step that drives all-zero bits cannot prove the write
                 # landed -- see init_sequence()'s caveat.
                 "verifiable": (value & mask) != 0}
        detail["steps"].append(entry)
        if not entry["match"]:
            detail["mismatches"].append(entry)
        log("  0x%03X <- 0x%02X  read 0x%02X  %s%s"
            % (addr, value, got, "ok" if entry["match"] else "MISMATCH",
               "" if entry["verifiable"] else " (writes 0 -- weak check)"))

    detail["verified"] = len(detail["steps"]) - len(detail["mismatches"])
    detail["total"] = len(detail["steps"])
    detail["weak"] = [s["label"] for s in detail["steps"] if not s["verifiable"]]
    if detail["mismatches"]:
        detail["reason"] = (
            "%d of %d registers did not read back the bits written; see "
            "'mismatches'.  A mismatch on a *write-only-ish* register can be "
            "benign, so inspect which one before concluding the SPI path is "
            "broken." % (len(detail["mismatches"]), detail["total"]))
        return False, detail
    return True, detail


def selftest():
    """Offline checks.  No board, no /dev/mem, no vendor driver needed."""
    failures = []

    def check(name, cond):
        print("%s %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    # ---- the derived LVDS values must equal the vendor's expressions ----
    # Recompute each from ad9361_api.c's own arithmetic rather than restating the
    # constant, so a transcription slip in the constants above fails here.
    pp0 = (1 << 7) | (1 << 6) | (0 << 5) | (0 << 4) | (1 << 3) | (0 << 2) | (0 << 1) | (0 << 0)
    check("PP_CONF_1 matches the vendor expression", PP_CONF_1 == pp0)
    pp2 = (0 << 7) | (0 << 6) | (0 << 5) | (1 << 4) | (0 << 3) | (0 << 2) | (0 << 1) | (0 << 0)
    check("PP_CONF_3 matches lvds_mode only", PP_CONF_3 == pp2)
    # The vendor's sanity fixup must be a no-op for these values, else the
    # constant above is not what the part actually ends up with.
    fixed = PP_CONF_3
    if fixed & LVDS_MODE:
        fixed &= ~(HALF_DUPLEX_MODE | SINGLE_DATA_RATE | SINGLE_PORT_MODE)
    check("LVDS sanity fixup leaves PP_CONF_3 unchanged", fixed == PP_CONF_3)
    check("RX_CLK_DATA_DELAY is data-delay 4",
          RX_CLK_DATA_DELAY_VAL == ((0 & 0xF) << 4) | (4 & 0xF))
    check("TX_CLK_DATA_DELAY is fb-delay 7",
          TX_CLK_DATA_DELAY_VAL == ((7 & 0xF) << 4) | (0 & 0xF))
    bias = ((150 - 75) // 75) & 0x7
    bias |= (1 << 5)
    check("LVDS_BIAS_CTRL matches the vendor expression",
          LVDS_BIAS_CTRL_VAL == bias)
    check("CLOCK_ENABLE has power-up, dflt and bbpll",
          CLOCK_ENABLE_VAL == (1 << 2) | (1 << 1) | (1 << 0))

    # ---- the sequence must be well-formed ----
    seq = init_sequence()
    check("sequence is non-empty", len(seq) > 0)
    check("every step has a nonzero mask", all(m != 0 for _, _, _, m in seq))
    addrs = [a for _, a, _, _ in seq]
    check("no register is written twice", len(addrs) == len(set(addrs)))
    check("does not write PRODUCT_ID", REG_PRODUCT_ID not in addrs)
    # The LVDS port config must be written; a sequence that only poked the clock
    # registers would leave the data interface unconfigured.
    check("writes all three parallel-port registers",
          {REG_PARALLEL_PORT_CONF_1, REG_PARALLEL_PORT_CONF_2,
           REG_PARALLEL_PORT_CONF_3} <= set(addrs))
    check("writes both LVDS invert registers",
          {REG_LVDS_INVERT_CTRL1, REG_LVDS_INVERT_CTRL2} <= set(addrs))

    # ---- minimal_init against a fake that honours writes ----
    class FakeSpi(object):
        def __init__(self, regs):
            self.regs = dict(regs)
            self.writes = []

        def read_reg(self, addr):
            return self.regs.get(addr, 0)

        def write_reg(self, addr, value):
            self.writes.append((addr, value))
            self.regs[addr] = value

    class FakeGpio(object):
        def __init__(self):
            self.writes = []

        def write32(self, off, val):
            self.writes.append((off, val))

        def read32(self, off):
            return 0

    healthy = {REG_PRODUCT_ID: 0x0A}
    gpio = FakeGpio()
    ok, detail = minimal_init(FakeSpi(healthy), gpio, log=lambda *a: None)
    check("healthy part passes and every step verifies",
          ok and detail["verified"] == detail["total"])
    check("reported the silicon revision", detail["silicon_revision"] == 0x2)
    check("wrote the LVDS port config",
          any(s["addr"] == REG_PARALLEL_PORT_CONF_3 and s["wrote"] == PP_CONF_3
              for s in detail["steps"]))
    check("asserted reset before releasing it",
          gpio.writes[0] == (GPIO_DATA, GPIO_ENABLE)
          and gpio.writes[1] == (GPIO_DATA, GPIO_ENABLE | GPIO_RESETB))

    # A part that is not there must fail before any write is attempted.
    ok2, detail2 = minimal_init(FakeSpi({REG_PRODUCT_ID: 0x00}), FakeGpio(),
                                log=lambda *a: None)
    check("absent part fails without writing",
          not ok2 and "reason" in detail2 and detail2["steps"] == [])

    # A part that swallows writes must fail the readback check -- and the
    # mismatch must name the register, not just report a count.
    class NoWrite(FakeSpi):
        def write_reg(self, addr, value):
            self.writes.append((addr, value))   # record, do not apply

    ok3, detail3 = minimal_init(NoWrite({REG_PRODUCT_ID: 0x0A}), FakeGpio(),
                                log=lambda *a: None)
    check("part that ignores writes fails",
          not ok3 and len(detail3["mismatches"]) > 0)
    check("mismatch names the failing register",
          detail3["mismatches"][0]["addr"] == REG_CTRL)
    # The step whose mask covers only zero bits cannot be caught this way, and
    # the function must say so rather than implying it verified everything.
    check("reports the step whose write cannot be verified",
          detail3["weak"] == ["parallel port conf 2"])

    # A register whose *unmasked* bits differ must still pass: that is the whole
    # point of comparing through a mask rather than the full byte.  Model a
    # reserved bit on the bandgap registers, whose mask is 0x1F -- bit 0x20 is
    # outside it, so the readback check must ignore it.  (The parallel-port
    # registers use a full 0xFF mask, so they cannot host such a bit.)
    class StickyOutside(FakeSpi):
        def write_reg(self, addr, value):
            self.writes.append((addr, value))
            if addr in (REG_BANDGAP_CONFIG0, REG_BANDGAP_CONFIG1):
                self.regs[addr] = (value | 0x20) & 0xFF
            else:
                self.regs[addr] = value

    ok4, detail4 = minimal_init(StickyOutside({REG_PRODUCT_ID: 0x0A}), FakeGpio(),
                                log=lambda *a: None)
    check("reserved bits outside the mask do not cause a false failure", ok4)

    # But a sticky bit *inside* a mask must be caught -- otherwise the masking
    # above would be hiding real failures rather than reserved-bit noise.  Set a
    # bit that lands inside the 0xFF parallel-port masks.
    class StickyInside(FakeSpi):
        def write_reg(self, addr, value):
            self.writes.append((addr, value))
            self.regs[addr] = (value | 0x02) & 0xFF

    ok5, detail5 = minimal_init(StickyInside({REG_PRODUCT_ID: 0x0A}), FakeGpio(),
                                log=lambda *a: None)
    # PP_CONF_2 is written 0x00 with a 0xFF mask, so a sticky 0x02 there is a
    # genuine mismatch that the check must report.
    check("a sticky bit inside the mask is still a failure",
          not ok5 and any(m["addr"] == REG_PARALLEL_PORT_CONF_2
                          for m in detail5["mismatches"]))

    print("=== %d FAIL ===" % len(failures) if failures else "=== selftest ALL PASS ===")
    return 0 if not failures else 1


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

    ok, detail = minimal_init(spi, gpio)
    print("  product_id        = %r" % detail.get("product_id"))
    print("  silicon_revision  = %r" % detail.get("silicon_revision"))
    print("  verified          = %r of %r"
          % (detail.get("verified"), detail.get("total")))
    for entry in detail.get("mismatches", []):
        print("  MISMATCH 0x%03X wrote 0x%02X read 0x%02X (mask 0x%02X)"
              % (entry["addr"], entry["wrote"], entry["read"], entry["mask"]))
    if "reason" in detail:
        print("  reason: %s" % detail["reason"])
    print("RESULT", "PASS" if ok else "FAIL")
    print("NOTE: PASS here means the control plane is alive and these registers "
          "read back. It does NOT mean the LVDS data interface or the RF chain "
          "works -- see this file's docstring.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())