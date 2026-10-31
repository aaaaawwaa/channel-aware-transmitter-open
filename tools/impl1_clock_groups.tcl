set ps_clk [get_clocks -quiet clk_fpga_0]
set ext_clk [get_clocks -quiet sys_clk]
if {[llength $ps_clk] != 1 || [llength $ext_clk] != 1} {
    error "Expected clk_fpga_0 and sys_clk before opt_design"
}
# 面板的像素时钟是 sys_clk 经 MMCM 生成的**生成时钟**, 必须一起拉进异步组 —— 否则
# lcd_fb 里 AXI 域(FCLK0)↔pclk 那条交叉会被当成真时序去展, 结果是假失败或掩盖真问题。
#
# ⚠ `set_clock_groups` **没有** -include_generated_clocks 这个开关: Vivado 2025.2 实测报
#   "ERROR: [Common 17-170] Unknown option '-include_generated_clocks'"(整整一轮构建白跑)。
#   官方写法是把生成时钟在 get_clocks 那一层带进来(见 `set_clock_groups -help` 的示例)。

# 第三组: AD9361 恢复出来的 DATA_CLK(合流后 ad9361_phy 那一半的整个数据面都跑在它上面)。
# 它来自电台自己的参考, 与 PS 的 FCLK0 / sys_clk 都无关, 频率还随采样率变。
#
# 这一组就是 rf_overlay/tools/impl1_clock_groups.tcl 那条, 逐字并进来 —— 那份独立工程
# 只声明 ps↔data_clk 两方, 合流后同一个网里多了 sys_clk, 所以从两组变三组。
#
# ⚠ data_clk 由 src/constraints 里的 rf 约束 `create_clock` 在 rx_clk_in_p 上建立(合流后
#   那份约束也在本工程里), 所以这里能 get_clocks 到。少了它本设计**不会**报错, 只会把
#   一整套跨域路径当真时序去展 —— 就是"RRC 时序经验"里记的那种假失败。
set dclk [get_clocks -quiet -include_generated_clocks data_clk]
if {[llength $dclk] < 1} {
    error "Expected data_clk (created on rx_clk_in_p by the rf constraints) before opt_design"
}

set_clock_groups -asynchronous \
    -group [get_clocks -include_generated_clocks clk_fpga_0] \
    -group [get_clocks -include_generated_clocks sys_clk] \
    -group $dclk
