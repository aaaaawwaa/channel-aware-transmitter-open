# Create host_bd: PS7 (UART1 MIO48/49 @115200, DDR, FCLK0 50MHz, M_AXI_GP0)
# + 13x dual/single 32-bit AXI GPIO (incl. axi_gpio_12 channel-impair control),
#   dual-bank AXI BRAM (axi_bram_ctrl_0) + channel tap-table BRAM
#   (axi_bram_ctrl_1) and awg_top (BD module).
# Regenerates the BD from scratch (delete + recreate) — safe because the BD
# is derived state and awg_top's port list is the source of truth.
# Usage: vivado -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
set proj_file [lindex $argv 0]
set script_dir [file dirname [file normalize [info script]]]

open_project $proj_file
source $script_dir/ps7_props.tcl

# Keep module-reference discovery in the project source fileset. A failed OOC
# rebuild can leave its block fileset holding RTL sources, which hides awg_top
# from create_bd_cell on the next regeneration.
if {[llength [get_filesets -quiet host_bd_awg_top_0_0]] > 0} {
    delete_fileset [get_filesets host_bd_awg_top_0_0]
}
set rtl_dir [file normalize [file join $script_dir .. src rtl]]
foreach rtl_file [lsort [glob -nocomplain [file join $rtl_dir *.v]]] {
    if {[llength [get_files -quiet $rtl_file]] == 0} {
        add_files -norecurse -fileset sources_1 $rtl_file
    }
}
update_compile_order -fileset sources_1

# ---- 显示/触摸通路的 RTL(合流后由本设计一并构建) ----
# 这几个文件**留在 `pynq_overlay/rtl/`**: 那份独立工程(显示/触摸人格, 见 P0-P4)自己也要
# 构建它们, 搬进来就会有两处副本、必然漂移。本设计按显式路径引用同一份源 ——
# ⚠ 改动这些文件必须触发重建, 所以 `scripts/build_bitstream.py` 的哈希集合里也加了
# `pynq_overlay/rtl/*.v`(否则就是"改了 RTL 却拿到旧位流"那类老坑)。
set lcd_rtl_dir [file normalize [file join $script_dir .. pynq_overlay rtl]]
foreach rtl_file [lsort [glob -nocomplain [file join $lcd_rtl_dir *.v]]] {
    if {[llength [get_files -quiet $rtl_file]] == 0} {
        add_files -norecurse -fileset sources_1 $rtl_file
    }
}
update_compile_order -fileset sources_1

# ---- AD9361 射频通路的 RTL + 引脚约束(合流后由本设计一并构建) ----
# 与上面 LCD 同一条理: 文件**留在 `rf_overlay/`**(那份独立工程自己也要构建), 本设计按
# 显式路径引用同一份源; 搬进来就会有两处副本、必然漂移。
#
# ⚠ `sine_rom.v` **故意跳过**: 它与 `src/rtl/sine_rom.v` 模块重名, 两份目前逐字节相同,
# 而本设计已经有后者(`awg_top` 也用)。两个同名模块同处一个 fileset 不报错, 综合器挑哪份
# 都行 —— 那正是"改了一份跑了另一份"最难查的形态。两份是否仍然一致由 `host/tests/
# test_rf_merge.py` 盯着。
set rf_rtl_dir [file normalize [file join $script_dir .. rf_overlay rtl]]
foreach rtl_file [lsort [glob -nocomplain [file join $rf_rtl_dir *.v]]] {
    if {[file tail $rtl_file] eq "sine_rom.v"} { continue }
    if {[llength [get_files -quiet $rtl_file]] == 0} {
        add_files -norecurse -fileset sources_1 $rtl_file
    }
}
update_compile_order -fileset sources_1
# FMC 引脚约束也复用独立那份, 不复制到 src/constraints —— 复制就是两份引脚表, 迟早对不上。
set rf_xdc [file normalize [file join $script_dir .. rf_overlay constraints rf.xdc]]
if {[llength [get_files -quiet $rf_xdc]] == 0} {
    add_files -norecurse -fileset constrs_1 $rf_xdc
}
set coeff_file [file join $rtl_dir rrc_coeffs.vh]
if {[llength [get_files -quiet $coeff_file]] == 0} {
    add_files -norecurse -fileset sources_1 $coeff_file
}
set_property file_type {Verilog Header} [get_files -quiet $coeff_file]

# --- remove existing BD + wrapper (regenerate from scratch) ---
if {[get_files -quiet host_bd.bd] ne ""} {
    set bd_dir [file dirname [get_files host_bd.bd]]
    remove_files host_bd.bd
    file delete -force $bd_dir
}
if {[get_files -quiet host_bd_wrapper.v] ne ""} {
    remove_files host_bd_wrapper.v
}

create_bd_design host_bd

# ---- PS7 ----
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
set_property -dict $ps7_props $ps7
# Vivado 2018.3 may recompute HIGHADDR when BASEADDR is applied; restore the
# complete 1-GiB DDR address window after all PS7 properties are set.
set_property CONFIG.PCW_DDR_RAM_HIGHADDR 0x3FFFFFFF $ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable"} \
    [get_bd_cells ps7_0]

# ---- 6x AXI GPIO (dual 32-bit all-output) ----
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2 axi_gpio_3 axi_gpio_4 axi_gpio_5} {
    set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $ip_name]
    set_property -dict [list \
        CONFIG.C_GPIO_WIDTH {32} \
        CONFIG.C_ALL_OUTPUTS {1} \
        CONFIG.C_IS_DUAL {1} \
        CONFIG.C_GPIO2_WIDTH {32} \
        CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio
}
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_6]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_IS_DUAL {0}] $agpio
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_7]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio
set stream_enable_slice [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 stream_enable_slice]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {0} CONFIG.DIN_TO {0} CONFIG.DOUT_WIDTH {1}] $stream_enable_slice
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_8]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $agpio
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_9]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_10]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $agpio
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_11]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $agpio
# ---- 信道损伤: CH1/CH2 控制字 GPIO (dual 32-bit all-output) ----
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_12]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio
set dma_benchmark_enable_slice [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 dma_benchmark_enable_slice]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {31} CONFIG.DIN_TO {31} CONFIG.DOUT_WIDTH {1}] $dma_benchmark_enable_slice

# ---- dual-bank user BRAM (2 banks x 512 bytes, 32-bit x 256 words) ----
# AXI BRAM Controller 4.1 requires a minimum 4K address aperture; software uses
# only the first 1K bytes for the two banks.
#
# ⚠ Enable_32bit_Address 必须为 true: BD 会把 BRAM 接口的地址端口固定成 32 位
# (BYTE_ADDRESS), 若此参数为 false, 生成的 IP generic 会自相矛盾
# (C_ADDRA_WIDTH=32 但 C_ENABLE_32BIT_ADDRESS=0) → blk_mem_gen 静默综合成
# 无存储器件的空网表(0 个 RAMB, 无黑盒告警, AXI 照常握手但读写全为 0)。
# 字节寻址下 A 口(AXI BRAM ctrl 输出字节地址)天然一致; B 口由 awg_top 给出
# 字节地址(word << 2)。
set bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 axi_bram_ctrl_0]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1}] $bram_ctrl
set bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 blk_mem_gen_0]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
    CONFIG.Write_Depth_A {256} CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Enable_B {Use_ENB_Pin} CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Byte_Size {8} CONFIG.Enable_32bit_Address {true}] $bram

# ---- 信道损伤抽头表 BRAM (32-bit x 16 words, True Dual Port) ----
# PS writes port A via axi_bram_ctrl_1; awg_top reads port B from sys_clk.
set tap_bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 axi_bram_ctrl_1]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1}] $tap_bram_ctrl
set tap_bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 blk_mem_gen_1]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
    CONFIG.Write_Depth_A {16} CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Enable_B {Use_ENB_Pin} CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Enable_32bit_Address {true}] $tap_bram

