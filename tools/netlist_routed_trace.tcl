# Routed DCP: trace harmonic address path + ROM LUT INIT spot check
set dcp {<LOCAL_PATH>
open_checkpoint $dcp
set h1 {host_bd_i/awg_top_0/inst/u_harm1}

puts "=== phase DSPs in routed ==="
foreach c [lsort [get_cells -hier -filter "NAME =~ $h1/ph* && REF_NAME == DSP48E1"]] {
    puts "$c LOC=[get_property LOC $c]"
}

# ---- map addr nets for rom1 (ph1__0 P[7..14]) ----
array set addrbit {}
set src "$h1/ph1__0"
for {set b 0} {$b < 8} {incr b} {
    set n [lindex [get_nets -quiet -of [get_pins [format "%s/P\[%d\]" $src [expr {7+$b}]]]] 0]
    if {$n ne ""} { set addrbit($n) $b }
}
puts "addr map size: [array size addrbit]"

puts ""
puts "=== sinks of each s1 addr net in routed (check for any non-LUT/mux consumer) ==="
set bad 0
foreach n [array names addrbit] {
    foreach sp [get_pins -quiet -leaf -filter {DIRECTION == IN} -of [get_nets $n]] {
        set scc [get_cells -quiet -of [get_pins $sp]]
        if {[llength $scc] > 0} {
            set ref [get_property REF_NAME $scc]
            set pn [lindex [split $sp /] end]
            if {$ref ne "LUT6" && $ref ne "LUT5" && $ref ne "MUXF7" && $ref ne "MUXF8" && $ref ne "LUT3" && $ref ne "LUT4"} {
                puts "SUSPICIOUS: $scc/$pn REF=$ref INIT=[get_property -quiet INIT $scc] LOC=[get_property LOC $scc]"
                incr bad
            }
        }
    }
}
puts "suspicious consumers: $bad"

puts ""
puts "=== LUT1 cells in u_harm1 (routed): all should be divide-by-100 logic ==="
foreach c [get_cells -hier -filter "NAME =~ $h1/* && REF_NAME == LUT1"] {
    set n [lindex [get_nets -quiet -of [get_pins "$c/I0"]] 0]
    set drv {}
    foreach dp [get_pins -quiet -leaf -filter {DIRECTION == OUT} -of [get_nets $n]] {
        set dcc [get_cells -quiet -of [get_pins $dp]]
        if {[llength $dcc] > 0} { lappend drv "$dcc" }
    }
    puts "$c INIT=[get_property INIT $c] I0<=[join $drv ", "] LOC=[get_property LOC $c]"
}

puts ""
puts "=== spot-check routed ROM LUT INITs (should equal OOC values) ==="
foreach c {prod11_i_25 prod11_i_29 prod11_i_22 prod11_i_9} {
    set co [get_cells -quiet "$h1/$c"]
    if {[llength $co] > 0} {
        puts "$h1/$c INIT=[get_property INIT $co] LOC=[get_property LOC $co]"
    } else {
        puts "$h1/$c NOT FOUND"
    }
}
