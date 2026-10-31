# 完整重建 bitstream: reset_target + generate_target + OOC 综合 + 主综合 + 实现 + 比特流
# 由 scripts/build_bitstream.py 调用(源文件变化检测后)。勿直接手工调用,否则可能
# 只重跑主综合而 OOC 不重跑(awg_top 是 BD 内模块, 主 synth run 不检测其源变化)。
# BD 是派生状态(见 create_bd.tcl 头部): 结构变化(新增 AXI GPIO 等)只有重新执行
# 生成脚本才生效; reset_target/generate_target 只从磁盘已有的 host_bd.bd 重生成输出,
# 不会添加新的 BD 单元。先重跑 create_bd.tcl(内部 open_project + 重建 + close_project)。
set script_dir [file dirname [file normalize [info script]]]
set root_dir [file normalize [file join $script_dir ..]]
set proj_file [file join $root_dir awg awg.xpr]
set create_bd_script [file join $root_dir scripts create_bd.tcl]
set argv [list $proj_file]
set rebuild_mode "full"
if {[info exists ::env(REBUILD_MODE)] && $::env(REBUILD_MODE) ne ""} {
    set rebuild_mode $::env(REBUILD_MODE)
}
if {[lsearch -exact {full prep synth impl restore} $rebuild_mode] < 0} {
    error "REBUILD_MODE must be full, prep, synth, impl, or restore (got '$rebuild_mode')"
}

# Main synthesis is isolated from OOC synthesis so the large Vivado process can
# be released before synthesis and the parent run can be reset cleanly.
if {$rebuild_mode eq "synth"} {
    open_project $proj_file
    set synth1_pre [file join $root_dir tools synth1_read_awgtop.tcl]
    set_property STEPS.SYNTH_DESIGN.TCL.PRE $synth1_pre [get_runs synth_1]
    set impl1_pre [file join $root_dir tools impl1_clock_groups.tcl]
    set_property STEPS.OPT_DESIGN.TCL.PRE $impl1_pre [get_runs impl_1]
    set bitstream_pre [file join $root_dir tools write_bitstream_pre.tcl]
    set_property STEPS.WRITE_BITSTREAM.TCL.PRE $bitstream_pre [get_runs impl_1]
    puts "SYNTH1-PRE-INJECTED"

    reset_run synth_1
    reset_run synth_1
    launch_runs synth_1 -jobs 1
    wait_on_run synth_1
    if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
        error "主综合失败: [get_property STATUS [get_runs synth_1]]"
    }
    puts "SYNTH-OK"

    set ::CBB_SOURCED 1
    source [file join $root_dir tools check_bd_bram.tcl]
    open_run synth_1
    cbb_check_synth
    close_design
    puts "GATE2-OK"
    close_project
    exit 0
}

# Low-memory implementation phase: reuse the completed synth_1 checkpoint but
# run the level-2 gate and implementation in a fresh Vivado process.
if {$rebuild_mode eq "impl"} {
    open_project $proj_file
    set ::CBB_SOURCED 1
    source [file join $root_dir tools check_bd_bram.tcl]
    if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
        error "主综合 checkpoint 不完整: [get_property STATUS [get_runs synth_1]]"
    }
    open_run synth_1
    cbb_check_synth
    close_design
    puts "GATE2-OK"

    set impl1_pre [file join $root_dir tools impl1_clock_groups.tcl]
    set bitstream_pre [file join $root_dir tools write_bitstream_pre.tcl]
    set_property STEPS.OPT_DESIGN.TCL.PRE $impl1_pre [get_runs impl_1]
    set_property STEPS.WRITE_BITSTREAM.TCL.PRE $bitstream_pre [get_runs impl_1]
    reset_run impl_1
    reset_run impl_1
    launch_runs impl_1 -to_step write_bitstream -jobs 1
    wait_on_run impl_1
    if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {
        error "实现/比特流失败: [get_property STATUS [get_runs impl_1]]"
    }
    puts "BITSTREAM-OK"
    close_project
    exit 0
}

# Low-memory OOC recovery phase: restore the module-reference checkpoint in a
# separate process after implementation has completed.
if {$rebuild_mode eq "restore"} {
    open_project $proj_file
    reset_run host_bd_awg_top_0_0_synth_1
    reset_run host_bd_awg_top_0_0_synth_1
    launch_runs host_bd_awg_top_0_0_synth_1 -jobs 1
    wait_on_run host_bd_awg_top_0_0_synth_1
    if {[get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]] ne "synth_design Complete!"} {
        error "OOC 恢复失败: [get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]]"
    }
    puts "OOC-RESTORED"
    close_project
    exit 0
}

if {$rebuild_mode eq "prep"} {
    # The normal flow below performs BD regeneration and OOC synthesis, then
    # exits before main synthesis and implementation in this mode.
    set stop_after_ooc 1
} else {
    set stop_after_ooc 0
    set stop_after_gate2 0
}

source $create_bd_script
puts "BD-REGENERATED"

open_project $proj_file

# BD 的 module_ref(awg_top)在源文件变化后 xci 引用会失效, 必须先重置+重新生成目标
reset_target all [get_files -quiet [file join $root_dir awg awg.srcs sources_1 bd host_bd host_bd.bd]]
generate_target all [get_files -quiet [file join $root_dir awg awg.srcs sources_1 bd host_bd host_bd.bd]]
puts "TARGETS-REGENERATED"

