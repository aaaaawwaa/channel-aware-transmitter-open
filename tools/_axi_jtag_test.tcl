connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "STEP1: unlock SLCR"
mwr -force 0xF8000008 0x0000DF0D
puts "BASE 0x170=[mrd -force 0xF8000170]"
puts "BASE 0x180=[mrd -force 0xF8000180]"
puts "BASE 0x900=[mrd -force 0xF8000900]"
puts "BASE 0x240=[mrd -force 0xF8000240]"
puts "BASE INT_STS=[mrd -force 0xF800700C]"
puts "STEP2: program PL with ledbtn.bit via JTAG"
fpga -file <LOCAL_PATH>
after 2000
puts "POST-FPGA INT_STS=[mrd -force 0xF800700C]"
puts "STEP3: enable level shifters, FCLK0 (50MHz+CLKACT0), FCLK1 (200MHz+CLKACT1)"
mwr -force 0xF8000900 0x0000000F
puts "0x900 now=[mrd -force 0xF8000900]"
mwr -force 0xF8000170 0x00400501
puts "0x170 now=[mrd -force 0xF8000170]"
mwr -force 0xF8000180 0x00100501
puts "0x180 now=[mrd -force 0xF8000180]"
mwr -force 0xF8000240 0x00000000
after 500
puts "STEP4: FIRST AXI READ axi_gpio_btn @0x41200000"
puts "BTN=[mrd -force 0x41200000]"
puts "STEP5: read axi_gpio_led @0x41240000"
puts "LED=[mrd -force 0x41240000]"
puts "STEP6: stability re-read of btn"
puts "BTN2=[mrd -force 0x41200000]"
puts "ALL-READS-OK"
exit
