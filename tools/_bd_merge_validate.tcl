# Validate the merged BD only: no synthesis, no implementation, no board access.
# Runs scripts/create_bd.tcl (which ends in validate_bd_design) inside the AWG
# project so the RF half and the AWG/TFT half are checked together.
set script_dir [file dirname [file normalize [info script]]]
source $script_dir/../scripts/create_bd.tcl
