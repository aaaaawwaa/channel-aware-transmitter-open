# BD 内嵌 blk_mem_gen 门禁 —— 两级, 任一级失败即 error 中止构建。
#
# 背景(2026-09-10, 板上 USER 数据路 + 信道损伤抽头表全部读到 0 的根因):
#   BD 会把 blk_mem_gen 的 BRAM 接口地址端口固定为 32 位(BYTE_ADDRESS),
#   于是生成的 IP generic 里 C_ADDRA_WIDTH=32。若同时 Enable_32bit_Address=false,
#   则 C_ENABLE_32BIT_ADDRESS=0 —— 这一对自相矛盾, blk_mem_gen 会**静默**综合成
#   没有任何存储器件的空网表(0 个 RAMB, 不报黑盒, 不报错), AXI BRAM ctrl 照常
#   握手 ACK, 但读写恒为 0。A/B 实验(bd1_wrapper 顶层综合, 仅此参数不同):
#       Enable_32bit_Address=false -> rambs=0
#       Enable_32bit_Address=true  -> rambs=2
#   修复: create_bd.tcl 两个 blk_mem_gen 均置 Enable_32bit_Address=true;
#   awg_top 的 B 口地址相应改为字节地址(word << 2)。
#
# 一级(秒级, 纯文本, 综合前): 检查生成 wrapper 的 generic 自洽。
#   blk_mem_gen 的 IP 综合模型 blk_mem_gen_v8_4_vhsyn_rfs.vhd 是加密 envelope,
#   无法脱离 IP 框架单独综合, 故一级只能做 generic 一致性检查。
# 二级(综合后, 实现前): 在 synth_1 结果里要求每个 blk_mem_gen 实例下存在真实
#   RAMB 器件, 且不被当作 BlackBox —— 这一级才是"存储器存在"的直接证据。
#
# generic 一律从实例的 generic map(NAME => value)解析: CORE_GENERATION_INFO
# 里的长字符串会被 Vivado 按行拼接, 关键字可能被切开(如 "C" & "_WRITE_DEPTH_B=")。
#
# 运行时消息用 ASCII(仅注释用中文): Vivado 控制台按 GBK 输出, UTF-8 中文会乱码,
# 构建门禁的输出要能被日志/CI 直接读。
#
# 独立运行: vivado2025.cmd ... -source tools/check_bd_bram.tcl
# 由 tools/rebuild_bitstream.tcl 内联调用(置 ::CBB_SOURCED 后 source)。

set ::CBB_ROOT [file normalize [file join [file dirname [file normalize [info script]]] ..]]

proc cbb_clog2 {n} {
    set b 0
    set v 1
    while {$v < $n} { set v [expr {$v * 2}]; incr b }
    return $b
}

proc cbb_mem_wrappers {} {
    set pat [file join $::CBB_ROOT awg awg.srcs sources_1 bd host_bd ip \
                 host_bd_blk_mem_gen_*_0 synth host_bd_blk_mem_gen_*_0.vhd]
    return [lsort [glob -nocomplain $pat]]
}

# 从 generic map 取整数值; 缺失返回 -1
proc cbb_gen {txt p} {
    if {[regexp "${p}\\s*=>\\s*(\[0-9\]+)" $txt -> v]} { return $v }
    if {[regexp "${p}=(\[0-9\]+)" $txt -> v]} { return $v }
    return -1
}

