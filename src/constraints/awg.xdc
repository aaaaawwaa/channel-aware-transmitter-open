# AC820 (xc7z020clg484-2) AWG constraints, LVCMOS33
# NOTE: GPIO0 pin mapping assumes ACM2108 plugs directly onto the 40-pin header,
#       module pin N <-> board pin N. VERIFY against the official pin table
#       before burning the bitstream.
set_property PACKAGE_PIN Y9 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]
create_clock -period 20.000 -name sys_clk [get_ports sys_clk]

# clk_fpga_0 is created after this XDC is read. Its asynchronous relationship to
# sys_clk is applied by the impl_1 opt_design pre-hook.

# LEDs (active high)
set_property PACKAGE_PIN P20 [get_ports {led[0]}]
set_property PACKAGE_PIN P21 [get_ports {led[1]}]
set_property PACKAGE_PIN R20 [get_ports {led[2]}]
set_property PACKAGE_PIN R21 [get_ports {led[3]}]
set_property PACKAGE_PIN R19 [get_ports {led[4]}]
set_property PACKAGE_PIN T19 [get_ports {led[5]}]
set_property PACKAGE_PIN T16 [get_ports {led[6]}]
set_property PACKAGE_PIN T17 [get_ports {led[7]}]

# GPIO0 -> ACM2108 DAC0 (module pin 1..10 -> PL_GPIO0_0..9)
# DAC0 DB7 (pin 1)
set_property PACKAGE_PIN U12 [get_ports {dac0_db[7]}]
# DACLK0  (pin 2)
set_property PACKAGE_PIN U11 [get_ports daclk0]
# DAC0 DB5 (pin 3)
set_property PACKAGE_PIN U10 [get_ports {dac0_db[5]}]
# DAC0 DB6 (pin 4)
set_property PACKAGE_PIN U9  [get_ports {dac0_db[6]}]
# DAC0 DB3 (pin 5)
set_property PACKAGE_PIN Y11 [get_ports {dac0_db[3]}]
# DAC0 DB4 (pin 6)
set_property PACKAGE_PIN Y10 [get_ports {dac0_db[4]}]
# DAC0 DB1 (pin 7)
set_property PACKAGE_PIN V7  [get_ports {dac0_db[1]}]
# DAC0 DB2 (pin 8)
set_property PACKAGE_PIN W7  [get_ports {dac0_db[2]}]
# DACLK1  (pin 9)
set_property PACKAGE_PIN Y6  [get_ports daclk1]
# DAC0 DB0 (pin 10)
set_property PACKAGE_PIN Y5  [get_ports {dac0_db[0]}]

# GPIO0 -> ACM2108 DAC1 (module pin 13..20 -> PL_GPIO0_10..17)
# DAC1 DB7 (pin 13)
set_property PACKAGE_PIN U6  [get_ports {dac1_db[7]}]
# DAC1 DB6 (pin 14)
set_property PACKAGE_PIN U5  [get_ports {dac1_db[6]}]
# DAC1 DB5 (pin 15)
set_property PACKAGE_PIN V5  [get_ports {dac1_db[5]}]
# DAC1 DB4 (pin 16)
set_property PACKAGE_PIN V4  [get_ports {dac1_db[4]}]
# DAC1 DB3 (pin 17)
set_property PACKAGE_PIN T4  [get_ports {dac1_db[3]}]
# DAC1 DB2 (pin 18)
set_property PACKAGE_PIN U4  [get_ports {dac1_db[2]}]
# DAC1 DB1 (pin 19)
set_property PACKAGE_PIN R6  [get_ports {dac1_db[1]}]
# DAC1 DB0 (pin 20)
set_property PACKAGE_PIN T6  [get_ports {dac1_db[0]}]

# DAC 面在 Bank 13, sys_clk 也在那一组(VCCO 3.3V), 与本设计其余部分无关。
set_property IOSTANDARD LVCMOS33 [get_ports {dac0_db[*] dac1_db[*] daclk0 daclk1}]

# ⚠ LED 在 **Bank 34**, 必须 LVCMOS25 —— 合流前这里写的是 LVCMOS33, 那是错的。
# 依据: VCCIO34 ≡ VCCIO35 ≡ FMC `VADJ_3`, 同一网络, 由 P18 的 U35(SPX3819) 供,
# 板上 TP8 实测 **2.5V**(2026-09-16; 原理图矢量追踪 + FB20 为 DNI + 实测三方一致,
# 见 docs/rf/compatibility.md §0)。`IOSTANDARD` 只声明要求、不设置 VCCO, Vivado 是反推的 ——
# 所以合流前"bank 里只有 LVCMOS33 引脚"时它默认 VCCO=3.3V 并不报错, 而板上实际是 2.5V。
# 合流把 FMC 的 LVDS_25/LVCMOS25 引脚放进同两个 bank 之后, 这个矛盾才第一次显形
# (DRC BIVC-1: 一个 bank 只能有一个 VCCO)。
# LED 是共阴灌电流(FPGA → 1K → LED → GND), 2.5V 驱动安全, 只是略暗。
set_property IOSTANDARD LVCMOS25 [get_ports {led[*]}]

# DAC output timing: DACLK = ~clk (combinational), rising edge at clk falling
# edge (10 ns after data update). Setup/hold are typical AD9708-compatible
# values (ACM2108 module, MS9708 DAC); 10 ns centered margin is ample.
create_generated_clock -name daclk0 -source [get_ports sys_clk] -divide_by 1 -invert [get_ports daclk0]
create_generated_clock -name daclk1 -source [get_ports sys_clk] -divide_by 1 -invert [get_ports daclk1]
set_output_delay -clock daclk0 -min 0.0 [get_ports {dac0_db[*]}]
set_output_delay -clock daclk0 -max 2.0 [get_ports {dac0_db[*]}]
set_output_delay -clock daclk1 -min 0.0 [get_ports {dac1_db[*]}]
set_output_delay -clock daclk1 -max 2.0 [get_ports {dac1_db[*]}]

