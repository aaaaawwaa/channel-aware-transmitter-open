connect
after 3000
puts "=== TARGETS ==="
catch {puts [targets]} e
puts $e
targets -set -filter {name =~ "ARM Cortex-A9 MPCore #0"}
catch {stop} se
puts "STOP: $se"
after 1000
puts "STATE: [state]"
puts "PC: [rrd pc]"
puts "DONE"
exit
