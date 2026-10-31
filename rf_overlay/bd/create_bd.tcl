# Create the AC820 RF (AD-FMCOMMS3-EBZ) overlay design:
#   PS7 (FCLK0 50 MHz for AXI, FCLK1 200 MHz for IDELAYCTRL)
#   + ad9361_phy            (vendor-example LVDS DDR data path)
#   + axi_ctrl_v1_0         (example regs 0-3: idelay_en/tap, phase_inc, phy_mode)
#   + pl_spi_master         (AD9361 control port; PS SPI0 is dead in this image)
#   + rf_tx_source          (DDS tone / external mux feeding the TX data path)
#   + axi_gpio_ctrl         (AD9361 control pins: resetb/enable/txnrx/sync/en_agc/ctl*
#
# Deliberately NOT carried over from pynq_overlay: the TFT, the LEDs/buttons and
# their S_AXI_HP0 framebuffer master.  The RF overlay is a separate target with a
# separate build chain, deployment directory and acceptance criteria; keeping the
# two apart is the discipline version_matrix.md §4 requires.
#
# Regenerated from scratch (the BD is derived state).
# Assumes a project is already open; driven by build.tcl.
set script_dir [file dirname [file normalize [info script]]]
source [file join $script_dir ps7_props_rf.tcl]

if {[get_files -quiet rf_bd.bd] ne ""} {
    set bd_dir [file dirname [get_files rf_bd.bd]]
    remove_files rf_bd.bd
    file delete -force $bd_dir
}
foreach stale [glob -nocomplain rf_bd_wrapper.*] {
    if {[llength [get_files -quiet $stale]] > 0} {
        remove_files $stale
    }
}

create_bd_design rf_bd

# ---- PS7 ----
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
set_property -dict $ps7_props $ps7
# Vivado may recompute HIGHADDR when BASEADDR is applied; restore the full
# 1-GiB DDR window after every PS7 property is set.
set_property CONFIG.PCW_DDR_RAM_HIGHADDR 0x3FFFFFFF $ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable"} \
    [get_bd_cells ps7_0]

# ---- AXI-Lite control plane registers (example regs 0-3) ----
set axi_ctrl [create_bd_cell -type module -reference axi_ctrl_v1_0 axi_ctrl_0]

# ---- AD9361 control pins: 9 outputs in one aperture ----
# resetb/enable/txnrx/sync_in/en_agc are the mode pins; ctl0-3 are the example's
# spare GPIO_CTL lines (all driven low there).  Land all nine on one 9-bit output
# so software writes them atomically rather than one pin at a time.
#   bit0 txnrx  bit1 enable  bit2 resetb  bit3 sync  bit4 en_agc  bits8:5 ctl0-3
# The example's gpio_status* lines are inputs and unused in its init sequence, so
# they are not wired here; a status GPIO is a later addition if needed.
set gpio_ctl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_ctrl]
set_property -dict [list CONFIG.C_GPIO_WIDTH {9} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $gpio_ctl
create_bd_port -dir O -from 8 -to 0 rf_gpio
connect_bd_net [get_bd_pins axi_gpio_ctrl/gpio_io_o] [get_bd_ports rf_gpio]

# ---- PL SPI master (AD9361 control port) ----
set spi [create_bd_cell -type module -reference pl_spi_master pl_spi_0]
create_bd_port -dir O spi_sclk
create_bd_port -dir O spi_mosi
create_bd_port -dir I spi_miso
create_bd_port -dir O spi_csn
connect_bd_net [get_bd_pins pl_spi_0/spi_sclk] [get_bd_ports spi_sclk]
connect_bd_net [get_bd_pins pl_spi_0/spi_mosi] [get_bd_ports spi_mosi]
connect_bd_net [get_bd_pins pl_spi_0/spi_miso] [get_bd_ports spi_miso]
connect_bd_net [get_bd_pins pl_spi_0/spi_csn]  [get_bd_ports spi_csn]

# ---- AD9361 data path ----
set phy [create_bd_cell -type module -reference ad9361_phy ad9361_phy_0]

# RX clock + data (Bank 34, LVDS_25). The _n halves must be ports too: an IBUFDS
# needs the true differential pair, not a single-ended input.
create_bd_port -dir I rx_clk_in_p
create_bd_port -dir I rx_clk_in_n
create_bd_port -dir I rx_frame_in_p
create_bd_port -dir I rx_frame_in_n
create_bd_port -dir I -from 5 -to 0 rx_data_in_p
create_bd_port -dir I -from 5 -to 0 rx_data_in_n
connect_bd_net [get_bd_pins ad9361_phy_0/rx_clk_in_p]   [get_bd_ports rx_clk_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_clk_in_n]   [get_bd_ports rx_clk_in_n]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_frame_in_p] [get_bd_ports rx_frame_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_frame_in_n] [get_bd_ports rx_frame_in_n]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_data_in_p]  [get_bd_ports rx_data_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_data_in_n]  [get_bd_ports rx_data_in_n]