# ---- awg_top as BD module ----
set awg [create_bd_cell -type module -reference awg_top awg_top_0]

# LED: 合流后由 **PS 侧**(axi_gpio_led)驱动, 所以 awg_top_0/led 悬空不接。
# awg_top 自己的指示是 `led[1:0]=CH1 波形 / led[7:2]=频率kHz[9:4]`, 这个语义**不丢** ——
# 守护进程会用同一份状态把它算出来再写 LED(见 `pynq_overlay/sw/pynq_status.py` 的 LED 模式),
# 于是既保住这个指示、又保住 Linux 侧的安全灯(心跳/写卡/可断电)。两者抢同一组脚(P20..T17),
# 硬件上只能有一个驱动者, 所以选软件这一侧。
create_bd_port -dir I -type clk sys_clk
create_bd_port -dir O -from 7 -to 0 led
create_bd_port -dir O -from 7 -to 0 dac0_db
create_bd_port -dir O -from 7 -to 0 dac1_db
create_bd_port -dir O daclk0
create_bd_port -dir O daclk1

connect_bd_net [get_bd_pins awg_top_0/sys_clk] [get_bd_ports sys_clk]
connect_bd_net [get_bd_pins awg_top_0/dac0_db] [get_bd_ports dac0_db]
connect_bd_net [get_bd_pins awg_top_0/dac1_db] [get_bd_ports dac1_db]
connect_bd_net [get_bd_pins awg_top_0/daclk0]  [get_bd_ports daclk0]
connect_bd_net [get_bd_pins awg_top_0/daclk1]  [get_bd_ports daclk1]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]  [get_bd_pins awg_top_0/status_clk]

# AXI GPIO outputs -> awg_top params (internal)
connect_bd_net [get_bd_pins axi_gpio_0/gpio_io_o]   [get_bd_pins awg_top_0/gpio0_in]
connect_bd_net [get_bd_pins axi_gpio_0/gpio2_io_o]  [get_bd_pins awg_top_0/gpio1_in]
connect_bd_net [get_bd_pins axi_gpio_1/gpio_io_o]   [get_bd_pins awg_top_0/gpio2_in]
connect_bd_net [get_bd_pins axi_gpio_1/gpio2_io_o]  [get_bd_pins awg_top_0/gpio3_in]
connect_bd_net [get_bd_pins axi_gpio_2/gpio_io_o]   [get_bd_pins awg_top_0/gpio4_in]
connect_bd_net [get_bd_pins axi_gpio_2/gpio2_io_o]  [get_bd_pins awg_top_0/gpio5_in]
connect_bd_net [get_bd_pins axi_gpio_3/gpio_io_o]   [get_bd_pins awg_top_0/gpio6_in]
connect_bd_net [get_bd_pins axi_gpio_3/gpio2_io_o]  [get_bd_pins awg_top_0/gpio7_in]
connect_bd_net [get_bd_pins axi_gpio_4/gpio_io_o]   [get_bd_pins awg_top_0/gpio8_in]
connect_bd_net [get_bd_pins axi_gpio_4/gpio2_io_o]  [get_bd_pins awg_top_0/gpio9_in]
connect_bd_net [get_bd_pins axi_gpio_5/gpio_io_o]   [get_bd_pins awg_top_0/gpio10_in]
connect_bd_net [get_bd_pins axi_gpio_5/gpio2_io_o]  [get_bd_pins awg_top_0/gpio11_in]
connect_bd_net [get_bd_pins axi_gpio_6/gpio_io_o]   [get_bd_pins awg_top_0/gpio12_in]
connect_bd_net [get_bd_pins axi_gpio_7/gpio_io_o]   [get_bd_pins awg_top_0/gpio13_in]
connect_bd_net [get_bd_pins axi_gpio_7/gpio2_io_o]  [get_bd_pins awg_top_0/user_stream_length]
connect_bd_net [get_bd_pins axi_gpio_7/gpio2_io_o]  [get_bd_pins stream_enable_slice/Din]
connect_bd_net [get_bd_pins axi_gpio_7/gpio2_io_o]  [get_bd_pins dma_benchmark_enable_slice/Din]
connect_bd_net [get_bd_pins stream_enable_slice/dout] [get_bd_pins awg_top_0/user_stream_enable]
connect_bd_net [get_bd_pins awg_top_0/gpio14_out]    [get_bd_pins axi_gpio_8/gpio_io_i]
connect_bd_net [get_bd_pins axi_gpio_9/gpio_io_o]     [get_bd_pins awg_top_0/play_ctrl_in]
connect_bd_net [get_bd_pins axi_gpio_9/gpio2_io_o]    [get_bd_pins awg_top_0/play_cfg_in]
connect_bd_net [get_bd_pins awg_top_0/play_status_out] [get_bd_pins axi_gpio_10/gpio_io_i]

# AXI BRAM controller owns port A; awg_top reads port B in sys_clk domain.
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_0/BRAM_PORTA] \
    [get_bd_intf_pins blk_mem_gen_0/BRAM_PORTA]
connect_bd_net [get_bd_pins awg_top_0/bram_b_clk]  [get_bd_pins blk_mem_gen_0/clkb]
connect_bd_net [get_bd_pins awg_top_0/bram_b_en]   [get_bd_pins blk_mem_gen_0/enb]
connect_bd_net [get_bd_pins awg_top_0/bram_b_addr] [get_bd_pins blk_mem_gen_0/addrb]
connect_bd_net [get_bd_pins blk_mem_gen_0/doutb]    [get_bd_pins awg_top_0/bram_b_rdata]

# Port B is read-only from awg_top.
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {32} CONFIG.CONST_VAL {0}] $vcc
connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins blk_mem_gen_0/dinb]
set web_zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_web_zero]
set_property -dict [list CONFIG.CONST_WIDTH {4} CONFIG.CONST_VAL {0}] $web_zero
connect_bd_net [get_bd_pins const_web_zero/dout] [get_bd_pins blk_mem_gen_0/web]

# ---- 信道损伤: 控制字 GPIO -> awg_top, 抽头表 BRAM B 口 ----
connect_bd_net [get_bd_pins axi_gpio_12/gpio_io_o]  [get_bd_pins awg_top_0/ch1_chan_word]
connect_bd_net [get_bd_pins axi_gpio_12/gpio2_io_o] [get_bd_pins awg_top_0/ch2_chan_word]
# AXI BRAM controller owns port A; awg_top reads port B in sys_clk domain.
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_1/BRAM_PORTA] \
    [get_bd_intf_pins blk_mem_gen_1/BRAM_PORTA]
connect_bd_net [get_bd_pins awg_top_0/bram_b_clk]  [get_bd_pins blk_mem_gen_1/clkb]
connect_bd_net [get_bd_pins awg_top_0/tap_b_en]    [get_bd_pins blk_mem_gen_1/enb]
connect_bd_net [get_bd_pins awg_top_0/tap_b_addr]  [get_bd_pins blk_mem_gen_1/addrb]
connect_bd_net [get_bd_pins blk_mem_gen_1/doutb]   [get_bd_pins awg_top_0/tap_b_rdata]
# Port B is read-only from awg_top (shared zero constants with the user BRAM).
connect_bd_net [get_bd_pins const_zero/dout]     [get_bd_pins blk_mem_gen_1/dinb]
connect_bd_net [get_bd_pins const_web_zero/dout] [get_bd_pins blk_mem_gen_1/web]

