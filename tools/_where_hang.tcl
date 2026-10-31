connect
after 2000
targets -set -filter {name =~ "ARM Cortex-A9 MPCore #0"}
stop
after 1000
puts "STATE: [state]"
puts "PC: [rrd pc]"
puts "LR: [rrd lr]"
puts "SP: [rrd sp]"
# read a bit of stack for context
puts "STACK0: [mrd -force 0x41200000 1]"
puts "DONE-STOPPED"
exit
