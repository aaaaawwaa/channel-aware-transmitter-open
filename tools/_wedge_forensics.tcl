# Post-Test-C wedge forensics + recovery.
# 1) Enumerate targets (is DAP stuck? is APU visible?)
# 2) DAP read SLCR 0x900 (APB via central interconnect) - alive or blocked?
# 3) DAP read PL BTN (would time out if GPV path is the wedge)
# 4) rst -system to recover, then con
connect
after 2000
puts "=== TARGETS ==="
targets
puts "=== try APU set ==="
catch {targets -set -filter {name =~ "*APU*"}} tapu
puts "APU: $tapu"
puts "=== SLCR via DAP (central interconnect liveness) ==="
if {[catch {mrd -force 0xF8000900} r]} { puts "SLCR READ FAILED: $r" } else { puts "SLCR 900: $r" }
puts "=== BTN via DAP (GPV path) ==="
if {[catch {mrd -force 0x41200000} r]} { puts "BTN READ FAILED: $r" } else { puts "BTN: $r" }
puts "=== recover: rst -system ==="
if {[catch {rst -system} r]} { puts "RST-SYSTEM FAILED: $r" } else { puts "RST OK" }
after 3000
catch {con}
after 1000
puts "FORENSICS-DONE"
exit