# TX clock + data (Bank 34, LVDS_25)
create_bd_port -dir O tx_clk_out_p
create_bd_port -dir O tx_clk_out_n
create_bd_port -dir O tx_frame_out_p
create_bd_port -dir O tx_frame_out_n
create_bd_port -dir O -from 5 -to 0 tx_data_out_p
create_bd_port -dir O -from 5 -to 0 tx_data_out_n
connect_bd_net [get_bd_pins ad9361_phy_0/tx_clk_out_p]   [get_bd_ports tx_clk_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_clk_out_n]   [get_bd_ports tx_clk_out_n]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_frame_out_p] [get_bd_ports tx_frame_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_frame_out_n] [get_bd_ports tx_frame_out_n]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_data_out_p]  [get_bd_ports tx_data_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_data_out_n]  [get_bd_ports tx_data_out_n]

# ---- TX sample source: DDS tone or a real buffer ----
set txsrc [create_bd_cell -type module -reference rf_tx_source rf_tx_source_0]
# The external input used to be five xlconstants tying ext_dac_* to zero, with a
# note that this is what gets rewired when a real source arrives.  It has now
# arrived: rf_tx_source instantiates rf_sample_reader internally and the BRAM
# below feeds it, so the mux select (src_sel) is a real register bit instead of
# a constant.

# ---- RX capture cell ----
set rxcaps [create_bd_cell -type module -reference rf_sample_capture rf_sample_capture_0]

# ---- the two sample buffers ----
# One per direction.  Both are made the same way, so the parameters are written
# once: they are load bearing and must not drift between the two instances.
#
# ⚠ Enable_32bit_Address = true is NOT a preference.  The BD fixes a BRAM's
# address ports at 32 bits (BYTE_ADDRESS), so the generated IP always carries
# C_ADDRA_WIDTH = 32; with this parameter false it also carries
# C_ENABLE_32BIT_ADDRESS = 0, and that pair of contradictions makes blk_mem_gen
# silently synthesise to an empty netlist carrying ZERO RAMB primitives.  No
# black box, no warning, AXI handshakes normally, every read returns 0.  Measured
# A/B on a minimal BD: false -> 0 RAMB, true -> 2 RAMB.  A standalone create_ip
# with the same settings is fine, so the trap exists only for a BD-embedded
# instance -- which is why an experiment outside the BD would conclude the
# parameters were fine.
#
# Port-side consequence: port A is byte addressed (the controller emits byte
# addresses), so port B must be too -- rf_sample_reader drives word << 2.
#
# SINGLE_PORT_BRAM 1 is what frees port B for the PL at all; with 0 the
# controller consumes both ports and there is nothing to read or write.
proc rf_make_bram {ctrl_name mem_name depth} {
    set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 $ctrl_name]
    set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1}] $ctrl
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 $mem_name]
    set_property -dict [list \
        CONFIG.Memory_Type {True_Dual_Port_RAM} \
        CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} \
        CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
        CONFIG.Write_Depth_A $depth CONFIG.Enable_A {Use_ENA_Pin} \
        CONFIG.Enable_B {Use_ENB_Pin} CONFIG.Use_Byte_Write_Enable {true} \
        CONFIG.Byte_Size {8} CONFIG.Enable_32bit_Address {true}] $mem
    connect_bd_intf_net [get_bd_intf_pins $ctrl_name/BRAM_PORTA] \
        [get_bd_intf_pins $mem_name/BRAM_PORTA]
    return $mem_name
}
rf_make_bram axi_bram_ctrl_tx blk_mem_gen_tx 4096
rf_make_bram axi_bram_ctrl_rx blk_mem_gen_rx 4096

