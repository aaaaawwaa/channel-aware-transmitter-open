/***************************************************************************//**
 *   @file   main.c
 *   @brief  Implementation of Main Function.
 *   @author DBogdan (dragos.bogdan@analog.com)
********************************************************************************
 * Copyright 2013(c) Analog Devices, Inc.
 *
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *  - Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *  - Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in
 *    the documentation and/or other materials provided with the
 *    distribution.
 *  - Neither the name of Analog Devices, Inc. nor the names of its
 *    contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 *  - The use of this software may or may not infringe the patent rights
 *    of one or more patent holders.  This license does not release you
 *    from the requirement that you obtain separate licenses from these
 *    patent holders to use this software.
 *  - Use of the software either in source or binary form, must be run
 *    on or directly connected to an Analog Devices Inc. component.
 *
 * THIS SOFTWARE IS PROVIDED BY ANALOG DEVICES "AS IS" AND ANY EXPRESS OR
 * IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, NON-INFRINGEMENT,
 * MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 * IN NO EVENT SHALL ANALOG DEVICES BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, INTELLECTUAL PROPERTY RIGHTS, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*******************************************************************************/

/******************************************************************************/
/***************************** Include Files **********************************/
/******************************************************************************/
#include "config.h"
#include "ad9361_api.h"
#include "platform.h"
#include <xil_cache.h>
#include "xil_io.h"
#include "xgpio.h"  // [新增] 引入 AXI GPIO 驱动
#include "sleep.h"  // [Added] For usleep function to fix warning

#include "xil_io.h" // 必须包含这个

// 这里请填入你在 Vivado Address Editor 里亲眼看到的 axi_gpio_0 地址
// 如果 Vivado 显示 41200000 就写 0x41200000
// 如果 Vivado 显示 44C00000 就写 0x44C00000
#define MANUAL_GPIO_BASE_ADDR      0x41200000

#define AXI_CTRL_BASE XPAR_AXI_CTRL_V1_0_0_BASEADDR
#define I_DELAY_EN		AXI_CTRL_BASE+0
#define I_DELAY_TAP 	AXI_CTRL_BASE+4
#define DDS_PHASE_INC	AXI_CTRL_BASE+8
#define PHY_MODE    	AXI_CTRL_BASE+12
#define GPIO_DEVICE_ID				XPAR_PS7_GPIO_0_DEVICE_ID
#define SPI_DEVICE_ID				XPAR_PS7_SPI_0_DEVICE_ID

// ============================================================================
//  AXI GPIO Configuration (For Carrier Frequency Readback)
// ============================================================================
// Device ID from xparameters.h
#ifndef XPAR_AXI_GPIO_0_DEVICE_ID
#define XPAR_AXI_GPIO_0_DEVICE_ID 0
#endif

// Channel Definitions (Matches Vivado Block Design)
#define GPIO_CHANNEL_FREQ       1   // Channel 1: 32-bit Carrier Frequency
#define GPIO_CHANNEL_LOCK       2   // Channel 2: 1-bit Lock Status

#define EMIO_TXNRX        		54+0
#define EMIO_ENABLE				54+1
#define EMIO_RESET				54+2
#define EMIO_SYNC				54+3
#define EMIO_EN_AGC				54+4
#define EMIO_CTL0				54+5
#define EMIO_CTL1				54+6
#define EMIO_CTL2				54+7
#define EMIO_CTL3				54+8

#define RF_GAIN_MGC              0
#define RF_GAIN_FASTATTACK_AGC   1
#define RF_GAIN_SLOWATTACK_AGC   2
#define RF_GAIN_HYBRID_AGC       3



#define AD9361_PHY_MODE_1R1T 1
#define AD9361_PHY_MODE_2R2T 0
uint32_t sample_rate = 60000000;
uint64_t tx_lo_freq = 80000000;
uint64_t rx_lo_freq = 80000000;
uint32_t bandwidth = 30000000;
int32_t gain = 30;
uint32_t txatt = 10000;
struct ad9361_rf_phy *ad9361_phy;

void AXI_REG_WRITE(u32 RegOffset, u32 Data);