# ---- M1: AXI DMA MM2S (simple/no-SG) -> async AXIS FIFO -> awg_top USER stream ----
# DMA control (S_AXI_LITE) rides the existing GP0 interconnect (M12); its memory
# master (M_AXI_MM2S, 32-bit) is up-sized into PS S_AXI_HP0 via smartconnect 1.0.
# The MM2S AXI-Stream output is in the s_axi_lite clock domain (FCLK0), so the
# axis_data_fifo S side is FCLK0; M side is the external sys_clk that awg_top
# samples. IS_ACLK_ASYNC=1 makes the FIFO synchronise the reset internally.
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list \
    CONFIG.c_include_mm2s {1} \
    CONFIG.c_include_s2mm {0} \
    CONFIG.c_include_sg {0} \
    CONFIG.c_m_axi_mm2s_data_width {32} \
    CONFIG.c_sg_length_width {23}] $dma
set sc_hp0 [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_dma_hp0]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc_hp0
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins sc_dma_hp0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_dma_hp0/M00_AXI]   [get_bd_intf_pins ps7_0/S_AXI_HP0]

set fifo [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo:2.0 axis_data_fifo_0]
set_property -dict [list \
    CONFIG.IS_ACLK_ASYNC {1} \
    CONFIG.FIFO_DEPTH {4096} \
    CONFIG.TDATA_NUM_BYTES {4} \
    CONFIG.HAS_TKEEP {1} \
    CONFIG.HAS_TLAST {1} \
    CONFIG.HAS_RD_DATA_COUNT {1}] $fifo
set dma_benchmark_sink [create_bd_cell -type module -reference axis_dma_benchmark_sink axis_dma_benchmark_sink_0]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/bench_first_valid_cycle] [get_bd_pins axi_gpio_8/gpio2_io_i]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/bench_fifo_watermark] [get_bd_pins axi_gpio_10/gpio2_io_i]
connect_bd_net [get_bd_pins dma_benchmark_enable_slice/dout] [get_bd_pins axis_dma_benchmark_sink_0/benchmark_enable]
connect_bd_net [get_bd_pins axi_gpio_7/gpio2_io_o] [get_bd_pins axis_dma_benchmark_sink_0/stream_control]
connect_bd_net [get_bd_pins awg_top_0/user_stream_underflow] [get_bd_pins axis_dma_benchmark_sink_0/stream_underflow]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/bench_status] [get_bd_pins axi_gpio_11/gpio_io_i]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/bench_checksum] [get_bd_pins axi_gpio_11/gpio2_io_i]
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins axis_dma_benchmark_sink_0/clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axis_dma_benchmark_sink_0/status_clk]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins axis_data_fifo_0/S_AXIS]
connect_bd_net [get_bd_pins axis_data_fifo_0/m_axis_tdata]  [get_bd_pins axis_dma_benchmark_sink_0/s_axis_tdata]
set fifo_rd_count_slice [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 fifo_rd_count_slice]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {12} CONFIG.DIN_TO {0} CONFIG.DOUT_WIDTH {13}] $fifo_rd_count_slice
connect_bd_net [get_bd_pins axis_data_fifo_0/axis_rd_data_count] [get_bd_pins fifo_rd_count_slice/Din]
connect_bd_net [get_bd_pins fifo_rd_count_slice/dout] [get_bd_pins axis_dma_benchmark_sink_0/fifo_rd_count]
connect_bd_net [get_bd_pins axis_data_fifo_0/m_axis_tkeep]  [get_bd_pins axis_dma_benchmark_sink_0/s_axis_tkeep]
connect_bd_net [get_bd_pins axis_data_fifo_0/m_axis_tvalid] [get_bd_pins axis_dma_benchmark_sink_0/s_axis_tvalid]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/s_axis_tready] [get_bd_pins axis_data_fifo_0/m_axis_tready]
connect_bd_net [get_bd_pins axis_data_fifo_0/m_axis_tlast]  [get_bd_pins axis_dma_benchmark_sink_0/s_axis_tlast]
# Normal mode forwards to awg_top; benchmark mode consumes with no backpressure.
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/m_axis_tdata]  [get_bd_pins awg_top_0/user_stream_tdata]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/m_axis_tkeep]  [get_bd_pins awg_top_0/user_stream_tkeep]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/m_axis_tvalid] [get_bd_pins awg_top_0/user_stream_tvalid]
connect_bd_net [get_bd_pins awg_top_0/user_stream_tready]            [get_bd_pins axis_dma_benchmark_sink_0/m_axis_tready]
connect_bd_net [get_bd_pins axis_dma_benchmark_sink_0/m_axis_tlast]  [get_bd_pins awg_top_0/user_stream_tlast]

# ===========================================================================
# 显示 / 触摸(从 pynq_overlay/bd/create_bd.tcl 搬来, 只把 HP0 换成 HP1)
# ===========================================================================

# ---- PL LEDs(8 位全输出, 高有效): 见上面 led 端口的说明 —— 合流后 LED 归 PS 侧 ----
set led_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_led]
set_property -dict [list CONFIG.C_GPIO_WIDTH {8} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $led_gpio
connect_bd_net [get_bd_pins axi_gpio_led/gpio_io_o] [get_bd_ports led]

# ---- 用户按键 S0/S1(2 位全输入, 板载按下为低): S0 长按 3 秒优雅关机(安全功能) ----
set btn_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_btn]
set_property -dict [list CONFIG.C_GPIO_WIDTH {2} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $btn_gpio
create_bd_port -dir I -from 1 -to 0 btn
connect_bd_net [get_bd_pins axi_gpio_btn/gpio_io_i] [get_bd_ports btn]

# ---- 帧缓冲控制: 32 位 DDR 基址 + 2 位控制字 ----
# 通道1 放读取器要走的物理地址; 通道2 的 bit0 在彩条/帧缓冲之间选源, bit1 驱动触摸复位
# (TP_RST = PL 脚 R15)。两条放同一个孔径里, 软件"先写基址再使能"就没有额外的时序风险。
set fb_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_fb]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {2} CONFIG.C_ALL_OUTPUTS_2 {1}] $fb_gpio

# ---- 帧缓冲自检抽头: 64 位只读调试总线(通道1=读取侧, 通道2=显示侧) ----
# 只读是故意的: 这里任何东西都不许扰动像素通路, 它只负责报告。
set dbg_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_dbg]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $dbg_gpio
# 第二个只读单元放逐行像素和签名(AXI GPIO 一个单元只带两个 32 位通道, 上面那个已经用满)。
set dbg2_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_dbg2]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $dbg2_gpio

# ---- 帧缓冲读主口 -> HP1: AXI4 -> AXI3 且要拆突发 ----
# S_AXI_HPx 在 Zynq-7000 上是 AXI3, AXI4 主口不能直连(BD 41-1285); 而且该口把 INCR 突发
# 限在 16 拍, 行读取器却发 200 拍。SmartConnect 两件事一起做(与 AWG 的 DMA 上 HP0 同款)。
set sc_fb [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_fb_hp1]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc_fb
connect_bd_intf_net [get_bd_intf_pins sc_fb_hp1/M00_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP1]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins sc_fb_hp1/aclk]

# ---- LCD 控制器(BD module): 输入吃 sys_clk(= Y9, 上面那个端口), 内部 MMCM 出 33.333MHz ----
# 端口**不带 -freq_hz**: 那会让 BD 自己生成 create_clock, 与 awg.xdc 里那条撞车
# (pynq_overlay 的注释里记着这个坑)。
set lcd [create_bd_cell -type module -reference lcd_overlay_top lcd_0]

create_bd_port -dir O lcd_pclk
create_bd_port -dir O lcd_de
create_bd_port -dir O lcd_hs
create_bd_port -dir O lcd_vs
create_bd_port -dir O lcd_bl
create_bd_port -dir O -from 15 -to 0 lcd_rgb
create_bd_port -dir O tp_rst