# ---- DATA_CLK domain: the phy runs entirely on the recovered clock ----
# ad9361_phy emits data_clk, which is the AD9361's DATA_CLK recovered through an
# IBUFDS. Everything inside the phy and rf_tx_source is clocked by it, so nothing
# in that domain may be driven from FCLK0.
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk] [get_bd_pins rf_tx_source_0/tx_clk]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_valid]   [get_bd_pins ad9361_phy_0/dac_valid]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_d1] [get_bd_pins ad9361_phy_0/dac_data_d1]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_q1] [get_bd_pins ad9361_phy_0/dac_data_q1]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_d2] [get_bd_pins ad9361_phy_0/dac_data_d2]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_q2] [get_bd_pins ad9361_phy_0/dac_data_q2]
# Example regs drive the phy's configuration, crossing from FCLK0 into the
# DATA_CLK domain unsynchronised (the example has no synchroniser on them either).
# What makes each of them safe differs, and only idelay_* is actually static:
#
#   idelay_en / idelay_tap -- genuinely static.  Written during bring-up, never
#       touched while data flows.
#
#   phy_mode -- NOT static.  Stage 2 writes it at run time to switch 1R1T <-> 2R2T.
#       The discipline that keeps that safe is ORDERING, not synchronisation.  The
#       authoritative statement of it is tools/impl1_clock_groups.tcl:12-14, and it
#       is STRICTER than "keep the data path quiet": the write must happen while
#       DATA_CLK is STOPPED, and only then may the clock be allowed to appear.
#       Quiet is not enough -- phase_inc = 0 merely removes edges from the data,
#       it does not stop DATA_CLK, and this crossing has no synchroniser, so a
#       write while the clock runs is a real metastability hazard on the
#       configuration bit itself.  `init 2r2t` is the sequence that satisfies it:
#       RESETB low, release, wait out the part's boot (BBPLL still unprogrammed,
#       so DATA_CLK has not appeared), write phase_inc = 0 and phy_mode = 0, and
#       only then bring the clock up.  It reaches rf_tx_source's strobe divider
#       combinationally as well as phy_tx's framing case, so a mid-stream change
#       would glitch both the sample period and the wire frame for a cycle.
#
#   phase_inc -- also written at run time (the C2 linearity test steps 128 -> 256
#       with the tone already running).  Safe for a different reason: it is a value
#       consumed once per strobe, not a framing control, so a write that tears
#       across the 12 bits costs at most one sample's phase step -- it cannot
#       break the frame the way phy_mode can.
connect_bd_net [get_bd_pins axi_ctrl_0/idelay_en]  [get_bd_pins ad9361_phy_0/idelay_en]
connect_bd_net [get_bd_pins axi_ctrl_0/idelay_tap] [get_bd_pins ad9361_phy_0/idelay_tap]
connect_bd_net [get_bd_pins axi_ctrl_0/phy_mode]   [get_bd_pins ad9361_phy_0/phy_mode]
# phy_mode now feeds the tone source as well as the phy: the DDS strobe period has
# to match the phy's sample framing (2 cycles/sample in 1R1T, 4 in 2R2T), or TX1's
# tone frequency doubles the moment the mode changes.
connect_bd_net [get_bd_pins axi_ctrl_0/phy_mode]   [get_bd_pins rf_tx_source_0/phy_mode]
connect_bd_net [get_bd_pins axi_ctrl_0/phase_inc]  [get_bd_pins rf_tx_source_0/phase_inc]
# DDS waveform shape (reg10[0], offset 0x28): sine or square.  Deliberately a
# separate register from phy_mode so that changing the waveform never touches a
# bit that has a clock-stopped write discipline attached to it.
connect_bd_net [get_bd_pins axi_ctrl_0/wave_sel]   [get_bd_pins rf_tx_source_0/wave_sel]
# Read-only STATUS: the recovered clock and rf_tx_source's reset liveness.  Both
# are DATA_CLK-domain; axi_ctrl_0 synchronises them into FCLK0 itself (Gray
# counter + 2-FF chains), which is why this crossing is safe unlike the
# configuration signals above (which rely on write ordering, not synchronisation).
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]        [get_bd_pins axi_ctrl_0/data_clk]
connect_bd_net [get_bd_pins rf_tx_source_0/rst_source_ok] [get_bd_pins axi_ctrl_0/rst_source_ok]