AD9361_InitParam default_init_param =
{
	/* Device selection */
	ID_AD9361,	// dev_sel
	/* Identification number */
	0,		//id_no;
	/* Reference Clock */
	40000000UL,	//reference_clk_rate
	/* Base Configuration */
	0,		//two_rx_two_tx_mode_enable *** adi,2rx-2tx-mode-enable //更改为1R1T
	0,		//one_rx_one_tx_mode_use_rx_num *** adi,1rx-1tx-mode-use-rx-num
	0,		//one_rx_one_tx_mode_use_tx_num *** adi,1rx-1tx-mode-use-tx-num
	1,//frequency_division_duplex_mode_enable *** adi,frequency-division-duplex-mode-enable //使能FDD模式 (频分双工)
	0,//frequency_division_duplex_independent_mode_enable *** adi,frequency-division-duplex-independent-mode-enable
	0,//tdd_use_dual_synth_mode_enable *** adi,tdd-use-dual-synth-mode-enable
	0,		//tdd_skip_vco_cal_enable *** adi,tdd-skip-vco-cal-enable
	0,		//tx_fastlock_delay_ns *** adi,tx-fastlock-delay-ns
	0,		//rx_fastlock_delay_ns *** adi,rx-fastlock-delay-ns
	0,		//rx_fastlock_pincontrol_enable *** adi,rx-fastlock-pincontrol-enable
	0,		//tx_fastlock_pincontrol_enable *** adi,tx-fastlock-pincontrol-enable
	0,		//external_rx_lo_enable *** adi,external-rx-lo-enable
	0,		//external_tx_lo_enable *** adi,external-tx-lo-enable
	5,		//dc_offset_tracking_update_event_mask *** adi,dc-offset-tracking-update-event-mask
	6,		//dc_offset_attenuation_high_range *** adi,dc-offset-attenuation-high-range
	5,		//dc_offset_attenuation_low_range *** adi,dc-offset-attenuation-low-range
	0x28,	//dc_offset_count_high_range *** adi,dc-offset-count-high-range
	0x32,	//dc_offset_count_low_range *** adi,dc-offset-count-low-range
	0,		//split_gain_table_mode_enable *** adi,split-gain-table-mode-enable
	MAX_SYNTH_FREF,	//trx_synthesizer_target_fref_overwrite_hz *** adi,trx-synthesizer-target-fref-overwrite-hz
	0,		// qec_tracking_slow_mode_enable *** adi,qec-tracking-slow-mode-enable
	/* ENSM Control */
	0,		//ensm_enable_pin_pulse_mode_enable *** adi,ensm-enable-pin-pulse-mode-enable
	0,		//ensm_enable_txnrx_control_enable *** adi,ensm-enable-txnrx-control-enable
	/* LO Control */
	2400000000UL,	//rx_synthesizer_frequency_hz *** adi,rx-synthesizer-frequency-hz //RX合成器频率（初始值，后续会修改）
	2400000000UL,	//tx_synthesizer_frequency_hz *** adi,tx-synthesizer-frequency-hz //TX合成器频率（初始值，后续会修改）
	1,				//tx_lo_powerdown_managed_enable *** adi,tx-lo-powerdown-managed-enable
	/* Rate & BW Control */ // RX时钟路径频率 (由API自动计算，这里给默认值)
	{ 1280000000, 320000000, 160000000, 80000000, 40000000, 40000000 },	//uint32_t	rx_path_clock_frequencies[6] *** adi,rx-path-clock-frequencies
	{ 1280000000, 160000000, 160000000, 80000000, 40000000, 40000000 },	//uint32_t	tx_path_clock_frequencies[6] *** adi,tx-path-clock-frequencies
	20000000,//rf_rx_bandwidth_hz *** adi,rf-rx-bandwidth-hz
	20000000,//rf_tx_bandwidth_hz *** adi,rf-tx-bandwidth-hz
	/* RF Port Control */
	0,		//rx_rf_port_input_select *** adi,rx-rf-port-input-select
	0,		//tx_rf_port_input_select *** adi,tx-rf-port-input-select
	/* TX Attenuation Control */
	10000,	//tx_attenuation_mdB *** adi,tx-attenuation-mdB
	0,		//update_tx_gain_in_alert_enable *** adi,update-tx-gain-in-alert-enable
	/* Reference Clock Control */
	0,		//xo_disable_use_ext_refclk_enable *** adi,xo-disable-use-ext-refclk-enable
	{8, 5920},	//dcxo_coarse_and_fine_tune[2] *** adi,dcxo-coarse-and-fine-tune
	0,	//clk_output_mode_select *** adi,clk-output-mode-select
	/* Gain Control */
	2,		//gc_rx1_mode *** adi,gc-rx1-mode    //改为RF_GAIN_SLOWATTACK_AGC
	2,		//gc_rx2_mode *** adi,gc-rx2-mode    //改为RF_GAIN_SLOWATTACK_AGC
	58,		//gc_adc_large_overload_thresh *** adi,gc-adc-large-overload-thresh
	4,		//gc_adc_ovr_sample_size *** adi,gc-adc-ovr-sample-size
	47,		//gc_adc_small_overload_thresh *** adi,gc-adc-small-overload-thresh
	8192,	//gc_dec_pow_measurement_duration *** adi,gc-dec-pow-measurement-duration
	0,		//gc_dig_gain_enable *** adi,gc-dig-gain-enable
	800,//gc_lmt_overload_high_thresh *** adi,gc-lmt-overload-high-thresh
	704,	//gc_lmt_overload_low_thresh *** adi,gc-lmt-overload-low-thresh
	24,		//gc_low_power_thresh *** adi,gc-low-power-thresh
	15,		//gc_max_dig_gain *** adi,gc-max-dig-gain
	/* Gain MGC Control */
	2,		//mgc_dec_gain_step *** adi,mgc-dec-gain-step
	2,		//mgc_inc_gain_step *** adi,mgc-inc-gain-step
	0,		//mgc_rx1_ctrl_inp_enable *** adi,mgc-rx1-ctrl-inp-enable
	0,		//mgc_rx2_ctrl_inp_enable *** adi,mgc-rx2-ctrl-inp-enable
	0,		//mgc_split_table_ctrl_inp_gain_mode *** adi,mgc-split-table-ctrl-inp-gain-mode
	/* Gain AGC Control */
	10,		//agc_adc_large_overload_exceed_counter *** adi,agc-adc-large-overload-exceed-counter
	2,		//agc_adc_large_overload_inc_steps *** adi,agc-adc-large-overload-inc-steps
	0,		//agc_adc_lmt_small_overload_prevent_gain_inc_enable *** adi,agc-adc-lmt-small-overload-prevent-gain-inc-enable
	10,		//agc_adc_small_overload_exceed_counter *** adi,agc-adc-small-overload-exceed-counter
	4,		//agc_dig_gain_step_size *** adi,agc-dig-gain-step-size
	3,		//agc_dig_saturation_exceed_counter *** adi,agc-dig-saturation-exceed-counter
	1000,	// agc_gain_update_interval_us *** adi,agc-gain-update-interval-us
	0,		//agc_immed_gain_change_if_large_adc_overload_enable *** adi,agc-immed-gain-change-if-large-adc-overload-enable
	0,		//agc_immed_gain_change_if_large_lmt_overload_enable *** adi,agc-immed-gain-change-if-large-lmt-overload-enable
	10,		//agc_inner_thresh_high *** adi,agc-inner-thresh-high
	1,		//agc_inner_thresh_high_dec_steps *** adi,agc-inner-thresh-high-dec-steps
	12,		//agc_inner_thresh_low *** adi,agc-inner-thresh-low
	1,		//agc_inner_thresh_low_inc_steps *** adi,agc-inner-thresh-low-inc-steps
	10,		//agc_lmt_overload_large_exceed_counter *** adi,agc-lmt-overload-large-exceed-counter
	2,		//agc_lmt_overload_large_inc_steps *** adi,agc-lmt-overload-large-inc-steps
	10,		//agc_lmt_overload_small_exceed_counter *** adi,agc-lmt-overload-small-exceed-counter
	5,		//agc_outer_thresh_high *** adi,agc-outer-thresh-high
	2,		//agc_outer_thresh_high_dec_steps *** adi,agc-outer-thresh-high-dec-steps
	18,		//agc_outer_thresh_low *** adi,agc-outer-thresh-low
	2,		//agc_outer_thresh_low_inc_steps *** adi,agc-outer-thresh-low-inc-steps
	1,		//agc_attack_delay_extra_margin_us; *** adi,agc-attack-delay-extra-margin-us
	0,		//agc_sync_for_gain_counter_enable *** adi,agc-sync-for-gain-counter-enable
	/* Fast AGC */
	64,		//fagc_dec_pow_measuremnt_duration ***  adi,fagc-dec-pow-measurement-duration
	260,	//fagc_state_wait_time_ns ***  adi,fagc-state-wait-time-ns
	/* Fast AGC - Low Power */
	0,		//fagc_allow_agc_gain_increase ***  adi,fagc-allow-agc-gain-increase-enable
	5,		//fagc_lp_thresh_increment_time ***  adi,fagc-lp-thresh-increment-time
	1,		//fagc_lp_thresh_increment_steps ***  adi,fagc-lp-thresh-increment-steps
	/* Fast AGC - Lock Level */
	1,		//fagc_lock_level_lmt_gain_increase_en ***  adi,fagc-lock-level-lmt-gain-increase-enable
	5,		//fagc_lock_level_gain_increase_upper_limit ***  adi,fagc-lock-level-gain-increase-upper-limit
	/* Fast AGC - Peak Detectors and Final Settling */
	1,		//fagc_lpf_final_settling_steps ***  adi,fagc-lpf-final-settling-steps
	1,		//fagc_lmt_final_settling_steps ***  adi,fagc-lmt-final-settling-steps
	3,		//fagc_final_overrange_count ***  adi,fagc-final-overrange-count
	/* Fast AGC - Final Power Test */
	0,		//fagc_gain_increase_after_gain_lock_en ***  adi,fagc-gain-increase-after-gain-lock-enable
	/* Fast AGC - Unlocking the Gain */
	0,		//fagc_gain_index_type_after_exit_rx_mode ***  adi,fagc-gain-index-type-after-exit-rx-mode
	1,		//fagc_use_last_lock_level_for_set_gain_en ***  adi,fagc-use-last-lock-level-for-set-gain-enable
	1,		//fagc_rst_gla_stronger_sig_thresh_exceeded_en ***  adi,fagc-rst-gla-stronger-sig-thresh-exceeded-enable
	5,		//fagc_optimized_gain_offset ***  adi,fagc-optimized-gain-offset
	10,		//fagc_rst_gla_stronger_sig_thresh_above_ll ***  adi,fagc-rst-gla-stronger-sig-thresh-above-ll
	1,		//fagc_rst_gla_engergy_lost_sig_thresh_exceeded_en ***  adi,fagc-rst-gla-engergy-lost-sig-thresh-exceeded-enable
	1,		//fagc_rst_gla_engergy_lost_goto_optim_gain_en ***  adi,fagc-rst-gla-engergy-lost-goto-optim-gain-enable
	10,		//fagc_rst_gla_engergy_lost_sig_thresh_below_ll ***  adi,fagc-rst-gla-engergy-lost-sig-thresh-below-ll
	8,		//fagc_energy_lost_stronger_sig_gain_lock_exit_cnt ***  adi,fagc-energy-lost-stronger-sig-gain-lock-exit-cnt
	1,		//fagc_rst_gla_large_adc_overload_en ***  adi,fagc-rst-gla-large-adc-overload-enable
	1,		//fagc_rst_gla_large_lmt_overload_en ***  adi,fagc-rst-gla-large-lmt-overload-enable
	0,		//fagc_rst_gla_en_agc_pulled_high_en ***  adi,fagc-rst-gla-en-agc-pulled-high-enable
	0,		//fagc_rst_gla_if_en_agc_pulled_high_mode ***  adi,fagc-rst-gla-if-en-agc-pulled-high-mode
	64,		//fagc_power_measurement_duration_in_state5 ***  adi,fagc-power-measurement-duration-in-state5
	/* RSSI Control */
	1,		//rssi_delay *** adi,rssi-delay
	1000,	//rssi_duration *** adi,rssi-duration
	3,		//rssi_restart_mode *** adi,rssi-restart-mode
	0,		//rssi_unit_is_rx_samples_enable *** adi,rssi-unit-is-rx-samples-enable
	1,		//rssi_wait *** adi,rssi-wait
	/* Aux ADC Control */
	256,	//aux_adc_decimation *** adi,aux-adc-decimation
	40000000UL,	//aux_adc_rate *** adi,aux-adc-rate
	/* AuxDAC Control */
	1,		//aux_dac_manual_mode_enable ***  adi,aux-dac-manual-mode-enable
	0,		//aux_dac1_default_value_mV ***  adi,aux-dac1-default-value-mV
	0,		//aux_dac1_active_in_rx_enable ***  adi,aux-dac1-active-in-rx-enable
	0,		//aux_dac1_active_in_tx_enable ***  adi,aux-dac1-active-in-tx-enable
	0,		//aux_dac1_active_in_alert_enable ***  adi,aux-dac1-active-in-alert-enable
	0,		//aux_dac1_rx_delay_us ***  adi,aux-dac1-rx-delay-us
	0,		//aux_dac1_tx_delay_us ***  adi,aux-dac1-tx-delay-us
	0,		//aux_dac2_default_value_mV ***  adi,aux-dac2-default-value-mV
	0,		//aux_dac2_active_in_rx_enable ***  adi,aux-dac2-active-in-rx-enable
	0,		//aux_dac2_active_in_tx_enable ***  adi,aux-dac2-active-in-tx-enable
	0,		//aux_dac2_active_in_alert_enable ***  adi,aux-dac2-active-in-alert-enable
	0,		//aux_dac2_rx_delay_us ***  adi,aux-dac2-rx-delay-us
	0,		//aux_dac2_tx_delay_us ***  adi,aux-dac2-tx-delay-us
	/* Temperature Sensor Control */
	256,	//temp_sense_decimation *** adi,temp-sense-decimation
	1000,	//temp_sense_measurement_interval_ms *** adi,temp-sense-measurement-interval-ms
	0xCE,	//temp_sense_offset_signed *** adi,temp-sense-offset-signed
	1,		//temp_sense_periodic_measurement_enable *** adi,temp-sense-periodic-measurement-enable
	/* Control Out Setup */
	0xFF,	//ctrl_outs_enable_mask *** adi,ctrl-outs-enable-mask
	0,		//ctrl_outs_index *** adi,ctrl-outs-index
	/* External LNA Control */
	0,		//elna_settling_delay_ns *** adi,elna-settling-delay-ns
	0,		//elna_gain_mdB *** adi,elna-gain-mdB
	0,		//elna_bypass_loss_mdB *** adi,elna-bypass-loss-mdB
	0,		//elna_rx1_gpo0_control_enable *** adi,elna-rx1-gpo0-control-enable
	0,		//elna_rx2_gpo1_control_enable *** adi,elna-rx2-gpo1-control-enable
	0,		//elna_gaintable_all_index_enable *** adi,elna-gaintable-all-index-enable
	/* Digital Interface Control */
	0,		//digital_interface_tune_skip_mode *** adi,digital-interface-tune-skip-mode
	0,		//digital_interface_tune_fir_disable *** adi,digital-interface-tune-fir-disable
	1,		//pp_tx_swap_enable *** adi,pp-tx-swap-enable
	1,		//pp_rx_swap_enable *** adi,pp-rx-swap-enable
	0,		//tx_channel_swap_enable *** adi,tx-channel-swap-enable
	0,		//rx_channel_swap_enable *** adi,rx-channel-swap-enable
	1,		//rx_frame_pulse_mode_enable *** adi,rx-frame-pulse-mode-enable
	0,		//two_t_two_r_timing_enable *** adi,2t2r-timing-enable
	0,		//invert_data_bus_enable *** adi,invert-data-bus-enable
	0,		//invert_data_clk_enable *** adi,invert-data-clk-enable
	0,		//fdd_alt_word_order_enable *** adi,fdd-alt-word-order-enable
	0,		//invert_rx_frame_enable *** adi,invert-rx-frame-enable
	0,		//fdd_rx_rate_2tx_enable *** adi,fdd-rx-rate-2tx-enable
	0,		//swap_ports_enable *** adi,swap-ports-enable
	0,		//single_data_rate_enable *** adi,single-data-rate-enable
	1,		//lvds_mode_enable *** adi,lvds-mode-enable
	0,		//half_duplex_mode_enable *** adi,half-duplex-mode-enable
	0,		//single_port_mode_enable *** adi,single-port-mode-enable
	0,		//full_port_enable *** adi,full-port-enable
	0,		//full_duplex_swap_bits_enable *** adi,full-duplex-swap-bits-enable
	0,		//delay_rx_data *** adi,delay-rx-data
	0,		//rx_data_clock_delay *** adi,rx-data-clock-delay
	4,		//rx_data_delay *** adi,rx-data-delay
	7,		//tx_fb_clock_delay *** adi,tx-fb-clock-delay
	0,		//tx_data_delay *** adi,tx-data-delay
	150,	//lvds_bias_mV *** adi,lvds-bias-mV
	1,		//lvds_rx_onchip_termination_enable *** adi,lvds-rx-onchip-termination-enable
	0,		//rx1rx2_phase_inversion_en *** adi,rx1-rx2-phase-inversion-enable
	0xFF,	//lvds_invert1_control *** adi,lvds-invert1-control
	0x0F,	//lvds_invert2_control *** adi,lvds-invert2-control
	/* GPO Control */
	0,		//gpo0_inactive_state_high_enable *** adi,gpo0-inactive-state-high-enable
	0,		//gpo1_inactive_state_high_enable *** adi,gpo1-inactive-state-high-enable
	0,		//gpo2_inactive_state_high_enable *** adi,gpo2-inactive-state-high-enable
	0,		//gpo3_inactive_state_high_enable *** adi,gpo3-inactive-state-high-enable
	0,		//gpo0_slave_rx_enable *** adi,gpo0-slave-rx-enable
	0,		//gpo0_slave_tx_enable *** adi,gpo0-slave-tx-enable
	0,		//gpo1_slave_rx_enable *** adi,gpo1-slave-rx-enable
	0,		//gpo1_slave_tx_enable *** adi,gpo1-slave-tx-enable
	0,		//gpo2_slave_rx_enable *** adi,gpo2-slave-rx-enable
	0,		//gpo2_slave_tx_enable *** adi,gpo2-slave-tx-enable
	0,		//gpo3_slave_rx_enable *** adi,gpo3-slave-rx-enable
	0,		//gpo3_slave_tx_enable *** adi,gpo3-slave-tx-enable
	0,		//gpo0_rx_delay_us *** adi,gpo0-rx-delay-us
	0,		//gpo0_tx_delay_us *** adi,gpo0-tx-delay-us
	0,		//gpo1_rx_delay_us *** adi,gpo1-rx-delay-us
	0,		//gpo1_tx_delay_us *** adi,gpo1-tx-delay-us
	0,		//gpo2_rx_delay_us *** adi,gpo2-rx-delay-us
	0,		//gpo2_tx_delay_us *** adi,gpo2-tx-delay-us
	0,		//gpo3_rx_delay_us *** adi,gpo3-rx-delay-us
	0,		//gpo3_tx_delay_us *** adi,gpo3-tx-delay-us
	/* Tx Monitor Control */
	37000,	//low_high_gain_threshold_mdB *** adi,txmon-low-high-thresh
	0,		//low_gain_dB *** adi,txmon-low-gain
	24,		//high_gain_dB *** adi,txmon-high-gain
	0,		//tx_mon_track_en *** adi,txmon-dc-tracking-enable
	0,		//one_shot_mode_en *** adi,txmon-one-shot-mode-enable
	511,	//tx_mon_delay *** adi,txmon-delay
	8192,	//tx_mon_duration *** adi,txmon-duration
	2,		//tx1_mon_front_end_gain *** adi,txmon-1-front-end-gain
	2,		//tx2_mon_front_end_gain *** adi,txmon-2-front-end-gain
	48,		//tx1_mon_lo_cm *** adi,txmon-1-lo-cm
	48 		//tx2_mon_lo_cm *** adi,txmon-2-lo-cm
	/* GPIO definitions */
};

