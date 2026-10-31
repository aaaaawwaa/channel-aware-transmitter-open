# Recover a wedged/poisoned DAP by resetting the system through the DAP
# target (the APU is often gone, so APU-filtered resets fail).
open_hw_manager
connect_hw_server -url localhost:3121
open_hw_target
set dap [lindex [get_hw_devices -filter {name =~ "arm_dap_*"}] 0]
puts "DAP=$dap"
current_hw_device $dap
puts "BEFORE_RESET cores=[llength [get_hw_cores]]"
catch {rst -system} r1
puts "RST_SYSTEM_RET=$r1"
after 4000
puts "AFTER cores=[llength [get_hw_cores]]"
foreach c [get_hw_cores] {
    puts "CORE [get_property NAME $c]"
}
exit