connect_bd_net [get_bd_ports sys_clk]       [get_bd_pins lcd_0/clk50m]
connect_bd_net [get_bd_pins lcd_0/lcd_pclk] [get_bd_ports lcd_pclk]
connect_bd_net [get_bd_pins lcd_0/lcd_de]   [get_bd_ports lcd_de]
connect_bd_net [get_bd_pins lcd_0/lcd_hs]   [get_bd_ports lcd_hs]
connect_bd_net [get_bd_pins lcd_0/lcd_vs]   [get_bd_ports lcd_vs]
connect_bd_net [get_bd_pins lcd_0/lcd_bl]   [get_bd_ports lcd_bl]
connect_bd_net [get_bd_pins lcd_0/lcd_rgb]  [get_bd_ports lcd_rgb]

connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]            [get_bd_pins lcd_0/aclk]
connect_bd_net [get_bd_pins axi_gpio_fb/gpio_io_o]      [get_bd_pins lcd_0/fb_base]
# 一条 2 位控制字整条进 RTL, 由 lcd_overlay_top 拆成 fb_en / tp_rst —— BD 的向量引脚不能
# 按位引用(get_bd_pins cell/pin[0] 匹配不到), 所以拆位放在 RTL 里做。
connect_bd_net [get_bd_pins axi_gpio_fb/gpio2_io_o]     [get_bd_pins lcd_0/fb_ctrl]
connect_bd_net [get_bd_pins lcd_0/tp_rst]               [get_bd_ports tp_rst]
connect_bd_net [get_bd_pins axi_gpio_dbg/gpio_io_i]     [get_bd_pins lcd_0/dbg0]
connect_bd_net [get_bd_pins axi_gpio_dbg/gpio2_io_i]    [get_bd_pins lcd_0/dbg1]
connect_bd_net [get_bd_pins axi_gpio_dbg2/gpio_io_i]    [get_bd_pins lcd_0/dbg2]
connect_bd_net [get_bd_pins axi_gpio_dbg2/gpio2_io_i]   [get_bd_pins lcd_0/dbg3]
connect_bd_intf_net [get_bd_intf_pins lcd_0/M_AXI]      [get_bd_intf_pins sc_fb_hp1/S00_AXI]

# ===========================================================================
# AD9361 / AD-FMCOMMS3-EBZ 射频通路(从 rf_overlay/bd/create_bd.tcl 搬来)
#
# 与那份独立设计的差别只有三处, 其余逐字相同:
#   1) PS7 用本设计已有的 `ps7_0` —— 它的 FCLK1 已在 ps7_props.tcl 里开到 200MHz
#      (IDELAYCTRL 的 REFCLK, 理由见那里的注释);
#   2) reset 复用本设计的 `rst_ps7`, 不再建第二个 proc_sys_reset(两个复位源驱动
#      同一组 AXI 从设备的 aresetn 是接不上的);
#   3) AXI 从设备挂在同一个 `axi_interconnect_0` 上(M21..M25), 不另起 interconnect ——
#      M_AXI_GP0 只有一条主口, 两个 interconnect 抢同一个主口是接不上的。
#
# ⚠ 与 AWG 那半边**共用 DATA_CLK 域之外的时钟**: AD9361 的整个数据面跑在它自己恢复
#   出来的 data_clk 上(rf.xdc 在 rx_clk_in_p 上 create_clock), 与 FCLK0/sys_clk 异步
#   —— `tools/impl1_clock_groups.tcl` 里那三个时钟组就是为这条准备的。
# ===========================================================================

# ---- AXI-Lite 控制面寄存器(例程 regs 0-3: idelay_en/tap, phase_inc, phy_mode) ----
set axi_ctrl [create_bd_cell -type module -reference axi_ctrl_v1_0 axi_ctrl_0]

# ---- AD9361 控制脚: 9 位输出, 一个孔径内原子写 ----
# resetb/enable/txnrx/sync_in/en_agc 是模式脚; ctl0-3 是例程的备用 GPIO_CTL(那里全拉低)。
# 九根落在同一个 9 位输出上, 软件才能一次写完而不是一根一根改。
#   bit0 txnrx  bit1 enable  bit2 resetb  bit3 sync  bit4 en_agc  bits8:5 ctl0-3
set gpio_ctl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_ctrl]
set_property -dict [list CONFIG.C_GPIO_WIDTH {9} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $gpio_ctl
create_bd_port -dir O -from 8 -to 0 rf_gpio
connect_bd_net [get_bd_pins axi_gpio_ctrl/gpio_io_o] [get_bd_ports rf_gpio]

# ---- PL SPI master(AD9361 控制口; 本镜像里 PS 的 SPI0 是死的) ----
set spi [create_bd_cell -type module -reference pl_spi_master pl_spi_0]
create_bd_port -dir O spi_sclk
create_bd_port -dir O spi_mosi
create_bd_port -dir I spi_miso
create_bd_port -dir O spi_csn
connect_bd_net [get_bd_pins pl_spi_0/spi_sclk] [get_bd_ports spi_sclk]
connect_bd_net [get_bd_pins pl_spi_0/spi_mosi] [get_bd_ports spi_mosi]
connect_bd_net [get_bd_pins pl_spi_0/spi_miso] [get_bd_ports spi_miso]
connect_bd_net [get_bd_pins pl_spi_0/spi_csn]  [get_bd_ports spi_csn]

# ---- AD9361 数据通路 ----
set phy [create_bd_cell -type module -reference ad9361_phy ad9361_phy_0]

# RX 时钟 + 数据(Bank 34, LVDS_25)。_n 那一半也必须是端口: IBUFDS 要的是真差分对,
# 不是单端输入。
create_bd_port -dir I rx_clk_in_p
create_bd_port -dir I rx_clk_in_n
create_bd_port -dir I rx_frame_in_p
create_bd_port -dir I rx_frame_in_n
create_bd_port -dir I -from 5 -to 0 rx_data_in_p
create_bd_port -dir I -from 5 -to 0 rx_data_in_n
connect_bd_net [get_bd_pins ad9361_phy_0/rx_clk_in_p]   [get_bd_ports rx_clk_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_clk_in_n]   [get_bd_ports rx_clk_in_n]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_frame_in_p] [get_bd_ports rx_frame_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_frame_in_n] [get_bd_ports rx_frame_in_n]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_data_in_p]  [get_bd_ports rx_data_in_p]
connect_bd_net [get_bd_pins ad9361_phy_0/rx_data_in_n]  [get_bd_ports rx_data_in_n]

# TX 时钟 + 数据(Bank 34, LVDS_25)
create_bd_port -dir O tx_clk_out_p
create_bd_port -dir O tx_clk_out_n
create_bd_port -dir O tx_frame_out_p
create_bd_port -dir O tx_frame_out_n
create_bd_port -dir O -from 5 -to 0 tx_data_out_p
create_bd_port -dir O -from 5 -to 0 tx_data_out_n
connect_bd_net [get_bd_pins ad9361_phy_0/tx_clk_out_p]   [get_bd_ports tx_clk_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_clk_out_n]   [get_bd_ports tx_clk_out_n]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_frame_out_p] [get_bd_ports tx_frame_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_frame_out_n] [get_bd_ports tx_frame_out_n]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_data_out_p]  [get_bd_ports tx_data_out_p]
connect_bd_net [get_bd_pins ad9361_phy_0/tx_data_out_n]  [get_bd_ports tx_data_out_n]

# ---- TX 样本源: DDS 单音或真实缓冲 ----
set txsrc [create_bd_cell -type module -reference rf_tx_source rf_tx_source_0]

