# Configure PL through the independent JTAG configuration chain only.
# Deliberately do NOT select an APU target and do NOT issue mrd/mwr:
# a timed-out DAP read poisons the interconnect and invalidates the CPU test.
connect
after 2000
targets -set -filter {name =~ "xc7z020*"}
puts "CONFIG-ONLY: programming ledbtn.bit"
fpga -file <LOCAL_PATH>
after 2000
puts "CONFIG-ONLY-DONE"
exit
