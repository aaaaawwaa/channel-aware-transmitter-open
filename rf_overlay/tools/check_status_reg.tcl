# R7 gate: prove the bitstream on disk was built from a BD revision that actually
# contains the new STATUS register.
#
# The project has a documented precedent of a stale bitstream whose manifest hash
# matched but whose logic came from an older create_bd.tcl (see CLAUDE.md).  A
# timestamp check alone cannot see that, so this opens the routed checkpoint and
# looks for cells that only exist in the new revision.
#
# Run after build.tcl:
#   vivado -mode batch -source rf_overlay/tools/check_status_reg.tcl

set dcp "rf_overlay/vivado/rf_overlay/rf_overlay.runs/impl_1/rf_bd_wrapper_routed.dcp"
if {![file exists $dcp]} {
    error "routed checkpoint not found: $dcp"
}
open_checkpoint $dcp

set want {
    win_cycles_reg*
    win_seq_reg*
    win_active_reg
    dclk_bin_reg*
    gray_s2_reg*
    rso_sync_reg*
}
set total 0
foreach pat $want {
    set n [llength [get_cells -quiet -hier -filter "NAME =~ \"*$pat\""]]
    puts "STATUS-CELL $pat = $n"
    incr total $n
}
if {$total < 10} {
    error "STATUS register logic missing from the bitstream ($total cells) -- the bitstream is from an older BD revision; rebuild before going to the board"
}
puts "STATUS-REG-OK ($total cells)"

# ---------------------------------------------------------------------------
# Stage-2 CONNECTIVITY: phy_mode must reach BOTH the phy and the tone source.
#
# The cell check above proves the BD revision behind this bitstream has the
# STATUS register.  This one proves it has the stage-2 *connection*, which is a
# different claim and the one stage 2 actually changes.
#
# In stage 1 phy_mode drove ad9361_phy_0 alone.  Stage 2 taps the same net into
# rf_tx_source_0's strobe divider, because the DDS must advance once per SAMPLE
# and the phy frames 2 DATA_CLK cycles per sample in 1R1T but 4 in 2R2T.  A
# bitstream missing that tap doubles TX1's tone the moment 2R2T is enabled --
# and that reads as the part behaving differently in the new mode, which is the
# worst available direction to misdiagnose.  Timestamps cannot see it (CLAUDE.md
# documents a bitstream whose manifest hash matched while the logic came from an
# older create_bd.tcl), so the routed net itself is inspected.
#
# Fixture: the stage-1 checkpoint.  Run this there and it reports ONE load,
# rf_bd_i/ad9361_phy_0/phy_mode, on the net rf_bd_i/axi_ctrl_0_phy_mode.  A gate
# that has never been shown to fail is not a gate, so that is the case it was
# written against.
set pm_nets [get_nets -quiet -hier -filter {NAME =~ "*axi_ctrl_0_phy_mode*"}]
if {[llength $pm_nets] != 1} {
    error "expected exactly one axi_ctrl_0_phy_mode net, found [llength $pm_nets] -- the stage-2 connection cannot be evaluated, and an unevaluable gate must not pass silently; inspect the checkpoint by hand"
}
set pm_net [lindex $pm_nets 0]
set pm_loads {}
foreach p [get_pins -quiet -of_objects $pm_net] {
    if {[get_property -quiet DIRECTION $p] eq "IN"} { lappend pm_loads $p }
}
puts "PHY-MODE-NET $pm_net"
foreach p [lsort $pm_loads] { puts "PHY-MODE-LOAD $p" }
if {[llength $pm_loads] < 2} {
    error "phy_mode drives [llength $pm_loads] load(s), but stage 2 requires the phy AND rf_tx_source_0 -- this bitstream predates the stage-2 BD connection, and in 2R2T it would double TX1's tone frequency"
}
set pm_hits 0
foreach p $pm_loads { if {[string match "*rf_tx_source_0*" $p]} { incr pm_hits } }
if {$pm_hits == 0} {
    error "phy_mode has [llength $pm_loads] loads but none of them is rf_tx_source_0: {[join $pm_loads {, }]}"
}
puts "PHY-MODE-OK ($pm_hits of [llength $pm_loads] loads is rf_tx_source_0)"

