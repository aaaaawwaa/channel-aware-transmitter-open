# Re-run ONLY the main synthesis after clearing the stale blk_mem_gen IP cache
# entries, then apply BRAM gate level 2 to the fresh checkpoint. Read-only with
# respect to the board; no implementation.
set root "<LOCAL_PATH>"
open_project [file join $root awg awg.xpr]
reset_run synth_1
reset_run synth_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synth_1 did not complete"
}
open_run synth_1
set ::CBB_SOURCED 1
source $root/tools/check_bd_bram.tcl
cbb_check_synth
puts "RESYNTH-PROBE-DONE"
close_project
