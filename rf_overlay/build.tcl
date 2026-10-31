# Build the AC820 RF (AD-FMCOMMS3-EBZ) overlay:
#   project -> block design -> synthesis -> implementation -> .bit + .hwh
#
# Usage: vivado -mode batch -source rf_overlay/build.tcl
# Artifacts land in rf_overlay/out/ (rf_overlay.bit + rf_overlay.hwh) and are what
# gets copied to the board and loaded with pynq.Overlay().
#
# This is a separate build chain from pynq_overlay/ on purpose: different target,
# different deployment directory, different acceptance criteria. Do not mix the
# artifacts of the two (see docs/rf/version_matrix.md §4).
set root [file dirname [file normalize [info script]]]
set out_dir [file join $root out]
set proj_dir [file join $root vivado]
file mkdir $out_dir

create_project -force rf_overlay $proj_dir/rf_overlay -part xc7z020clg484-2
set_property target_language Verilog [current_project]
set proj_path [get_property DIRECTORY [current_project]]

# Every module the BD references must be in the project and analysed before
# create_bd.tcl instantiates it. ad9361_phy/phy_rx/phy_tx, axi_ctrl_v1_0 and
# pl_spi_master are module references; sine_rom is pulled in by dds_inc.
# Testbenches are deliberately NOT added -- they run separately under xsim.
add_files -norecurse \
    [file join $root rtl ad9361_phy.v] \
    [file join $root rtl phy_rx.v] \
    [file join $root rtl phy_tx.v] \
    [file join $root rtl axi_ctrl_v1_0.v] \
    [file join $root rtl axi_ctrl_v1_0_S00_AXI.v] \
    [file join $root rtl pl_spi_master.v] \
    [file join $root rtl dds_inc.v] \
    [file join $root rtl rf_tx_source.v] \
    [file join $root rtl rf_sample_reader.v] \
    [file join $root rtl rf_decimate.v] \
    [file join $root rtl rf_sample_capture.v] \
    [file join $root rtl sine_rom.v]
update_compile_order -fileset sources_1

source [file join $root bd create_bd.tcl]

add_files -fileset constrs_1 -norecurse [file join $root constraints rf.xdc]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synthesis failed"
}

# The recovered DATA_CLK and the PS clocks are unrelated sources; declare them
# asynchronous before opt_design. It cannot live in rf.xdc (clk_fpga_0 does not
# exist when user constraints are read).
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root tools impl1_clock_groups.tcl] [get_runs impl_1]

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "implementation failed"
}

# Report timing explicitly: the plan requires WNS/WHS to be recorded, and a run can
# be "100%" while still carrying violations.  The run must be opened first -- in
# batch mode a finished run leaves no design in memory, and get_timing_paths fails
# with "No open design" rather than returning anything.
open_run impl_1
set wns [get_property SLACK [get_timing_paths -delay_type max]]
set whs [get_property SLACK [get_timing_paths -delay_type min]]
puts "TIMING WNS=$wns WHS=$whs"
if {$wns < 0 || $whs < 0} {
    error "timing not met: WNS=$wns WHS=$whs"
}

set bit [file join $proj_path rf_overlay.runs impl_1 rf_bd_wrapper.bit]
set hwh [glob -nocomplain [file join $proj_path rf_overlay.gen sources_1 bd rf_bd hw_handoff *.hwh]]
if {[llength $hwh] == 0} {
    error "no .hwh produced"
}
file copy -force $bit [file join $out_dir rf_overlay.bit]
file copy -force [lindex $hwh 0] [file join $out_dir rf_overlay.hwh]
puts "OVERLAY-OK [file join $out_dir rf_overlay.bit]"
