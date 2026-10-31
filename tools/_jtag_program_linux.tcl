# Test B: under RUNNING Linux, configure PL via JTAG (ledbtn.bit),
# then DAP-read BTN twice. V0 proved this works under U-Boot; here the
# only change is Linux runtime. If BTN reads fine -> DAP->PL path under
# Linux is intact, and historical probe3/5/7 wedges (PL configured,
# CPU read) are a different mechanism from V1's (PL maybe unconfigured).
connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "B PRE INT_STS: [mrd -force 0xF800700C]"
fpga -file <LOCAL_PATH>
after 2000
set post [mrd -force 0xF800700C]
puts "B POST INT_STS: $post"
puts "B CLKACT 170: [mrd -force 0xF8000170]"
puts "B CLKACT 180: [mrd -force 0xF8000180]"
puts "B CLKACT 900: [mrd -force 0xF8000900]"
puts "B BTN read next (configured, under Linux)"
puts "B BTN: [mrd -force 0x41200000]"
puts "B BTN2: [mrd -force 0x41200000]"
puts "B LED: [mrd -force 0x41240000]"
puts "B-DONE"
exit
