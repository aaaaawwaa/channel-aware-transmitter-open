connect
after 2000
targets -set -filter {name =~ "ARM Cortex-A9 MPCore #0"}
stop
after 1000
puts "STATE: [state]"
puts "PC: [rrd pc]"
puts "0x170: [mrd -force 0xF8000170]"
puts "0x240: [mrd -force 0xF8000240]"
puts "0x900: [mrd -force 0xF8000900]"
puts "INT_STS: [mrd -force 0xF800700C]"
con
puts "CONTINUED"
exit