# ---- RX 采集单元 ----
set rxcaps [create_bd_cell -type module -reference rf_sample_capture rf_sample_capture_0]

# ---- 两个样本缓冲 ----
# 两个方向各一个, 参数只写一次 —— 它们是承重的, 不许在两个实例间漂移。
#
# ⚠ Enable_32bit_Address = true 不是偏好: BD 把 BRAM 接口地址端口固定成 32 位
# (BYTE_ADDRESS), 生成的 IP 恒有 C_ADDRA_WIDTH=32; 此参数为 false 时还带上
# C_ENABLE_32BIT_ADDRESS=0, 这对矛盾让 blk_mem_gen **静默**综合成没有存储器件的空网表
# (0 个 RAMB, 无黑盒, 不报错, AXI 照常握手, 读回全 0)。单独 create_ip 同参数是正常的,
# 所以这个坑只在 **BD 内嵌** 实例上出现 —— 见仓库根 CLAUDE.md 里那段 A/B 实测。
#
# 端口侧的后果: A 口是字节地址(AXI BRAM ctrl 输出的就是), 所以 B 口也必须是 ——
# rf_sample_reader 给的是 word << 2。
#
# SINGLE_PORT_BRAM 1 才是把 B 口让给 PL 的关键; 为 0 时控制器把两个口都占了,
# PL 既读不到也写不进。
proc rf_make_bram {ctrl_name mem_name depth} {
    set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 $ctrl_name]
    set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1}] $ctrl
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 $mem_name]
    set_property -dict [list \
        CONFIG.Memory_Type {True_Dual_Port_RAM} \
        CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} \
        CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
        CONFIG.Write_Depth_A $depth CONFIG.Enable_A {Use_ENA_Pin} \
        CONFIG.Enable_B {Use_ENB_Pin} CONFIG.Use_Byte_Write_Enable {true} \
        CONFIG.Byte_Size {8} CONFIG.Enable_32bit_Address {true}] $mem
    connect_bd_intf_net [get_bd_intf_pins $ctrl_name/BRAM_PORTA] \
        [get_bd_intf_pins $mem_name/BRAM_PORTA]
    return $mem_name
}
rf_make_bram axi_bram_ctrl_tx blk_mem_gen_tx 4096
rf_make_bram axi_bram_ctrl_rx blk_mem_gen_rx 4096

# ---- DATA_CLK 域: phy 整个跑在恢复出来的时钟上 ----
# ad9361_phy 输出 data_clk(AD9361 的 DATA_CLK 经 IBUFDS 恢复), phy 内部与
# rf_tx_source 全部由它驱动 —— 这个域里任何东西都不许由 FCLK0 驱动。
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk] [get_bd_pins rf_tx_source_0/tx_clk]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_valid]   [get_bd_pins ad9361_phy_0/dac_valid]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_d1] [get_bd_pins ad9361_phy_0/dac_data_d1]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_q1] [get_bd_pins ad9361_phy_0/dac_data_q1]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_d2] [get_bd_pins ad9361_phy_0/dac_data_d2]
connect_bd_net [get_bd_pins rf_tx_source_0/dac_data_q2] [get_bd_pins ad9361_phy_0/dac_data_q2]
# 例程的 regs 驱动 phy 的配置位, 从 FCLK0 跨进 DATA_CLK 域且**没有同步器**(那份例程
# 也没有)。每一条之所以安全的原因不同, 只有 idelay_* 真正是静态的:
#
#   idelay_en / idelay_tap —— 真静态。bring-up 时写一次, 数据流动期间不再碰。
#
#   phy_mode —— **不是**静态的。Stage 2 会在运行中写它来切 1R1T <-> 2R2T。维持它安全
#       的是**次序**而不是同步: 写必须发生在 DATA_CLK **停住**的时候, 之后才允许时钟出现。
#       "保持数据安静"是不够的 —— phase_inc=0 只是让数据没有边沿, 并没有停 DATA_CLK,
#       而这个交叉没有同步器, 时钟在跑时写是配置位上的真亚稳态风险。`init 2r2t` 就是
#       满足这条的序列: RESETB 拉低、放开、等器件自举走完(BBPLL 还没编程, 所以 DATA_CLK
#       尚未出现)、写 phase_inc=0 与 phy_mode=0, 然后才把时钟放出来。它还组合地到达
#       rf_tx_source 的 strobe 分频与 phy_tx 的成帧选择, 所以中途改会把样本周期和线上帧
#       同时打嗝一拍。
#
#   phase_inc —— 同样运行中写(C2 线性度用例在单音已经跑着时把 128 步进到 256)。它安全
#       是另一个原因: 它是每次 strobe 消费一次的值, 不是成帧控制, 所以一次跨 12 位的写
#       最多让一个样本的相位步进错, 不会像 phy_mode 那样破坏帧。
connect_bd_net [get_bd_pins axi_ctrl_0/idelay_en]  [get_bd_pins ad9361_phy_0/idelay_en]
connect_bd_net [get_bd_pins axi_ctrl_0/idelay_tap] [get_bd_pins ad9361_phy_0/idelay_tap]
connect_bd_net [get_bd_pins axi_ctrl_0/phy_mode]   [get_bd_pins ad9361_phy_0/phy_mode]
# phy_mode 现在也喂给单音源: DDS 的 strobe 周期必须与 phy 的样本成帧一致
# (1R1T 每样本 2 周期, 2R2T 每样本 4 周期), 否则模式一换 TX1 的音频就翻倍。
connect_bd_net [get_bd_pins axi_ctrl_0/phy_mode]   [get_bd_pins rf_tx_source_0/phy_mode]
connect_bd_net [get_bd_pins axi_ctrl_0/phase_inc]  [get_bd_pins rf_tx_source_0/phase_inc]
# DDS 波形(reg10[0], 偏移 0x28): 正弦或方波。**故意**与 phy_mode 分开一个寄存器,
# 于是改波形永远不碰那个带"停时钟写"纪律的位。
connect_bd_net [get_bd_pins axi_ctrl_0/wave_sel]   [get_bd_pins rf_tx_source_0/wave_sel]
# 只读 STATUS: 恢复时钟与 rf_tx_source 的复位存活。两者都是 DATA_CLK 域, axi_ctrl_0
# 自己在内部把它们同步进 FCLK0(Gray 计数器 + 2-FF 链)—— 所以这条交叉是安全的, 与上面
# 那些靠**写次序**而非同步的配置信号不同。
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]        [get_bd_pins axi_ctrl_0/data_clk]
connect_bd_net [get_bd_pins rf_tx_source_0/rst_source_ok] [get_bd_pins axi_ctrl_0/rst_source_ok]

# ---- IDELAYCTRL 参考时钟: PS FCLK1 @200MHz ----
# IDELAYE2/IDELAYCTRL 要求 200MHz ±10MHz 的 REFCLK。**故意**用 PS 而不是板上 Y9
# 振荡器 —— 后者所在 bank 的电压是 2.5V, 是否可用本身还是个悬案。
#
# 载入 overlay 之后**去核实**这个频率, 不要**去设**它。PYNQ 的 Clocks.fclkX_mhz 是按
# ZedBoard 的 50MHz PS_CLK 写死的, 本板 PS_CLK 是 33.333MHz, 所以它报/写的每个值都差
# 1.5 倍: 设 fclk1_mhz=200 会让 PYNQ 去解 d0*d1 = 1500/200 = 7, 写 DIVISOR0=7, FCLK1
# 落到 142.857MHz —— 低于 IDELAYCTRL 规格。Overlay() 不碰分频器, 所以 BD 里的 5/1
# (真 200MHz)本来就是对的, 什么都不用做。判据读 SLCR: FPGA1_CLK_CTRL = 0xF8000180,
# DIVISOR0 = bits[13:8], DIVISOR1 = bits[25:20], f = 1000MHz / (DIV0 * DIV1)。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] [get_bd_pins ad9361_phy_0/ref_clk200m]

