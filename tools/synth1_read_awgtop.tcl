# 若未来 module_ref 的 xci 能被正确导入 IP cache(run 脚本生成器不再排除 RTL),
# 本 PRE 注入会与生成器重复读入同模块, 需先移除本文件与 rebuild 中的 PRE 注入。
# 注入 synth_1 run 脚本(STEPS.SYNTH_DESIGN.TCL.PRE), 由 tools/rebuild_bitstream.tcl 设置。
# 原理: awg_top 的 OOC run 存在时, run 脚本生成器会把 IP 归属文件(模块包装器 + RTL)
# 从主综合读入列表排除; 而 2018.3 批处理下模块参考 IP 无法导出到项目 IP cache
# (blockset 挂不进 xci, config_ip_cache -add 静默无效), 主综合便报
# "module 'host_bd_awg_top_0_0' not found"。这里显式读回包装器 + 全部 RTL,
# 让主综合在线(in-context)解析 awg_top 真实逻辑(非黑盒), 实现直接使用主综合网表。
set script_dir [file dirname [file normalize [info script]]]
set root_dir [file normalize [file join $script_dir ..]]
set rtl_dir [file join $root_dir src rtl]
set wrapper [file join $root_dir awg awg.srcs sources_1 bd host_bd ip host_bd_awg_top_0_0 synth host_bd_awg_top_0_0.v]
set coeff_file [file join $rtl_dir rrc_coeffs.vh]
set synth_fileset [get_filesets -quiet sources_1]
if {[llength $synth_fileset] == 0} {
    set synth_fileset [create_fileset -srcset sources_1]
}
if {[llength [get_files -quiet $coeff_file]] == 0} {
    add_files -norecurse -fileset $synth_fileset $coeff_file
}
set_property file_type {Verilog Header} [get_files -quiet $coeff_file]
read_verilog -library xil_defaultlib $wrapper
foreach v [lsort [glob -nocomplain [file join $rtl_dir *.v]]] {
    read_verilog -library xil_defaultlib $v
}
