# AC820 PL user buttons S0/S1 and the eight PL LEDs.
#
# LEDs are active high (LED0~7 = P20 P21 R20 R21 R19 T19 T16 T17).
# Buttons are active low and pulled up on the board. Pin assignment by the
# vendor's pin table, which names these two S1/S2 -- one higher than the
# silkscreen. The silkscreen labels (S0/S1) are what a user actually reads on
# the board, so they are the names used here: S0 = H18, S1 = H19.
# S2 (PS MIO47) and S3 (POR_B) are not wired into this design.
#
# Note the board's own display designs set UNUSEDPIN Pullnone; matching them
# keeps the unconfigured FPC/HDMI pins in the same state as the vendor examples.
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]

set_property PACKAGE_PIN P20 [get_ports {led[0]}]
set_property PACKAGE_PIN P21 [get_ports {led[1]}]
set_property PACKAGE_PIN R20 [get_ports {led[2]}]
set_property PACKAGE_PIN R21 [get_ports {led[3]}]
set_property PACKAGE_PIN R19 [get_ports {led[4]}]
set_property PACKAGE_PIN T19 [get_ports {led[5]}]
set_property PACKAGE_PIN T16 [get_ports {led[6]}]
set_property PACKAGE_PIN T17 [get_ports {led[7]}]

set_property PACKAGE_PIN H18 [get_ports {btn[0]}]
set_property PACKAGE_PIN H19 [get_ports {btn[1]}]

set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]
set_property IOSTANDARD LVCMOS33 [get_ports {btn[*]}]

# The buttons are mechanical inputs with no timing relationship to FCLK0; they
# only feed an AXI GPIO read. The LEDs are static indicators.
set_false_path -from [get_ports {btn[*]}]
set_false_path -to [get_ports {led[*]}]

# ---------------------------------------------------------------------------
# Touch controller reset: TP_RST = PL pin R15, driven by axi_gpio_fb channel 2
# bit 1 (see create_bd.tcl). R15 is in bank 34 -- the same bank as the eight LEDs
# above, which this design already drives at LVCMOS33 and which work on the board,
# so bank 34's VCCO is 3.3 V and LVCMOS33 is right here too.
#
# The GT911 is held in reset until software raises this bit (its reset value is 0),
# and it samples its I2C address-select pin at the moment the reset is released --
# which is why software needs control of the edge rather than a constant high.
set_property PACKAGE_PIN R15 [get_ports tp_rst]
set_property IOSTANDARD LVCMOS33 [get_ports tp_rst]

# ---------------------------------------------------------------------------
# 5" 800x480 TFT on the FPC connector, RGB565.
#
# The TFT shares these RGB and control pins with the SiI9022 (HDMI_1) encoder --
# the same PL pins reach both connectors, so driving the panel needs no mux. The
# flip side is that the SiI9022 sees the same signals; it stays silent until it is
# configured over I2C, which this design does not do.
#
# Pins are the vendor's ch24_ac820_tft800x480_ctrl assignment, which three separate
# display examples (ch24/ch27/ch32) agree on. Note the backlight is U19: an earlier
# note in the board pin table lists LCD_BL as Y8, but Y8 is an ES8388 audio pin on
# the v1.1 pin map and no vendor display example uses it.
#
# Pixel clock source is the board's 50 MHz oscillator on Y9, through an MMCM inside
# lcd_overlay_top. There is no PL clock constraint to derive it from otherwise.
create_clock -period 20.000 -name clk50m [get_ports clk50m]
set_property PACKAGE_PIN Y9 [get_ports clk50m]

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

set_property IOSTANDARD LVCMOS33 [get_ports clk50m]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_pclk]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_de]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_hs]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_vs]
set_property IOSTANDARD LVCMOS33 [get_ports lcd_bl]
set_property IOSTANDARD LVCMOS33 [get_ports {lcd_rgb[*]}]
