# AC820 RF overlay pin constraints: AD-FMCOMMS3-EBZ on the FMC LPC connector.
#
# Authoritative source: docs/rf/compatibility.md 附二 (signal -> FMC pin -> LA ->
# AC820 pin -> bank), which is itself built from ADI's system_constr.xdc plus the
# vendor FMC xlsx plus Vivado's own get_package_pins.  Cross-checked against the
# user's working example (rf_overlay/rtl_ref/toppin.xdc), which ran on this board.
#
# Both halves of the FMC sit at VCCO = 2.5 V (VCCIO34 and VCCIO35 are one net,
# driven through the P18 jumper; TP8 measured 2.5 V).  That is what makes the
# LVDS_25 data half and the LVCMOS25 control half simultaneously legal:
#   * LA00-LA16  (data/clk + enable/txnrx) -> Bank 34, LVDS_25  (HR bank, VCCO 2.5)
#   * LA19-LA28  (SPI/resetb/GPIO)         -> Bank 35, LVCMOS25
#
# P18 MUST stay on the 2.5 V position.  The 3.3 V position would exceed the
# AD9361 VDD_INTERFACE absolute maximum (2.8 V) and risk damaging the part.

# RX half drives INTO the FPGA, so the diff pairs are terminated: DIFF_TERM TRUE.
set_property -dict {PACKAGE_PIN M19 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports rx_clk_in_p]
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports rx_clk_in_n]
set_property -dict {PACKAGE_PIN N19 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports rx_frame_in_p]
set_property -dict {PACKAGE_PIN N20 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports rx_frame_in_n]
set_property -dict {PACKAGE_PIN J21 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[0]}]
set_property -dict {PACKAGE_PIN J22 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[0]}]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[1]}]
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[1]}]
set_property -dict {PACKAGE_PIN J15 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[2]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[2]}]
set_property -dict {PACKAGE_PIN L17 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[3]}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[3]}]
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[4]}]
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[4]}]
set_property -dict {PACKAGE_PIN K16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_p[5]}]
set_property -dict {PACKAGE_PIN L16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {rx_data_in_n[5]}]

# TX half is driven BY the FPGA; no termination (nothing to terminate into).
set_property -dict {PACKAGE_PIN P17 IOSTANDARD LVDS_25} [get_ports tx_clk_out_p]
set_property -dict {PACKAGE_PIN P18 IOSTANDARD LVDS_25} [get_ports tx_clk_out_n]
set_property -dict {PACKAGE_PIN N22 IOSTANDARD LVDS_25} [get_ports tx_frame_out_p]
set_property -dict {PACKAGE_PIN P22 IOSTANDARD LVDS_25} [get_ports tx_frame_out_n]
set_property -dict {PACKAGE_PIN J18 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[0]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[0]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[1]}]
set_property -dict {PACKAGE_PIN K20 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[1]}]
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[2]}]
set_property -dict {PACKAGE_PIN T18 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[2]}]
set_property -dict {PACKAGE_PIN M21 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[3]}]
set_property -dict {PACKAGE_PIN M22 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[3]}]
set_property -dict {PACKAGE_PIN L21 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[4]}]
set_property -dict {PACKAGE_PIN L22 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[4]}]
set_property -dict {PACKAGE_PIN J20 IOSTANDARD LVDS_25} [get_ports {tx_data_out_p[5]}]
set_property -dict {PACKAGE_PIN K21 IOSTANDARD LVDS_25} [get_ports {tx_data_out_n[5]}]

# ---------------------------------------------------------------------------
# SPI control port (Bank 35).  CSN gets a pull-up: it must sit deasserted while
# the PL is unconfigured, so the AD9361 does not see a spurious burst before the
# overlay is loaded.
set_property -dict {PACKAGE_PIN B16 IOSTANDARD LVCMOS25 PULLTYPE PULLUP} [get_ports spi_csn]
set_property -dict {PACKAGE_PIN B17 IOSTANDARD LVCMOS25} [get_ports spi_sclk]
set_property -dict {PACKAGE_PIN D22 IOSTANDARD LVCMOS25} [get_ports spi_mosi]
set_property -dict {PACKAGE_PIN C22 IOSTANDARD LVCMOS25} [get_ports spi_miso]

