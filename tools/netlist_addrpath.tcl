# Verify harmonic address path purity + ROM LUT INIT consistency (OOC netlist)
set dcp {<LOCAL_PATH>
open_checkpoint $dcp
set h1 {inst/u_harm1}

# ---- sine table: round(127*sin(2*pi*i/256)), half-up, as unsigned byte ----
set pi 3.141592653589793
for {set i 0} {$i < 256} {incr i} {
    set v [expr {int(floor(127.0*sin(2.0*$pi*$i/256.0) + 0.5))}]
    if {$v < 0} { set v [expr {$v + 256}] }
    set sine($i) $v
}

# ---- map net name -> (rom, addrbit) ----
array set addrbit {}
array set addrrom {}
foreach {rom dsp} {rom0 "" rom1 ph1 rom2 ph2 rom3 ph3 rom4 ph4} {
    for {set b 0} {$b < 8} {incr b} {
        if {$rom eq "rom0"} {
            set src [format "%s/acc0_reg\[%d\]" $h1 [expr {24+$b}]]
            set net [lindex [get_nets -quiet -of [get_pins "$src/Q"]] 0]
        } else {
            set src [format "%s/%s__0" $h1 $dsp]
            set net [lindex [get_nets -quiet -of [get_pins [format "%s/P\[%d\]" $src [expr {7+$b}]]]] 0]
        }
        if {$net ne ""} { set addrbit($net) $b; set addrrom($net) $rom }
    }
}

proc net_of_pin {cell pin} {
    set n [lindex [get_nets -quiet -of [get_pins "$cell/$pin"]] 0]
    if {$n eq ""} { return "UNCONN" }
    return $n
}

# ---- 1. sinks of ph1__0 P[7..14] (the s1 address bus) ----
puts "=== ph1__0 P[7..14] sinks (s1 addr bus consumers) ==="
for {set b 7} {$b <= 14} {incr b} {
    set n [net_of_pin "$h1/ph1__0" "P\[$b\]"]
    puts "s1\[[expr {$b-7}]\] net=$n"
    foreach sp [get_pins -quiet -leaf -filter {DIRECTION == IN} -of [get_nets $n]] {
        set scc [get_cells -quiet -of [get_pins $sp]]
        if {[llength $scc] > 0} {
            puts "    -> $scc/[lindex [split $sp /] end]  REF=[get_property REF_NAME $scc] INIT=[get_property -quiet INIT $scc]"
        } else {
            puts "    -> PORT $sp"
        }
    }
}

# ---- 2. prod DSP A/B inputs ----
puts ""
puts "=== prod DSP inputs (A=sine?, B=amp?) ==="
foreach p {prod00 prod11 prod21 prod31 prod41} {
    set c "$h1/$p"
    foreach port {A B} {
        set drv {}
        set n [net_of_pin $c "$port\[0\]"]
        foreach dp [get_pins -quiet -leaf -filter {DIRECTION == OUT} -of [get_nets $n]] {
            set dcc [get_cells -quiet -of [get_pins $dp]]
            if {[llength $dcc] > 0} { lappend drv "$dcc [lindex [split $dp /] end]" } else { lappend drv "PORT [lindex [split $dp /] end]" }
        }
        puts "$p : $port\[0\]<=[join $drv ", "]"
    }
}

# ---- 3. MUXF7/MUXF8 select pins: must be driven directly by addr nets ----
puts ""
puts "=== MUXF7/MUXF8 in u_harm1: select pin drivers (expect acc0_reg Q / ph* DSP P) ==="
foreach c [lsort [get_cells -hier -filter "NAME =~ $h1/* && (REF_NAME == MUXF7 || REF_NAME == MUXF8)"]] {
    set sel [net_of_pin $c S]
    set drv {}
    foreach dp [get_pins -quiet -leaf -filter {DIRECTION == OUT} -of [get_nets $sel]] {
        set dcc [get_cells -quiet -of [get_pins $dp]]
        if {[llength $dcc] > 0} { lappend drv "$dcc [lindex [split $dp /] end]" } else { lappend drv "PORT [lindex [split $dp /] end]" }
    }
    set tag ""
    if {[info exists addrbit($sel)]} { set tag "  <== addr bit [set addrbit($sel)] of [set addrrom($sel)]" }
    puts "$c S<=[join $drv ", "]$tag"
}

# ---- 4. ROM LUT INIT verification (LUT5/LUT6 with only-addr inputs) ----
puts ""
puts "=== ROM LUT INIT verification ==="
set checked 0; set matched 0; set mismatched 0
foreach c [lsort [get_cells -hier -filter "NAME =~ $h1/* && (REF_NAME == LUT6 || REF_NAME == LUT5)"]] {
    set ref [get_property REF_NAME $c]
    set ninputs [expr {$ref eq "LUT6" ? 6 : 5}]
    set rom ""
    set inputbit {}
    set ok 1
    for {set i 0} {$i < $ninputs} {incr i} {
        set n [net_of_pin $c "I$i"]
        if {![info exists addrbit($n)]} { set ok 0; break }
        if {$rom eq ""} { set rom [set addrrom($n)] } elseif {$rom ne [set addrrom($n)]} { set ok 0; break }
        lappend inputbit [set addrbit($n)]
    }
    if {!$ok} { continue }
    incr checked
    set init [get_property INIT $c]
    set nbits [expr {$ref eq "LUT6" ? 64 : 32}]
    # missing addr bits
    set missing {}
    foreach b {0 1 2 3 4 5 6 7} { if {[lsearch -exact $inputbit $b] < 0} { lappend missing $b } }
    set hit 0; set hitdesc ""
    set nm [llength $missing]
    set maxm [expr {1 << $nm}]
    for {set m 0} {$m < $maxm} {incr m} {
        # m encodes values of missing bits (bit j of m -> missing[j])
        for {set k 0} {$k < 8} {incr k} {
            set exp 0
            for {set p 0} {$p < $nbits} {incr p} {
                set addr 0
                for {set j 0} {$j < $nm} {incr j} {
                    if {($m >> $j) & 1} { set addr [expr {$addr | (1 << [lindex $missing $j])}] }
                }
                for {set i 0} {$i < $ninputs} {incr i} {
                    if {($p >> $i) & 1} { set addr [expr {$addr | (1 << [lindex $inputbit $i])}] }
                }
                set sv [expr {([lindex [array get sine $addr] 1] >> $k) & 1}]
                if {$sv} { set exp [expr {$exp | (1 << $p)}] }
            }
            set inithex [string range $init [expr {[string first ' $init]+2}] end]
            if {$exp == [expr "0x$inithex"]} {
                set hit 1
                set hitdesc "rom=$rom sinebit=$k missing={"
                for {set j 0} {$j < $nm} {incr j} { append hitdesc "[lindex $missing $j]=[expr {($m>>$j)&1}] " }
                append hitdesc "}"
            }
        }
    }
    if {$hit} {
        incr matched
        puts "MATCH   $c  $hitdesc inputmap=$inputbit"
    } else {
        incr mismatched
        puts "MISMATCH $c  REF=$ref INIT=$init rom=$rom inputmap=$inputbit"
    }
}
puts "checked=$checked matched=$matched mismatched=$mismatched"