/*AD9361_RXFIRConfig rx_fir_config =
{
	3, // rx;
	-6, // rx_gain;
	2, // rx_dec;
	{ -15 - 79, -116, -137, -119, -59, 27, 101, 127, 89, 4, -77, -102, -48,
			58, 151, 165, 78, -64, -170, -161, -27, 156, 266, 216, 18, -210,
			-312, -199, 80, 350, 420, 213, -163, -466, -471, -135, 352, 664,
			558, 49, -559, -847, -565, 172, 909, 1118, 564, -485, -1363,
			-1405, -417, 1101, 2163, 1879, 91, -2340, -3829, -2858, 1132,
			7263, 13459, 17341, 17341, 13459, 7263, 1132, -2858, -3829,
			-2340, 91, 1879, 2163, 1101, -417, -1405, -1363, -485, 564,
			1118, 909, 172, -565, -847, -559, 49, 558, 664, 352, -135, -471,
			-466, -163, 213, 420, 350, 80, -199, -312, -210, 18, 216, 266,
			156, -27, -161, -170, -64, 78, 165, 151, 58, -48, -102, -77, 4,
			89, 127, 101, 27, -59, -119, -137, -116, -79, -39, -15 }, // rx_coef[128]
	128, // rx_coef_size
	{ 0, 0, 0, 0, 0, 0 }, //rx_path_clks[6]
	0 // rx_bandwidth
};*/

