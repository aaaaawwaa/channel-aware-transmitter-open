connect
after 2000
targets -set -filter {name =~ "*APU*"}
# Control: AXI read is known-working in this exact state
puts "CTRL-READ-1: [mrd -force 0x41200000]"
# Replicate configure_ps_clocks() EXACTLY: full 32-bit write, bit0=0
mwr -force 0xF8000008 0x0000DF0D
after 200
mwr -force 0xF8000170 0x00400500
after 500
puts "WROTE-0x170-00400500"
puts "CTRL-READ-2: [mrd -force 0x41200000]"
mwr -force 0xF8000180 0x00100500
after 500
puts "WROTE-0x180-00100500"
puts "CTRL-READ-3: [mrd -force 0x41200000]"
puts "ALL-DONE"
exit
