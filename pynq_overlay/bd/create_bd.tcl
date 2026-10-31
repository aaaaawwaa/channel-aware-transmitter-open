# Create the AC820 PYNQ board-support design:
#   PS7 + 8-bit LED output GPIO + 2-bit user-button input GPIO
#   + framebuffer control GPIO (DDR base + source select)
#   + 5" 800x480 TFT (RGB565) controller, fed from colour bars or a DDR
#     framebuffer read back through S_AXI_HP0.
# Regenerated from scratch (the BD is derived state).
# Assumes a project is already open; driven by build.tcl.
set script_dir [file dirname [file normalize [info script]]]
source [file join $script_dir ps7_props.tcl]

if {[get_files -quiet ledbtn_bd.bd] ne ""} {
    set bd_dir [file dirname [get_files ledbtn_bd.bd]]
    remove_files ledbtn_bd.bd
    file delete -force $bd_dir
}
foreach stale [glob -nocomplain ledbtn_bd_wrapper.*] {
    if {[llength [get_files -quiet $stale]] > 0} {
        remove_files $stale
    }
}

create_bd_design ledbtn_bd

# ---- PS7 (FCLK0 50 MHz, DDR, UART1 MIO48/49) ----
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
set_property -dict $ps7_props $ps7
# Vivado may recompute HIGHADDR when BASEADDR is applied; restore the full
# 1-GiB DDR window after every PS7 property is set.
set_property CONFIG.PCW_DDR_RAM_HIGHADDR 0x3FFFFFFF $ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable"} \
    [get_bd_cells ps7_0]

# ---- PL LEDs (8-bit, all outputs, active high) ----
set led [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_led]
set_property -dict [list CONFIG.C_GPIO_WIDTH {8} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $led
create_bd_port -dir O -from 7 -to 0 led
connect_bd_net [get_bd_pins axi_gpio_led/gpio_io_o] [get_bd_ports led]

# ---- User buttons S1/S2 (2-bit, all inputs, active low) ----
set btn [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_btn]
set_property -dict [list CONFIG.C_GPIO_WIDTH {2} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {0}] $btn
create_bd_port -dir I -from 1 -to 0 btn
connect_bd_net [get_bd_pins axi_gpio_btn/gpio_io_i] [get_bd_ports btn]

# ---- framebuffer control: 32-bit DDR base address + 2-bit control ----
# Channel 1 carries the physical address the reader walks; channel 2 bit 0 selects
# the framebuffer over the colour bars, bit 1 drives the touch controller's reset
# (TP_RST = PL pin R15 -- the GT911 is released from reset by raising it, and it
# samples its I2C address select pin at that moment, so software needs the edge).
# A dual-channel cell puts both in one aperture so software writes base, then
# enable, with no ordering hazard beyond its own write order.
set fb [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_fb]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {2} CONFIG.C_ALL_OUTPUTS_2 {1}] $fb
create_bd_port -dir O tp_rst

# ---- framebuffer self-check taps: 64-bit read-only debug bus ----
# Channel 1 = dbg0 (reader side), channel 2 = dbg1 (display side). Read-only on
# purpose: nothing here may perturb the pixel path, it only reports.
set dbg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_dbg]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $dbg

# A second read-only debug cell for the per-line data signature. AXI GPIO cannot
# carry more than two 32-bit channels, and the ring taps above already use both.
set dbg2 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_dbg2]
set_property -dict [list CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $dbg2

# ---- AXI interconnect (1 SI / 5 MI) ----
set axiint [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {5} CONFIG.NUM_SI {1}] $axiint
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins axi_gpio_led/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M01_AXI] [get_bd_intf_pins axi_gpio_btn/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M02_AXI] [get_bd_intf_pins axi_gpio_fb/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M03_AXI] [get_bd_intf_pins axi_gpio_dbg/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M04_AXI] [get_bd_intf_pins axi_gpio_dbg2/S_AXI]
foreach port {ACLK S00_ACLK M00_ACLK M01_ACLK M02_ACLK M03_ACLK M04_ACLK} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/$port]
}
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]
# HP0 clock: the LCD framebuffer master rides this port.
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
foreach ip_name {axi_gpio_led axi_gpio_btn axi_gpio_fb axi_gpio_dbg axi_gpio_dbg2} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $ip_name/s_axi_aclk]
}

# ---- the framebuffer master reaches DDR through a protocol converter ----
# S_AXI_HP0 is AXI3 on Zynq-7000, so an AXI4 master cannot attach to it directly
# (BD 41-1285), and even as AXI3 the port caps INCR bursts at 16 beats while the
# line reader issues 200. SmartConnect does both jobs: AXI4 -> AXI3 and burst
# splitting. Same pattern the AWG design uses for its DMA on this port.
set sc_fb [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_fb_hp0]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc_fb
connect_bd_intf_net [get_bd_intf_pins sc_fb_hp0/M00_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP0]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins sc_fb_hp0/aclk]