// ----------------------------------------------------------------------------
// FIR Configuration 1: Wide band Search Mode (60MSPS, 30M BW)
// ----------------------------------------------------------------------------
AD9361_RXFIRConfig rx_fir_config_search = {
    3, -6, 1, // rx_dec=1
    { 0, 0, -1, 1, -2, 3, -4, 4, -4, 4, -2, 0, 3, -8,
      13, -19, 26, -32, 38, -43, 46, -46, 44, -39, 29, -16, 0, 20, -42, 66,
      -91, 115, -136, 154, -167, 172, -169, 156, -133, 99, -55, 0, 63, -133, 208, -284, 358, -426,
      483, -527, 550, -550, 520, -457, 354, -204, 0, 272, -633, 1125,
      -1838, 3009, -5521, 17341, 17341, -5521, 3009, -1838, 1125, -633,
      272, 0, -204, 354, -457, 520, -550, 550, -527, 483,
      -426, 358, -284, 208, -133, 63, 0, -55, 99, -133,
      156, -169, 172, -167, 154, -136, 115, -91, 66, -42,
      20, 0, -16, 29, -39, 44, -46, 46, -43, 38, -32, 26, -19, 13,
      -8, 3, 0, -2, 4, -4, 4, -4 ,3, -2 ,1, -1, 0, 0 },
    128, { 0, 0, 0, 0, 0, 0 }, 30000000
};

// ----------------------------------------------------------------------------
// FIR Configuration 2: Narrow band Demodulation Mode (4.8MSPS, 50k BW)
// ----------------------------------------------------------------------------
AD9361_RXFIRConfig rx_fir_config_demod = {
    3, -6, 2, // rx_dec=2
    { -15, -79, -116, -137, -119, -59, 27, 101, 127, 89, 4, -77, -102, -48,
      58, 151, 165, 78, -64, -170, -161, -27, 156, 266, 216, 18, -210,
      -312, -199, 80, 350, 420, 213, -163, -466, -471, -135, 352, 664,
      558, 49, -559, -847, -565, 172, 909, 1118, 564, -485, -1363,
      -1405, -417, 1101, 2163, 1879, 91, -2340, -3829, -2858, 1132,
      7263, 13459, 17341, 17341, 13459, 7263, 1132, -2858, -3829,
      -2340, 91, 1879, 2163, 1101, -417, -1405, -1363, -485, 564,
      1118, 909, 172, -565, -847, -559, 49, 558, 664, 352, -135, -471,
      -466, -163, 213, 420, 350, 80, -199, -312, -210, 18, 216, 266,
      156, -27, -161, -170, -64, 78, 165, 151, 58, -48, -102, -77, 4,
      89, 127, 101, 27, -59, -119, -137, -116, -79, -39, -15 },
    128, { 0, 0, 0, 0, 0, 0 }, 50000
};

AD9361_TXFIRConfig tx_fir_config =
{
	3, // tx;
	0, // tx_gain;
	2, // tx_inc;
	{ -23, -44, -92, -124, -144, -114, -47, 46, 119, 141, 93, 4, -79, -98,
			-37, 73, 164, 170, 75, -69, -171, -154, -13, 171, 275, 215, 9,
			-218, -311, -187, 97, 363, 421, 203, -177, -471, -464, -118,
			368, 670, 550, 33, -571, -846, -551, 190, 918, 1114, 550, -498,
			-1364, -1394, -402, 1107, 2154, 1863, 84, -2324, -3793, -2826,
			1125, 7188, 13311, 17146, 17146, 13311, 7188, 1125, -2826,
			-3793, -2324, 84, 1863, 2154, 1107, -402, -1394, -1364, -498,
			550, 1114, 918, 190, -551, -846, -571, 33, 550, 670, 368, -118,
			-464, -471, -177, 203, 421, 363, 97, -187, -311, -218, 9, 215,
			275, 171, -13, -154, -171, -69, 75, 170, 164, 73, -37, -98, -79,
			4, 93, 141, 119, 46, -47, -114, -144, -124, -92, -44, -23 }, // tx_coef[128]
	128, // tx_coef_size
	{ 0, 0, 0, 0, 0, 0 }, // tx_path_clks[6]
	0 // tx_bandwidth
};


void gpio_initial()
{   // 配置GPIO方向为输出
	gpio_direction(EMIO_TXNRX  ,1);
	gpio_direction(EMIO_ENABLE ,1);
	gpio_direction(EMIO_RESET  ,1);
	gpio_direction(EMIO_SYNC   ,1);
	gpio_direction(EMIO_EN_AGC     ,1);
	gpio_direction(EMIO_CTL0   ,1);
	gpio_direction(EMIO_CTL1   ,1);
	gpio_direction(EMIO_CTL2   ,1);
	gpio_direction(EMIO_CTL3   ,1);

	 // 设置初始值
	gpio_set_value(EMIO_TXNRX  ,1);// 设置为RX模式
	gpio_set_value(EMIO_ENABLE ,0);// 初始禁用，//改为0
	gpio_set_value(EMIO_RESET  ,1);// 复位释放
	gpio_set_value(EMIO_SYNC   ,0);// 同步信号 //改为0
	gpio_set_value(EMIO_EN_AGC  ,0);// 禁用硬件AGC (使用软件AGC)//此处已禁用，改为0
	gpio_set_value(EMIO_CTL0 ,0);
	gpio_set_value(EMIO_CTL1 ,0);
	gpio_set_value(EMIO_CTL2 ,0);
	gpio_set_value(EMIO_CTL3 ,0);
}