# ---- 源选择与缓冲配置(FCLK0 -> DATA_CLK) ----
# 每一条都从 FCLK0 跨进 DATA_CLK 域, BD 这一层没有同步器。rf_tx_source 按各自的性质
# 处理: src_sel 是单比特、走 2-FF 链; smp_len 与 smp_wrap 是多比特、靠一个 toggle 提交,
# 因为逐位独立的链可能在不同的沿上送达各自的位, 拼出一个没人写过的配置。
connect_bd_net [get_bd_pins axi_ctrl_0/src_sel]    [get_bd_pins rf_tx_source_0/src_sel]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_len]    [get_bd_pins rf_tx_source_0/smp_len]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_wrap]   [get_bd_pins rf_tx_source_0/smp_wrap]
connect_bd_net [get_bd_pins axi_ctrl_0/smp_commit] [get_bd_pins rf_tx_source_0/smp_commit]
connect_bd_net [get_bd_pins rf_tx_source_0/smp_pos] [get_bd_pins axi_ctrl_0/smp_pos]
# 采集侧的配置与状态, 同一套纪律。
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_arm]    [get_bd_pins rf_sample_capture_0/cfg_arm]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_cont]   [get_bd_pins rf_sample_capture_0/cfg_cont]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_commit] [get_bd_pins rf_sample_capture_0/cfg_commit]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_ratio]  [get_bd_pins rf_sample_capture_0/cfg_ratio]
connect_bd_net [get_bd_pins axi_ctrl_0/cfg_len]    [get_bd_pins rf_sample_capture_0/cfg_len]
connect_bd_net [get_bd_pins rf_sample_capture_0/pos]  [get_bd_pins axi_ctrl_0/cap_pos]
connect_bd_net [get_bd_pins rf_sample_capture_0/done] [get_bd_pins axi_ctrl_0/cap_done]

# phy 的复位就是 AXI 系统复位, 分别在 rf_tx_source(内部 rst_source_ok)与 ad9361_phy
# 自己(4 级同步)里降到恢复时钟域。从 AXI 复位驱动(而不是某个 PS GPIO)意味着 overlay
# 一加载 phy 就出复位 —— 在 DATA_CLK 可能还不存在的时候, 这是唯一安全的选择。

# ===========================================================================
# 样本缓冲的 B 口: 每个 BRAM 的 PL 那一侧。
#
# 两个口都由恢复出来的 DATA_CLK 驱动 —— 与 reader/capture 同一个时钟, 直接从
# ad9361_phy 引出来而不经模块端口, 于是只有一条时钟网、没有转发时钟。
#
# TX 缓冲的 B 口只读, 所以 dinb/web 拉死; RX 缓冲只写, 所以 doutb 干脆不接。
# 常量宽度必须与端口一致: 1 位常量接到 32 位端口只连 LSB、另外 31 位悬空(BD 41-2383),
# 那是静默的 tie-off 而不是零。
# ===========================================================================
set tx_din0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tx_bram_din0]
set_property -dict [list CONFIG.CONST_WIDTH {32} CONFIG.CONST_VAL {0}] $tx_din0
set tx_web0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tx_bram_web0]
set_property -dict [list CONFIG.CONST_WIDTH {4} CONFIG.CONST_VAL {0}] $tx_web0

connect_bd_net [get_bd_pins tx_bram_din0/dout] [get_bd_pins blk_mem_gen_tx/dinb]
connect_bd_net [get_bd_pins tx_bram_web0/dout] [get_bd_pins blk_mem_gen_tx/web]
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]  [get_bd_pins blk_mem_gen_tx/clkb]
connect_bd_net [get_bd_pins rf_tx_source_0/bram_en] [get_bd_pins blk_mem_gen_tx/enb]
connect_bd_net [get_bd_pins rf_tx_source_0/bram_addr] [get_bd_pins blk_mem_gen_tx/addrb]
connect_bd_net [get_bd_pins blk_mem_gen_tx/doutb]   [get_bd_pins rf_tx_source_0/bram_rdata]

connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]         [get_bd_pins blk_mem_gen_rx/clkb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_en]   [get_bd_pins blk_mem_gen_rx/enb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_we]   [get_bd_pins blk_mem_gen_rx/web]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_addr] [get_bd_pins blk_mem_gen_rx/addrb]
connect_bd_net [get_bd_pins rf_sample_capture_0/bram_wdata] [get_bd_pins blk_mem_gen_rx/dinb]

# 采集直接从解串器取样本。adc_d1q1_valid 是每样本一拍(phy_rx 从帧移位寄存器里译出来),
# 是 RX 侧与 dds_ce 对称的那一根, 也是抽取器用来计数的。只用 RX1: 那是判据唯一用到的
# 复通道。
connect_bd_net [get_bd_pins ad9361_phy_0/data_clk]       [get_bd_pins rf_sample_capture_0/clk]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_d1q1_valid] [get_bd_pins rf_sample_capture_0/ce]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_data_d1]    [get_bd_pins rf_sample_capture_0/in_i]
connect_bd_net [get_bd_pins ad9361_phy_0/adc_data_q1]    [get_bd_pins rf_sample_capture_0/in_q]

# ---- AXI interconnect (1SI/26MI) ----
# 从 16 加到 21(显示/触摸), 再加到 26(射频那一半的 5 个从设备)。
# ⚠ 每加一个 MI 都要同时补 {mi slave} 表、Mxx_ACLK 与 Mxx_ARESETN 两个列表 ——
# 漏一个就是"BD 校验过了但那个 IP 永远在复位里"(本仓库有前科, 见 ac820-pynq 技能)。
set axiint [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {26} CONFIG.NUM_SI {1} CONFIG.S00_HAS_REGSLICE {4}] $axiint
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
foreach {mi slave} {M00_AXI axi_gpio_0/S_AXI  M01_AXI axi_gpio_1/S_AXI  M02_AXI axi_gpio_2/S_AXI \
        M03_AXI axi_gpio_3/S_AXI  M04_AXI axi_gpio_4/S_AXI  M05_AXI axi_gpio_5/S_AXI \
        M06_AXI axi_gpio_6/S_AXI  M07_AXI axi_gpio_7/S_AXI  M08_AXI axi_gpio_8/S_AXI \
        M09_AXI axi_bram_ctrl_0/S_AXI  M10_AXI axi_gpio_9/S_AXI  M11_AXI axi_gpio_10/S_AXI \
        M12_AXI axi_dma_0/S_AXI_LITE M13_AXI axi_gpio_11/S_AXI \
        M14_AXI axi_gpio_12/S_AXI M15_AXI axi_bram_ctrl_1/S_AXI \
        M16_AXI axi_gpio_led/S_AXI M17_AXI axi_gpio_btn/S_AXI \
        M18_AXI axi_gpio_fb/S_AXI M19_AXI axi_gpio_dbg/S_AXI M20_AXI axi_gpio_dbg2/S_AXI \
        M21_AXI axi_ctrl_0/S00_AXI M22_AXI pl_spi_0/s_axi \
        M23_AXI axi_gpio_ctrl/S_AXI M24_AXI axi_bram_ctrl_tx/S_AXI \
        M25_AXI axi_bram_ctrl_rx/S_AXI} {
    connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/$mi] [get_bd_intf_pins $slave]
}
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/S00_ACLK]
foreach mi {M00_ACLK M01_ACLK M02_ACLK M03_ACLK M04_ACLK M05_ACLK M06_ACLK M07_ACLK M08_ACLK M09_ACLK M10_ACLK M11_ACLK M12_ACLK M13_ACLK M14_ACLK M15_ACLK M16_ACLK M17_ACLK M18_ACLK M19_ACLK M20_ACLK M21_ACLK M22_ACLK M23_ACLK M24_ACLK M25_ACLK} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/$mi]
}
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2 axi_gpio_3 axi_gpio_4 axi_gpio_5 axi_gpio_6 axi_gpio_7 axi_gpio_8 axi_gpio_9 axi_gpio_10 axi_gpio_11 axi_gpio_12 axi_bram_ctrl_0 axi_bram_ctrl_1 axi_gpio_led axi_gpio_btn axi_gpio_fb axi_gpio_dbg axi_gpio_dbg2 axi_gpio_ctrl axi_bram_ctrl_tx axi_bram_ctrl_rx} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $ip_name/s_axi_aclk]
}
# 这两个**不能**进上面那张表: axi_ctrl_0 的时钟是 `s00_axi_aclk`, pl_spi_master 是手写
# 模块、时钟叫光秃秃的 `aclk`(没有 s_axi_ 前缀)。用通配符去匹配它们只会静默匹配到空,
# 失败要到很后面以一个看不懂的 connect_bd_net 报错浮出来 —— 所以这里显式写引脚名。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_ctrl_0/s00_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins pl_spi_0/aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
# 面板的帧缓冲读取走**另一路 HP**: HP0 上跑着 DAC 的 DMA(连续供数, 带宽要保),
# 与它共用会互相抢仲裁 —— 分开就互不影响。HP1 的 64 位/ID 宽度 6 已在 ps7_props.tcl 里。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/S_AXI_HP1_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_dma_0/s_axi_lite_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_dma_0/m_axi_mm2s_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins sc_dma_hp0/ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axis_data_fifo_0/s_axis_aclk]
connect_bd_net [get_bd_ports sys_clk]        [get_bd_pins axis_data_fifo_0/m_axis_aclk]