# ---------------------------------------------------------------------------
# TFT 面板 + 触摸复位(合流: 显示/触摸通路并进 AWG 位流)
#
# 这份约束是 `pynq_overlay/constraints/led_btn.xdc` 的显示部分**逐字搬过来的**, 只去掉
# 三样与 AWG 冲突的东西:
#   1) `clk50m` 的 create_clock 与 PACKAGE_PIN —— 本设计里 Y9 已经是 `sys_clk`(上面),
#      **同一个脚不能有两个顶层端口**(会 DRC 报错), 所以面板的 MMCM 输入直接吃 `sys_clk`
#      这张网(BD 里 lcd_0/clk50m 接它)。
#   2) LED/按键那几条 —— LED 归 axi_gpio_led(见 create_bd.tcl 的说明), 按键本设计没有。
#   3) 其余照搬 —— TFT 整组在 **Bank 33**(VCCO 3.3V), LVCMOS33 不变。注意下面 LED/按键/
#      tp_rst 那三条**不能**跟着写 LVCMOS33: 它们在 Bank 34/35, 那两个 bank 由 FMC 的
#      `VADJ_3` 供 **2.5V**(合流后才第一次被 DRC 抓到, 详见下面 LED 段的说明)。
#
# TFT 与 HDMI_1(SiI9022) 共用这些 RGB/控制脚 —— 同一条 PL 脚通到两个座子, 驱动面板不需要
# 任何 mux; 反面是 SiI9022 也看得到同样的信号, 但它要经 I2C 配置才会出声, 本设计不配。
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]

# GT911 复位: 位流默认 0(器件被按在复位里), 软件放开; 它在复位释放的那一刻采样地址选择脚,
# 所以软件要控制这个沿, 而不是恒高。
set_property PACKAGE_PIN R15 [get_ports tp_rst]

# 用户按键 S0/S1(H18/H19, 板载按下为低)。丝印叫 S0/S1, 厂商引脚表记作 S1/S2(差一位),
# 这里用丝印。S0 长按 3 秒 = 优雅关机(合流后由 PYNQ 侧的守护进程实现)。
set_property PACKAGE_PIN H18 [get_ports {btn[0]}]
set_property PACKAGE_PIN H19 [get_ports {btn[1]}]

set_property PACKAGE_PIN V22  [get_ports lcd_pclk]
set_property PACKAGE_PIN AB19 [get_ports lcd_de]
set_property PACKAGE_PIN AB14 [get_ports lcd_hs]
set_property PACKAGE_PIN U14  [get_ports lcd_vs]
set_property PACKAGE_PIN U19  [get_ports lcd_bl]

set_property PACKAGE_PIN AB21 [get_ports {lcd_rgb[15]}]
set_property PACKAGE_PIN AA17 [get_ports {lcd_rgb[14]}]
set_property PACKAGE_PIN AB17 [get_ports {lcd_rgb[13]}]
set_property PACKAGE_PIN AA16 [get_ports {lcd_rgb[12]}]
set_property PACKAGE_PIN AB16 [get_ports {lcd_rgb[11]}]
set_property PACKAGE_PIN Y13  [get_ports {lcd_rgb[10]}]
set_property PACKAGE_PIN AA13 [get_ports {lcd_rgb[9]}]
set_property PACKAGE_PIN W22  [get_ports {lcd_rgb[8]}]
set_property PACKAGE_PIN AA22 [get_ports {lcd_rgb[7]}]
set_property PACKAGE_PIN AB22 [get_ports {lcd_rgb[6]}]
set_property PACKAGE_PIN AA21 [get_ports {lcd_rgb[5]}]
set_property PACKAGE_PIN AB15 [get_ports {lcd_rgb[4]}]
set_property PACKAGE_PIN W13  [get_ports {lcd_rgb[3]}]
set_property PACKAGE_PIN V13  [get_ports {lcd_rgb[2]}]
set_property PACKAGE_PIN V15  [get_ports {lcd_rgb[1]}]
set_property PACKAGE_PIN V14  [get_ports {lcd_rgb[0]}]

# tp_rst 是 R15, 同样在 **Bank 34**; btn 是 H18/H19, 在 **Bank 35** —— 两个 bank 都是 2.5V,
# 故同样 LVCMOS25(理由见上面 LED 那段)。按键是上拉到 VCCIO35 的, 与 2.5V 档自洽。
# GT911 的 VIH≈2.31V 而 2.5V 驱动只余 0.19V, 这一条是上板实测项, 不能靠约束保证。
set_property IOSTANDARD LVCMOS25 [get_ports tp_rst]
set_property IOSTANDARD LVCMOS25 [get_ports {btn[*]}]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_pclk]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_de]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_hs]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_vs]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_bl]
set_property IOSTANDARD LVCMOS33 [get_ports {lcd_rgb[*]}]

# 注: lcd_pclk 是 lcd_overlay_top 里 MMCM 生成的 33.333MHz(源 = sys_clk/Y9), 属"生成时钟",
# 由工具从 MMCM 自动推导, **不要**在这里再 create_clock; sys_clk 与 FCLK0 的异步关系由
# tools/impl1_clock_groups.tcl 施加(那里必须带 -include_generated_clocks, 否则 pclk 不在
# 异步组里, AXI↔pclk 的跨域会被当真时序去展)。
