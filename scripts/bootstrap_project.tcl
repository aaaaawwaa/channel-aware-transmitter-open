# Create a Vivado project from tracked sources for a clean checkout.
# Usage: vivado -mode batch -source scripts/bootstrap_project.tcl
set script_dir [file dirname [file normalize [info script]]]
set root_dir [file normalize [file join $script_dir ..]]
set project_dir [file join $root_dir awg]
set project_file [file join $project_dir awg.xpr]
set part xc7z020clg484-2

file mkdir $project_dir
create_project awg $project_dir -part $part -force
set rtl_dir [file join $root_dir src rtl]
foreach rtl_file [lsort [glob -nocomplain [file join $rtl_dir *.v]]] {
    add_files -norecurse -fileset sources_1 $rtl_file
}
set coeff_file [file join $rtl_dir rrc_coeffs.vh]
if {[file exists $coeff_file]} {
    add_files -norecurse -fileset sources_1 $coeff_file
    set_property file_type {Verilog Header} [get_files $coeff_file]
}
foreach xdc_file [lsort [glob -nocomplain [file join $root_dir src constraints *.xdc]]] {
    add_files -norecurse -fileset constrs_1 $xdc_file
}
update_compile_order -fileset sources_1
close_project
puts "PROJECT-BOOTSTRAPPED $project_file"

set argv [list $project_file]
source [file join $script_dir create_bd.tcl]
