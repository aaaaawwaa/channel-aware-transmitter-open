connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "V0 INT_STS: [mrd -force 0xF800700C]"
puts "V0 0x170: [mrd -force 0xF8000170]"
puts "V0 0x180: [mrd -force 0xF8000180]"
puts "V0 0x900: [mrd -force 0xF8000900]"
puts "V0 0x240: [mrd -force 0xF8000240]"
puts "V0 BTN (0x41200000): [mrd -force 0x41200000]"
puts "V0-DONE"
exit
