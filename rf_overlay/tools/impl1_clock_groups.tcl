# Declare the recovered sample clock and the PS clock domains asynchronous, run as
# impl_1's opt_design PRE hook.
#
# ad9361_phy runs entirely on DATA_CLK, recovered from the AD9361 through an IBUFDS.
# That clock is unrelated to PS FCLK0 (AXI) and FCLK1 (IDELAYCTRL REFCLK): it comes
# from the radio's own reference, and its frequency depends on the sample rate the
# AD9361 has been programmed to.
#
# What legitimately crosses these domains, and why each is safe:
#   * axi_ctrl_0 idelay_en/idelay_tap/phy_mode -> ad9361_phy.  No synchroniser, so
#     these are TIMING DISCIPLINE, not a structural guarantee: software writes them
#     BEFORE DATA_CLK exists and does not change them while data flows.  Stage 2
#     writes phy_mode live, which is why the write must happen with the AD9361 still
#     in a state where DATA_CLK is stopped, then the clock is allowed to appear.
#   * axi_ctrl_0 phase_inc -> rf_tx_source -> dds_inc.  Same discipline: written at
#     bring-up.  Changing it while the phy is live is a real CDC hazard, so software
#     does not do that.
#   * ad9361_phy_0/data_clk -> axi_ctrl_0 (STATUS).  This one IS synchronised by
#     design: a DATA_CLK-domain Gray counter crossed by a 3-deep Gray sync, plus
#     2-FF chains for the reset and for rst_source_ok.
#   * rst_ps7/peripheral_aresetn -> the phy's rst_n.  ad9361_phy synchronises this
#     down to data_clk internally with a 4-deep chain, and rf_tx_source with a 2-deep
#     one, so the asynchronous release is handled by design.
#
# Without this, the tool times the static crossings against DATA_CLK and reports
# failures that cannot be fixed in the RTL -- the same reasoning as the AWG side's
# set_clock_groups and pynq_overlay/tools/impl1_clock_groups.tcl.
#
# It cannot live in rf.xdc: clk_fpga_0 does not exist when user constraints are read.
set ps_clk [get_clocks -quiet clk_fpga_0]
if {[llength $ps_clk] != 1} {
    error "expected clk_fpga_0 before opt_design, got: $ps_clk"
}
# The recovered clock is created in rf.xdc on rx_clk_in_p. -include_generated_clocks
# also picks up its BUFG-derived copies, so nothing on the data side is left timed
# against the PS domain.
set dclk [get_clocks -quiet -include_generated_clocks data_clk]
if {[llength $dclk] < 1} {
    error "expected data_clk before opt_design, got: $dclk"
}
set_clock_groups -asynchronous -group $ps_clk -group $dclk