# ---- reset ----
# proc_sys_reset parks AXI peripherals in reset when dcm_locked is left open.
set rst_clock_locked [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_clock_locked]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_clock_locked
set rst_aux_inactive [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 rst_aux_inactive]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $rst_aux_inactive
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]       [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N]   [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins rst_clock_locked/dout] [get_bd_pins rst_ps7/dcm_locked]
connect_bd_net [get_bd_pins rst_aux_inactive/dout] [get_bd_pins rst_ps7/aux_reset_in]
foreach ip_name {axi_gpio_led axi_gpio_btn axi_gpio_fb axi_gpio_dbg axi_gpio_dbg2} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins $ip_name/s_axi_aresetn]
}
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins sc_fb_hp0/aresetn]
# The interconnect needs its per-interface resets as well; leaving them dangling
# ties them to 0, which parks every AXI slave in reset (the GPIOs then never
# respond even though the design "validates").
connect_bd_net [get_bd_pins rst_ps7/interconnect_aresetn] [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/S00_ARESETN]
foreach mi {M00_ARESETN M01_ARESETN M02_ARESETN M03_ARESETN M04_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_interconnect_0/$mi]
}

# ---- 5" 800x480 TFT (RGB565) ----
# The pixel clock and its MMCM live inside lcd_overlay_top and run off the board's
# own 50 MHz oscillator (Y9), deliberately NOT off FCLK_CLK0: PYNQ's Overlay() does
# not apply the .hwh clock settings (a design declaring 50 MHz was measured running
# at 75 MHz), so a FCLK-derived pixel clock would silently take the image's boot
# default. The AXI side of the same cell does run on FCLK_CLK0 and reads the
# framebuffer through S_AXI_HP0; the two domains meet only inside lcd_fb's line
# buffers, via async-FIFO toggles. That crossing is why
# tools/impl1_clock_groups.tcl declares the two clock groups asynchronous before
# opt_design -- an XDC cannot do it here because clk_fpga_0 does not exist yet when
# the user constraints are read.
# The port carries no -freq_hz on purpose: that would make the BD emit its own
# create_clock, which then collides with the explicit one in constraints/led_btn.xdc.
create_bd_port -dir I -type clk clk50m
set lcd [create_bd_cell -type module -reference lcd_overlay_top lcd_0]

create_bd_port -dir O lcd_pclk
create_bd_port -dir O lcd_de
create_bd_port -dir O lcd_hs
create_bd_port -dir O lcd_vs
create_bd_port -dir O lcd_bl
create_bd_port -dir O -from 15 -to 0 lcd_rgb

connect_bd_net [get_bd_ports clk50m]      [get_bd_pins lcd_0/clk50m]
connect_bd_net [get_bd_pins lcd_0/lcd_pclk] [get_bd_ports lcd_pclk]
connect_bd_net [get_bd_pins lcd_0/lcd_de]   [get_bd_ports lcd_de]
connect_bd_net [get_bd_pins lcd_0/lcd_hs]   [get_bd_ports lcd_hs]
connect_bd_net [get_bd_pins lcd_0/lcd_vs]   [get_bd_ports lcd_vs]
connect_bd_net [get_bd_pins lcd_0/lcd_bl]   [get_bd_ports lcd_bl]
connect_bd_net [get_bd_pins lcd_0/lcd_rgb]  [get_bd_ports lcd_rgb]

connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]            [get_bd_pins lcd_0/aclk]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins lcd_0/aresetn]
connect_bd_net [get_bd_pins axi_gpio_fb/gpio_io_o]      [get_bd_pins lcd_0/fb_base]
# 一条 2 位控制字整条进 RTL, 由 lcd_overlay_top 拆成 fb_en / tp_rst —— BD 里的向量引脚
# 不能按位引用(get_bd_pins cell/pin[0] 匹配不到), 所以拆位放在 RTL 里做。
connect_bd_net [get_bd_pins axi_gpio_fb/gpio2_io_o]     [get_bd_pins lcd_0/fb_ctrl]
connect_bd_net [get_bd_pins lcd_0/tp_rst]               [get_bd_ports tp_rst]
connect_bd_net [get_bd_pins axi_gpio_dbg/gpio_io_i]     [get_bd_pins lcd_0/dbg0]
connect_bd_net [get_bd_pins axi_gpio_dbg/gpio2_io_i]    [get_bd_pins lcd_0/dbg1]
connect_bd_net [get_bd_pins axi_gpio_dbg2/gpio_io_i]    [get_bd_pins lcd_0/dbg2]
connect_bd_net [get_bd_pins axi_gpio_dbg2/gpio2_io_i]   [get_bd_pins lcd_0/dbg3]
connect_bd_intf_net [get_bd_intf_pins lcd_0/M_AXI]      [get_bd_intf_pins sc_fb_hp0/S00_AXI]

assign_bd_address
# The framebuffer master has to reach DDR, not just the GP0 registers.
set lcd_spaces [get_bd_addr_spaces -quiet -of_objects [get_bd_cells lcd_0]]
if {[llength $lcd_spaces] != 1} {
    error "expected one address space on lcd_0, got: $lcd_spaces"
}
assign_bd_address -target $lcd_spaces \
    [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
validate_bd_design
save_bd_design
make_wrapper -files [get_files ledbtn_bd.bd] -top -import
set wrapper [get_files -quiet -filter {NAME =~ *ledbtn_bd_wrapper*}]
if {[llength $wrapper] == 0} {
    error "wrapper generation failed"
}
set_property top [file rootname [file tail [lindex $wrapper 0]]] [current_fileset]
generate_target all [get_files ledbtn_bd.bd]
