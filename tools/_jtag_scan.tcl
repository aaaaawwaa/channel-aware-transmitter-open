# Read-only JTAG scan v2. No PL access, no resets.
open_hw
connect_hw_server -url localhost:3121
open_hw_target
set hwt [lindex [get_hw_targets] 0]
puts "HWT=$hwt"
puts "DEVICES_BEGIN"
foreach d [get_hw_devices] {
    puts "DEVICE [get_property NAME $d] PART=[get_property PART $d] CLASS=[get_property CLASS $d]"
}
puts "DEVICES_END"
puts "CORES_BEGIN"
foreach c [get_hw_cores] {
    puts "CORE [get_hw_core_property NAME $c] TYPE=[get_property TYPE $c]"
}
puts "CORES_END"
puts "SILICON_UID_BEGIN"
foreach d [get_hw_devices] {
    catch {puts "SILICON [get_property NAME $d] UID=[get_property SILICON_UID $d]"} e
    puts "SILICON_ERR $e"
}
puts "SILICON_UID_END"
exit
