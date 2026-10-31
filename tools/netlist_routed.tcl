# Routed DCP check: hierarchy, acc1_reg presence, address path structure
set dcp {<LOCAL_PATH>
open_checkpoint $dcp

puts "=== cells matching *harm1 (hierarchy) ==="
foreach c [lsort [get_cells -hier -filter {NAME =~ *harm1* && IS_PRIMITIVE == FALSE}]] { puts $c }

puts ""
puts "=== any acc1..acc4 regs? ==="
set acc [get_cells -hier -filter {NAME =~ *acc[1234]_reg*}]
puts "count=[llength $acc]"
foreach c $acc { puts "$c REF=[get_property REF_NAME $c]" }

puts ""
puts "=== acc0_reg + phase DSPs present in routed? ==="
foreach c [lsort [get_cells -hier -filter {NAME =~ *acc0_reg* && REF_NAME == FDRE || NAME =~ *acc0_reg* && REF_NAME == FDCE}]] { puts "$c REF=[get_property REF_NAME $c] LOC=[get_property LOC $c]" }
puts "-- phase DSPs --"
foreach c [lsort [get_cells -hier -filter {NAME =~ *ph1 && REF_NAME == DSP48E1}]] { puts "$c LOC=[get_property LOC $c]" }
