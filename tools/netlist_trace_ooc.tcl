# Deep trace of harmonic phase address path in OOC synthesized netlist
set dcp {<LOCAL_PATH>
open_checkpoint $dcp
set h1 {inst/u_harm1}

puts "=== 1. cells in u_harm1 grouped by REF_NAME ==="
foreach c [lsort [get_cells -hier -filter "NAME =~ $h1/*"]] {
    lappend byref([get_property REF_NAME $c]) $c
}
foreach r [lsort [array names byref]] {
    puts "REF=$r : [llength $byref($r)]"
    foreach c $byref($r) { puts "    $c" }
}

puts ""
puts "=== 2. acc1..acc4 registers present? (should be none) ==="
foreach c [lsort [get_cells -hier -filter "NAME =~ $h1/acc*_reg*"]] {
    puts "$c  REF=[get_property REF_NAME $c]"
}
puts "total acc*_reg in u_harm1: [llength [get_cells -hier -filter "NAME =~ $h1/acc*_reg*"]]"

puts ""
puts "=== 3. u_rom1 addr pins -> nets -> drivers ==="
foreach pin [lsort [get_pins -hier -filter "NAME =~ $h1/u_rom1/addr*"]] {
    set nets [get_nets -quiet -of [get_pins $pin]]
    if {[llength $nets] == 0} { puts "$pin : UNCONNECTED"; continue }
    set n [lindex $nets 0]
    set drv [get_pins -quiet -leaf -filter {DIRECTION == OUT} -of [get_nets $n]]
    puts "$pin  <- net $n"
    foreach d $drv {
        set dc [get_cells -quiet -of [get_pins $d]]
        if {[llength $dc] == 0} { puts "    driver pin $d (no cell = port?)"; continue }
        set ref [get_property REF_NAME $dc]
        set init [get_property -quiet INIT $dc]
        set ipin [lindex [split $d /] end]
        puts "    driven by $dc  REF=$ref  pin=$ipin  INIT=$init"
        # show all inputs of that driver cell
        foreach ip [lsort [get_pins -quiet -filter {DIRECTION == IN} -of $dc]] {
            set inet [get_nets -quiet -of [get_pins $ip]]
            set src ""
            foreach dp [get_pins -quiet -leaf -filter {DIRECTION == OUT} -of [get_nets $inet]] {
                set dcc [get_cells -quiet -of [get_pins $dp]]
                if {[llength $dcc] > 0} { lappend src "$dcc [lindex [split $dp /] end]" } else { lappend src "PORT $dp" }
            }
            puts "        input [lindex [split $ip /] end] <= [join $src ", "]"
        }
    }
}
