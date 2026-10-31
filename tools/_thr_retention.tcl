# xsct: clear the FCLK throttle counters, wait, then read them back.
# Tests whether the gates come back on their own once cleared (i.e. whether
# clearing them from userspace is a stable fix or a race against the kernel).
connect
after 2000
targets -set -filter {name =~ "APU"}
after 1000
foreach a {0x178 0x188 0x198 0x1A8} {
    catch {mwr -force [format "0xF800%04X" $a] 0} w
    puts "CLEAR $a ret=$w"
}
puts "IMMEDIATE_BEGIN"
foreach a {0x178 0x17C 0x188 0x18C 0x198 0x19C 0x1A8 0x1AC} {
    catch {mrd -force [format "0xF800%04X" $a]} v
    puts "IMM $a [lindex [split [string trim $v]] end]"
}
puts "IMMEDIATE_END"
after 5000
puts "AFTER5S_BEGIN"
foreach a {0x178 0x17C 0x188 0x18C 0x198 0x19C 0x1A8 0x1AC} {
    catch {mrd -force [format "0xF800%04X" $a]} v
    puts "AFTER5S $a [lindex [split [string trim $v]] end]"
}
puts "AFTER5S_END"
after 15000
puts "AFTER20S_BEGIN"
foreach a {0x178 0x17C 0x188 0x18C 0x198 0x19C 0x1A8 0x1AC} {
    catch {mrd -force [format "0xF800%04X" $a]} v
    puts "AFTER20S $a [lindex [split [string trim $v]] end]"
}
puts "AFTER20S_END"
exit
