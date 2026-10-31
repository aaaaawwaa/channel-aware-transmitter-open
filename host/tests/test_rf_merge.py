"""Structural guards for the RF <-> display merge in scripts/create_bd.tcl.

The merge puts the AD9361 half (rf_overlay) and the AWG + TFT half into one block
design, so one bitstream now serves both.  That creates three hazards a build
alone does not catch:

* **Address drift.**  Firmware and host code hold the AWG peripheral bases as
  constants.  ``assign_bd_address`` is heuristic -- measured 2026-09-21: adding
  the two 16 KiB RF BRAMs relocated ``axi_bram_ctrl_0`` from 0x40000000 to
  0x42000000, with no warning.  So the script pins every slave, and the pinning
  is checked here against the deployed ``awg_merged.hwh`` that the software was
  actually built against.
* **A duplicate module.**  ``rf_overlay/rtl/sine_rom.v`` and
  ``src/rtl/sine_rom.v`` define the same module.  Two copies in one fileset is
  not an error -- the synthesiser picks one -- which is exactly why it needs a
  test rather than a comment.
* **A slave that validates but sits in reset.**  Every MI needs an entry in the
  slave table *and* in the ACLK and ARESETN lists.  Missing one from the reset
  lists leaves the peripheral held in reset forever while the BD still
  validates; this repository has been bitten by it before.
"""
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
BD_SCRIPT = ROOT / "scripts" / "create_bd.tcl"
AWG_XDC = ROOT / "src" / "constraints" / "awg.xdc"
RF_XDC = ROOT / "rf_overlay" / "constraints" / "rf.xdc"
SRC_SINE_ROM = ROOT / "src" / "rtl" / "sine_rom.v"
RF_SINE_ROM = ROOT / "rf_overlay" / "rtl" / "sine_rom.v"
CLOCK_GROUPS = ROOT / "tools" / "impl1_clock_groups.tcl"
MANIFEST = ROOT / "scripts" / "release_manifest.py"
# The live staging copy that gets scp'd to the board.
DEPLOYED_HWH = ROOT / "pynq_overlay" / "out_merged" / "awg_merged.hwh"
PLATFORM_RF_H = ROOT / "rf_overlay" / "sw" / "board_app" / "platform_rf.h"
PYNQ_SERVICE = ROOT / "pynq_overlay" / "sw" / "pynq-status.service"
PYNQ_STATUS = ROOT / "pynq_overlay" / "sw" / "pynq_status.py"
PYNQ_INSTALL = ROOT / "pynq_overlay" / "sw" / "install.sh"
# The AWG half's frozen contract: cell -> base as of the PRE-MERGE overlay the
# firmware and host were actually built against.  Extracted into its own file
# because the merge updates the deployed overlay -- if the baseline were read
# from the deployed .hwh, then deploying would silently move the thing it is
# compared against, and the check would become "the file agrees with itself".
AWG_CONTRACT = ROOT / "host" / "tests" / "data" / "awg_merged_contract.json"

AWG_SLAVE_COUNT = 21
RF_CELLS = ("axi_ctrl_0", "pl_spi_0", "axi_gpio_ctrl",
            "axi_bram_ctrl_tx", "axi_bram_ctrl_rx")


