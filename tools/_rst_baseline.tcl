connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "SELECTED-OK"
rst -system
after 3000
targets -set -filter {name =~ "ARM Cortex-A9 MPCore #0"}
after 1000
puts "STATE: [state]"
puts "PC: [rrd pc]"
puts "=== SLCR (post rst -system, pre-boot) ==="
puts "0x170 FPGA0_CLK_CTRL : [mrd -force 0xF8000170]"
puts "0x180 FPGA1_CLK_CTRL : [mrd -force 0xF8000180]"
puts "0x12C APER_CLK_CTRL  : [mrd -force 0xF800012C]"
puts "0x240 FPGA_RST_CTRL  : [mrd -force 0xF8000240]"
puts "0x900 LVL_SHFTR_EN   : [mrd -force 0xF8000900]"
puts "BASELINE-DONE"
exit