# ---------------------------------------------------------------------------
# Mode / control pins, packed as rf_gpio[8:0] by create_bd.tcl:
#   bit0 txnrx  bit1 enable  bit2 resetb  bit3 sync_in  bit4 en_agc  bits8:5 ctl0-3
# enable/txnrx sit in Bank 34 (same bank as the LVDS data) but are ordinary
# LVCMOS25 -- a 2.5 V single-ended signal is legal in an HR bank, and the working
# example constrains them exactly this way.  Not moved to Bank 35 on purpose:
# staying identical to the verified design is worth more than tidier banking.
set_property -dict {PACKAGE_PIN J17 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[0]}]
set_property -dict {PACKAGE_PIN J16 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[1]}]
set_property -dict {PACKAGE_PIN F16 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[2]}]
set_property -dict {PACKAGE_PIN B22 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[3]}]
set_property -dict {PACKAGE_PIN B21 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[4]}]
set_property -dict {PACKAGE_PIN A18 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[5]}]
set_property -dict {PACKAGE_PIN A19 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[6]}]
set_property -dict {PACKAGE_PIN A16 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[7]}]
set_property -dict {PACKAGE_PIN A17 IOSTANDARD LVCMOS25}               [get_ports {rf_gpio[8]}]

# rf_gpio are static mode pins written once during bring-up; the AD9361 samples them
# asynchronously, so there is no board-level timing relationship to meet.  Use -to,
# not -from: an output port is an endpoint, and -from on it matches no startpoint
# (Vivado reports "list of objects ... contains no valid startpoints" and silently
# applies nothing, so the intent would not actually be honoured).
set_false_path -to [get_ports {rf_gpio[*]}]
# spi_miso is an input, so -from is the correct direction here.
set_false_path -from [get_ports spi_miso]

# ---------------------------------------------------------------------------
# Recovered sample clock.  DERIVED, not typed in.
#
# DATA_CLK arrives as a differential pair on rx_clk_in_p/n and reaches the phy
# through IBUFDS -> BUFG; the whole data path (phy_rx, phy_tx, rf_tx_source) runs
# on that recovered clock.
#
# DATA_CLK is 2 x the sample rate in 1R1T and 4 x in 2R2T.  That is not a
# convention, it falls out of the wire: phy_tx puts a 12-bit I and a 12-bit Q out
# over 6 DDR lanes, which is 12 bits per DATA_CLK cycle, so a 24-bit sample takes
# exactly 2 cycles in 1R1T and 4 in 2R2T (two channels interleaved on the same
# bus).  At 60 MSPS that is 120 MHz and 240 MHz.
#
# The overlay switches between those framings AT RUN TIME, and one static
# constraint can only declare one period, so it declares the WORSE one.  A
# constraint looser than reality is worse than useless -- it reports MET for
# paths that would fail.  The previous value here was 33.333 ns, from a comment
# claiming DATA_CLK was 30 MHz (i.e. fs/2, half a clock per sample, which the
# bus arithmetic above rules out), and it survived several builds because
# nothing downstream could tell.
#
# Measured on this board 2026-09-19: the AD9361's own post-recompute clock
# readback gives CLKTF = 120 MHz with TX_SAMPL = 60 MHz, and the DDS sideband
# offset measures 1888.0 kHz against 1875.0 kHz predicted for 120 MHz and
# 312.5 kHz for 20 MHz.  Do not copy the example's create_clock -period 40.000
# either; that implies 25 MHz and is equally unsourced.
#
# If the sample rate changes, change RF_SAMPLE_RATE_HZ and nothing else.
set RF_SAMPLE_RATE_HZ 60000000
set RF_DATA_CLK_HZ [expr {4 * $RF_SAMPLE_RATE_HZ}]
create_clock -name data_clk -period [expr {1.0e9 / $RF_DATA_CLK_HZ}] [get_ports rx_clk_in_p]

# The asynchronous relationship between this recovered clock and the PS FCLK0/FCLK1
# domains cannot be declared here: clk_fpga_0 does not exist yet when user
# constraints are read.  tools/impl1_clock_groups.tcl does it before opt_design,
# the same way the AWG and pynq_overlay builds handle their cross-domain paths.

# Unused pins: match the board's other designs so unconfigured FPC/HDMI pins stay
# in the vendor example's state.
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]
