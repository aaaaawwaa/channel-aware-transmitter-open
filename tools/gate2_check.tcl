# 单独复核 BRAM 门禁二级 —— 对**已有的** synth_1 checkpoint 只读运行, 不重跑综合/实现。
#
# 用途(2026-09-23): 构建被内存/超时掐断时, 磁盘上留着 synth_1 结果而没人回头跑门禁,
# 半成品 .bit 就很容易被当成"构建成功"拿去上板。这个脚本就是补那一次校验。
#
# 用法:
#   <LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat -mode batch -notrace -nolog \
#     -nojournal -source tools/gate2_check.tcl
# 见到 "GATE-2 OK" 才算存储器真的在网表里; 见 "GATE-2 FAIL" 先清 IP cache 再 --force 重建。
set root [file normalize [file join [file dirname [file normalize [info script]]] ..]]
set dcp [file join $root awg awg.runs synth_1 host_bd_wrapper.dcp]
if {![file exists $dcp]} {
    error "GATE2 PROBE FAIL: $dcp not found (synth_1 has not produced a checkpoint)"
}
puts "GATE2-PROBE loading $dcp"
open_checkpoint $dcp
set ::CBB_SOURCED 1
source [file join $root tools check_bd_bram.tcl]
cbb_check_synth
puts "GATE2-OK"
