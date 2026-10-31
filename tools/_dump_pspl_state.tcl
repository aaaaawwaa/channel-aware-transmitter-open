# Read-only snapshot of the complete SLCR window plus DevC status.
# Safe in U-Boot and Linux; no PL address is touched.
connect
after 2000
targets -set -filter {name =~ "*APU*"}
for {set off 0} {$off <= 0xB00} {incr off 4} {
    set a [expr {0xF8000000 + $off}]
    if {[catch {mrd -force $a} v]} {
        puts [format "SLCR %03X ERROR" $off]
    } else {
        # mrd formats as "ADDRESS: VALUE"; normalize value to final token.
        set words [split [string trim $v]]
        puts [format "SLCR %03X %s" $off [lindex $words end]]
    }
}
foreach a {0xF8007000 0xF8007004 0xF8007008 0xF800700C 0xF8007010 0xF8007014 0xF8007018 0xF800701C 0xF8007020 0xF8007024 0xF8007028 0xF800702C 0xF8007030 0xF8007034} {
    if {[catch {mrd -force $a} v]} {
        puts [format "DEVCFG %08X ERROR" $a]
    } else {
        set words [split [string trim $v]]
        puts [format "DEVCFG %08X %s" $a [lindex $words end]]
    }
}
exit