/***************************************************************************//**
 * @brief main
*******************************************************************************/
int main(void)
{
	Xil_ICacheEnable();// 使能指令缓存
	Xil_DCacheEnable();// 使能数据缓存

	// 1. Initialize GPIO & SPI (PS Side)
	//ad936x resetpin
	// 配置AD9361的复位引脚和GPIO参数
	//default_init_param 是 AD9361 的初始化参数结构体，用于指定硬件连接方式（如引脚映射）
	default_init_param.gpio_resetb = EMIO_RESET;// 复位引脚使用EMIO（FPGA扩展IO）
	default_init_param.gpio_sync=-1;            // 不使用sync引脚
	default_init_param.gpio_cal_sw1=-1;         // 不使用校准开关1
	default_init_param.gpio_cal_sw2=-1;         // 不使用校准开关2

	printf("Initializing GPIO...\r");

	gpio_init(GPIO_DEVICE_ID);                  // 初始化GPIO控制器（基于设备ID）
	gpio_direction(default_init_param.gpio_resetb, 1);// 配置复位引脚为输出方向（1表示输出）
	gpio_initial();	//init gpio emio            // 初始化GPIO EMIO（具体初始化细节在该函数中实现）

	printf("Initializing SPI...\r");

	spi_init(SPI_DEVICE_ID, 1, 0);	            //init SPI

	// 初始化 AD9361
	printf("Initializing AD9361...\r");

// 2. Initialize AXI GPIO for Carrier Detection (PL Side)
	    printf("System Init: AXI GPIO...\r");
	    XGpio CarrierGpio;
	    int Status = XGpio_Initialize(&CarrierGpio, XPAR_AXI_GPIO_0_DEVICE_ID);
	    if (Status != XST_SUCCESS) {
	        printf("AXI GPIO Init Failed!\r");
	        return XST_FAILURE;
	    }
	    // Set both channels to Input
	    XGpio_SetDataDirection(&CarrierGpio, GPIO_CHANNEL_FREQ, 0xFFFFFFFF);
	    XGpio_SetDataDirection(&CarrierGpio, GPIO_CHANNEL_LOCK, 0xFFFFFFFF);

// 3. Initialize AD9361
	//initial ad936x
	ad9361_init(&ad9361_phy, &default_init_param);//ad9361_phy 是 AD9361 设备的句柄（结构体），后续操作通过该句柄访问设备

	printf("AD9361 initialized successfully.\n\r");

// 4. Configure Initial Search Mode (Wideband)
	printf("Configuring Search Mode (60MSPS, 80MHz LO)...\r");

	//set fir parameter // 配置发送（TX）和接收（RX）的FIR滤波器，
	//tx_fir_config 和 rx_fir_config 是预定义的滤波器参数结构体（包含系数、阶数等）
	ad9361_set_tx_fir_config(ad9361_phy, tx_fir_config);// 设置TX FIR参数
	ad9361_set_rx_fir_config(ad9361_phy, rx_fir_config_search);// 设置RX FIR参数
	ad9361_set_tx_fir_en_dis(ad9361_phy, 1);            // 使能TX FIR滤波器（1表示使能）
	ad9361_set_rx_fir_en_dis(ad9361_phy, 1);            // 使能RX FIR滤波器

	int32_t ret;
	// 配置采样率
	printf("Configuring sample rates...\r");

	//set sample rate  // 设置TX和RX的采样率（基带采样频率）
	ad9361_set_tx_sampling_freq(ad9361_phy, 60000000);
	ad9361_set_rx_sampling_freq(ad9361_phy, 60000000);
	//set lo           // 设置TX和RX的射频本振频率（决定工作频段）
	ad9361_set_tx_lo_freq(ad9361_phy, 80000000);
	ad9361_set_rx_lo_freq(ad9361_phy, 80000000);
	//set bandwidth    // 设置TX和RX的射频带宽（信号通过的频率范围）
	ad9361_set_tx_rf_bandwidth(ad9361_phy, 30000000);
	ad9361_set_rx_rf_bandwidth(ad9361_phy, 30000000);

	// ========== 配置软件AGC ==========
	printf("Configuring Software AGC...\r");
	// 设置RX1为Slow Attack AGC模式
    ret = ad9361_set_rx_gain_control_mode(ad9361_phy, 0, RF_GAIN_SLOWATTACK_AGC);
	if (ret == 0) {
	    printf("  RX1: Slow Attack AGC enabled [OK]\r");
	} else {
	    printf("  RX1: Failed to set AGC (ret=%ld)\r", ret);
	}
	//set rx channel gain // 设置RX通道增益（接收信号放大倍数）
	ad9361_set_rx_rf_gain(ad9361_phy, 0, gain);
	ad9361_set_rx_rf_gain(ad9361_phy, 1, gain);
	//set tx att          // 设置TX通道衰减（发送信号衰减量，与增益相反）
	ad9361_set_tx_attenuation(ad9361_phy, 0, txatt);
	ad9361_set_tx_attenuation(ad9361_phy, 1, txatt);


	// 配置 AXI 寄存器
	printf("Configuring AXI registers...\n\r");

	// 在 AXI_REG_WRITE(PHY_MODE, ...) 之前添加调试信息
	printf("Setting PHY_MODE to 1R1T...\r");
	printf("  AD9361_PHY_MODE_1R1T = %d\r", AD9361_PHY_MODE_1R1T);

	//SET I DELAY/DDS INCREAMENT  // 通过AXI总线写入FPGA内部寄存器（控制硬件逻辑）
	//AXI_REG_WRITE 函数封装了 Xilinx 的Xil_Out32，用于向指定地址（AXI 从设备地址）写入 32 位数据
	AXI_REG_WRITE(I_DELAY_EN, 0x7f); // 使能I路延迟
	AXI_REG_WRITE(I_DELAY_TAP, 15);  // 设置I路延迟抽头
	AXI_REG_WRITE(DDS_PHASE_INC, 128); // 设置DDS（直接数字合成器）的相位增量（决定输出频率）
	AXI_REG_WRITE(PHY_MODE, 1);// 设置PHY模式为1收1发   // 直接写入 1

	// 读回验证
	uint32_t phy_mode_readback = Xil_In32(PHY_MODE);
	printf("  PHY_MODE readback = %u\r", (unsigned int)phy_mode_readback);

	if (phy_mode_readback == 1) {
	    printf("  PHY_MODE set successfully! [OK]\r");
	} else {
	    printf("  ERROR: PHY_MODE = %u (expected 1)\r", (unsigned int)phy_mode_readback);
	}

	// 在 AXI_REG_WRITE(PHY_MODE, 1) 之后添加
	printf("\n========== PHY_MODE Debug ==========\r");
	printf("Base address: 0x%08x\r", AXI_CTRL_BASE);
	printf("PHY_MODE address: 0x%08x\r", PHY_MODE);

	// 读取所有寄存器
	for (int i = 0; i < 4; i++) {
	    uint32_t val = Xil_In32(AXI_CTRL_BASE + i * 4);
	    printf("slv_reg%d (0x%08x) = 0x%08lx\r", i, AXI_CTRL_BASE + i * 4, val);
	}

	// 多次写入和读取 PHY_MODE
	for (int i = 0; i < 3; i++) {
	    printf("\n--- Iteration %d ---\r", i);

	    // 写入 0
	    AXI_REG_WRITE(PHY_MODE, 0);
	    //usleep(1000);
	    uint32_t val0 = Xil_In32(PHY_MODE);
	    printf("Write 0, read back: 0x%08lx\r", val0);

	    // 写入 1
	    AXI_REG_WRITE(PHY_MODE, 1);
	   // usleep(1000);
	    uint32_t val1 = Xil_In32(PHY_MODE);
	    printf("Write 1, read back: 0x%08lx\r", val1);
	}
	printf("====================================\r\n");


	//  关闭 BIST 模式
	printf("Disabling BIST mode...\r");
	//bist test mode
//	// 通过SPI写入AD9361寄存器，配置BIST（内置自测试）模式
//	ad9361_spi_write(ad9361_phy->spi, 0x3f4, 0x03);//tx tone，配置TX为测试 tone（固定频率信号）
//	ad9361_spi_write(ad9361_phy->spi, 0x3f5, 0x01);//no rf loop，禁用RF环路（不使用内部环路测试）
    ad9361_spi_write(ad9361_phy->spi, 0x3f4, 0x00);  // 关闭TX tone
    ad9361_spi_write(ad9361_phy->spi, 0x3f5, 0x00);  // 关闭测试模式
	ad9361_spi_write(ad9361_phy->spi, 0x3f6, 0x00);// 额外配置（具体功能需参考AD9361寄存器手册）

    //  强制进入 FDD 模式
    printf("Setting AD9361 to FDD mode...\r");
    ad9361_set_en_state_machine_mode(ad9361_phy, ENSM_MODE_FDD);
	//  等待状态稳定
	//usleep(10000);  // 10ms
	//  关键：强制使能 TX1 和 RX1
	printf("Forcing TX1/RX1 enable...\r");
//	ad9361_spi_write(ad9361_phy->spi, 0x002, 0x01);  // 使能 TX1
//	ad9361_spi_write(ad9361_phy->spi, 0x003, 0x01);  // 使能 RX1
	// 等待生效
	//usleep(1000);  // 1ms

	printf("\n\r========================================\r");
	printf("  System Ready. Scanning for Carrier...\r");
	printf("========================================\r");

	u32 locked = 0;
	u32 detected_freq = 0;
	u32 last_print_freq = 0;

// 6. Search Loop (Only Read, No Demod Config yet)
	while(1) {
	    // Read Lock Status (Channel 2)
	    locked = XGpio_DiscreteRead(&CarrierGpio, GPIO_CHANNEL_LOCK);
	    if (locked == 1) {
	                detected_freq = XGpio_DiscreteRead(&CarrierGpio, GPIO_CHANNEL_FREQ);
	                printf("\n!!! Carrier Locked !!! Frequency: %u Hz\r", (unsigned int)detected_freq);

	                // 收到频率后，跳出搜索循环，进入重配置
	                break;
	            }
	    usleep(50000);
	}
	  /*  // --- 方法 2: 直接读取物理地址 (用于验证) ---
	            // AXI GPIO Channel 1 数据寄存器偏移量是 0x00
	            // AXI GPIO Channel 2 数据寄存器偏移量是 0x08
	    u32 raw_freq = Xil_In32(MANUAL_GPIO_BASE_ADDR + 0x00);
	    u32 raw_lock = Xil_In32(MANUAL_GPIO_BASE_ADDR + 0x08);

	    /*if (locked == 1) {
	        // Read Frequency (Channel 1)
	        detected_freq = XGpio_DiscreteRead(&CarrierGpio, GPIO_CHANNEL_FREQ);

	        // Print only if frequency changed to avoid flooding console
	        if (detected_freq != last_print_freq) {
	            printf("\n!!! Carrier Locked !!!\r");
	            printf("Frequency Detected: %u Hz\r", (unsigned int)detected_freq);
	            last_print_freq = detected_freq;
	        }
	    }

	    if (locked == 1 || raw_lock == 1) {
	                detected_freq = XGpio_DiscreteRead(&CarrierGpio, GPIO_CHANNEL_FREQ);

	                // 打印对比，看看谁是对的
	                printf("\n--- Debug Info ---\r");
	                printf("Driver Read Freq: %u Hz\r", (unsigned int)detected_freq);
	                printf("Raw Mem Read Freq: %u Hz\r", (unsigned int)raw_freq); // 这个应该是对的

	                if (raw_freq == 106000000 || (raw_freq > 105000000 && raw_freq < 107000000)) {
	                     printf("SUCCESS! Raw read matches ILA!\r");
	                } else {
	                     printf("Still wrong? check Address Editor again!\r");
	                }

	                usleep(1000000); // 慢点刷，方便看
	            } else {
	                // 如果两个都读不到锁，打印 raw_lock 看看
	                // printf("Raw Lock Value: %d\r", (int)raw_lock);
	            }

	    usleep(100000); // Check every 100ms
	}*/

	// 7. 重配置为解调模式 (Demodulation)
	    printf("\nReconfiguring for Demodulation...\r");

	    // A. 设置 LO 为检测到的频率
	    ad9361_set_rx_lo_freq(ad9361_phy, (uint64_t)detected_freq);
	    printf(" -> Set LO to %u Hz\r", (unsigned int)detected_freq);

	    // B. 设置采样率为 4.8MHz
	    // API 会自动计算分频。因为 FIR rx_dec=2，所以实际 ADC 可能是 9.6M 或更高
	    ad9361_set_rx_sampling_freq(ad9361_phy, 4800000);
	    printf(" -> Set Sample Rate to 4.8 MHz\r");

	    // C. 切换到窄带 FIR
	    printf(" -> Loading Narrowband FIR...\r");
	    ad9361_set_rx_fir_en_dis(ad9361_phy, 0); // 必须先 disable
	    ad9361_set_rx_fir_config(ad9361_phy, rx_fir_config_demod);
	    ad9361_set_rx_fir_en_dis(ad9361_phy, 1);

	    // D. 设置射频带宽 (窄带 200k)
	    ad9361_set_rx_rf_bandwidth(ad9361_phy, 200000);
	    printf(" -> Set RF Bandwidth to 200 kHz\r");

	    printf("\nDemodulation Started. System Holding.\r");
/*
	// ========== 纯诊断代码 - 只读取状态 ==========
	printf("\n\r========================================\r");
	printf("  AD9361 Status Diagnostics\r");
	printf("========================================\r");


	// 1. ENSM状态
	printf("	1. ENSM State:\r");
	uint32_t ensm_mode = 0;
	ret = ad9361_get_en_state_machine_mode(ad9361_phy, &ensm_mode);
	if (ret == 0) {
	    printf("   ENSM Mode: 0x%x ", (unsigned int)ensm_mode);
	    switch(ensm_mode) {
	        case ENSM_MODE_SLEEP:
	            printf("(SLEEP)\r");
	            break;
	        case ENSM_MODE_ALERT:
	            printf("(ALERT)\r");
	            break;
	        case ENSM_MODE_FDD:
	            printf("(FDD)\r");
	            break;
	        case ENSM_MODE_RX:
	            printf("(RX)\r");
	            break;
	        case ENSM_MODE_TX:
	            printf("(TX)\r");
	            break;
	        default:
	            printf("(Unknown)\r");
	    }
	} else {
	    printf("   ERROR: Failed to get ENSM mode (ret=%ld)\r", ret);
	}

	// 2. RX LO频率
	printf("	2. RX LO Frequency:\r");
	uint64_t actual_rx_lo = 0;
	ret = ad9361_get_rx_lo_freq(ad9361_phy, &actual_rx_lo);
	if (ret == 0) {
	    printf("   Configured: %llu Hz\r", (unsigned long long)rx_lo_freq);
	    printf("   Actual:     %llu Hz\r", (unsigned long long)actual_rx_lo);
	    if (actual_rx_lo == rx_lo_freq) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %lld Hz\r",
	               (long long)(actual_rx_lo - rx_lo_freq));
	    }
	} else {
	    printf("   ERROR: Failed to get RX LO (ret=%ld)\r", ret);
	}

	// 3. TX LO频率
	printf("	3. TX LO Frequency:\r");
	uint64_t actual_tx_lo = 0;
	ret = ad9361_get_tx_lo_freq(ad9361_phy, &actual_tx_lo);
	if (ret == 0) {
	    printf("   Configured: %llu Hz\r", (unsigned long long)tx_lo_freq);
	    printf("   Actual:     %llu Hz\r", (unsigned long long)actual_tx_lo);
	    if (actual_tx_lo == tx_lo_freq) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %lld Hz\r",
	               (long long)(actual_tx_lo - tx_lo_freq));
	    }
	} else {
	    printf("   ERROR: Failed to get TX LO (ret=%ld)\r", ret);
	}

	// 4. RX采样率
	printf("	4. RX Sampling Frequency:\r");
	uint32_t actual_rx_rate = 0;
	ret = ad9361_get_rx_sampling_freq(ad9361_phy, &actual_rx_rate);
	if (ret == 0) {
	    printf("   Configured: %u Hz\r", (unsigned int)sample_rate);
	    printf("   Actual:     %u Hz\r", (unsigned int)actual_rx_rate);
	    if (actual_rx_rate == sample_rate) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %d Hz\r",
	               (int)(actual_rx_rate - sample_rate));
	    }
	} else {
	    printf("   ERROR: Failed to get RX rate (ret=%ld)\r", ret);
	}

	// 5. TX采样率
	printf("	5. TX Sampling Frequency:\r");
	uint32_t actual_tx_rate = 0;
	ret = ad9361_get_tx_sampling_freq(ad9361_phy, &actual_tx_rate);
	if (ret == 0) {
	    printf("   Configured: %u Hz\r", (unsigned int)sample_rate);
	    printf("   Actual:     %u Hz\r", (unsigned int)actual_tx_rate);
	    if (actual_tx_rate == sample_rate) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %d Hz\r",
	               (int)(actual_tx_rate - sample_rate));
	    }
	} else {
	    printf("   ERROR: Failed to get TX rate (ret=%ld)\r", ret);
	}

	// 6. RX RF带宽
	printf("	6. RX RF Bandwidth:\r");
	uint32_t actual_rx_bw = 0;
	ret = ad9361_get_rx_rf_bandwidth(ad9361_phy, &actual_rx_bw);
	if (ret == 0) {
	    printf("   Configured: %u Hz\r", (unsigned int)bandwidth);
	    printf("   Actual:     %u Hz\r", (unsigned int)actual_rx_bw);
	    if (actual_rx_bw == bandwidth) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %d Hz\r",
	               (int)(actual_rx_bw - bandwidth));
	    }
	} else {
	    printf("   ERROR: Failed to get RX BW (ret=%ld)\r", ret);
	}

	// 7. TX RF带宽
	printf("	7. TX RF Bandwidth:\r");
	uint32_t actual_tx_bw = 0;
	ret = ad9361_get_tx_rf_bandwidth(ad9361_phy, &actual_tx_bw);
	if (ret == 0) {
	    printf("   Configured: %u Hz\r", (unsigned int)bandwidth);
	    printf("   Actual:     %u Hz\r", (unsigned int)actual_tx_bw);
	    if (actual_tx_bw == bandwidth) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH] Delta = %d Hz\r",
	               (int)(actual_tx_bw - bandwidth));
	    }
	} else {
	    printf("   ERROR: Failed to get TX BW (ret=%ld)\r", ret);
	}

	// 8. RX1增益
	printf("	8. RX1 RF Gain:\r");
	int32_t actual_rx1_gain = 0;
	ret = ad9361_get_rx_rf_gain(ad9361_phy, 0, &actual_rx1_gain);  // Channel 0
	if (ret == 0) {
	    printf("   RX1 Gain: %d dB\r", (int)actual_rx1_gain);
	    if (actual_rx1_gain < 30) {
	        printf("   Note: Gain is LOW (< 30 dB)\r");
	    } else if (actual_rx1_gain >= 70) {
	        printf("   Note: Gain is at MAXIMUM\r");
	    }
	} else {
	    printf("   ERROR: Failed to get RX1 gain (ret=%ld)\r", ret);
	}

	// 9. RX2增益（如果是2R2T模式）
	printf("	9. RX2 RF Gain:\r");
	int32_t actual_rx2_gain = 0;
	ret = ad9361_get_rx_rf_gain(ad9361_phy, 1, &actual_rx2_gain);  // Channel 1
	if (ret == 0) {
	    printf("   RX2 Gain: %d dB\r", (int)actual_rx2_gain);
	} else {
	    printf("   RX2 not available or error (ret=%ld)\r", ret);
	}

	// 10. RX1增益控制模式
	printf("	10. RX1 Gain Control Mode:\r");
	uint8_t rx1_gc_mode = 0;
	ret = ad9361_get_rx_gain_control_mode(ad9361_phy, 0, &rx1_gc_mode);
	if (ret == 0) {
	    printf("   RX1 Mode: ");
	    switch(rx1_gc_mode) {
	        case RF_GAIN_MGC:
	            printf("MGC (Manual Gain Control)\r");
	            break;
	        case RF_GAIN_FASTATTACK_AGC:
	            printf("Fast Attack AGC\r");
	            break;
	        case RF_GAIN_SLOWATTACK_AGC:
	            printf("Slow Attack AGC\r");
	            break;
	        case RF_GAIN_HYBRID_AGC:
	            printf("Hybrid AGC\r");
	            break;
	        default:
	            printf("Unknown (0x%x)\r", rx1_gc_mode);
	    }
	} else {
	    printf("   ERROR: Failed to get gain control mode (ret=%ld)\r", ret);
	}

	// 11. TX1衰减
	printf("	11. TX1 Attenuation:\r");
	uint32_t actual_tx1_att = 0;
	ret = ad9361_get_tx_attenuation(ad9361_phy, 0, &actual_tx1_att);
	if (ret == 0) {
	    printf("   Configured: %u mdB (%.2f dB)\r",
	           (unsigned int)txatt, txatt / 1000.0);
	    printf("   Actual:     %u mdB (%.2f dB)\r",
	           (unsigned int)actual_tx1_att, actual_tx1_att / 1000.0);
	    if (actual_tx1_att == txatt) {
	        printf("   Status: [OK]\r");
	    } else {
	        printf("   Status: [MISMATCH]\r");
	    }
	} else {
	    printf("   ERROR: Failed to get TX1 attenuation (ret=%ld)\r", ret);
	}

	// 12. RX FIR状态
	printf("	12. RX FIR Filter:\r");
	uint8_t rx_fir_en = 0;
	ret = ad9361_get_rx_fir_en_dis(ad9361_phy, &rx_fir_en);
	if (ret == 0) {
	    printf("   RX FIR: %s\r", rx_fir_en ? "[Enabled]" : "[Disabled]");
	} else {
	    printf("   ERROR: Failed to get RX FIR status (ret=%ld)\r", ret);
	}

	// 13. TX FIR状态
	printf("	13. TX FIR Filter:\r");
	uint8_t tx_fir_en = 0;
	ret = ad9361_get_tx_fir_en_dis(ad9361_phy, &tx_fir_en);
	if (ret == 0) {
	    printf("   TX FIR: %s\r", tx_fir_en ? "[Enabled]" : "[Disabled]");
	} else {
	    printf("   ERROR: Failed to get TX FIR status (ret=%ld)\r", ret);
	}

	// 14. RX RSSI
	printf("	14. RX1 RSSI:\r");
	struct rf_rssi rssi_data;
	ret = ad9361_get_rx_rssi(ad9361_phy, 0, &rssi_data);
	if (ret == 0) {
	    printf("   Symbol:     %u\r", (unsigned int)rssi_data.symbol);
	    printf("   Preamble:   %u\r", (unsigned int)rssi_data.preamble);
	    printf("   Multiplier: %u\r", (unsigned int)rssi_data.multiplier);
	    printf("   Duration:   %u\r", (unsigned int)rssi_data.duration);

	    if (rssi_data.symbol == 0 && rssi_data.preamble == 0) {
	        printf("   WARNING: RSSI = 0, no signal detected!\r");
	    }
	} else {
	    printf("   ERROR: Failed to get RSSI (ret=%ld)\r", ret);
	}

	// 15. 温度
	printf("	15. Temperature:\r");
	int32_t temp = 0;
	ret = ad9361_get_temperature(ad9361_phy, &temp);
	if (ret == 0) {
	    printf("   Temperature: %d milli-C (%.2f C)\r",
	           (int)temp, temp / 1000.0);
	} else {
	    printf("   ERROR: Failed to get temperature (ret=%ld)\r", ret);
	}

	// 16. RX LO电源状态
	printf("	16. RX LO Power:\r");
	uint8_t rx_lo_power = 0;
	ret = ad9361_get_rx_lo_power(ad9361_phy, &rx_lo_power);
	if (ret == 0) {
	    printf("   RX LO: %s\r", rx_lo_power ? "[Powered ON]" : "[Powered DOWN]");
	} else {
	    printf("   ERROR: Failed to get RX LO power (ret=%ld)\r", ret);
	}

	// 17. TX LO电源状态
	printf("	17. TX LO Power:\r");
	uint8_t tx_lo_power = 0;
	ret = ad9361_get_tx_lo_power(ad9361_phy, &tx_lo_power);
	if (ret == 0) {
	    printf("   TX LO: %s\r", tx_lo_power ? "[Powered ON]" : "[Powered DOWN]");
	} else {
	    printf("   ERROR: Failed to get TX LO power (ret=%ld)\r", ret);
	}

	// 18. RX RF端口
	printf("	18. RX RF Port:\r");
	uint32_t rx_port = 0;
	ret = ad9361_get_rx_rf_port_input(ad9361_phy, &rx_port);
	if (ret == 0) {
	    printf("   RX Port: 0x%x\r", (unsigned int)rx_port);
	} else {
	    printf("   ERROR: Failed to get RX port (ret=%ld)\r", ret);
	}

	// 19. TX RF端口
	printf("	19. TX RF Port:\r");
	uint32_t tx_port = 0;
	ret = ad9361_get_tx_rf_port_output(ad9361_phy, &tx_port);
	if (ret == 0) {
	    printf("   TX Port: 0x%x\r", (unsigned int)tx_port);
	} else {
	    printf("   ERROR: Failed to get TX port (ret=%ld)\r", ret);
	}

	// 20. 时钟路径
	printf("	20. TRX Path Clocks:\r");
	uint32_t rx_path_clks[6] = {0};
	uint32_t tx_path_clks[6] = {0};
	ret = ad9361_get_trx_path_clks(ad9361_phy, rx_path_clks, tx_path_clks);
	if (ret == 0) {
	    printf("   RX Path Clocks:\r");
	    printf("     BBPLL:    %u Hz\r", (unsigned int)rx_path_clks[0]);
	    printf("     ADC:      %u Hz\r", (unsigned int)rx_path_clks[1]);
	    printf("     R2:       %u Hz\r", (unsigned int)rx_path_clks[2]);
	    printf("     R1:       %u Hz\r", (unsigned int)rx_path_clks[3]);
	    printf("     CLKRF:    %u Hz\r", (unsigned int)rx_path_clks[4]);
	    printf("     RX_SAMPL: %u Hz\r", (unsigned int)rx_path_clks[5]);

	    printf("   TX Path Clocks:\r");
	    printf("     BBPLL:    %u Hz\r", (unsigned int)tx_path_clks[0]);
	    printf("     DAC:      %u Hz\r", (unsigned int)tx_path_clks[1]);
	    printf("     T2:       %u Hz\r", (unsigned int)tx_path_clks[2]);
	    printf("     T1:       %u Hz\r", (unsigned int)tx_path_clks[3]);
	    printf("     CLKTF:    %u Hz\r", (unsigned int)tx_path_clks[4]);
	    printf("     TX_SAMPL: %u Hz\r", (unsigned int)tx_path_clks[5]);
	} else {
	    printf("   ERROR: Failed to get path clocks (ret=%ld)\r", ret);
	}

	// ========== 读取数字接口寄存器 ==========
	printf("\n21. Digital Interface Registers:\r");

	uint32_t reg_val;

	// 0x010: 并口配置1
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x010);
	printf("   Reg 0x010 (Parallel Port Config 1): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D7] PP Tx Swap IQ: %ld\r", (reg_val >> 7) & 1);
	printf("     [D6] PP Rx交换IQ: %ld\r", (reg_val >> 6) & 1);
	printf("     [D5] Tx Channel Swap: %ld\r", (reg_val >> 5) & 1);
	printf("     [D4] Rx通道交换: %ld\r", (reg_val >> 4) & 1);
	printf("     [D3] Rx帧脉冲模式: %ld\r", (reg_val >> 3) & 1);
	printf("     [D2] 2R2T时机: %ld\r", (reg_val >> 2) & 1);
	printf("     [D1] 总线反转: %ld\r", (reg_val >> 1) & 1);
	printf("     [D0] Invert DATA_CLK: %ld\r", reg_val & 1);

	// 0x011: 并口配置2
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x011);
	printf("   Reg 0x011 (Parallel Port Config 2): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D7] FDD Alt Word Order: %ld\r", (reg_val >> 7) & 1);
	printf("     [D6:D5] 必须为0: %ld\r", (reg_val >> 5) & 3);
	printf("     [D4] d4-选受Tx1: %ld\r", (reg_val >> 4) & 1);
	printf("     [D3] Rx数据脉冲模式: %ld\r", (reg_val >> 3) & 1);
	printf("     [D1:D0] 延迟Rx数据[1:0]: %ld\r", reg_val & 3);

	// 0x012: 并口配置3
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x012);
	printf("   Reg 0x012 (Parallel Port Config 3): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D7] FDD Rx Rate = 2*Tx Rate: %ld\r", (reg_val >> 7) & 1);
	printf("     [D6] 交换端口: %ld\r", (reg_val >> 6) & 1);
	printf("     [D5] 单数据速率: %ld\r", (reg_val >> 5) & 1);
	printf("     [D4] LVDS模式: %ld\r", (reg_val >> 4) & 1);
	printf("     [D3] 半双工模式: %ld\r", (reg_val >> 3) & 1);
	printf("     [D2] 单口模式: %ld\r", (reg_val >> 2) & 1);
	printf("     [D1] 全端口: %ld\r", (reg_val >> 1) & 1);
	printf("     [D0] 全双工交换位: %ld\r", reg_val & 1);

	// 0x006: Rx数据延迟
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x006);
	printf("   Reg 0x006 (Rx Data Delay): 0x%02x (%d clocks)\r",
	       (unsigned int)reg_val, (unsigned int)reg_val);

	// 0x007: Tx数据延迟
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x007);
	printf("   Reg 0x007 (Tx Data Delay): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D7:D4] FB_CLK延迟: %ld\r", (reg_val >> 4) & 0xF);
	printf("     [D3:D0] Tx数据延迟: %ld\r", reg_val & 0xF);

	// 0x000: SPI配置
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x000);
	printf("   Reg 0x000 (SPI Config): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D7] 必须为0: %ld\r", (reg_val >> 7) & 1);
	printf("     [D6] d1-3线SPI: %ld\r", (reg_val >> 6) & 1);
	printf("     [D1] LSB第一: %ld\r", (reg_val >> 1) & 1);
	printf("     [D0] 必须为0: %ld\r", reg_val & 1);

	// 0x001: 多芯片同步
	reg_val = ad9361_spi_read(ad9361_phy->spi, 0x001);
	printf("   Reg 0x001 (Multi-Chip Sync): 0x%02x\r", (unsigned int)reg_val);
	printf("     [D6] Tx2 Monitor Enable: %ld\r", (reg_val >> 6) & 1);
	printf("     [D5] Tx1 Monitor Enable: %ld\r", (reg_val >> 5) & 1);
	printf("     [D2] MCS BBPLL使能: %ld\r", (reg_val >> 2) & 1);
	printf("     [D1] MCS数字CLK使能: %ld\r", (reg_val >> 1) & 1);
	printf("     [D0] MCS BB使能: %ld\r", reg_val & 1);

	printf("========================================\r");

*/


    printf("initial done.\n\r");
	while(1);// 进入死循环，程序在此处挂起

	// 以下代码不会执行（因死循环），用于正常退出时关闭缓存
	Xil_DCacheDisable();
	Xil_ICacheDisable();


	return 0;
}

void AXI_REG_WRITE(u32 REG_ADDR, u32 Data)
{
	Xil_Out32(REG_ADDR,Data);
}

