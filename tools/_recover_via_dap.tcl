# Recovery attempt via the DAP target directly (APU not enumerable).
# UG585: SLCR PSS_RST_CTRL (0xF8000200) bit0 = soft reset (writes 1,
# PS resets; write from DAP if the AHB-AP is still usable for APB reads).
connect
after 2000
puts "=== targets ==="
targets
puts "=== select DAP ==="
catch {targets -set -filter {name =~ "DAP*"}}
catch {targets -set -filter {name =~ "1"}}
puts "=== try SLCR reads via DAP ==="
if {[catch {mrd -force 0xF8000900} r]} { puts "DAP SLCR READ FAILED: $r" } else { puts "DAP SLCR 900: $r" }
if {[catch {mrd -force 0xF8000170} r]} { puts "DAP SLCR READ FAILED: $r" } else { puts "DAP SLCR 170: $r" }
puts "=== unlock + PSS_RST_CTRL soft reset via DAP ==="
catch {mwr -force 0xF8000008 0x0000DF0D} e1
puts "unlock: $e1"
catch {mwr -force 0xF8000200 0x00000001} e2
puts "soft reset write: $e2"
after 4000
puts "=== after reset: re-list targets ==="
disconnect
after 2000
connect
after 2000
targets
puts "RECOVER-DAP-DONE"
exit
