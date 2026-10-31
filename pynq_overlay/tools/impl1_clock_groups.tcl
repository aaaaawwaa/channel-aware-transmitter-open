# Declare the two PL clock domains asynchronous, run as impl_1's opt_design PRE hook.
#
# lcd_overlay_top has a pixel path on the Y9 50 MHz oscillator (via its own MMCM)
# and an AXI path on PS FCLK_CLK0. They are unrelated sources, and the only thing
# crossing between them is lcd_fb's async-FIFO toggle handshake, which is safe by
# construction. Without this the tool times those crossings against each other and
# reports failures that cannot be fixed in the RTL.
#
# It cannot go in led_btn.xdc: clk_fpga_0 does not exist when the user constraints
# are read. Same reason tools/impl1_clock_groups.tcl exists on the AWG side.
set ps_clk [get_clocks -quiet clk_fpga_0]
if {[llength $ps_clk] != 1} {
    error "expected clk_fpga_0 before opt_design, got: $ps_clk"
}
# -include_generated_clocks pulls in the MMCM output that actually clocks the pixel
# side; a group on clk50m alone would leave the crossings timed.
set lcd_clks [get_clocks -quiet -include_generated_clocks clk50m]
if {[llength $lcd_clks] < 1} {
    error "expected clk50m before opt_design, got: $lcd_clks"
}
set_clock_groups -asynchronous -group $ps_clk -group $lcd_clks
