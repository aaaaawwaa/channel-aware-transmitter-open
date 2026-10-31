# Test A: under RUNNING Linux (daemon masked).
# 1) Safe APB reads: devcfg INT_STS, SLCR trio, SCU, L2C baselines.
# 2) THE new measurement: DAP read of 0x41200000 with PL (probably) unconfigured.
#    If PL is absent this may hang the AP transaction -> bash timeout = answer.
connect
after 2000
targets -set -filter {name =~ "*APU*"}
puts "A INT_STS: [mrd -force 0xF800700C]"
puts "A 0x170: [mrd -force 0xF8000170]"
puts "A 0x180: [mrd -force 0xF8000180]"
puts "A 0x900: [mrd -force 0xF8000900]"
puts "A 0x240: [mrd -force 0xF8000240]"
puts "A SCU_CTRL: [mrd -force 0xF8F00000]"
puts "A SCU_CFG: [mrd -force 0xF8F00004]"
puts "A SCU_PWR: [mrd -force 0xF8F00008]"
puts "A L2C_ID: [mrd -force 0xF8F02000]"
puts "A L2C_CTRL: [mrd -force 0xF8F02100]"
puts "A L2C_AUX: [mrd -force 0xF8F02104]"
puts "A L2C_PREFETCH: [mrd -force 0xF8F02C00]"
puts "A BTN read next - may hang if PL unconfigured"
puts "A BTN: [mrd -force 0x41200000]"
puts "A BTN2: [mrd -force 0x41200000]"
puts "A-DONE"
exit