# ---- reset ----
# proc_sys_reset holds AXI peripherals in reset when dcm_locked is left open.
set rst_clock_locked [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_clock_locked]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_clock_locked
set rst_aux_inactive [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_aux_inactive]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_aux_inactive
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]     [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins rst_clock_locked/dout] [get_bd_pins rst_ps7/dcm_locked]
connect_bd_net [get_bd_pins rst_aux_inactive/dout] [get_bd_pins rst_ps7/aux_reset_in]
# Keep the asynchronous FIFO empty across STOP/error recovery. The existing
# stream-enable GPIO is in the FIFO S-side FCLK0 domain; benchmark mode also
# releases the FIFO while leaving the awg_top stream disabled.
set fifo_run_enable_gate [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:util_vector_logic:2.0 fifo_run_enable_gate]
set_property -dict [list CONFIG.C_OPERATION {or} CONFIG.C_SIZE {1}] $fifo_run_enable_gate
connect_bd_net [get_bd_pins stream_enable_slice/dout] [get_bd_pins fifo_run_enable_gate/Op1]
connect_bd_net [get_bd_pins dma_benchmark_enable_slice/dout] [get_bd_pins fifo_run_enable_gate/Op2]
set fifo_reset_gate [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:util_vector_logic:2.0 fifo_reset_gate]
set_property -dict [list CONFIG.C_OPERATION {and} CONFIG.C_SIZE {1}] $fifo_reset_gate
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins fifo_reset_gate/Op1]
connect_bd_net [get_bd_pins fifo_run_enable_gate/Res] [get_bd_pins fifo_reset_gate/Op2]
connect_bd_net [get_bd_pins fifo_reset_gate/Res] [get_bd_pins axis_data_fifo_0/s_axis_aresetn]
connect_bd_net [get_bd_pins rst_ps7/interconnect_aresetn] [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/S00_ARESETN]
foreach mi {M00_ARESETN M01_ARESETN M02_ARESETN M03_ARESETN M04_ARESETN M05_ARESETN M06_ARESETN M07_ARESETN M08_ARESETN M09_ARESETN M10_ARESETN M11_ARESETN M12_ARESETN M13_ARESETN M14_ARESETN M15_ARESETN M16_ARESETN M17_ARESETN M18_ARESETN M19_ARESETN M20_ARESETN M21_ARESETN M22_ARESETN M23_ARESETN M24_ARESETN M25_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_interconnect_0/$mi]
}
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2 axi_gpio_3 axi_gpio_4 axi_gpio_5 axi_gpio_6 axi_gpio_7 axi_gpio_8 axi_gpio_9 axi_gpio_10 axi_gpio_11 axi_gpio_12 axi_bram_ctrl_0 axi_bram_ctrl_1 axi_gpio_led axi_gpio_btn axi_gpio_fb axi_gpio_dbg axi_gpio_dbg2 axi_gpio_ctrl axi_bram_ctrl_tx axi_bram_ctrl_rx} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins $ip_name/s_axi_aresetn]
}
# 同 ACLK 那一节: 这两个的复位引脚名也不带 s_axi_ 前缀。
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_ctrl_0/s00_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins pl_spi_0/aresetn]
# phy 与两个样本单元同样是 AXI 系统复位; 它们内部各自降到 DATA_CLK 域。
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins ad9361_phy_0/rst_n]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins rf_tx_source_0/rst_n]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins rf_sample_capture_0/rst_n]
# DMA/FIFO path shares the FCLK0 peripheral reset. In async mode the
# axis_data_fifo carries a single s_axis_aresetn; the IP synchronises reset
# into the m_axis (external sys_clk) domain internally.
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_dma_0/axi_resetn]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins sc_dma_hp0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins sc_fb_hp1/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins lcd_0/aresetn]

# axis_data_fifo_0/s_axis_aresetn is driven by fifo_reset_gate above; the
# stream-enable low level provides the FIFO flush between transfers.

# ---- wrap up ----
assign_bd_address
# Route the DMA MM2S memory master to the PS HP0 DDR window. The PS7 low-OCM
# segment spans the full 1-GiB aperture; firmware places the DMA buffer inside
# the DDR window beyond the reserved first MiB.
assign_bd_address -target [get_bd_addr_segs axi_dma_0/Data_MM2S] \
    [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
# 面板的帧缓冲读主口同样要能到 DDR(不止 GP0 寄存器), 走它自己的 HP1 段。
set lcd_spaces [get_bd_addr_spaces -quiet -of_objects [get_bd_cells lcd_0]]
if {[llength $lcd_spaces] != 1} {
    error "expected one address space on lcd_0, got: $lcd_spaces"
}
assign_bd_address -target $lcd_spaces \
    [get_bd_addr_segs ps7_0/S_AXI_HP1/HP1_DDR_LOWOCM] -force
# ===========================================================================
# 地址映射: **显式给死, 不靠 assign_bd_address 的启发式分配**
#
# 为什么必须给死(2026-09-21 实测): 上面那次 `assign_bd_address` 是**启发式**的, 既不是
# 按 MI 顺序也不是按孔径大小 —— 仅仅把两个 16KiB 的射频 BRAM 加进来, 它就
# 把 `axi_bram_ctrl_0` 从 0x40000000 挪到了 0x42000000。而固件(sdk/src/main.c)、板载
# 守护进程和上位机 GUI 都把 AWG 那一套基址**写死在源码里**: 悄悄挪一格, 上板现象就是
# "某个外设读回全 0", 与当年的 BD 修订落后、blk_mem_gen 空网表一模一样, 从现象反推
# 根因要烧掉一整轮。
#
# 所以合流后的映射是**本表**定义的, 不是综合出来的。任何一格变化都必须同时改源码里
# 那几个常量 —— 表里的数字就是那份契约。
#
# ⚠ 匹配串末尾那个下划线是关键: `*SEG_axi_gpio_1_*` 匹配不到 `SEG_axi_gpio_10_Mem0`
#   (后面跟的是 `0` 而不是 `_`), 否则 1 和 10..12 会互相串。
#
# 两趟写: 先把 26 个全部搬到一段**临时高位区**, 再落到最终值。一趟写的话, 给 A 设的
# 目标地址可能正被 B 占着, 中间态重叠 —— 是否报错取决于 Vivado 在 set_property 当场
# 校验还是留到 validate, 那种"有时行有时不行"的东西不值得依赖。临时区取 0x4F000000,
# 与最终区(0x40000000..0x43043FFF)和 DDR 区(0x00000000..0x3FFFFFFF)都不挨着。
proc pin_bd_addr {cell offset range} {
    set seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data] \
        -filter "NAME =~ *SEG_${cell}_*"]
    if {[llength $seg] != 1} {
        error "pin_bd_addr: expected exactly one segment for $cell, got: $seg"
    }
    set_property OFFSET $offset $seg
    set_property RANGE $range $seg
}