def _load_pynq_status():
    sw = str(PYNQ_STATUS.parent)
    if sw not in sys.path:
        sys.path.insert(0, sw)
    spec = importlib.util.spec_from_file_location("_pynq_status_clock_test", PYNQ_STATUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSlcr(bytearray):
    """SLCR 的假映射: 必须**是**字节数组。

    `configure_ps_clocks()` 用 `struct.pack_into`/`unpack_from` 走 buffer 协议读写,
    所以只实现 `__getitem__`/`__setitem__` 的包装类会直接把 `pack_into` 挡在门外
    (`TypeError: argument must be read-write bytes-like object`)。

    STA 按计数器现算 —— 这是硬件行为(板上实测: 写 FPGAx_THR_CNT=0 之后 RUNNING
    立刻置 1), 不是测试的发明。少了这半, 函数末尾的 `_verify_fclk_running`
    会拿着硬件不可能出现的组合判失败。
    """

    def __init__(self, daemon):
        bytearray.__init__(self, 0x1000)
        self._daemon = daemon

    def sync_sta(self):
        for cnt, sta in zip(self._daemon.FPGA_THR_CNT, self._daemon.FPGA_THR_STA):
            value = struct.unpack_from("<I", self, cnt - self._daemon.SLCR_OFFSET)[0]
            running = self._daemon.FPGA_THR_RUNNING if value == 0 else 0
            struct.pack_into("<I", self, sta - self._daemon.SLCR_OFFSET, running)

    def close(self):
        pass


def _bd_text():
    return BD_SCRIPT.read_text(encoding="utf-8")


def _declared_num_mi(text):
    """NUM_MI of the *GP0 interconnect*.

    The two smartconnects also carry ``CONFIG.NUM_MI {1}``, so an unanchored
    search finds one of those instead.  Anchor on ``$axiint``, the variable the
    interconnect is assigned to.
    """
    line = next(line for line in text.splitlines()
                if "CONFIG.NUM_MI" in line and "$axiint" in line)
    return int(re.search(r"CONFIG\.NUM_MI\s+\{(\d+)\}", line).group(1))


def _pinned_addresses(text):
    """{cell: offset} from the bd_addr_map table, in file order."""
    body = re.search(r"set bd_addr_map \{(.*?)\n\}", text, re.S).group(1)
    entries = re.findall(r"^\s*(\w+)\s+(0x[0-9A-Fa-f]+)\s+\S+\s*$", body, re.M)
    assert entries, "bd_addr_map did not parse out of create_bd.tcl"
    return {cell: int(offset, 16) for cell, offset in entries}


def _slave_table_cells(text):
    table = re.search(r"foreach \{mi slave\} \{(.*?)\}\s*\{", text, re.S).group(1)
    return [slave.split("/")[0] for slave in
            re.findall(r"M\d\d_AXI\s+(\S+)", table)]


def _hwh_addresses(path=DEPLOYED_HWH):
    """{cell: base} from a .hwh, keyed by the MODULE's own INSTANCE.

    Anchoring on INSTANCE matters: within one ``<MODULE>`` block the nested
    ``<PARAMETER NAME="Component_Name">`` is the *IP* name (``host_bd_axi_gpio_1_0``),
    and splitting on it leaves the search for ``C_BASEADDR`` unbounded, so a block
    with no address of its own (``rst_ps7``) silently adopts the next block's.
    That produced a plausible-looking but scrambled map when this was first
    written.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    found = {}
    for chunk in text.split("<MODULE ")[1:]:
        inst = re.match(r'[^>]*INSTANCE="([\w]+)"', chunk)
        base = re.search(
            r'<PARAMETER NAME="C_(?:S_AXI_)?BASEADDR"\s+VALUE="(0x[0-9A-Fa-f]+)"',
            chunk)
        if inst and base:
            found[inst.group(1)] = int(base.group(1), 16)
    return found


def _contract_addresses():
    """{cell: base} from the frozen pre-merge contract fixture."""
    data = json.loads(AWG_CONTRACT.read_text(encoding="utf-8"))
    return {cell: int(base, 16) for cell, base in data["addresses"].items()}


def _package_pins(path):
    pins = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.search(r"PACKAGE_PIN\s+([A-Z]+\d+)", stripped)
        if match:
            pins.add(match.group(1))
    return pins


def test_bd_pins_every_slave_and_the_map_covers_the_slave_table():
    text = _bd_text()
    pinned = _pinned_addresses(text)
    assert _declared_num_mi(text) == len(pinned) == 26
    assert sorted(_slave_table_cells(text)) == sorted(pinned)


def test_the_awg_half_of_the_map_matches_the_frozen_pre_merge_contract():
    """The 21 AWG bases must equal what the firmware/host were built against.

    Read from the frozen pre-merge mapping rather than from the script, so a
    typo in the pinning table cannot be its own witness.
    """
    pinned = _pinned_addresses(_bd_text())
    contract = _contract_addresses()
    assert len(contract) >= AWG_SLAVE_COUNT, "contract fixture did not parse"
    mismatched = {
        cell: (hex(pinned[cell]), hex(contract[cell]))
        for cell in list(pinned)[:AWG_SLAVE_COUNT]
        if cell in contract and pinned[cell] != contract[cell]
    }
    assert not mismatched, f"merged BD moved AWG peripherals: {mismatched}"


def test_the_awg_bases_the_host_code_hardcodes_are_unchanged():
    """The three bases that appear as literals in software, held directly.

    These are what a moved peripheral actually breaks, and they are checked
    against the literals rather than only against the table: awg_ctl.py's DMA
    base and platform_rf.h's five are the places a wrong address turns into a
    write against somebody else's registers.
    """
    pinned = _pinned_addresses(_bd_text())
    ctl = (ROOT / "pynq_overlay" / "sw" / "awg_ctl.py").read_text(encoding="utf-8")
    m = re.search(r'"base":\s*(0x[0-9A-Fa-f]+)', ctl)
    assert m, "awg_ctl.py no longer states a DMA base"
    assert int(m.group(1), 16) == pinned["axi_dma_0"], (
        f"awg_ctl.py pins the DMA at {m.group(1)} but the BD puts it at "
        f"{hex(pinned['axi_dma_0'])}"
    )


def test_the_rf_half_does_not_collide_with_the_awg_half():
    pinned = _pinned_addresses(_bd_text())
    names = list(pinned)
    awg = {names[i]: pinned[names[i]] for i in range(AWG_SLAVE_COUNT)}
    rf = {names[i]: pinned[names[i]] for i in range(AWG_SLAVE_COUNT, len(names))}
    assert sorted(rf) == sorted(RF_CELLS)
    # New bases, not a reuse of an AWG one (which would alias two peripherals).
    assert not (set(awg.values()) & set(rf.values()))


def test_the_live_staging_overlay_agrees_with_the_pinned_map():
    """Whichever cells the staging .hwh has, their bases must match the table.

    Deliberately not "the RF cells must be absent": once the merged overlay is
    staged, they are present and correct, and an assertion of absence would have
    to be deleted at exactly the moment it became load-bearing.  Checking
    agreement holds on both sides of the deployment.
    """
    pinned = _pinned_addresses(_bd_text())
    staged = _hwh_addresses()
    assert staged, "staging .hwh did not parse"
    mismatched = {
        cell: (hex(base), hex(pinned[cell]))
        for cell, base in staged.items()
        if cell in pinned and base != pinned[cell]
    }
    assert not mismatched, (
        f"the staged overlay disagrees with the pinned table "
        f"(cell: staged vs pinned): {mismatched}"
    )


def test_every_mi_has_a_clock_and_a_reset_entry():
    """Missing from the reset lists == parked in reset, with the BD still valid."""
    text = _bd_text()
    count = _declared_num_mi(text)
    for signal in ("ACLK", "ARESETN"):
        listed = len(re.findall(rf"\bM\d\d_{signal}\b", text))
        assert listed == count, f"{signal} list has {listed}, NUM_MI is {count}"


def test_bd_imports_the_rf_sources_and_pin_constraints():
    text = _bd_text()
    assert "rf_overlay rtl" in text and "rf_overlay constraints rf.xdc" in text
    # sine_rom.v is skipped by name; adding it would put two definitions of the
    # module in one fileset, and the synthesiser picks one silently.
    assert 'if {[file tail $rtl_file] eq "sine_rom.v"} { continue }' in text
    assert re.search(r"fileset constrs_1 \$rf_xdc", text)


def test_the_two_sine_rom_copies_are_still_identical():
    """If they drift, which module the design gets depends on file ordering."""
    src = SRC_SINE_ROM.read_bytes().replace(b"\r\n", b"\n")
    rf = RF_SINE_ROM.read_bytes().replace(b"\r\n", b"\n")
    assert src == rf, (
        "src/rtl/sine_rom.v and rf_overlay/rtl/sine_rom.v have diverged; "
        "the merge compiles only src/rtl/sine_rom.v, so the RF copy would be "
        "silently unused"
    )


def test_the_rf_cells_are_clocked_and_reset_explicitly():
    """pl_spi_master's clock is bare ``aclk``; a ``*_aclk`` pattern matches it
    and silently connects nothing."""
    text = _bd_text()
    for net in ("axi_ctrl_0/s00_axi_aclk", "pl_spi_0/aclk",
                "axi_ctrl_0/s00_axi_aresetn", "pl_spi_0/aresetn",
                "ad9361_phy_0/rst_n", "rf_tx_source_0/rst_n",
                "rf_sample_capture_0/rst_n"):
        assert net in text, f"{net} is not connected"


def test_data_clk_is_declared_asynchronous_to_both_ps_and_board_clocks():
    text = CLOCK_GROUPS.read_text(encoding="utf-8")
    assert "data_clk" in text
    assert text.count("-group ") == 3
    # -include_generated_clocks is not a set_clock_groups option in 2025.2
    # (Common 17-170); it has to be applied at the get_clocks layer.
    assert "-include_generated_clocks" in text
    assert "set_clock_groups -asynchronous \\" in text


def test_bank_34_and_35_ports_are_not_declared_lvcmos33():
    """The LED/button/reset pins share a bank with the FMC pins, so they are 2.5V.

    VCCIO34 = VCCIO35 = the FMC's VADJ_3 net, driven by P18's regulator and
    measured at 2.5V on the board (docs/rf/compatibility.md §0).  The FMC half
    needs LVDS_25, which *requires* VCCO = 2.5V, so 2.5V is not negotiable and
    these three port groups have to follow it.

    They read LVCMOS33 before the merge and built fine, because IOSTANDARD
    declares a requirement rather than setting VCCO -- with only LVCMOS33 pins
    in the bank, Vivado infers 3.3V and has nothing to contradict.  Adding the
    FMC pins is what first made the contradiction visible (DRC BIVC-1, one VCCO
    per bank).  place_design does catch it, but only after a full synthesis, so
    this fails in a second instead.  See awg.xdc's LED block for the derivation.
    """
    text = AWG_XDC.read_text(encoding="utf-8")
    for ports in ("{led[*]}", "tp_rst", "{btn[*]}"):
        std = re.search(
            rf"IOSTANDARD\s+(\w+)\s+\[get_ports\s+{re.escape(ports)}\]", text)
        assert std, f"no IOSTANDARD for {ports} in awg.xdc"
        assert std.group(1) == "LVCMOS25", (
            f"{ports} is {std.group(1)}; it sits in bank 34/35, whose VCCO is "
            "2.5V (FMC VADJ_3), so LVCMOS33 conflicts with the FMC pins"
        )
    # The TFT is a different bank and stays 3.3V -- this is not a blanket change.
    assert re.search(r"IOSTANDARD\s+LVCMOS33\s+\[get_ports\s+\{lcd_rgb\[\*\]\}\]",
                     text)


def test_awg_and_fmc_pins_do_not_overlap():
    """Two top-level ports on one package pin is a DRC error, so the merge is
    only legal because these two sets are disjoint."""
    overlap = _package_pins(AWG_XDC) & _package_pins(RF_XDC)
    assert not overlap, f"package pins claimed twice: {sorted(overlap)}"


def test_pynq_uart_bridge_has_declarative_tty_ownership():
    """The bridge must own ttyPS0 through systemd, not a racing stop command.

    ``scope_verify.py`` failed before opening VISA because serial-getty could
    restart after the bridge's worker thread tried to stop it.  The unit is the
    owner boundary: it points at the merged overlay and conflicts with getty.
    """
    unit = PYNQ_SERVICE.read_text(encoding="utf-8")
    assert "Conflicts=serial-getty@ttyPS0.service" in unit
    assert "Before=serial-getty@ttyPS0.service getty.target" in unit
    assert "Type=notify" in unit
    assert "Environment=LEDBTN_OVERLAY=/home/xilinx/overlays/awg_merged/current/awg_merged.bit" in unit
    assert "ExecStartPre=/bin/sh -c 'i=0; while [ ! -f /home/xilinx/overlays/awg_merged/current/pynq_status.py ]" in unit
    assert "ExecStart=/usr/bin/python3 -B -u /home/xilinx/overlays/awg_merged/current/pynq_status.py" in unit

    source = PYNQ_STATUS.read_text(encoding="utf-8")
    bridge = source[source.index("class BridgeThread"):source.index("def run_self_test")]
    assert "subprocess.run" not in bridge
    assert "open_serial(self.path)" in bridge
    assert "UART bridge ready on %s" in bridge


def test_pynq_initializes_ps_clocks_before_axi_mmio():
    source = PYNQ_STATUS.read_text(encoding="utf-8")
    assert "0x00400500" in source
    assert "0x00100500" in source
    assert "0x01EC004D" in source
    assert "0x0000000F" in source
    assert "Clocks.fclk1_mhz" not in source
    load_overlay = source[source.index("def load_overlay"):source.index("def sd_notify_ready")]
    assert "release, _identity = resolve_verified_release()" in load_overlay
    assert load_overlay.index("configure_ps_clocks()") < load_overlay.index(
        "overlay = Overlay(str(release / merged_release.BIT_NAME))")
    assert load_overlay.rindex("configure_ps_clocks()") > load_overlay.index(
        "overlay = Overlay(str(release / merged_release.BIT_NAME))")


def test_pynq_clock_setup_preserves_existing_peripheral_clocks(monkeypatch):
    import mmap

    daemon = _load_pynq_status()
    slcr = _FakeSlcr(daemon)
    initial_aper = 0x80000400  # unrelated live clock plus SDIO0 (bit 10)
    struct.pack_into("<I", slcr, 0x12C, initial_aper)

    monkeypatch.setattr(daemon.os, "open", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(daemon.os, "close", lambda _fd: None)
    monkeypatch.setattr(mmap, "MAP_SHARED", 1, raising=False)
    monkeypatch.setattr(mmap, "PROT_READ", 1, raising=False)
    monkeypatch.setattr(mmap, "PROT_WRITE", 2, raising=False)
    # STA 由硬件跟着计数器走, 假映射只在被读的那一拍刷新它。
    monkeypatch.setattr(mmap, "mmap", lambda *_args, **_kwargs: (slcr.sync_sta(), slcr)[1])

    daemon.configure_ps_clocks()

    aper = struct.unpack_from("<I", slcr, 0x12C)[0]
    assert aper == initial_aper | 0x01EC004D


def test_pynq_install_contract_uses_the_merged_overlay():
    script = PYNQ_INSTALL.read_text(encoding="utf-8")
    assert '${LEDBTN_DEST:-/home/xilinx/overlays/awg_merged}' in script
    assert 'if [ "$DEST" != /home/xilinx/overlays/awg_merged ]; then' in script
    assert "OVERLAY_SRC=${LEDBTN_SOURCE:?" in script
    assert '--require-software --check-panel' in script
    assert "BIT_NAME=awg_merged.bit" in script
    assert "HWH_NAME=awg_merged.hwh" in script


def test_release_hash_covers_the_rf_sources():
    text = MANIFEST.read_text(encoding="utf-8")
    assert 'rf_overlay" / "rtl"' in text
    assert 'rf_overlay" / "constraints"' in text
    assert "rf_rtl.glob" in text and "rf_constr.glob" in text


def test_platform_rf_header_agrees_with_the_pinned_table():
    """platform_rf.h is the RF side's only address source; it must not drift.

    The header is compiled into the board app while the table lives in the BD
    script, so nothing but a test ties them together.  Getting this wrong is not
    a crash: the firmware's SPI writes would land in an AWG peripheral's
    registers and the AD9361 would simply never come up.
    """
    pinned = _pinned_addresses(_bd_text())
    header = PLATFORM_RF_H.read_text(encoding="utf-8")
    wanted = {
        "RF_AXI_CTRL_BASE": "axi_ctrl_0",
        "RF_PL_SPI_BASE": "pl_spi_0",
        "RF_GPIO_CTRL_BASE": "axi_gpio_ctrl",
        "RF_BRAM_TX_BASE": "axi_bram_ctrl_tx",
        "RF_BRAM_RX_BASE": "axi_bram_ctrl_rx",
    }
    for macro, cell in wanted.items():
        m = re.search(rf"^#define\s+{macro}\s+(0x[0-9A-Fa-f]+)u", header, re.M)
        assert m, f"{macro} not found in platform_rf.h"
        assert int(m.group(1), 16) == pinned[cell], (
            f"{macro} is {m.group(1)} but the BD pins {cell} at "
            f"{hex(pinned[cell])}"
        )
    # And no AWG base may appear as one of these macros' values.
    awg_offsets = {v for k, v in pinned.items() if k not in RF_CELLS}
    assert not (awg_offsets & {pinned[c] for c in RF_CELLS})