# ---- IDELAYCTRL reference clock: PS FCLK1 at 200 MHz ----
# IDELAYE2/IDELAYCTRL require a 200 MHz +/-10 MHz REFCLK. Deliberately PS-sourced
# rather than the board's Y9 oscillator, whose bank voltage at 2.5 V is an open
# question.
#
# VERIFY this rate after loading the overlay; do NOT set it.  PYNQ's
# Clocks.fclkX_mhz property is scaled for ZedBoard's 50 MHz PS_CLK, but this
# board's PS_CLK is 33.333 MHz, so every value it reports or writes is off by
# 1.5x.  Setting fclk1_mhz = 200 makes PYNQ solve d0*d1 = 1500/200 = 7 and it
# writes DIVISOR0 = 7, landing FCLK1 at 142.857 MHz -- below the IDELAYCTRL
# spec.  Overlay() does not touch the divisors, so the BD's 5/1 (a true
# 200 MHz) is already correct and needs nothing.  Judge from the SLCR
# registers (FPGA0_CLK_CTRL = 0xF8000170, FPGA1 = 0xF8000180, DIVISOR0 =
# bits[13:8], DIVISOR1 = bits[25:20], f = 1000 MHz / (DIV0 * DIV1)).
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] [get_bd_pins ad9361_phy_0/ref_clk200m]

# ---- source select and buffer configuration (FCLK0 -> DATA_CLK) ----
# src_sel used to be a constant 0 holding the source on the DDS tone.  It is now
# a register bit, which is what makes the BRAM path reachable at all -- and that
# is the whole of criterion D1.
#
# Every one of these crosses from FCLK0 into the DATA_CLK domain with no
# synchroniser at the BD level.  rf_tx_source handles each according to what it
# is: src_sel is a single bit and gets a 2-FF chain; smp_len and smp_wrap are
# multi-bit and are committed on a toggle, because independent per-bit chains can
# deliver their bits on different edges and assemble a configuration nobody
# wrote.
connect_bd_net [get_bd_pins axi_ctrl_0/src_sel]    [get_bd_pins rf_tx_source_0/src_sel]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_len]    [get_bd_pins rf_tx_source_0/smp_len]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_wrap]   [get_bd_pins rf_tx_source_0/smp_wrap]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_commit] [get_bd_pins rf_tx_source_0/smp_commit]
connect_bd_net [get_bd_pins rf_tx_source_0/smp_pos] [get_bd_pins axi_ctrl_0/smp_pos]
# The capture side's configuration and status, same discipline.
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_arm]    [get_bd_pins rf_sample_capture_0/cfg_arm]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_cont]   [get_bd_pins rf_sample_capture_0/cfg_cont]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_commit] [get_bd_pins rf_sample_capture_0/cfg_commit]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_ratio]  [get_bd_pins rf_sample_capture_0/cfg_ratio]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_len]    [get_bd_pins rf_sample_capture_0/cfg_len]
connect_bd_net [get_bd_pins rf_sample_capture_0/pos]  [get_bd_pins axi_ctrl_0/cap_pos]
connect_bd_net [get_bd_pins rf_sample_capture_0/done] [get_bd_pins axi_ctrl_0/cap_done]