# `get_property RANGE` 回的是**字节数的十六进制**(4K -> 0x00001000), 不是当初写进去的
# 字符串, 所以核对时两边都要化成字节数 —— 直接拿 "4K" 去比会永远不等。
proc range_bytes {rng} {
    if {[regexp {^([0-9]+)K$} $rng -> k]} { return [expr {$k * 1024}] }
    if {[regexp {^([0-9]+)M$} $rng -> m]} { return [expr {$m * 1024 * 1024}] }
    if {[string is integer -strict $rng]} { return $rng }
    error "range_bytes: cannot parse '$rng'"
}

# {cell 基址 孔径} —— AWG 21 个在前(前 21 格的值 = 现有源码里的常量, 一格都不许动),
# 射频 5 个在后。
#
# ⚠ 这 21 个数字**不是** MI 顺序, 是 2026-09-14 那份已在板上跑的 `awg_merged.hwh` 里的
#   实际分配 —— 按 **cell 名的字典序**排(`axi_gpio_1` < `axi_gpio_10` < `axi_gpio_2`,
#   因为 `1` < `2` 是字符比较), 所以 gpio_10/11/12 夹在 gpio_1 与 gpio_2 之间, led/btn/
#   fb/dbg/dbg2 落在末尾。我先前照 MI 顺序(M10=gpio_9, M14=gpio_12 …)抄了一份, 结果是
#   16/21 格与板上不符 —— 若这么上板, 去写 gpio_10 的软件会打到 gpio_2 上。
#   Vivado 的 `assign_bd_address` 就是这个脾气, 本合流第一轮已经实测到它把
#   `axi_bram_ctrl_0` 从 0x40000000 挤到 0x42000000。契约以那份 .hwh 为准, 由
#   `host/tests/test_rf_merge.py` 直接读它对表(不是拿本表自己证自己)。
#   佐证: `pynq_overlay/sw/awg_ctl.py:707` 硬编码 DMA = 0x40400000, 与下表一致。
set bd_addr_map {
    axi_bram_ctrl_0  0x40000000 4K
    axi_dma_0        0x40400000 64K
    axi_gpio_0       0x41200000 64K
    axi_gpio_1       0x41210000 64K
    axi_gpio_10      0x41220000 64K
    axi_gpio_11      0x41230000 64K
    axi_gpio_12      0x41240000 64K
    axi_gpio_2       0x41250000 64K
    axi_gpio_3       0x41260000 64K
    axi_gpio_4       0x41270000 64K
    axi_gpio_5       0x41280000 64K
    axi_gpio_6       0x41290000 64K
    axi_gpio_7       0x412A0000 64K
    axi_gpio_8       0x412B0000 64K
    axi_gpio_9       0x412C0000 64K
    axi_gpio_btn     0x412D0000 64K
    axi_gpio_dbg     0x412E0000 64K
    axi_gpio_dbg2    0x412F0000 64K
    axi_gpio_fb      0x41300000 64K
    axi_gpio_led     0x41310000 64K
    axi_bram_ctrl_1  0x42000000 4K
    axi_ctrl_0       0x43000000 64K
    pl_spi_0         0x43010000 64K
    axi_gpio_ctrl    0x43020000 64K
    axi_bram_ctrl_tx 0x43030000 16K
    axi_bram_ctrl_rx 0x43040000 16K
}
if {[llength $bd_addr_map] % 3 != 0} {
    error "bd_addr_map must be a multiple of 3, got [llength $bd_addr_map] entries"
}

set scratch_base 0x4F000000
set scratch_stride 0x20000
set i 0
foreach {cell offset range} $bd_addr_map {
    pin_bd_addr $cell [expr {$scratch_base + $i * $scratch_stride}] $range
    incr i
}
set i 0
foreach {cell offset range} $bd_addr_map {
    pin_bd_addr $cell $offset $range
    incr i
}

# 回读核对。注: 这些值刚由本脚本写入, 所以这条本身**不是**独立证据(写进去再读出来必然
# 相等 —— 本仓库把这类同义反复单独记过一笔)。它的用处只有两个: 抓 `pin_bd_addr` 里的
# 笔误/静默 no-op(过滤器匹配空表时 set_property 是不报错的空操作), 以及抓两趟写里
# 后一趟覆盖前一趟的顺序错。真正独立的证据是构建后从生成的 .hwh 里读地址。
set awg_addr_count 21
set i 0
foreach {cell want range} $bd_addr_map {
    set seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data] \
        -filter "NAME =~ *SEG_${cell}_*"]
    if {[llength $seg] != 1} {
        error "address check: expected exactly one segment for $cell, got: $seg"
    }
    set got [get_property OFFSET $seg]
    if {$got != $want} {
        error "address check: $cell wanted $want but the BD has $got"
    }
    set grange [get_property RANGE $seg]
    if {[range_bytes $grange] != [range_bytes $range]} {
        error "address check: $cell wanted range $range but the BD has $grange"
    }
    incr i
}
puts "ADDR OK: $awg_addr_count AWG + [expr {$i - $awg_addr_count}] RF slaves pinned exactly"

# 射频那 5 个的基址同时打印一份, 供抄进 `rf_overlay/sw/board_app/platform_rf.h` ——
# 那里现在那 5 个常量与 AWG 全部冲突(0x40000000/0x40001000/0x41200000/0x44000000/
# 0x42000000), 必须按本表替换, 否则射频软件会去写 AWG 的寄存器。
foreach {cell offset range} [lrange $bd_addr_map [expr {$awg_addr_count * 3}] end] {
    puts [format "RF-ADDR %-18s %s range %s" $cell $offset $range]
}
# Runtime AXI writes populate the user BRAM; no ELF is merged into PL memory.
# Remove Vivado 2018.3's optional processor/BMM preload metadata, whose generated
# hierarchy is invalid for this module-ref design and is not used by the firmware.
set_property HDL_ATTRIBUTE.BMM_INFO_PROCESSOR {} [get_bd_cells ps7_0]
foreach bram_ctrl {axi_bram_ctrl_0 axi_bram_ctrl_1 axi_bram_ctrl_tx axi_bram_ctrl_rx} {
    set_property HDL_ATTRIBUTE.BMM_INFO_ADDRESS_SPACE {} [get_bd_cells $bram_ctrl]
}
validate_bd_design
save_bd_design
make_wrapper -files [get_files host_bd.bd] -top
set wrapper [file join [file dirname [get_files host_bd.bd]] hdl host_bd_wrapper.v]
add_files -norecurse $wrapper
set_property top host_bd_wrapper [current_fileset]
generate_target all [get_files host_bd.bd]
close_project
