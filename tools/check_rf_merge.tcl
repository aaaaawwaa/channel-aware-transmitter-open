# Merged-design gate: prove the bitstream on disk really contains BOTH halves.
#
# Why this exists rather than trusting the build log.  This repository has a
# documented precedent of a bitstream whose manifest hash matched while its logic
# came from an older create_bd.tcl (CLAUDE.md, "板上 bitstream 可能落后源码一个 BD
# 修订").  A timestamp check cannot see that.  The merge multiplies the risk: the
# AWG half and the RF half are produced by the same script, so a build that
# dropped or silently no-op'd the RF block still produces a .bit that programs,
# boots, and drives the AWG perfectly -- and the only symptom is an AD9361 that
# never comes up, which reads as an RF problem.
#
# tools/check_bd_bram.tcl's two-level gate already globs `host_bd_blk_mem_gen_*_0`,
# so it covers blk_mem_gen_tx and blk_mem_gen_rx as well as the two AWG ones, and
# it runs during the build on synth_1.  Section 5 below re-counts RAMB on the
# ROUTED checkpoint instead: that is a different and stronger claim -- not "the
# synthesiser built a memory" but "a memory survived into the bitstream".
#
# Run after build_bitstream.py:
#   vivado -mode batch -source tools/check_rf_merge.tcl

set dcp "awg/awg.runs/impl_1/host_bd_wrapper_routed.dcp"
if {![file exists $dcp]} {
    error "routed checkpoint not found: $dcp -- implementation has not completed"
}
open_checkpoint $dcp

# ---- 1. Both halves are present ------------------------------------------
# awg_top and the display/touch cells were in the design before the merge, so
# they double as the control: if these are missing the checkpoint is not this
# design at all, and every RF check below would be meaningless.
# lcd_0 is the BD cell; it wraps lcd_overlay_top, which in turn instantiates
# lcd_timing.  The BD cell name is what appears at this level.
set awg_cells {awg_top_0 lcd_0}
set rf_cells  {axi_ctrl_0 pl_spi_0 axi_gpio_ctrl ad9361_phy_0 \
               rf_tx_source_0 rf_sample_capture_0}

foreach grp {awg_cells rf_cells} {
    set missing {}
    foreach c [set $grp] {
        if {[llength [get_cells -quiet -hier -filter "NAME =~ *${c}*"]] == 0} {
            lappend missing $c
        }
    }
    if {[llength $missing] > 0} {
        error "[string toupper $grp] absent from the routed netlist: $missing -- this bitstream was not built from the merged BD"
    }
    puts "MERGE-CELLS-OK $grp ([llength [set $grp]] cells)"
}

# ---- 2. The RF BRAM controllers are real, and PS-visible ------------------
# The memories themselves are covered by check_bd_bram.tcl; what is unique to
# the merge is that these two controllers sit behind a 26-MI interconnect that
# used to have 21.  A slave wired into the BD but dropped from the address map
# still appears as a cell -- so the address assertion below is separate.
foreach ctrl {axi_bram_ctrl_tx axi_bram_ctrl_rx} {
    if {[llength [get_cells -quiet -hier -filter "NAME =~ *${ctrl}*"]] == 0} {
        error "$ctrl is absent from the routed netlist -- the software's RF_BRAM_TX/RX_BASE would fault on first access"
    }
    puts "RF-BRAM-CTRL-OK $ctrl"
}

# ---- 3. The FMC pins made it through placement ----------------------------
# The RF half is the only part of this design that reaches the FMC connector.
# If rf.xdc were dropped or its ports renamed, the design still builds -- with
# the phy's differential pairs unconstrained and unrouted -- and the board
# reports an AD9361 that never responds.
set fmc_ports 0
foreach p [get_ports -quiet] {
    if {[string match "rx_*" $p] || [string match "tx_*" $p] ||
        [string match "spi_*" $p] || [string match "rf_gpio*" $p]} {
        incr fmc_ports
    }
}
puts "FMC-PORTS $fmc_ports"
# 12 RX differential pairs + 12 TX differential pairs + spi + gpio + control.
# The exact count is asserted nowhere upstream, so use a floor that a dropped
# constraint file cannot meet but a renamed port could not fake either.
if {$fmc_ports < 30} {
    error "only $fmc_ports FMC-ish top-level ports found -- rf.xdc did not reach this build, and the phy is unconstrained"
}

# ---- 4. data_clk exists as a real clock ------------------------------------
# The RF data path lives on a clock recovered from the AD9361's own reference.
# Everything about the merge's timing story depends on that clock existing;
# without it the paths are unconstrained and every timing number is fiction.
set dclk_nets [get_nets -quiet -hier -filter {NAME =~ *data_clk*}]
puts "DATA-CLK-NETS [llength $dclk_nets]"
if {[llength $dclk_nets] == 0} {
    error "no data_clk net in the netlist -- the RF data path is not clocked"
}

puts "RF-MERGE-OK"

# ---- 5. Every blk_mem_gen still holds real RAMB, after implementation ------
# This is the Enable_32bit_Address trap (0 RAMB, no black box, AXI still
# handshakes, every read returns 0) re-asserted on the routed netlist.  A memory
# can be present and correct in synth_1 and still be worth re-counting here:
# the routed checkpoint is what the bitstream was made from.
#
# Two filters cannot be combined -- no cell is both named `*blk_mem_gen*` and a
# RAMB -- so locate the wrapper, then count RAMB descendants by path prefix.
# tools/check_bd_bram.tcl records the same trap.
set all_rambs [get_cells -quiet -hier -filter {REF_NAME =~ RAMB*}]
puts "ROUTED-TOTAL-RAMB [llength $all_rambs]"

set mem_fails {}
foreach inst {blk_mem_gen_0 blk_mem_gen_1 blk_mem_gen_tx blk_mem_gen_rx} {
    set wraps [get_cells -quiet -hier -filter "REF_NAME =~ *${inst}_0*"]
    if {[llength $wraps] == 0} {
        lappend mem_fails "$inst: wrapper not in the routed netlist"
        puts "ROUTED-BRAM $inst: WRAPPER NOT FOUND"
        continue
    }
    set prefixes {}
    foreach w $wraps { lappend prefixes [get_property NAME $w] }
    set n 0
    foreach r $all_rambs {
        set rn [get_property NAME $r]
        foreach p $prefixes {
            if {[string match "${p}/*" $rn]} { incr n; break }
        }
    }
    puts "ROUTED-BRAM $inst rambs=$n"
    if {$n < 1} {
        lappend mem_fails "$inst: 0 RAMB in the routed netlist - a read from it returns 0 on the board and nothing else reports it"
    }
}
if {[llength $mem_fails] > 0} {
    foreach f $mem_fails { puts "ROUTED-BRAM FAIL: $f" }
    error "MEMORY FAIL ([llength $mem_fails]): a blk_mem_gen did not survive into the bitstream"
}
puts "ROUTED-BRAM-OK"