# ---------------------------------------------------------------------------
# SAMPLE-PATH gate: the two buffers must be REAL memories, and the reader and
# capture must be in the netlist.
#
# The trap this exists for: a BD-embedded blk_mem_gen whose Enable_32bit_Address
# is false -- while the BD has already fixed its address ports at 32 bits --
# synthesises to a netlist carrying ZERO RAMB primitives.  It does not report a
# black box and does not warn; AXI handshakes normally and every read returns 0.
# Measured A/B on a minimal BD: false -> 0 RAMB, true -> 2 RAMB.
#
# A cell-count gate CANNOT see this: the wrapper cell is present either way, and
# so is every port.  Only the primitive count underneath tells the difference,
# which is why this counts RAMB cells rather than checking that the buffer
# "exists".
#
# Two passes, not one filter.  `NAME =~ *mem*` and `REF_NAME =~ RAMB*` cannot be
# combined: no cell satisfies both (the wrapper is not a RAMB, the RAMB is not
# under that name), the filter returns nothing, and the gate reports a failure
# that is not there.  tools/check_bd_bram.tcl records the same trap.
set all_rambs [get_cells -quiet -hier -filter {REF_NAME =~ RAMB*}]
puts "BRAM-TOTAL ramb primitives in design = [llength $all_rambs]"

foreach mem {blk_mem_gen_tx blk_mem_gen_rx} {
    # Count RAMB cells whose PATH is under this instance.  The first version
    # matched cells whose NAME contained the instance name -- which is 48
    # hierarchical cells, not one -- and accumulated a hit from each, so it
    # reported 20 primitives for a memory that has four.  The verdict (>0) was
    # still right, but a gate that prints a number that wrong is a gate nobody
    # can use to tell a built memory from a half-built one.
    if {[llength [get_cells -quiet -hier -filter "NAME =~ *${mem}*"]] == 0} {
        error "$mem is absent from this bitstream -- the BD revision behind it has no sample buffer"
    }
    set rambs 0
    foreach c $all_rambs {
        if {[string match "*rf_bd_i/${mem}/*" [get_property NAME $c]]} { incr rambs }
    }
    puts "BRAM-CELL $mem rambs=$rambs"
    if {$rambs < 1} {
        error "$mem synthesised with ZERO RAMB primitives -- this is the Enable_32bit_Address trap. Every read from it returns 0, AXI still handshakes, and nothing else in the build reports it."
    }
}

# The readers/writers themselves.  rf_sample_reader lives inside rf_tx_source_0
# (instantiated as u_reader) and rf_sample_capture is its own BD cell.
set reader [get_cells -quiet -hier -filter {NAME =~ *u_reader*}]
puts "READER-CELLS [llength $reader]"
if {[llength $reader] == 0} {
    error "rf_sample_reader is not in the netlist -- rf_tx_source is still the version whose ext_dac_* inputs were tied to constants"
}
set cap [get_cells -quiet -hier -filter {NAME =~ *rf_sample_capture*}]
puts "CAPTURE-CELLS [llength $cap]"
if {[llength $cap] == 0} {
    error "rf_sample_capture is not in the netlist -- this bitstream predates the RX capture path"
}

# src_sel must actually reach rf_tx_source_0.  Same reasoning as the phy_mode
# check above: the net existing proves nothing, the load does.  If the bitstream
# predates this revision the source stays on the DDS tone and the buffer is
# unreachable, which is precisely the failure criterion D1 is written against.
set ss_nets [get_nets -quiet -hier -filter {NAME =~ "*axi_ctrl_0_src_sel*"}]
if {[llength $ss_nets] != 1} {
    error "expected exactly one axi_ctrl_0_src_sel net, found [llength $ss_nets] -- an unevaluable gate must not pass silently"
}
set ss_loads {}
foreach p [get_pins -quiet -of_objects [lindex $ss_nets 0]] {
    if {[get_property -quiet DIRECTION $p] eq "IN"} { lappend ss_loads $p }
}
puts "SRC-SEL-LOADS [llength $ss_loads]"
if {[llength $ss_loads] == 0} {
    error "src_sel drives no loads -- the source select is not connected and the buffer can never be selected"
}

puts "SAMPLE-PATH-OK"