# phy reset is the AXI system reset, synchronised down to the recovered clock
# inside rf_tx_source (rst_source_ok) and inside ad9361_phy itself (4-deep sync).
# Driving it from the AXI reset (not from a PS GPIO) means the phy comes out of
# reset as soon as the overlay is programmed, which is the only safe option while
# DATA_CLK may not exist yet.

# ---- AXI interconnect (1 SI / 5 MI) ----
# NUM_MI is now 5: two AXI BRAM controllers joined the three AXI-Lite control
# slaves.  It must equal the number of slaves actually connected -- an
# unconnected master is flagged by validate_bd_design as an incomplete address
# path, and the previous value of 3 came with a comment saying exactly that.
set axiint [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {5} CONFIG.NUM_SI {1}] $axiint
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins axi_ctrl_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M01_AXI] [get_bd_intf_pins pl_spi_0/s_axi]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M02_AXI] [get_bd_intf_pins axi_gpio_ctrl/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M03_AXI] [get_bd_intf_pins axi_bram_ctrl_tx/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M04_AXI] [get_bd_intf_pins axi_bram_ctrl_rx/S_AXI]
foreach port {ACLK S00_ACLK M00_ACLK M01_ACLK M02_ACLK M03_ACLK M04_ACLK} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/$port]
}
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]
# Explicit pin names, not wildcards: pl_spi_master is a hand-written module whose
# clock/reset are bare `aclk`/`aresetn`, so a `*_aclk` pattern silently matches
# nothing and the failure surfaces later as a confusing connect_bd_net error.
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_ctrl_0/s00_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_gpio_ctrl/s_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins pl_spi_0/aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_bram_ctrl_tx/s_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_bram_ctrl_rx/s_axi_aclk]

# ---- reset ----
# proc_sys_reset parks AXI peripherals in reset when dcm_locked is left open.
set rst_clock_locked [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_clock_locked]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_clock_locked
set rst_aux_inactive [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_aux_inactive]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_aux_inactive
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]       [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N]   [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins rst_clock_locked/dout] [get_bd_pins rst_ps7/dcm_locked]
connect_bd_net [get_bd_pins rst_aux_inactive/dout] [get_bd_pins rst_ps7/aux_reset_in]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_ctrl_0/s00_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_gpio_ctrl/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins pl_spi_0/aresetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_bram_ctrl_tx/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_bram_ctrl_rx/s_axi_aresetn]
# The interconnect needs its per-interface resets as well; leaving them dangling
# ties them to 0, which parks every AXI slave in reset.
connect_bd_net [get_bd_pins rst_ps7/interconnect_aresetn] [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/S00_ARESETN]
foreach mi {M00_ARESETN M01_ARESETN M02_ARESETN M03_ARESETN M04_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_interconnect_0/$mi]
}
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins ad9361_phy_0/rst_n]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins rf_tx_source_0/rst_n]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins rf_sample_capture_0/rst_n]

# ===========================================================================
# Sample buffer port B: the PL side of each BRAM.
#
# Both ports are clocked by the recovered DATA_CLK, which is the same clock the
# reader and the capture run on -- routed directly from ad9361_phy rather than
# through a module port, so there is one clock net and no forwarded clock.
#
# The TX buffer only reads on port B, so dinb/web are tied off; the RX buffer
# only writes, so its doutb is simply not connected.  Constant WIDTHS must match
# the ports: a 1-bit constant into a 32-bit port connects the LSB and leaves the
# other 31 dangling (BD 41-2383), which is a silent tie-off rather than a zero.
# ===========================================================================