# ---- 一级: generic 自洽(综合前) ----
proc cbb_check_static {} {
    set wrappers [cbb_mem_wrappers]
    if {[llength $wrappers] == 0} {
        error "GATE-1 FAIL: no host_bd_blk_mem_gen_*_0 generated netlist found (BD not regenerated?)"
    }
    set fails {}
    foreach w $wrappers {
        set name [file tail $w]
        set fh [open $w r]
        set txt [read $fh]
        close $fh
        set a32    [cbb_gen $txt C_ENABLE_32BIT_ADDRESS]
        set aw     [cbb_gen $txt C_ADDRA_WIDTH]
        set depA   [cbb_gen $txt C_WRITE_DEPTH_A]
        set depB   [cbb_gen $txt C_WRITE_DEPTH_B]
        set dep    [expr {$depA > $depB ? $depA : $depB}]
        puts "GATE-1 $name: a32=$a32 addra_w=$aw depth=$dep"

        if {$a32 < 0 || $aw < 0 || $dep < 1} {
            lappend fails "$name: generics not parsable (IP not generated?)"
            continue
        }
        if {$a32 != 1} {
            lappend fails "$name: C_ENABLE_32BIT_ADDRESS=$a32 with C_ADDRA_WIDTH=$aw (contradiction -> memory silently elided)"
            continue
        }
        if {$aw != 32} {
            lappend fails "$name: byte-addressed memory needs C_ADDRA_WIDTH=32, got $aw"
            continue
        }
        set need [expr {[cbb_clog2 $dep] + 2}]
        if {$aw < $need} {
            lappend fails "$name: address width $aw cannot cover depth $dep (need >= $need)"
        }
        # 注意: 此处**不能**检查 IP 的 .dcp 是否存在 —— generate_target 只产出
        # synth/*.vhd + .xci + _ooc.xdc, .dcp 要等综合(BD IP 由 synth_1 内联综合)
        # 才出现。"存储器确实存在"由二级门禁在 synth_1 之后直接查 RAMB 器件。
    }
    if {[llength $fails] > 0} {
        foreach f $fails { puts "GATE-1 FAIL: $f" }
        error "GATE-1 FAIL ([llength $fails]): BD blk_mem_gen generics inconsistent or missing - AXI read/write would read back 0 on board"
    }
    puts "GATE-1 OK: [llength $wrappers] BD blk_mem_gen generics consistent"
}

# ---- 二级: synth_1 结果里的真实存储器件(需已 open_run synth_1) ----
proc cbb_check_synth {} {
    set names {}
    foreach w [cbb_mem_wrappers] {
        lappend names [file tail [file dirname [file dirname $w]]]
    }
    if {[llength $names] == 0} { error "GATE-2 FAIL: no BD blk_mem_gen instance names found" }

    set fails {}
    # 注意匹配方式: 综合后 BD 里的实例名是 blk_mem_gen_0, 包装模块名
    # host_bd_blk_mem_gen_0_0 只出现在该实例的 REF_NAME 上, 而 RAMB 器件是它的
    # **后代**(NAME = host_bd_i/blk_mem_gen_0/U0/.../ramloop[0].ram.r/prim_noinit.ram,
    # REF_NAME = RAMB36E1)。所以不能用一个 filter 同时要求"名字含包装名"和
    # "REF_NAME 是 RAMB*" —— 没有任何 cell 同时满足, 会得到假 FAIL。
    # 正确做法: 先定位包装实例, 再用层次名前缀统计其下的 RAMB 后代。
    foreach inst $names {
        set wrapcells [get_cells -hier -quiet -filter "REF_NAME =~ *${inst}*"]
        if {[llength $wrapcells] == 0} {
            lappend fails "$inst: wrapper instance not found in synth_1"
            puts "GATE-2 $inst: wrapper NOT FOUND"
            continue
        }
        set prefixes {}
        foreach w $wrapcells { lappend prefixes [get_property NAME $w] }
        set n 0
        foreach r [get_cells -hier -quiet -filter {REF_NAME =~ RAMB*}] {
            set rn [get_property NAME $r]
            foreach p $prefixes {
                if {[string match "${p}/*" $rn]} { incr n; break }
            }
        }
        puts "GATE-2 $inst: RAMB=$n (root [lindex $prefixes 0], [llength $prefixes] matched cells)"
        if {$n < 1} { lappend fails "$inst: 0 RAMB - memory absent from netlist" }
    }
    foreach c [get_cells -hier -quiet -filter {IS_BLACKBOX == 1}] {
        if {[string match "*blk_mem_gen*" $c]} { lappend fails "left as BlackBox: $c" }
    }
    if {[llength $fails] > 0} {
        foreach f $fails { puts "GATE-2 FAIL: $f" }
        puts "GATE-2 DIAG total_RAMB=[llength [get_cells -hier -quiet -filter {REF_NAME =~ RAMB*}]] blk_mem_cells=[get_cells -hier -quiet -filter {REF_NAME =~ *blk_mem_gen*}]"
        error "GATE-2 FAIL ([llength $fails]): synth_1 has no real memory cells for blk_mem_gen"
    }
    puts "GATE-2 OK: [llength $names] BD blk_mem_gen each contain real RAMB, no blackbox"
}

if {![info exists ::CBB_SOURCED]} {
    cbb_check_static
    puts "CHECK-BD-BRAM-DONE"
}