# 一级门禁(秒级): BD 内嵌 blk_mem_gen 的 generic 必须自洽, 否则 IP 会静默综合成
# 无存储器件的空网表(见 tools/check_bd_bram.tcl 头部)。放在 OOC 综合之前,
# 避免白跑几十分钟。二级门禁在 synth_1 之后、impl_1 之前。
set ::CBB_SOURCED 1
source [file join $root_dir tools check_bd_bram.tcl]
cbb_check_static
puts "GATE1-OK"

# create_bd.tcl 删除重建 BD 时会剪掉其 OOC run + BlockSrcs fileset(2018.3 批处理
# generate_target 不会自动重建), 必须显式重建。module_ref 的 xci 无文件引用,
# OOC 源由 blockset 直接携带(synth 包装器定义单元顶层 + 全部 RTL)。
# 注意: blockset 无法挂 xci(add_files 静默忽略/重导入冲突), 故 OOC run 是普通 RTL
# 综合, 不会把 DCP 导出到项目 IP cache(config_ip_cache -add 也无实效); 主综合靠
# ips.enableIPCacheLiteLoad 解析 OOC 黑盒, 缺缓存条目时报 "module 'host_bd_awg_top_0_0'
# not found", 且 launch synth_1 会把该子 run 判过期重置(删 DCP)。解法见下方
# STEPS.SYNTH_DESIGN.TCL.PRE 注入(主综合在线解析 awg_top 真实逻辑)。
if {[llength [get_filesets -quiet host_bd_awg_top_0_0]] == 0} {
    create_fileset -blockset host_bd_awg_top_0_0
}
set_property top {host_bd_awg_top_0_0} [get_filesets host_bd_awg_top_0_0]
set ooc_files [list \
    [file join $root_dir awg awg.srcs sources_1 bd host_bd ip host_bd_awg_top_0_0 synth host_bd_awg_top_0_0.v] \
    [file join $root_dir awg awg.srcs sources_1 bd host_bd ip host_bd_awg_top_0_0 host_bd_awg_top_0_0.xci]]
foreach v [lsort [glob -nocomplain [file join $root_dir src rtl *.v]]] {
    lappend ooc_files $v
}
foreach f $ooc_files {
    if {[lsearch [get_files -quiet -of_objects [get_filesets host_bd_awg_top_0_0]] $f] < 0} {
        add_files -norecurse -fileset host_bd_awg_top_0_0 $f
    }
}
if {[llength [get_runs -quiet host_bd_awg_top_0_0_synth_1]] == 0} {
    create_run host_bd_awg_top_0_0_synth_1 -parent_run synth_1 -flow {Vivado Synthesis 2018} -constrset constrs_1
}
set_property SRCSET {host_bd_awg_top_0_0} [get_runs host_bd_awg_top_0_0_synth_1]
set_property CONSTRSET {host_bd_awg_top_0_0} [get_runs host_bd_awg_top_0_0_synth_1]
puts "OOC-RUN-CREATED"

reset_run host_bd_awg_top_0_0_synth_1
reset_run host_bd_awg_top_0_0_synth_1
launch_runs host_bd_awg_top_0_0_synth_1 -jobs 1
wait_on_run host_bd_awg_top_0_0_synth_1
if {[get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]] ne "synth_design Complete!"} {
    error "OOC 综合失败: [get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]]"
}
puts "OOC-OK"

if {$stop_after_ooc} {
    close_project
    puts "PREP-OK"
    exit 0
}

# Main synthesis continues inline for the normal full flow. The low-memory
# path runs the same operations with REBUILD_MODE=synth in a fresh process.

# IP cache DCP, 需 PRE 注入显式读回包装器 + RTL(见 synth1_read_awgtop.tcl 头部)。
set synth1_pre [file join $root_dir tools synth1_read_awgtop.tcl]
set_property STEPS.SYNTH_DESIGN.TCL.PRE $synth1_pre [get_runs synth_1]
set impl1_pre [file join $root_dir tools impl1_clock_groups.tcl]
set_property STEPS.OPT_DESIGN.TCL.PRE $impl1_pre [get_runs impl_1]
set bitstream_pre [file join $root_dir tools write_bitstream_pre.tcl]
set_property STEPS.WRITE_BITSTREAM.TCL.PRE $bitstream_pre [get_runs impl_1]
puts "SYNTH1-PRE-INJECTED"

reset_run synth_1
reset_run synth_1
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "主综合失败: [get_property STATUS [get_runs synth_1]]"
}
puts "SYNTH-OK"

# 二级门禁: 真正确认存储器存在于网表中(每实例下有 RAMB 且无黑盒残留)。
# 必须在 impl_1 之前 —— 不通过就不要浪费实现+比特流的时间。
open_run synth_1
cbb_check_synth
close_design
puts "GATE2-OK"

if {$stop_after_gate2} {
    close_project
    puts "PREP-OK"
    exit 0
}

launch_runs impl_1 -to_step write_bitstream -jobs 1
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {
    error "实现/比特流失败: [get_property STATUS [get_runs impl_1]]"
}
puts "BITSTREAM-OK"

# launch synth_1/impl_1 会把无 IP cache 条目的 OOC 子 run 判过期重置(删 DCP),
# 结尾重跑一次恢复 DCP(build_bitstream.py 把 OOC dcp 存在作为完整性判据之一)。
reset_run host_bd_awg_top_0_0_synth_1
reset_run host_bd_awg_top_0_0_synth_1
launch_runs host_bd_awg_top_0_0_synth_1 -jobs 1
wait_on_run host_bd_awg_top_0_0_synth_1
if {[get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]] ne "synth_design Complete!"} {
    error "OOC 恢复失败: [get_property STATUS [get_runs host_bd_awg_top_0_0_synth_1]]"
}
puts "OOC-RESTORED"

close_project
