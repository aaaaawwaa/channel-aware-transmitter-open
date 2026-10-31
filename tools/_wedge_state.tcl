# xsct: read the post-wedge SLCR state and the APU core states.
# Read-only.
connect
after 2000
targets -set -filter {name =~ "APU"}
after 1000
puts "APU_SELECTED"
foreach core {2 3} {
    catch {targets $core} t
    catch {state} st
    puts "CORE_$core STATE=$st"
    catch {rrd pc} pc
    puts "CORE_$core PC=$pc"
}
foreach a {0x170 0x178 0x17C 0x180 0x188 0x18C 0x190 0x198 0x19C 0x1A0 0x1A8 0x1AC 0x12C 0x240 0x900 0x004 0x008} {
    catch {mrd -force [format "0xF800%04X" $a]} v
    set words [split [string trim $v]]
    puts "SLCR $a [lindex $words end]"
}
catch {mrd -force 0xF800700C} v2
puts "DEVCFG 700C [lindex [split [string trim $v2]] end]"
exit
