# Try every rst variant available without an APU target.
connect
after 2000
puts "=== no-target rst -srst ==="
if {[catch {rst -srst} r]} { puts "srst(global): $r" } else { puts "srst(global): OK" }
after 2000
puts "=== DAP target rst -srst ==="
catch {targets -set -filter {name =~ "DAP*"}}
if {[catch {rst -srst} r]} { puts "srst(DAP): $r" } else { puts "srst(DAP): OK" }
after 2000
puts "=== xc7z020 rst -por (PL power-on reset) ==="
catch {targets -set -filter {name =~ "xc7z020*"}}
if {[catch {rst -por} r]} { puts "por(7z): $r" } else { puts "por(7z): OK" }
after 2000
puts "=== re-enumerate ==="
disconnect
after 2000
connect
after 2000
targets
puts "RST-VARIANTS-DONE"
exit
