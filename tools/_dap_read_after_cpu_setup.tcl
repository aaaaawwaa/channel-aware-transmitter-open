# Test E read phase: PL was configured through the JTAG configuration
# chain; Linux CPU then set all SLCR clock/reset/level-shifter state and
# exited without touching PL. This is the first and only DAP PL access.
connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "TEST_E_DAP BEFORE_BTN"
puts "TEST_E_DAP BTN=[mrd -force 0x41200000]"
puts "TEST_E_DAP BTN2=[mrd -force 0x41200000]"
puts "TEST_E_DAP LED=[mrd -force 0x41240000]"
puts "TEST_E_DAP_DONE"
exit
