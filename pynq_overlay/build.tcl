# Build the AC820 PYNQ board-support overlay:
#   project -> block design -> synthesis -> implementation -> .bit + .hwh
#
# Usage: vivado -mode batch -source pynq_overlay/build.tcl
# Artifacts land in pynq_overlay/out/ (ledbtn.bit + ledbtn.hwh), which is what
# gets copied to the board and loaded with pynq.Overlay().
#
# (The artifact keeps the "ledbtn" name it has always had on the board, even though
# the design now also carries the TFT controller; renaming it would mean touching
# the deployed path, the systemd unit and install.sh for no functional gain.)
set root [file dirname [file normalize [info script]]]
set out_dir [file join $root out]
set proj_dir [file join $root vivado]
file mkdir $out_dir

create_project -force ledbtn $proj_dir/ledbtn -part xc7z020clg484-2
set_property target_language Verilog [current_project]
# create_project puts the project inside <proj_dir>/ledbtn, so derive the
# artifact paths from the project itself instead of assuming a layout.
set proj_path [get_property DIRECTORY [current_project]]

# The LCD controller is a BD module reference, so its sources have to be in the
# project (and analysed) before create_bd.tcl instantiates it. The testbenches are
# deliberately NOT added -- they run separately under xsim.
add_files -norecurse [file join $root rtl lcd_timing.v] \
                     [file join $root rtl fb_axi_reader.v] \
                     [file join $root rtl lcd_fb.v] \
                     [file join $root rtl lcd_overlay_top.v]
update_compile_order -fileset sources_1

source [file join $root bd create_bd.tcl]

add_files -fileset constrs_1 -norecurse [file join $root constraints led_btn.xdc]

launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synthesis failed"
}

# Declare the PS clock and the Y9/MMCM pixel clock asynchronous before opt_design.
# It cannot live in the XDC: clk_fpga_0 is created after the user constraints are
# read, and a run with the two unrelated clocks still timed against each other
# reports nonsense on the line-buffer crossing.
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root tools impl1_clock_groups.tcl] [get_runs impl_1]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "implementation failed"
}

set bit [file join $proj_path ledbtn.runs impl_1 ledbtn_bd_wrapper.bit]
set hwh [glob -nocomplain [file join $proj_path ledbtn.gen sources_1 bd ledbtn_bd hw_handoff *.hwh]]
if {[llength $hwh] == 0} {
    error "no .hwh produced"
}
file copy -force $bit [file join $out_dir ledbtn.bit]
file copy -force [lindex $hwh 0] [file join $out_dir ledbtn.hwh]
puts "OVERLAY-OK [file join $out_dir ledbtn.bit]"
