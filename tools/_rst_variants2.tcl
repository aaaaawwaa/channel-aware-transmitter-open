connect
after 2000
catch {targets -set -filter {name =~ "DAP*"}}
if {[catch {rst -system} r]} { puts "system(DAP): $r" } else { puts "system(DAP): OK" }
after 2000
catch {targets -set -filter {name =~ "xc7z020*"}}
if {[catch {rst -srst} r]} { puts "srst(7z): $r" } else { puts "srst(7z): OK" }
after 3000
puts "RSTV2-DONE"
exit