set tx_din0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tx_bram_din0]
set_property -dict [list CONFIG.CONST_WIDTH {32} CONFIG.CONST_VAL {0}] $tx_din0
set tx_web0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tx_bram_web0]
set_property -dict [list CONFIG.CONST_WIDTH {4} CONFIG.CONST_VAL {0}] $tx_web0

connect_bd_net [get_bd_pins tx_bram_din0/dout] [get_bd_pins blk_mem_gen_tx/dinb]
connect_bd_net [get_bd_pins tx_bram_web0/dout] [get_bd_pins blk_mem_gen_tx/web]
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]           [get_bd_pins blk_mem_gen_tx/clkb]
connect_bd_net [get_bd_pins rf_tx_source_0/bram_en]          [get_bd_pins blk_mem_gen_tx/enb]
connect_bd_net [get_bd_pins rf_tx_source_0/bram_addr]        [get_bd_pins blk_mem_gen_tx/addrb]
connect_bd_net [get_bd_pins blk_mem_gen_tx/doutb]            [get_bd_pins rf_tx_source_0/bram_rdata]

connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]           [get_bd_pins blk_mem_gen_rx/clkb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_en]     [get_bd_pins blk_mem_gen_rx/enb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_we]     [get_bd_pins blk_mem_gen_rx/web]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_addr]   [get_bd_pins blk_mem_gen_rx/addrb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_wdata]  [get_bd_pins blk_mem_gen_rx/dinb]

# The capture takes its samples straight off the deserialiser.  adc_d1q1_valid is
# a one-cycle pulse per sample (phy_rx decodes it out of the frame shift
# register), which is the RX-side mirror of dds_ce and is what the decimator
# counts on.  RX1 only: this is the single complex channel the criteria use.
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]       [get_bd_pins rf_sample_capture_0/clk]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_d1q1_valid] [get_bd_pins rf_sample_capture_0/ce]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_data_d1]    [get_bd_pins rf_sample_capture_0/in_i]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_data_q1]    [get_bd_pins rf_sample_capture_0/in_q]

assign_bd_address

# Each AXI BRAM Controller has a minimum 4K aperture; these memories are 16 KiB
# (4096 words x 4 bytes), so the range is set to match the memory rather than
# left at whatever assign_bd_address picked.  Checked for existence first: a
# filter that matches nothing returns an empty list, set_property on that is a
# silent no-op, and the aperture would quietly stay wrong.
# `foreach {a b} {...}` consumes the list TWO AT A TIME.  Written as
# `foreach pair {...}` it consumes one element per iteration, so $pair is a bare
# string and [lindex $pair 1] is empty -- which reaches set_property as an empty
# range and fails with "Proposed range '' is invalid" rather than anything that
# points at the loop.
foreach {ctrl rng} {axi_bram_ctrl_tx 16K axi_bram_ctrl_rx 16K} {
    set seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data] \
        -filter "NAME =~ *SEG_${ctrl}_Mem0"]
    if {[llength $seg] != 1} {
        error "expected exactly one address segment for $ctrl, got: $seg"
    }
    set_property RANGE $rng $seg
    puts "ADDR $ctrl range set to $rng"
}
validate_bd_design
save_bd_design
make_wrapper -files [get_files rf_bd.bd] -top -import
set wrapper [get_files -quiet -filter {NAME =~ *rf_bd_wrapper*}]
if {[llength $wrapper] == 0} {
    error "wrapper generation failed"
}
set_property top [file rootname [file tail [lindex $wrapper 0]]] [current_fileset]
generate_target all [get_files rf_bd.bd]
