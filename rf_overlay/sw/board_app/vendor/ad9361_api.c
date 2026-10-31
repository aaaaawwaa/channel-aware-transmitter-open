/***************************************************************************//**
 *   @file   ad9361_api.c
 *   @brief  Implementation of AD9361 API Driver.
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
#include "ad9361.h"
#include "ad9361_api.h"
#include "platform.h"
#include "util.h"
#include "config.h"
#include <string.h>

#ifndef AXI_ADC_NOT_PRESENT
/******************************************************************************/
/************************ Constants Definitions *******************************/
/******************************************************************************/
// 若启用AXI ADC（FPGA侧ADC），定义ADC芯片信息表
static struct axiadc_chip_info axiadc_chip_info_tbl[] = {
	{
		"4_CH_DEV",
		4
	},
	{
		"2_CH_DEV",
		2
	},
};
#endif

extern struct gain_table_info ad9361_adi_gt_info[];

/**
 * Initialize the AD9361 part.
 * @param init_param The structure that contains the AD9361 initial parameters.
 * @return A structure that contains the AD9361 current state in case of
 *         success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
int32_t ad9361_init (struct ad9361_rf_phy **ad9361_phy,
		     AD9361_InitParam *init_param)
{
	struct ad9361_rf_phy *phy;   // 定义AD9361状态结构体指针（核心句柄）
	int32_t ret = 0;
	int32_t rev = 0;
	int32_t i   = 0;

	// 为AD9361核心状态结构体分配内存（zmalloc会初始化为0）
	phy = (struct ad9361_rf_phy *)zmalloc(sizeof(*phy));
	if (!phy) {
		return -ENOMEM;
	}
	// 为SPI设备结构体分配内存（AD9361通过SPI通信）
	phy->spi = (struct spi_device *)zmalloc(sizeof(*phy->spi));
	if (!phy->spi) {
		return -ENOMEM;
	}
	// 为参考时钟结构体分配内存（AD9361的时钟基准）
	phy->clk_refin = (struct clk *)zmalloc(sizeof(*phy->clk_refin));
	if (!phy->clk_refin) {
		return -ENOMEM;
	}
	// 为平台数据结构体分配内存（存储硬件配置参数）
	phy->pdata = (struct ad9361_phy_platform_data *)zmalloc(sizeof(*phy->pdata));
	if (!phy->pdata) {
		return -ENOMEM;
	}
	// 若启用AXI ADC，为ADC相关结构体分配内存（FPGA侧ADC控制）
#ifndef AXI_ADC_NOT_PRESENT
	phy->adc_conv = (struct axiadc_converter *)zmalloc(sizeof(*phy->adc_conv));
	if (!phy->adc_conv) {
		return -ENOMEM;
	}

	phy->adc_state = (struct axiadc_state *)zmalloc(sizeof(*phy->adc_state));
	if (!phy->adc_state) {
		return -ENOMEM;
	}
	phy->adc_state->phy = phy;
#endif

	/* Device selection */
	phy->dev_sel = init_param->dev_sel;

	/* Identification number */
	phy->spi->id_no = init_param->id_no;
	phy->id_no = init_param->id_no;

	/* Reference Clock */
	phy->clk_refin->rate = init_param->reference_clk_rate;

	/* Base Configuration */
	// 频分双工（FDD）模式使能（1=使能，0=时分双工TDD）
	phy->pdata->fdd = init_param->frequency_division_duplex_mode_enable;
	// FDD独立模式（收发配置完全独立）
	phy->pdata->fdd_independent_mode =
		init_param->frequency_division_duplex_independent_mode_enable;
	// 2收2发（2R2T）模式使能（AD9361支持多通道，1=启用2R2T，0=1R1T）
	phy->pdata->rx2tx2 = init_param->two_rx_two_tx_mode_enable;
	// 1R1T模式下使用的接收/发送通道号（如通道0或1）
	phy->pdata->rx1tx1_mode_use_rx_num = init_param->one_rx_one_tx_mode_use_rx_num;
	phy->pdata->rx1tx1_mode_use_tx_num = init_param->one_rx_one_tx_mode_use_tx_num;
	// TDD模式是否使用双合成器（1=使用，提高切换速度）
	phy->pdata->tdd_use_dual_synth = init_param->tdd_use_dual_synth_mode_enable;
	// TDD模式是否跳过VCO校准（1=跳过，加快切换速度，牺牲精度）
	phy->pdata->tdd_skip_vco_cal = init_param->tdd_skip_vco_cal_enable;
	// RX快速锁定延迟（单位：ns，TDD模式下RX锁定前的等待时间）
	phy->pdata->rx_fastlock_delay_ns = init_param->rx_fastlock_delay_ns;
	//TX快速锁定延迟（单位：ns，TDD模式下TX锁定前的等待时间）
	phy->pdata->tx_fastlock_delay_ns = init_param->tx_fastlock_delay_ns;
	//RX快速锁定引脚控制使能（1=启用硬件引脚控制RX快速锁定）
	phy->pdata->trx_fastlock_pinctrl_en[0] =
		init_param->rx_fastlock_pincontrol_enable;
	//TX快速锁定引脚控制使能（1=启用硬件引脚控制TX快速锁定）
	phy->pdata->trx_fastlock_pinctrl_en[1] =
		init_param->tx_fastlock_pincontrol_enable;
	//AD9363A强制禁用外部LO（AD9363A硬件不支持外部本振）
	if (phy->dev_sel == ID_AD9363A) {
		phy->pdata->use_ext_rx_lo = false;
		phy->pdata->use_ext_tx_lo = false;
	} else {
		phy->pdata->use_ext_rx_lo = init_param->external_rx_lo_enable;
		phy->pdata->use_ext_tx_lo = init_param->external_tx_lo_enable;
	}
	//直流偏移跟踪更新事件掩码（指定哪些事件触发直流偏移校准）
	phy->pdata->dc_offset_update_events =
		init_param->dc_offset_tracking_update_event_mask;
	//直流偏移衰减高范围（高信号强度下的衰减值）
	phy->pdata->dc_offset_attenuation_high =
		init_param->dc_offset_attenuation_high_range;
	//直流偏移衰减低范围（低信号强度下的衰减值）
	phy->pdata->dc_offset_attenuation_low =
		init_param->dc_offset_attenuation_low_range;
	//射频直流偏移计数高范围（高信号强度下的校准采样数）
	phy->pdata->rf_dc_offset_count_high = init_param->dc_offset_count_high_range;
	//射频直流偏移计数低范围（低信号强度下的校准采样数）
	phy->pdata->rf_dc_offset_count_low = init_param->dc_offset_count_low_range;
	//拆分增益表模式（1=启用，RX1/RX2使用独立增益表）
	phy->pdata->split_gt = init_param->split_gain_table_mode_enable;
	//收发合成器目标参考频率（覆盖默认参考频率，单位：Hz）
	phy->pdata->trx_synth_max_fref =
		init_param->trx_synthesizer_target_fref_overwrite_hz;
	//QEC跟踪慢速模式（1=启用，适合静态场景，提高校准精度）
	phy->pdata->qec_tracking_slow_mode_en =
		init_param->qec_tracking_slow_mode_enable;

	/* ENSM Control */
	//ENSM引脚脉冲模式（1=脉冲触发，0=电平触发）
	phy->pdata->ensm_pin_pulse_mode = init_param->ensm_enable_pin_pulse_mode_enable;
	//ENSM引脚控制TXNRX（1=ENSM引脚控制收发切换，0=软件控制）
	phy->pdata->ensm_pin_ctrl = init_param->ensm_enable_txnrx_control_enable;

	/* LO Control */
	//RX合成器频率（接收本振频率，单位：Hz，如2.4GHz）
	phy->pdata->rx_synth_freq = init_param->rx_synthesizer_frequency_hz;
	//TX合成器频率（发送本振频率，单位：Hz）
	phy->pdata->tx_synth_freq = init_param->tx_synthesizer_frequency_hz;
	//TX LO掉电管理使能（1=自动掉电未使用的TX LO，节能）
	phy->pdata->lo_powerdown_managed_en =
		init_param->tx_lo_powerdown_managed_enable;

	/* Rate & BW Control */
	// RX路径时钟频率（6个时钟点的频率，如采样时钟、基带时钟等）
	for(i = 0; i < 6; i++) {
		phy->pdata->rx_path_clks[i] = init_param->rx_path_clock_frequencies[i];
	}
	//TX路径时钟频率（6个时钟点的频率）
	for(i = 0; i < 6; i++) {
		phy->pdata->tx_path_clks[i] = init_param->tx_path_clock_frequencies[i];
	}
	//RX射频带宽（接收信号的频率范围，单位：Hz，如20MHz）
	phy->pdata->rf_rx_bandwidth_Hz = init_param->rf_rx_bandwidth_hz;
	//TX射频带宽（发送信号的频率范围，单位：Hz）
	phy->pdata->rf_tx_bandwidth_Hz = init_param->rf_tx_bandwidth_hz;

	/* RF Port Control */
	//RX射频端口选择（指定RX信号从哪个硬件引脚输入）
	phy->pdata->rf_rx_input_sel = init_param->rx_rf_port_input_select;
	//TX射频端口选择（指定TX信号从哪个硬件引脚输出）
	phy->pdata->rf_tx_output_sel = init_param->tx_rf_port_input_select;

	/* TX Attenuation Control */
	//TX衰减值（单位：0.25dB，如40表示10dB衰减）
	phy->pdata->tx_atten = init_param->tx_attenuation_mdB;
	//告警时更新TX增益（1=告警事件触发TX增益调整）
	phy->pdata->update_tx_gain_via_alert =
		init_param->update_tx_gain_in_alert_enable;

	/* Reference Clock Control */
	switch (phy->dev_sel) {
	case ID_AD9363A:
		phy->pdata->use_extclk = true;
		break;
	default:
		phy->pdata->use_extclk = init_param->xo_disable_use_ext_refclk_enable;
	}
	phy->pdata->dcxo_coarse = init_param->dcxo_coarse_and_fine_tune[0];
	phy->pdata->dcxo_fine = init_param->dcxo_coarse_and_fine_tune[1];
	phy->pdata->ad9361_clkout_mode = (enum ad9361_clkout)
					 init_param->clk_output_mode_select;

	/* Gain Control */
	phy->pdata->gain_ctrl.rx1_mode = (enum rf_gain_ctrl_mode)
					 init_param->gc_rx1_mode;
	phy->pdata->gain_ctrl.rx2_mode = (enum rf_gain_ctrl_mode)
					 init_param->gc_rx2_mode;
	phy->pdata->gain_ctrl.adc_large_overload_thresh =
		init_param->gc_adc_large_overload_thresh;
	phy->pdata->gain_ctrl.adc_ovr_sample_size = init_param->gc_adc_ovr_sample_size;
	phy->pdata->gain_ctrl.adc_small_overload_thresh =
		init_param->gc_adc_small_overload_thresh;
	phy->pdata->gain_ctrl.dec_pow_measuremnt_duration =
		init_param->gc_dec_pow_measurement_duration;
	phy->pdata->gain_ctrl.dig_gain_en = init_param->gc_dig_gain_enable;
	phy->pdata->gain_ctrl.lmt_overload_high_thresh =
		init_param->gc_lmt_overload_high_thresh;
	phy->pdata->gain_ctrl.lmt_overload_low_thresh =
		init_param->gc_lmt_overload_low_thresh;
	phy->pdata->gain_ctrl.low_power_thresh = init_param->gc_low_power_thresh;
	phy->pdata->gain_ctrl.max_dig_gain = init_param->gc_max_dig_gain;

	/* Gain MGC Control */
	phy->pdata->gain_ctrl.mgc_dec_gain_step = init_param->mgc_dec_gain_step;
	phy->pdata->gain_ctrl.mgc_inc_gain_step = init_param->mgc_inc_gain_step;
	phy->pdata->gain_ctrl.mgc_rx1_ctrl_inp_en = init_param->mgc_rx1_ctrl_inp_enable;
	phy->pdata->gain_ctrl.mgc_rx2_ctrl_inp_en = init_param->mgc_rx2_ctrl_inp_enable;
	phy->pdata->gain_ctrl.mgc_split_table_ctrl_inp_gain_mode =
		init_param->mgc_split_table_ctrl_inp_gain_mode;

	/* Gain AGC Control */
	phy->pdata->gain_ctrl.adc_large_overload_exceed_counter =
		init_param->agc_adc_large_overload_exceed_counter;
	phy->pdata->gain_ctrl.adc_large_overload_inc_steps =
		init_param->agc_adc_large_overload_inc_steps;
	phy->pdata->gain_ctrl.adc_lmt_small_overload_prevent_gain_inc =
		init_param->agc_adc_lmt_small_overload_prevent_gain_inc_enable;
	phy->pdata->gain_ctrl.adc_small_overload_exceed_counter =
		init_param->agc_adc_small_overload_exceed_counter;
	phy->pdata->gain_ctrl.dig_gain_step_size = init_param->agc_dig_gain_step_size;
	phy->pdata->gain_ctrl.dig_saturation_exceed_counter =
		init_param->agc_dig_saturation_exceed_counter;
	phy->pdata->gain_ctrl.gain_update_interval_us =
		init_param->agc_gain_update_interval_us;
	phy->pdata->gain_ctrl.immed_gain_change_if_large_adc_overload =
		init_param->agc_immed_gain_change_if_large_adc_overload_enable;
	phy->pdata->gain_ctrl.immed_gain_change_if_large_lmt_overload =
		init_param->agc_immed_gain_change_if_large_lmt_overload_enable;
	phy->pdata->gain_ctrl.agc_inner_thresh_high = init_param->agc_inner_thresh_high;
	phy->pdata->gain_ctrl.agc_inner_thresh_high_dec_steps =
		init_param->agc_inner_thresh_high_dec_steps;
	phy->pdata->gain_ctrl.agc_inner_thresh_low = init_param->agc_inner_thresh_low;
	phy->pdata->gain_ctrl.agc_inner_thresh_low_inc_steps =
		init_param->agc_inner_thresh_low_inc_steps;
	phy->pdata->gain_ctrl.lmt_overload_large_exceed_counter =
		init_param->agc_lmt_overload_large_exceed_counter;
	phy->pdata->gain_ctrl.lmt_overload_large_inc_steps =
		init_param->agc_lmt_overload_large_inc_steps;
	phy->pdata->gain_ctrl.lmt_overload_small_exceed_counter =
		init_param->agc_lmt_overload_small_exceed_counter;
	phy->pdata->gain_ctrl.agc_outer_thresh_high = init_param->agc_outer_thresh_high;
	phy->pdata->gain_ctrl.agc_outer_thresh_high_dec_steps =
		init_param->agc_outer_thresh_high_dec_steps;
	phy->pdata->gain_ctrl.agc_outer_thresh_low = init_param->agc_outer_thresh_low;
	phy->pdata->gain_ctrl.agc_outer_thresh_low_inc_steps =
		init_param->agc_outer_thresh_low_inc_steps;
	phy->pdata->gain_ctrl.agc_attack_delay_extra_margin_us =
		init_param->agc_attack_delay_extra_margin_us;
	phy->pdata->gain_ctrl.sync_for_gain_counter_en =
		init_param->agc_sync_for_gain_counter_enable;

	/* Fast AGC */
	phy->pdata->gain_ctrl.f_agc_dec_pow_measuremnt_duration =
		init_param->fagc_dec_pow_measuremnt_duration;
	phy->pdata->gain_ctrl.f_agc_state_wait_time_ns =
		init_param->fagc_state_wait_time_ns;
	/* Fast AGC - Low Power */
	phy->pdata->gain_ctrl.f_agc_allow_agc_gain_increase =
		init_param->fagc_allow_agc_gain_increase;
	phy->pdata->gain_ctrl.f_agc_lp_thresh_increment_time =
		init_param->fagc_lp_thresh_increment_time;
	phy->pdata->gain_ctrl.f_agc_lp_thresh_increment_steps =
		init_param->fagc_lp_thresh_increment_steps;
	/* Fast AGC - Lock Level (Lock Level is set via slow AGC inner high threshold) */
	phy->pdata->gain_ctrl.f_agc_lock_level_lmt_gain_increase_en =
		init_param->fagc_lock_level_lmt_gain_increase_en;
	phy->pdata->gain_ctrl.f_agc_lock_level_gain_increase_upper_limit =
		init_param->fagc_lock_level_gain_increase_upper_limit;
	/* Fast AGC - Peak Detectors and Final Settling */
	phy->pdata->gain_ctrl.f_agc_lpf_final_settling_steps =
		init_param->fagc_lpf_final_settling_steps;
	phy->pdata->gain_ctrl.f_agc_lmt_final_settling_steps =
		init_param->fagc_lmt_final_settling_steps;
	phy->pdata->gain_ctrl.f_agc_final_overrange_count =
		init_param->fagc_final_overrange_count;
	/* Fast AGC - Final Power Test */
	phy->pdata->gain_ctrl.f_agc_gain_increase_after_gain_lock_en =
		init_param->fagc_gain_increase_after_gain_lock_en;
	/* Fast AGC - Unlocking the Gain */
	phy->pdata->gain_ctrl.f_agc_gain_index_type_after_exit_rx_mode =
		(enum f_agc_target_gain_index_type)
		init_param->fagc_gain_index_type_after_exit_rx_mode;
	phy->pdata->gain_ctrl.f_agc_use_last_lock_level_for_set_gain_en =
		init_param->fagc_use_last_lock_level_for_set_gain_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_stronger_sig_thresh_exceeded_en =
		init_param->fagc_rst_gla_stronger_sig_thresh_exceeded_en;
	phy->pdata->gain_ctrl.f_agc_optimized_gain_offset =
		init_param->fagc_optimized_gain_offset;
	phy->pdata->gain_ctrl.f_agc_rst_gla_stronger_sig_thresh_above_ll =
		init_param->fagc_rst_gla_stronger_sig_thresh_above_ll;
	phy->pdata->gain_ctrl.f_agc_rst_gla_engergy_lost_sig_thresh_exceeded_en =
		init_param->fagc_rst_gla_engergy_lost_sig_thresh_exceeded_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_engergy_lost_goto_optim_gain_en =
		init_param->fagc_rst_gla_engergy_lost_goto_optim_gain_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_engergy_lost_sig_thresh_below_ll =
		init_param->fagc_rst_gla_engergy_lost_sig_thresh_below_ll;
	phy->pdata->gain_ctrl.f_agc_energy_lost_stronger_sig_gain_lock_exit_cnt =
		init_param->fagc_energy_lost_stronger_sig_gain_lock_exit_cnt;
	phy->pdata->gain_ctrl.f_agc_rst_gla_large_adc_overload_en =
		init_param->fagc_rst_gla_large_adc_overload_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_large_lmt_overload_en =
		init_param->fagc_rst_gla_large_lmt_overload_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_en_agc_pulled_high_en =
		init_param->fagc_rst_gla_en_agc_pulled_high_en;
	phy->pdata->gain_ctrl.f_agc_rst_gla_if_en_agc_pulled_high_mode =
		(enum f_agc_target_gain_index_type)
		init_param->fagc_rst_gla_if_en_agc_pulled_high_mode;
	phy->pdata->gain_ctrl.f_agc_power_measurement_duration_in_state5 =
		init_param->fagc_power_measurement_duration_in_state5;

	/* RSSI Control */
	phy->pdata->rssi_ctrl.rssi_delay = init_param->rssi_delay;
	phy->pdata->rssi_ctrl.rssi_duration = init_param->rssi_duration;
	phy->pdata->rssi_ctrl.restart_mode = (enum rssi_restart_mode)
					     init_param->rssi_restart_mode;
	phy->pdata->rssi_ctrl.rssi_unit_is_rx_samples =
		init_param->rssi_unit_is_rx_samples_enable;
	phy->pdata->rssi_ctrl.rssi_wait = init_param->rssi_wait;

	/* Aux ADC Control */
	phy->pdata->auxadc_ctrl.auxadc_decimation = init_param->aux_adc_decimation;
	phy->pdata->auxadc_ctrl.auxadc_clock_rate = init_param->aux_adc_rate;

	/* AuxDAC Control */
	phy->pdata->auxdac_ctrl.auxdac_manual_mode_en =
		init_param->aux_dac_manual_mode_enable;
	phy->pdata->auxdac_ctrl.dac1_default_value =
		init_param->aux_dac1_default_value_mV;
	phy->pdata->auxdac_ctrl.dac1_in_rx_en =
		init_param->aux_dac1_active_in_rx_enable;
	phy->pdata->auxdac_ctrl.dac1_in_tx_en =
		init_param->aux_dac1_active_in_tx_enable;
	phy->pdata->auxdac_ctrl.dac1_in_alert_en =
		init_param->aux_dac1_active_in_alert_enable;
	phy->pdata->auxdac_ctrl.dac1_rx_delay_us = init_param->aux_dac1_rx_delay_us;
	phy->pdata->auxdac_ctrl.dac1_tx_delay_us = init_param->aux_dac1_tx_delay_us;
	phy->pdata->auxdac_ctrl.dac2_default_value =
		init_param->aux_dac2_default_value_mV;
	phy->pdata->auxdac_ctrl.dac2_in_rx_en =
		init_param->aux_dac2_active_in_rx_enable;
	phy->pdata->auxdac_ctrl.dac2_in_tx_en =
		init_param->aux_dac2_active_in_tx_enable;
	phy->pdata->auxdac_ctrl.dac2_in_alert_en =
		init_param->aux_dac2_active_in_alert_enable;
	phy->pdata->auxdac_ctrl.dac2_rx_delay_us = init_param->aux_dac2_rx_delay_us;
	phy->pdata->auxdac_ctrl.dac2_tx_delay_us = init_param->aux_dac2_tx_delay_us;

	/* Temperature Sensor Control */
	phy->pdata->auxadc_ctrl.temp_sensor_decimation =
		init_param->temp_sense_decimation;
	phy->pdata->auxadc_ctrl.temp_time_inteval_ms =
		init_param->temp_sense_measurement_interval_ms;
	phy->pdata->auxadc_ctrl.offset = init_param->temp_sense_offset_signed;
	phy->pdata->auxadc_ctrl.periodic_temp_measuremnt =
		init_param->temp_sense_periodic_measurement_enable;

	/* Control Out Setup */
	phy->pdata->ctrl_outs_ctrl.en_mask = init_param->ctrl_outs_enable_mask;
	phy->pdata->ctrl_outs_ctrl.index = init_param->ctrl_outs_index;

	/* External LNA Control */
	phy->pdata->elna_ctrl.settling_delay_ns = init_param->elna_settling_delay_ns;
	phy->pdata->elna_ctrl.gain_mdB = init_param->elna_gain_mdB;
	phy->pdata->elna_ctrl.bypass_loss_mdB = init_param->elna_bypass_loss_mdB;
	phy->pdata->elna_ctrl.elna_1_control_en =
		init_param->elna_rx1_gpo0_control_enable;
	phy->pdata->elna_ctrl.elna_2_control_en =
		init_param->elna_rx2_gpo1_control_enable;
	phy->pdata->elna_ctrl.elna_in_gaintable_all_index_en =
		init_param->elna_gaintable_all_index_enable;

	/* Digital Interface Control */
	phy->pdata->dig_interface_tune_skipmode =
		(init_param->digital_interface_tune_skip_mode);
	phy->pdata->dig_interface_tune_fir_disable =
		(init_param->digital_interface_tune_fir_disable);
	phy->pdata->port_ctrl.pp_conf[0] = (init_param->pp_tx_swap_enable << 7);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->pp_rx_swap_enable << 6);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->tx_channel_swap_enable << 5);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->rx_channel_swap_enable << 4);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->rx_frame_pulse_mode_enable <<
					     3);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->two_t_two_r_timing_enable <<
					     2);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->invert_data_bus_enable << 1);
	phy->pdata->port_ctrl.pp_conf[0] |= (init_param->invert_data_clk_enable << 0);
	phy->pdata->port_ctrl.pp_conf[1] = (init_param->fdd_alt_word_order_enable << 7);
	phy->pdata->port_ctrl.pp_conf[1] |= (init_param->invert_rx_frame_enable << 2);
	phy->pdata->port_ctrl.pp_conf[2] = (init_param->fdd_rx_rate_2tx_enable << 7);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->swap_ports_enable << 6);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->single_data_rate_enable << 5);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->lvds_mode_enable << 4);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->half_duplex_mode_enable << 3);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->single_port_mode_enable << 2);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->full_port_enable << 1);
	phy->pdata->port_ctrl.pp_conf[2] |= (init_param->full_duplex_swap_bits_enable <<
					     0);
	phy->pdata->port_ctrl.pp_conf[1] |= (init_param->delay_rx_data & 0x3);
	phy->pdata->port_ctrl.rx_clk_data_delay = DATA_CLK_DELAY(
				init_param->rx_data_clock_delay);
	phy->pdata->port_ctrl.rx_clk_data_delay |= RX_DATA_DELAY(
				init_param->rx_data_delay);
	phy->pdata->port_ctrl.tx_clk_data_delay = FB_CLK_DELAY(
				init_param->tx_fb_clock_delay);
	phy->pdata->port_ctrl.tx_clk_data_delay |= TX_DATA_DELAY(
				init_param->tx_data_delay);
	phy->pdata->port_ctrl.lvds_bias_ctrl = ((init_param->lvds_bias_mV - 75) / 75) &
					       0x7;
	phy->pdata->port_ctrl.lvds_bias_ctrl |=
		(init_param->lvds_rx_onchip_termination_enable << 5);
	phy->pdata->rx1rx2_phase_inversion_en = init_param->rx1rx2_phase_inversion_en;

	/* GPO Control */
	phy->pdata->gpo_ctrl.gpo0_inactive_state_high_en =
		init_param->gpo0_inactive_state_high_enable;
	phy->pdata->gpo_ctrl.gpo1_inactive_state_high_en =
		init_param->gpo1_inactive_state_high_enable;
	phy->pdata->gpo_ctrl.gpo2_inactive_state_high_en =
		init_param->gpo2_inactive_state_high_enable;
	phy->pdata->gpo_ctrl.gpo3_inactive_state_high_en =
		init_param->gpo3_inactive_state_high_enable;

	phy->pdata->gpo_ctrl.gpo0_slave_rx_en = init_param->gpo0_slave_rx_enable;
	phy->pdata->gpo_ctrl.gpo0_slave_tx_en = init_param->gpo0_slave_tx_enable;
	phy->pdata->gpo_ctrl.gpo1_slave_rx_en = init_param->gpo1_slave_rx_enable;
	phy->pdata->gpo_ctrl.gpo1_slave_tx_en = init_param->gpo1_slave_tx_enable;
	phy->pdata->gpo_ctrl.gpo2_slave_rx_en = init_param->gpo2_slave_rx_enable;
	phy->pdata->gpo_ctrl.gpo2_slave_tx_en = init_param->gpo2_slave_tx_enable;
	phy->pdata->gpo_ctrl.gpo3_slave_rx_en = init_param->gpo3_slave_rx_enable;
	phy->pdata->gpo_ctrl.gpo3_slave_tx_en = init_param->gpo3_slave_tx_enable;

	phy->pdata->gpo_ctrl.gpo0_rx_delay_us = init_param->gpo0_rx_delay_us;
	phy->pdata->gpo_ctrl.gpo0_tx_delay_us = init_param->gpo0_tx_delay_us;
	phy->pdata->gpo_ctrl.gpo1_rx_delay_us = init_param->gpo1_rx_delay_us;
	phy->pdata->gpo_ctrl.gpo1_tx_delay_us = init_param->gpo1_tx_delay_us;
	phy->pdata->gpo_ctrl.gpo2_rx_delay_us = init_param->gpo2_rx_delay_us;
	phy->pdata->gpo_ctrl.gpo2_tx_delay_us = init_param->gpo2_tx_delay_us;
	phy->pdata->gpo_ctrl.gpo3_rx_delay_us = init_param->gpo3_rx_delay_us;
	phy->pdata->gpo_ctrl.gpo3_tx_delay_us = init_param->gpo3_tx_delay_us;

	/* Tx Monitor Control */
	phy->pdata->txmon_ctrl.low_high_gain_threshold_mdB =
		init_param->low_high_gain_threshold_mdB;
	phy->pdata->txmon_ctrl.low_gain_dB = init_param->low_gain_dB;
	phy->pdata->txmon_ctrl.high_gain_dB = init_param->high_gain_dB;
	phy->pdata->txmon_ctrl.tx_mon_track_en = init_param->tx_mon_track_en;
	phy->pdata->txmon_ctrl.one_shot_mode_en = init_param->one_shot_mode_en;
	phy->pdata->txmon_ctrl.tx_mon_delay = init_param->tx_mon_delay;
	phy->pdata->txmon_ctrl.tx_mon_duration = init_param->tx_mon_duration;
	phy->pdata->txmon_ctrl.tx1_mon_front_end_gain =
		init_param->tx1_mon_front_end_gain;
	phy->pdata->txmon_ctrl.tx2_mon_front_end_gain =
		init_param->tx2_mon_front_end_gain;
	phy->pdata->txmon_ctrl.tx1_mon_lo_cm = init_param->tx1_mon_lo_cm;
	phy->pdata->txmon_ctrl.tx2_mon_lo_cm = init_param->tx2_mon_lo_cm;

	phy->pdata->debug_mode = true;
	phy->pdata->gpio_resetb = init_param->gpio_resetb;
	/* Optional: next three GPIOs are used for MCS synchronization */
	phy->pdata->gpio_sync = init_param->gpio_sync;
	phy->pdata->gpio_cal_sw1 = init_param->gpio_cal_sw1;
	phy->pdata->gpio_cal_sw2 = init_param->gpio_cal_sw2;

	phy->pdata->port_ctrl.digital_io_ctrl = 0;
	phy->pdata->port_ctrl.lvds_invert[0] = init_param->lvds_invert1_control;
	phy->pdata->port_ctrl.lvds_invert[1] = init_param->lvds_invert2_control;

	if (AD9364_DEVICE) {
		phy->pdata->rx2tx2 = false;
		phy->pdata->rx1tx1_mode_use_rx_num = 1;
		phy->pdata->rx1tx1_mode_use_tx_num = 1;
	}

#ifndef AXI_ADC_NOT_PRESENT
	phy->adc_conv->chip_info = &axiadc_chip_info_tbl[phy->pdata->rx2tx2 ?
						      ID_AD9361 : ID_AD9364];
#endif

	phy->rx_eq_2tx = false;

	phy->current_table = -1;
	phy->bypass_tx_fir = true;
	phy->bypass_rx_fir = true;
	phy->rate_governor = 1;
	phy->rfdc_track_en = true;
	phy->bbdc_track_en = true;
	phy->quad_track_en = true;

	phy->gt_info = ad9361_adi_gt_info;

	phy->bist_loopback_mode = 0;
	phy->bist_config = 0;
	phy->bist_prbs_mode = BIST_DISABLE;
	phy->bist_tone_mode = BIST_DISABLE;
	phy->bist_tone_freq_Hz = 0;
	phy->bist_tone_level_dB = 0;
	phy->bist_tone_mask = 0;

	ad9361_reset(phy);

	ret = ad9361_spi_read(phy->spi, REG_PRODUCT_ID);
	if ((ret & PRODUCT_ID_MASK) != PRODUCT_ID_9361) {
		printf("%s : Unsupported PRODUCT_ID 0x%X", __func__, (unsigned int)ret);
		ret = -ENODEV;
		goto out;
	}
	rev = ret & REV_MASK;

	phy->ad9361_rfpll_ext_recalc_rate = init_param->ad9361_rfpll_ext_recalc_rate;
	phy->ad9361_rfpll_ext_round_rate = init_param->ad9361_rfpll_ext_round_rate;
	phy->ad9361_rfpll_ext_set_rate = init_param->ad9361_rfpll_ext_set_rate;

	ret = register_clocks(phy);
	if (ret < 0)
		goto out;

#ifndef AXI_ADC_NOT_PRESENT
	axiadc_init(phy);
	phy->adc_state->pcore_version = axiadc_read(phy->adc_state, ADI_REG_VERSION);
#endif

	ret = ad9361_setup(phy);
	if (ret < 0)
		goto out;

#ifndef AXI_ADC_NOT_PRESENT
	/* platform specific wrapper to call ad9361_post_setup() */
	ret = axiadc_post_setup(phy);
	if (ret < 0)
		goto out;
#endif

	printf("%s : AD936x Rev %d successfully initialized\n", __func__, (int)rev);

	*ad9361_phy = phy;

	return 0;

out:
	free(phy->spi);
#ifndef AXI_ADC_NOT_PRESENT
	free(phy->adc_conv);
	free(phy->adc_state);
#endif
	free(phy->clk_refin);
	free(phy->pdata);
	free(phy);
	printf("%s : AD936x initialization error\n", __func__);

	return -ENODEV;
}

/**
 * Set the Enable State Machine (ENSM) mode.
 * @param phy The AD9361 current state structure.
 * @param mode The ENSM mode.
 * 			   Accepted values:
 * 				ENSM_MODE_TX
 * 				ENSM_MODE_RX
 * 				ENSM_MODE_ALERT
 * 				ENSM_MODE_FDD
 * 				ENSM_MODE_WAIT
 * 				ENSM_MODE_SLEEP
 * 				ENSM_MODE_PINCTRL
 * 				ENSM_MODE_PINCTRL_FDD_INDEP
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//ENSM（Enable State Machine）是 AD9361 的状态机，控制芯片的工作模式（如收发、休眠等）
int32_t ad9361_set_en_state_machine_mode (struct ad9361_rf_phy *phy,
		uint32_t mode)
{
	int32_t ret;
	uint8_t ensm_state;
	bool pinctrl = false;

	phy->pdata->fdd_independent_mode = false;

	switch (mode) {
	case ENSM_MODE_TX:
		ensm_state = ENSM_STATE_TX;
		break;
	case ENSM_MODE_RX:
		ensm_state = ENSM_STATE_RX;
		break;
	case ENSM_MODE_ALERT:
		ensm_state = ENSM_STATE_ALERT;
		break;
	case ENSM_MODE_FDD:
		ensm_state = ENSM_STATE_FDD;
		break;
	case ENSM_MODE_WAIT:
		ensm_state = ENSM_STATE_SLEEP_WAIT;
		break;
	case ENSM_MODE_SLEEP:
		ensm_state = ENSM_STATE_SLEEP;
		break;
	case ENSM_MODE_PINCTRL:
		ensm_state = ENSM_STATE_SLEEP_WAIT;
		pinctrl = true;
		break;
	case ENSM_MODE_PINCTRL_FDD_INDEP:
		ensm_state = ENSM_STATE_FDD;
		phy->pdata->fdd_independent_mode = true;
		break;
	default:
		return -EINVAL;
	}
	// 配置ENSM模式（如FDD使能、引脚控制使能）
	ad9361_set_ensm_mode(phy, phy->pdata->fdd, pinctrl);
	// 将状态写入硬件寄存器，完成模式切换
	ret = ad9361_ensm_set_state(phy, ensm_state, pinctrl);

	return ret;
}

/**
 * Get the Enable State Machine (ENSM) mode.
 * @param phy The AD9361 current state structure.
 * @param mode A variable to store the selected ENSM mode.
 * @return 0 in case of success, negative error code otherwise.
 */
//读取硬件寄存器，获取 AD9361 当前的工作模式（如正在接收还是发射），用于状态查询或判断
int32_t ad9361_get_en_state_machine_mode (struct ad9361_rf_phy *phy,
		uint32_t *mode)
{
	uint8_t ensm_state;
	bool pinctrl = false;
	int32_t ret;

	ensm_state = ad9361_spi_read(phy->spi, REG_STATE);
	ensm_state &= ENSM_STATE(~0);
	ret = ad9361_spi_read(phy->spi, REG_ENSM_CONFIG_1);
	if ((ret & ENABLE_ENSM_PIN_CTRL) == ENABLE_ENSM_PIN_CTRL)
		pinctrl = true;  // 寄存器中引脚控制位为1，标记启用

	// 将硬件状态（ensm_state）映射回用户可理解的模式（mode）
	switch (ensm_state) {
	case ENSM_STATE_TX:
		*mode = ENSM_MODE_TX;
		break;
	case ENSM_STATE_RX:
		*mode = ENSM_MODE_RX;
		break;
	case ENSM_STATE_ALERT:
		*mode = ENSM_MODE_ALERT;
		break;
	case ENSM_STATE_FDD:
		if (phy->pdata->fdd_independent_mode)
			*mode = ENSM_MODE_PINCTRL_FDD_INDEP;
		else
			*mode = ENSM_MODE_FDD;
		break;
	case ENSM_STATE_SLEEP_WAIT:
		if (pinctrl)
			*mode = ENSM_MODE_PINCTRL;
		else
			*mode = ENSM_MODE_WAIT;
		break;
	case ENSM_STATE_SLEEP:
		*mode = ENSM_MODE_SLEEP;
		break;
	default:
		return -EINVAL;
	}

	return 0;
}

/**
 * Set the receive RF gain for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel number (RX1, RX2).
 * 			 Accepted values in 2x2 mode:
 * 			  RX1 (0)
 * 			  RX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  RX1 (0)
 * @param gain_db The RF gain (dB).
 * 				  Example:
 * 				   10 (10 dB)
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_set_rx_rf_gain (struct ad9361_rf_phy *phy,
			       uint8_t ch, int32_t gain_db)
{
	struct rf_rx_gain rx_gain = {0, 0, 0, 0, 0, 0, 0, 0, 0};
	int32_t ret = 0;

	rx_gain.gain_db = gain_db;// 将用户输入的dB值存入结构体
	// 调用底层函数设置增益：
	// 1. ad9361_1rx1tx_channel_map：将用户通道号（0/1）映射到硬件实际通道号
	// 2. ad9361_set_rx_gain：底层实现，将dB值转换为寄存器值并写入硬件
	ret = ad9361_set_rx_gain(phy,
				 ad9361_1rx1tx_channel_map(phy, false,
						 ch + 1), &rx_gain);

	return ret;
}

/**
 * Get current receive RF gain for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel number (RX1, RX2).
 * 			 Accepted values in 2x2 mode:
 * 			  RX1 (0)
 * 			  RX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  RX1 (0)
 * @param gain_db A variable to store the RF gain (dB).
 * @return 0 in case of success, negative error code otherwise.
 */
// 获取当前接收通道增益
int32_t ad9361_get_rx_rf_gain (struct ad9361_rf_phy *phy,
			       uint8_t ch, int32_t *gain_db)
{
	struct rf_rx_gain rx_gain = {0, 0, 0, 0, 0, 0, 0, 0, 0};
	int32_t ret = 0;

	// 调用底层函数获取增益：
	// 1. 映射通道号
	// 2. ad9361_get_rx_gain：从硬件寄存器读取增益值，转换为dB存入rx_gain
	ret = ad9361_get_rx_gain(phy, ad9361_1rx1tx_channel_map(phy,
				 false, ch + 1), &rx_gain);

	*gain_db = rx_gain.gain_db;

	return ret;
}

/**
 * Set the RX RF bandwidth.
 * @param phy The AD9361 current state structure.
 * @param bandwidth_hz The desired bandwidth (Hz).
 * 					   Example:
 * 					    18000000 (18 MHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//配置接收射频滤波器的带宽（如 20MHz），决定接收信号的频率范围（仅允许带宽内的信号通过，抑制带外干扰）
int32_t ad9361_set_rx_rf_bandwidth (struct ad9361_rf_phy *phy,
				    uint32_t bandwidth_hz)
{
	int32_t ret = 0;
	// 校验带宽合法性（AD9361支持的带宽范围有限，如最小200kHz，最大56MHz）
	bandwidth_hz = ad9361_validate_rf_bw(phy, bandwidth_hz);

	// 若目标带宽与当前带宽不同，才执行更新（避免重复操作）
	if (phy->current_rx_bw_Hz != bandwidth_hz)
		// 调用底层函数更新带宽（同时需保持TX带宽不变）
		ret = ad9361_update_rf_bandwidth(phy, bandwidth_hz,
						 phy->current_tx_bw_Hz);
	else
		ret = 0;

	return ret;
}

/**
 * Get the RX RF bandwidth.
 * @param phy The AD9361 current state structure.
 * @param bandwidth_hz A variable to store the bandwidth value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
//获取当前接收带宽值（Hz），用于确认带宽配置是否生效
int32_t ad9361_get_rx_rf_bandwidth (struct ad9361_rf_phy *phy,
				    uint32_t *bandwidth_hz)
{
	*bandwidth_hz = phy->current_rx_bw_Hz;

	return 0;
}

/**
 * Set the RX sampling frequency.
 * @param phy The AD9361 current state structure.
 * @param sampling_freq_hz The desired frequency (Hz).
 * 						   Example:
 * 						    30720000 (30.72 MHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//设置接收链路的采样频率（如 30.72MHz），决定 ADC 的采样速度，是数字信号处理的基础（需满足奈奎斯特采样定理）
int32_t ad9361_set_rx_sampling_freq (struct ad9361_rf_phy *phy,
				     uint32_t sampling_freq_hz)
{
	int32_t ret;
	uint32_t rx[6], tx[6];

	// 计算采样率对应的时钟链参数：
	// 输入目标采样率，输出RX和TX链路的6个时钟点频率（如ADC采样时钟、FIR时钟）
	ret = ad9361_calculate_rf_clock_chain(phy, sampling_freq_hz,
					      phy->rate_governor, rx, tx);
	if (ret < 0)
		return ret;

	// 配置RX和TX的时钟链（将计算出的时钟值写入硬件寄存器）
	ad9361_set_trx_clock_chain(phy, rx, tx);

	// 带宽依赖采样率，需重新更新带宽配置（保持原带宽值不变）
	ret = ad9361_update_rf_bandwidth(phy, phy->current_rx_bw_Hz,
					 phy->current_tx_bw_Hz);

	return ret;
}

/**
 * Get current RX sampling frequency.
 * @param phy The AD9361 current state structure.
 * @param sampling_freq_hz A variable to store the frequency value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
//获取当前接收采样率，用于确认采样率配置是否正确（如与基带处理单元的时钟同步）
int32_t ad9361_get_rx_sampling_freq (struct ad9361_rf_phy *phy,
				     uint32_t *sampling_freq_hz)
{
	*sampling_freq_hz = (uint32_t)clk_get_rate(phy,
			    phy->ref_clk_scale[RX_SAMPL_CLK]);

	return 0;
}

/**
 * Set the RX LO frequency.
 * @param phy The AD9361 current state structure.
 * @param lo_freq_hz The desired frequency (Hz).
 * 					 Example:
 * 					  2400000000 (2.4 GHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//设置接收本振（LO）频率（如 2.4GHz），决定接收信号的频段（通过混频将射频信号转换为基带信号）
int32_t ad9361_set_rx_lo_freq (struct ad9361_rf_phy *phy,
			       uint64_t lo_freq_hz)
{
	int32_t ret;

	// 调用时钟设置函数：
	// 1. ad9361_to_clk：将频率值转换为时钟结构体的内部表示
	// 2. clk_set_rate：配置RX射频锁相环（RFPLL）的频率，即本振频率
	ret = clk_set_rate(phy, phy->ref_clk_scale[RX_RFPLL],
			   ad9361_to_clk(lo_freq_hz));

	return ret;
}

/**
 * Get current RX LO frequency.
 * @param phy The AD9361 current state structure.
 * @param lo_freq_hz A variable to store the frequency value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
//获取当前接收本振频率，用于确认工作频段是否正确（如是否在目标通信频段内）
int32_t ad9361_get_rx_lo_freq (struct ad9361_rf_phy *phy,
			       uint64_t *lo_freq_hz)
{
	// 从RX RFPLL时钟结构体中读取频率，转换为Hz
	*lo_freq_hz = ad9361_from_clk(clk_get_rate(phy,
				      phy->ref_clk_scale[RX_RFPLL]));

	return 0;
}

/**
 * Switch between internal and external LO.
 * @param phy The AD9361 state structure.
 * @param int_ext The selected lo (INT_LO, EXT_LO).
 * 			  Accepted values:
 * 			   INT_LO // 内部本振（芯片自身合成）
 * 			   EXT_LO // 外部本振（外部信号源输入）
 * @return 0 in case of success, negative error code otherwise.
 */
//选择接收本振的来源（内部合成或外部输入），外部本振通常用于需要更高频率精度的场景
int32_t ad9361_set_rx_lo_int_ext(struct ad9361_rf_phy *phy, uint8_t int_ext)
{
	if ((phy->dev_sel == ID_AD9363A) && (int_ext == EXT_LO)) {
		printf("%s : EXT_LO is not supported by AD9363!\n", __func__);
		return -1;
	}

	phy->pdata->use_ext_rx_lo = int_ext;

	return ad9361_clk_mux_set_parent(phy->ref_clk_scale[RX_RFPLL], int_ext);
}

/**
 * Get the RSSI for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel (RX1, RX2).
 * 			 Accepted values in 2x2 mode:
 * 			  RX1 (0)
 * 			  RX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  RX1 (0)
 * @param rssi A variable to store the RSSI.
 * @return 0 in case of success, negative error code otherwise.
 */
//获取指定接收通道的信号强度（RSSI），用于判断接收信号的强弱（如通信质量评估）
int32_t ad9361_get_rx_rssi (struct ad9361_rf_phy *phy,
			    uint8_t ch, struct rf_rssi *rssi)
{
	int32_t ret;

	rssi->ant = ad9361_1rx1tx_channel_map(phy, false, ch + 1);
	rssi->duration = 1;
	ret = ad9361_read_rssi(phy, rssi);

	return ret;
}

/**
 * Set the gain control mode for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel (RX1, RX2).
 * 			 Accepted values in 2x2 mode:
 * 			  RX1 (0)
 * 			  RX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  RX1 (0)
 * @param gc_mode The gain control mode (manual, fast_attack, slow_attack,
 * 				  hybrid).
 *                Accepted values:
 *				   RF_GAIN_MGC (manual)
 *				   RF_GAIN_FASTATTACK_AGC (fast_attack)
 *				   RF_GAIN_SLOWATTACK_AGC (slow_attack)
 *				   RF_GAIN_HYBRID_AGC (hybrid)
 * @return 0 in case of success, negative error code otherwise.
 */
//选择接收通道的增益控制模式（手动或自动），决定增益是由开发者手动设置还是芯片自动调整
int32_t ad9361_set_rx_gain_control_mode (struct ad9361_rf_phy *phy,
		uint8_t ch, uint8_t gc_mode)
{
	struct rf_gain_ctrl gc = {0, 0};

	// 将用户通道号（0/1）映射到硬件实际通道号
	gc.ant = ad9361_1rx1tx_channel_map(phy, false, ch + 1);
	// 保存模式到状态结构体（供后续查询），并赋值给gc结构体
	gc.mode = phy->agc_mode[ch] = gc_mode;

	// 调用底层函数，将模式配置写入硬件寄存器
	ad9361_set_gain_ctrl_mode(phy, &gc);

	return 0;
}

/**
 * Get the gain control mode for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel (RX1, RX2).
 * 			 Accepted values:
 * 			  RX1 (0)
 * 			  RX2 (1)
 * @param gc_mode A variable to store the gain control mode.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询当前接收通道的增益控制模式（确认是手动还是自动）。
int32_t ad9361_get_rx_gain_control_mode (struct ad9361_rf_phy *phy,
		uint8_t ch, uint8_t *gc_mode)
{
	*gc_mode = phy->agc_mode[ch];

	return 0;
}

/**
 * Set the RX FIR filter configuration.
 * @param phy The AD9361 current state structure.
 * @param fir_cfg FIR filter configuration.// FIR配置结构体（包含系数、增益、抽取率等）
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//配置接收链路的 FIR 滤波器（如自定义滤波系数、增益、抽取率），用于信号滤波和速率调整
int32_t ad9361_set_rx_fir_config (struct ad9361_rf_phy *phy,
				  AD9361_RXFIRConfig fir_cfg)
{
	int32_t ret;

	// 保存FIR抽取率（将输入数据按比例抽取，降低采样率）
	phy->rx_fir_dec = fir_cfg.rx_dec;
	// 调用底层函数加载FIR系数：
	// 1. 配置目标（RX通道）
	// 2. 写入增益、系数长度和具体系数值
	ret = ad9361_load_fir_filter_coef(phy, (enum fir_dest)(fir_cfg.rx | FIR_IS_RX),
					  fir_cfg.rx_gain, fir_cfg.rx_coef_size, fir_cfg.rx_coef);

	return ret;
}

/**
 * Get the RX FIR filter configuration.
 * @param phy The AD9361 current state structure.
 * @param tx_ch The selected RX channel (RX1, RX2).
 * 				Accepted values:
 * 				 RX1 (0)
 * 				 RX2 (1)
 * @param fir_cfg FIR filter configuration output file.
 * @return 0 in case of success, negative error code otherwise.
 */
//读取当前接收 FIR 滤波器的配置（系数、增益、抽取率等），用于验证配置是否正确或备份参数
int32_t ad9361_get_rx_fir_config(struct ad9361_rf_phy *phy, uint8_t rx_ch,
				 AD9361_RXFIRConfig *fir_cfg)
{
	int32_t ret;
	uint32_t fir_conf;
	uint8_t index;

	rx_ch += 1;

	// 读取RX FIR配置寄存器（REG_RX_FILTER_CONFIG）
	ret = ad9361_spi_read(phy->spi, REG_RX_FILTER_CONFIG);
	if(ret < 0)
		return ret;
	fir_conf = ret;

	// 计算FIR系数长度（从寄存器位解析，公式：(位值+1)*16）
	fir_cfg->rx_coef_size = (((fir_conf & FIR_NUM_TAPS(7)) >> 5) + 1) * 16;

	// 读取RX FIR增益寄存器（REG_RX_FILTER_GAIN），转换为dB值
	ret = ad9361_spi_read(phy->spi, REG_RX_FILTER_GAIN);
	if(ret < 0)
		return ret;
	fir_cfg->rx_gain = -6 * (ret & FILTER_GAIN(3)) + 6;
	fir_cfg->rx = rx_ch;

	// 配置FIR寄存器，启用系数读取模式
	fir_conf &= ~FIR_SELECT(3);// 清除原有选择位
	fir_conf |= FIR_SELECT(rx_ch) | FIR_START_CLK;// 选择目标通道并启动时钟
	ad9361_spi_write(phy->spi, REG_RX_FILTER_CONFIG, fir_conf);

	// 读取128个FIR系数（硬件最多支持128个）
	for(index = 0; index < 128; index++) {
		ad9361_spi_write(phy->spi, REG_RX_FILTER_COEF_ADDR, index);
		// 读取低8位系数
		ret = ad9361_spi_read(phy->spi, REG_RX_FILTER_COEF_READ_DATA_1);
		if(ret < 0)
			return ret;
		fir_cfg->rx_coef[index] = ret;
		// 读取高8位系数，拼接为16位值
		ret = ad9361_spi_read(phy->spi, REG_RX_FILTER_COEF_READ_DATA_2);
		if(ret < 0)
			return ret;
		fir_cfg->rx_coef[index] |= (ret << 8);
	}

	// 关闭系数读取模式
	fir_conf &= ~FIR_START_CLK;
	ad9361_spi_write(phy->spi, REG_RX_FILTER_CONFIG, fir_conf);

	// 从状态结构体中读取FIR抽取率
	fir_cfg->rx_dec = phy->rx_fir_dec;

	return 0;
}

/**
 * Enable/disable the RX FIR filter.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//控制接收 FIR 滤波器的启用 / 禁用（禁用时信号直接绕过 FIR，用于特殊场景调试）
int32_t ad9361_set_rx_fir_en_dis (struct ad9361_rf_phy *phy,
				  uint8_t en_dis)
{
	int32_t ret = 0;

	// 若当前状态与目标状态一致，直接返回（避免重复操作）
	if(phy->bypass_rx_fir == !en_dis)
		return ret;

	// 更新状态结构体：bypass_rx_fir为true表示禁用（bypass即绕过）
	phy->bypass_rx_fir = !en_dis;
	// 调用底层函数验证并启用/禁用FIR（检查是否与其他配置冲突）
	ret = ad9361_validate_enable_fir(phy);
	if (ret < 0) {
		phy->bypass_rx_fir = true;
	}

	return ret;
}

/**
 * Get the status of the RX FIR filter.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询接收 FIR 滤波器是否启用
int32_t ad9361_get_rx_fir_en_dis (struct ad9361_rf_phy *phy,
				  uint8_t *en_dis)
{
	// bypass_rx_fir为false表示启用（!false=1），true表示禁用（!true=0）
	*en_dis = !phy->bypass_rx_fir;

	return 0;
}

/**
 * Enable/disable the RX RFDC Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 */
// 启用 / 禁用射频直流偏移自动校准，减少直流分量对信号的影响。
int32_t ad9361_set_rx_rfdc_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t en_dis)
{
	int32_t ret = 0;

	if(phy->rfdc_track_en == en_dis)
		return ret;

	// 更新状态结构体
	phy->rfdc_track_en = en_dis;
	// 调用底层函数，同步更新直流偏移、正交失衡的跟踪状态
	ret = ad9361_tracking_control(phy, phy->bbdc_track_en,
				      phy->rfdc_track_en, phy->quad_track_en);

	return ret;
}

/**
 * Get the status of the RX RFDC Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//获取 RX 射频直流偏移跟踪状态
int32_t ad9361_get_rx_rfdc_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t *en_dis)
{
	// 直接读取状态结构体
	*en_dis = phy->rfdc_track_en;

	return 0;
}

/**
 * Enable/disable the RX BasebandDC Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 */
//启用 / 禁用 RX 基带直流偏移跟踪，确保基带信号直流分量最小
int32_t ad9361_set_rx_bbdc_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t en_dis)
{
	int32_t ret = 0;

	if(phy->bbdc_track_en == en_dis)
		return ret;

	phy->bbdc_track_en = en_dis;
	// 同步更新所有跟踪功能状态
	ret = ad9361_tracking_control(phy, phy->bbdc_track_en,
				      phy->rfdc_track_en, phy->quad_track_en);

	return ret;
}

/**
 * Get the status of the RX BasebandDC Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_rx_bbdc_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t *en_dis)
{
	*en_dis = phy->bbdc_track_en;

	return 0;
}

/**
 * Enable/disable the RX Quadrature Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 */
// 启用 / 禁用 I/Q 正交失衡自动校准，提高信号解调精度
int32_t ad9361_set_rx_quad_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t en_dis)
{
	int32_t ret = 0;

	if(phy->quad_track_en == en_dis)
		return ret;

	phy->quad_track_en = en_dis;
	ret = ad9361_tracking_control(phy, phy->bbdc_track_en,
				      phy->rfdc_track_en, phy->quad_track_en);

	return ret;
}

/**
 * Get the status of the RX Quadrature Tracking.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
// 获取 I/Q 正交失衡自动校准状态
int32_t ad9361_get_rx_quad_track_en_dis (struct ad9361_rf_phy *phy,
		uint8_t *en_dis)
{
	*en_dis = phy->quad_track_en;

	return 0;
}

/**
 * Set the RX RF input port.
 * @param phy The AD9361 current state structure.
 * @param mode The RF port.// 射频端口模式（平衡/非平衡、监控端口等）
 * 			   Accepted values:
 *				A_BALANCED (0 - (RX1A_N &  RX1A_P) and (RX2A_N & RX2A_P) enabled; balanced)
 *				B_BALANCED (1 - (RX1B_N &  RX1B_P) and (RX2B_N & RX2B_P) enabled; balanced)
 *				C_BALANCED (2 - (RX1C_N &  RX1C_P) and (RX2C_N & RX2C_P) enabled; balanced)
 *				A_N		   (3 - RX1A_N and RX2A_N enabled; unbalanced)
 *				A_P		   (4 - RX1A_P and RX2A_P enabled; unbalanced)
 *				B_N		   (5 - RX1B_N and RX2B_N enabled; unbalanced)
 *				B_P		   (6 - RX1B_P and RX2B_P enabled; unbalanced)
 *				C_N		   (7 - RX1C_N and RX2C_N enabled; unbalanced)
 *				C_P		   (8 - RX1C_P and RX2C_P enabled; unbalanced)
 *				TX_MON1	   (9 - TX_MONITOR1)
 *				TX_MON2	   (10 - TX_MONITOR2)
 *				TX_MON1_2  (11 - TX_MONITOR1 & TX_MONITOR2)
 * @return 0 in case of success, negative error code otherwise.
 */
//指定接收射频信号的输入引脚（根据硬件布线选择，如使用 RX1A 平衡端口）
int32_t ad9361_set_rx_rf_port_input (struct ad9361_rf_phy *phy,
				     uint32_t mode)
{
	int32_t ret;

	phy->pdata->rf_rx_input_sel = mode;

	// 调用底层函数配置射频端口（同时配置TX端口，保持一致性）
	ret = ad9361_rf_port_setup(phy, false,
				   phy->pdata->rf_rx_input_sel,
				   phy->pdata->rf_tx_output_sel);

	return ret;
}

/**
 * Get the selected RX RF input port.
 * @param phy The AD9361 current state structure.
 * @param mode The RF port.
 * @return 0 in case of success, negative error code otherwise.
 */
//获取接收射频信号的输入引脚
int32_t ad9361_get_rx_rf_port_input (struct ad9361_rf_phy *phy,
				     uint32_t *mode)
{
	*mode = phy->pdata->rf_rx_input_sel;

	return 0;
}

/**
 * Store RX fastlock profile.
 * To create a profile tune the synthesizer (ad9361_set_rx_lo_freq()) and then
 * call this function specifying the target profile number.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * 		    * Store RX fastlock profile.  // 存储快速锁定配置文件
 *          （先调用ad9361_set_rx_lo_freq设置频率，再调用此函数保存到指定profile）
 * @param profile The profile number (0 - 7).  // 配置文件编号（0-7）
 * @return 0 in case of success, negative error code otherwise.
 */
//在 TDD 场景下，快速切换接收本振频率（通过预存配置减少校准时间）
int32_t ad9361_rx_fastlock_store(struct ad9361_rf_phy *phy, uint32_t profile)
{
	return ad9361_fastlock_store(phy, 0, profile);
}

/**
 * Recall specified RX fastlock profile.
 * When in fastlock pin select mode (init_param->rx_fastlock_pincontrol_enable),
 * the function needs to be called before then the pin-control can be used.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_rx_fastlock_recall(struct ad9361_rf_phy *phy, uint32_t profile)
{
	return ad9361_fastlock_recall(phy, 0, profile);
}

/**
 * Load RX fastlock profile. A previously saved profile can be loaded in any
 * of the 8 available slots.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @param values Fastlock profile program data.
 * 				 Example:
 * 				  val0,val1,val2,聟,val15
 * @return 0 in case of success, negative error code otherwise.
 */
//将外部预定义的快速锁定配置（如本振校准参数）加载到芯片的指定配置文件中，用于自定义快速切换场景
int32_t ad9361_rx_fastlock_load(struct ad9361_rf_phy *phy, uint32_t profile,
				uint8_t *values)
{
	// 调用底层函数加载配置数据到指定profile（0表示RX链路）
	return ad9361_fastlock_load(phy, 0, profile, values);
}

/**
 * Save RX fastlock profile. In order to use more than 8 Profiles, an existing
 * profile can be read back and stored by the user application.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @param values Fastlock profile program data.
 * @return 0 in case of success, negative error code otherwise.
 */
//将芯片中已存储的快速锁定配置文件读取到外部（如保存到内存），用于备份或复用（超过 8 个配置文件时）
int32_t ad9361_rx_fastlock_save(struct ad9361_rf_phy *phy, uint32_t profile,
				uint8_t *values)
{
	// 调用底层函数读取指定profile的配置数据并保存到外部（0表示RX链路）
	return ad9361_fastlock_save(phy, 0, profile, values);
}

/**
 * Power down the RX Local Oscillator.
 * @param phy The AD9361 state structure.
 * @param option The option (ON, OFF).
 * 				 Accepted values:
 * 				  ON (0)
 * 				  OFF (1)
 * @return 0 in case of success, negative error code otherwise.
 */
//在不需要接收时关闭 RX 本振电源（如 TDD 的发射阶段），降低功耗
int32_t ad9361_rx_lo_powerdown(struct ad9361_rf_phy *phy, uint8_t option)
{
	// 调用底层函数：option为OFF时关闭本振（LO_OFF），ON时开启（LO_ON）
	return ad9361_synth_lo_powerdown(phy, option ? LO_OFF : LO_ON, LO_DONTCARE);
}

/**
 * Get the RX Local Oscillator power status.
 * @param phy The AD9361 state structure.
 * @param option Store the option (ON, OFF).
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_rx_lo_power(struct ad9361_rf_phy *phy, uint8_t *option)
{
	// 从缓存的合成器电源状态中判断：未断电则为ON，否则为OFF
	*option = !(phy->cached_synth_pd[1] & RX_LO_POWER_DOWN) ? OFF : ON;

	return 0;
}

/**
 * Set the transmit attenuation for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel number (TX1, TX2).
 * 			 Accepted values in 2x2 mode:
 * 			  TX1 (0)
 * 			  TX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  TX1 (0)
 * @param attenuation_mdb The attenuation (mdB).
 * 						  Example:
 * 						   10000 (10 dB)
 * @return 0 in case of success, negative error code otherwise.
 */
//控制发射通道的衰减量（如设置 10dB 衰减），调节输出功率以符合通信标准或避免干扰
int32_t ad9361_set_tx_attenuation (struct ad9361_rf_phy *phy,
				   uint8_t ch, uint32_t attenuation_mdb)
{
	int32_t ret;
	int32_t channel;

	channel = ad9361_1rx1tx_channel_map(phy, true, ch);
	ret = ad9361_set_tx_atten(phy, attenuation_mdb,
				  channel == 0, channel == 1,
				  !phy->pdata->update_tx_gain_via_alert);

	return ret;
}

/**
 * Get current transmit attenuation for the selected channel.
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel number (TX1, TX2).
 * 			 Accepted values in 2x2 mode:
 * 			  TX1 (0)
 * 			  TX2 (1)
 * 			 Accepted values in 1x1 mode:
 * 			  TX1 (0)
 * @param attenuation_mdb A variable to store the attenuation value (mdB).
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_tx_attenuation (struct ad9361_rf_phy *phy,
				   uint8_t ch, uint32_t *attenuation_db)
{
	int32_t ret;

	// 调用底层函数获取指定通道的衰减值（毫分贝）
	ret = ad9361_get_tx_atten(phy,
				  ad9361_1rx1tx_channel_map(phy, true,
						  ch + 1));

	if(ret < 0)
		return ret;
	*attenuation_db = ret;

	return 0;
}

/**
 * Set the TX RF bandwidth.
 * @param phy The AD9361 current state structure.
 * @param bandwidth_hz The desired bandwidth (Hz).
 * 					   Example:
 * 					    18000000 (18 MHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
// 设置TX射频带宽（同RX，限制发射信号的频率范围）
int32_t ad9361_set_tx_rf_bandwidth (struct ad9361_rf_phy *phy,
				    uint32_t  bandwidth_hz)
{
	int32_t ret = 0;

	bandwidth_hz = ad9361_validate_rf_bw(phy, bandwidth_hz);

	if (phy->current_tx_bw_Hz != bandwidth_hz)
		ret = ad9361_update_rf_bandwidth(phy,
						 phy->current_rx_bw_Hz, bandwidth_hz);
	else
		ret = 0;

	return ret;
}

/**
 * Get the TX RF bandwidth.
 * @param phy The AD9361 current state structure.
 * @param bandwidth_hz A variable to store the bandwidth value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_tx_rf_bandwidth (struct ad9361_rf_phy *phy,
				    uint32_t *bandwidth_hz)
{
	// 直接从状态结构体读取（设置时已更新）
	*bandwidth_hz = phy->current_tx_bw_Hz;

	return 0;
}

/**
 * Set the TX sampling frequency.
 * @param phy The AD9361 current state structure.
 * @param sampling_freq_hz The desired frequency (Hz).
 * 						   Example:
 * 						    30720000 (30.72 MHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
// 设置TX采样频率（同RX，配置DAC采样速度）
int32_t ad9361_set_tx_sampling_freq (struct ad9361_rf_phy *phy,
				     uint32_t sampling_freq_hz)
{
	int32_t ret;
	uint32_t rx[6], tx[6];

	ret = ad9361_calculate_rf_clock_chain(phy, sampling_freq_hz,
					      phy->rate_governor, rx, tx);
	if (ret < 0)
		return ret;

	ad9361_set_trx_clock_chain(phy, rx, tx);

	ret = ad9361_update_rf_bandwidth(phy, phy->current_rx_bw_Hz,
					 phy->current_tx_bw_Hz);

	return ret;
}

/**
 * Get current TX sampling frequency.
 * @param phy The AD9361 current state structure.
 * @param sampling_freq_hz A variable to store the frequency value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_tx_sampling_freq (struct ad9361_rf_phy *phy,
				     uint32_t *sampling_freq_hz)
{
	// 从时钟结构体中获取TX采样时钟频率
	*sampling_freq_hz = (uint32_t)clk_get_rate(phy,
			    phy->ref_clk_scale[TX_SAMPL_CLK]);

	return 0;
}

/**
 * Set the TX LO frequency.
 * @param phy The AD9361 current state structure.
 * @param lo_freq_hz The desired frequency (Hz).
 * 					 Example:
 * 					  2400000000 (2.4 GHz)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
// 设置TX本振频率（同RX，决定发射信号的频段）
int32_t ad9361_set_tx_lo_freq (struct ad9361_rf_phy *phy,
			       uint64_t lo_freq_hz)
{
	int32_t ret;

	ret = clk_set_rate(phy, phy->ref_clk_scale[TX_RFPLL],
			   ad9361_to_clk(lo_freq_hz));

	return ret;
}

/**
 * Get current TX LO frequency.
 * @param phy The AD9361 current state structure.
 * @param lo_freq_hz A variable to store the frequency value (Hz).
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_get_tx_lo_freq (struct ad9361_rf_phy *phy,
			       uint64_t *lo_freq_hz)
{
	*lo_freq_hz = ad9361_from_clk(clk_get_rate(phy,
				      phy->ref_clk_scale[TX_RFPLL]));

	return 0;
}

/**
 * Switch between internal and external LO.
 * @param phy The AD9361 state structure.
 * @param int_ext The selected lo (INT_LO, EXT_LO).
 * 			  Accepted values:
 * 			   INT_LO
 * 			   EXT_LO
 * @return 0 in case of success, negative error code otherwise.
 */
int32_t ad9361_set_tx_lo_int_ext(struct ad9361_rf_phy *phy, uint8_t int_ext)
{
	if ((phy->dev_sel == ID_AD9363A) && (int_ext == EXT_LO)) {
		printf("%s : EXT_LO is not supported by AD9363!\n", __func__);
		return -1;
	}

	// 更新状态结构体中的本振来源标志
	phy->pdata->use_ext_tx_lo = int_ext;

	// 调用底层函数切换TX RFPLL的时钟源（内部/外部）
	return ad9361_clk_mux_set_parent(phy->ref_clk_scale[TX_RFPLL], int_ext);
}

/**
 * Set the TX FIR filter configuration.
 * @param phy The AD9361 current state structure.
 * @param fir_cfg FIR filter configuration.
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//配置发射链路的 FIR 滤波器（如自定义滤波系数、增益、插值率），用于信号成形和速率调整
int32_t ad9361_set_tx_fir_config (struct ad9361_rf_phy *phy,
				  AD9361_TXFIRConfig fir_cfg)
{
	int32_t ret;

	// 保存FIR插值率（将输入数据按比例插值，提高采样率）
	phy->tx_fir_int = fir_cfg.tx_int;
	// 调用底层函数加载TX FIR系数：
	// 1. 指定目标为TX通道
	// 2. 写入增益、系数长度和具体系数值
	ret = ad9361_load_fir_filter_coef(phy, (enum fir_dest)fir_cfg.tx,
					  fir_cfg.tx_gain, fir_cfg.tx_coef_size, fir_cfg.tx_coef);

	return ret;
}

/**
 * Get the TX FIR filter configuration.
 * @param phy The AD9361 current state structure.
 * @param tx_ch The selected TX channel (TX1, TX2).
 * 				Accepted values:
 * 				 TX1 (0)
 * 				 TX2 (1)
 * @param fir_cfg FIR filter configuration output file.
 * @return 0 in case of success, negative error code otherwise.
 */
//读取当前发射 FIR 滤波器的配置（系数、增益、插值率等），用于验证配置或备份参数
int32_t ad9361_get_tx_fir_config(struct ad9361_rf_phy *phy, uint8_t tx_ch,
				 AD9361_TXFIRConfig *fir_cfg)
{
	int32_t ret;
	uint32_t fir_conf;
	uint8_t index;

	tx_ch += 1;

	// 读取TX FIR配置寄存器（REG_TX_FILTER_CONF）
	ret = ad9361_spi_read(phy->spi, REG_TX_FILTER_CONF);
	if(ret < 0)
		return ret;
	fir_conf = ret;
	// 计算FIR系数长度（公式：(位值+1)*16）
	fir_cfg->tx_coef_size = (((fir_conf & FIR_NUM_TAPS(7)) >> 5) + 1) * 16;
	// 解析FIR增益（单位：dB，公式：-6 * 寄存器位值）
	fir_cfg->tx_gain = -6 * (fir_conf & TX_FIR_GAIN_6DB);
	fir_cfg->tx = tx_ch;

	// 配置FIR寄存器，启用系数读取模式
	fir_conf &= ~FIR_SELECT(3); // 清除原有选择位
	fir_conf |= FIR_SELECT(tx_ch) | FIR_START_CLK;// 选择目标通道并启动时钟
	ad9361_spi_write(phy->spi, REG_TX_FILTER_CONF, fir_conf);

	// 读取128个FIR系数（拼接为16位值）
	for(index = 0; index < 128; index++) {
		ad9361_spi_write(phy->spi, REG_TX_FILTER_COEF_ADDR, index);
		ret = ad9361_spi_read(phy->spi, REG_TX_FILTER_COEF_READ_DATA_1);
		if(ret < 0)
			return ret;
		fir_cfg->tx_coef[index] = ret;
		ret = ad9361_spi_read(phy->spi, REG_TX_FILTER_COEF_READ_DATA_2);
		if(ret < 0)
			return ret;
		fir_cfg->tx_coef[index] |= (ret << 8);
	}

	// 关闭系数读取模式
	fir_conf &= ~FIR_START_CLK;
	ad9361_spi_write(phy->spi, REG_TX_FILTER_CONF, fir_conf);

	// 从状态结构体中读取FIR插值率
	fir_cfg->tx_int = phy->tx_fir_int;

	return 0;
}

/**
 * Enable/disable the TX FIR filter.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//控制发射 FIR 滤波器的启用 / 禁用（禁用时信号直接绕过 FIR，用于调试）
int32_t ad9361_set_tx_fir_en_dis (struct ad9361_rf_phy *phy,
				  uint8_t en_dis)
{
	int32_t ret = 0;

	if(phy->bypass_tx_fir == !en_dis)
		return ret;

	// 更新状态结构体：bypass_tx_fir为true表示禁用
	phy->bypass_tx_fir = !en_dis;
	// 验证并启用/禁用FIR（检查与其他配置的冲突）
	ret = ad9361_validate_enable_fir(phy);
	if (ret < 0) {
		phy->bypass_tx_fir = true;
	}

	return ret;
}

/**
 * Get the status of the TX FIR filter.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询发射 FIR 滤波器是否启用。
int32_t ad9361_get_tx_fir_en_dis (struct ad9361_rf_phy *phy,
				  uint8_t *en_dis)
{
	// bypass_tx_fir为false表示启用（!false=1），true表示禁用（!true=0）
	*en_dis = !phy->bypass_tx_fir;

	return 0;
}

/**
 * Get the TX RSSI for the selected channel (TX_MON should be enabled).
 * @param phy The AD9361 current state structure.
 * @param ch The desired channel (TX1, TX2).
 * 			 Accepted values:
 * 			  TX1 (0)
 * 			  TX2 (1)
 * @param rssi_db_x_1000 A variable to store the RSSI.
 * @return 0 in case of success, negative error code otherwise.
 */
//获取发射通道的信号强度（需先启用 TX 监控功能），用于评估发射功率是否正常
int32_t ad9361_get_tx_rssi (struct ad9361_rf_phy *phy,
			    uint8_t ch,
			    uint32_t *rssi_db_x_1000)
{
	uint8_t reg_val_buf[3];
	uint32_t val;
	int32_t ret;

	// 连续读取3个RSSI相关寄存器（REG_TX_RSSI_LSB开始）
	ret = ad9361_spi_readm(phy->spi, REG_TX_RSSI_LSB,
			       reg_val_buf, ARRAY_SIZE(reg_val_buf));
	if (ret < 0) {
		return ret;
	}

	// 根据通道号解析RSSI值（硬件寄存器位定义不同）
	switch (ch) {
	case 0:
		val = (reg_val_buf[2] << 1) | (reg_val_buf[0] & TX_RSSI_1);
		break;
	case 1:
		val = (reg_val_buf[1] << 1) | ((reg_val_buf[0] & TX_RSSI_2) >> 1);
		break;
	default:
		return -EINVAL;
	}

	// 转换为毫分贝（精度处理）
	val *= RSSI_RESOLUTION;

	*rssi_db_x_1000 = ((val / RSSI_MULTIPLIER) * 1000) +
			  (val % RSSI_MULTIPLIER);

	return 0;
}

/**
 * Set the TX RF output port.
 * @param phy The AD9361 current state structure.
 * @param mode The RF port.
 * 			   Accepted values:
 *				TXA	(0)
 *				TXB	(1)
 * @return 0 in case of success, negative error code otherwise.
 */
//指定发射射频信号的输出引脚（根据硬件布线选择 TXA 或 TXB）
int32_t ad9361_set_tx_rf_port_output (struct ad9361_rf_phy *phy,
				      uint32_t mode)
{
	int32_t ret;

	// 保存端口模式到状态结构体
	phy->pdata->rf_tx_output_sel = mode;

	// 调用底层函数配置射频端口（同时配置RX端口，保持一致性）
	ret = ad9361_rf_port_setup(phy, true,
				   phy->pdata->rf_rx_input_sel,
				   phy->pdata->rf_tx_output_sel);

	return ret;
}

/**
 * Get the selected TX RF output port.
 * @param phy The AD9361 current state structure.
 * @param mode The RF port.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询当前发射射频信号使用的输出端口
int32_t ad9361_get_tx_rf_port_output (struct ad9361_rf_phy *phy,
				      uint32_t *mode)
{
	*mode = phy->pdata->rf_tx_output_sel;

	return 0;
}

/**
 * Enable/disable the auto calibration.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 */
//控制发射链路的自动校准功能（如本振频率校准、滤波器校准），启用后芯片会定期自动优化参数
int32_t ad9361_set_tx_auto_cal_en_dis (struct ad9361_rf_phy *phy,
				       uint8_t en_dis)
{
	// 更新自动校准使能标志（0=禁用，1=启用）
	if (en_dis == 0)
		phy->auto_cal_en = 0;
	else
		phy->auto_cal_en = 1;

	return 0;
}

/**
 * Get the status of the auto calibration flag.
 * @param phy The AD9361 current state structure.
 * @param en_dis The enable/disable status buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询发射链路自动校准功能是否启用
int32_t ad9361_get_tx_auto_cal_en_dis (struct ad9361_rf_phy *phy,
				       uint8_t *en_dis)
{
	*en_dis = phy->auto_cal_en;

	return 0;
}

/**
 * Store TX fastlock profile.// 存储TX快速锁定配置
 * To create a profile tune the synthesizer (ad9361_set_tx_lo_freq()) and then
 * call this function specifying the target profile number.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).配置文件编号（0-7）
 * 				  Accepted values:
 * 				   0 - 7
 * @return 0 in case of success, negative error code otherwise.
 */
//在 TDD 模式下快速切换发射本振频率（减少切换时间，提升通信效率）
int32_t ad9361_tx_fastlock_store(struct ad9361_rf_phy *phy, uint32_t profile)
{
	return ad9361_fastlock_store(phy, 1, profile);
}

/**
 * Recall specified TX fastlock profile.
 * When in fastlock pin select mode (init_param->tx_fastlock_pincontrol_enable),
 * the function needs to be called before then the pin-control can be used.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @return 0 in case of success, negative error code otherwise.
 */
/**
 * Recall TX fastlock profile.  // 调用TX预存配置
 * @param profile 配置文件编号
 * @return 0 in case of success
 */
int32_t ad9361_tx_fastlock_recall(struct ad9361_rf_phy *phy, uint32_t profile)
{
	return ad9361_fastlock_recall(phy, 1, profile);
}

/**
 * Load TX fastlock profile. A previously saved profile can be loaded in any
 * of the 8 available slots.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @param values Fastlock profile program data.
 * 				 Example:
 * 				  val0,val1,val2,聟,val15
 * @return 0 in case of success, negative error code otherwise.
 */
/**
 * Load TX fastlock profile.  // 加载外部TX配置到芯片
 * @param profile 配置文件编号，values 配置数据
 * @return 0 in case of success
 */
int32_t ad9361_tx_fastlock_load(struct ad9361_rf_phy *phy, uint32_t profile,
				uint8_t *values)
{
	return ad9361_fastlock_load(phy, 1, profile, values);
}

/**
 * Save TX fastlock profile. In order to use more than 8 Profiles, an existing
 * profile can be read back and stored by the user application.
 * @param phy The AD9361 state structure.
 * @param profile The profile number (0 - 7).
 * 				  Accepted values:
 * 				   0 - 7
 * @param values Fastlock profile program data.
 * @return 0 in case of success, negative error code otherwise.
 */
/**
 * Save TX fastlock profile.  // 保存芯片中TX配置到外部
 * @param profile 配置文件编号，values 输出配置数据
 * @return 0 in case of success
 */
int32_t ad9361_tx_fastlock_save(struct ad9361_rf_phy *phy, uint32_t profile,
				uint8_t *values)
{
	return ad9361_fastlock_save(phy, 1, profile, values);
}

/**
 * Power down the TX Local Oscillator.
 * @param phy The AD9361 state structure.
 * @param option The option (ON, OFF).
 * 				 Accepted values:
 * 				  ON (0)
 * 				  OFF (1)
 * @return 0 in case of success, negative error code otherwise.
 */
//在不需要发射时关闭 TX 本振电源（如 TDD 的接收阶段），降低功耗。
int32_t ad9361_tx_lo_powerdown(struct ad9361_rf_phy *phy, uint8_t option)
{
	// 调用底层函数：option为OFF时关闭TX本振，ON时开启
	return ad9361_synth_lo_powerdown(phy, LO_DONTCARE, option ? LO_OFF : LO_ON);
}

/**
 * Get the TX Local Oscillator power status.
 * @param phy The AD9361 state structure.
 * @param option Store the option (ON, OFF).
 * @return 0 in case of success, negative error code otherwise.
 */
//查询 TX 本振当前是否开启（如确认 TDD 模式下本振切换是否正确）
int32_t ad9361_get_tx_lo_power(struct ad9361_rf_phy *phy, uint8_t *option)
{
	*option = !(phy->cached_synth_pd[0] & TX_LO_POWER_DOWN) ? OFF : ON;

	return 0;
}

/**
 * Set the RX and TX path rates.
 * @param phy The AD9361 state structure.
 * @param rx_path_clks RX path rates buffer.
 * @param tx_path_clks TX path rates buffer.
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//精细化配置收发链路各节点的时钟频率（如 ADC/DAC 时钟、FIR 时钟），用于高级时钟同步场景
int32_t ad9361_set_trx_path_clks(struct ad9361_rf_phy *phy,
				 uint32_t *rx_path_clks,
				 uint32_t *tx_path_clks)
{
	int32_t ret;

	// 调用底层函数配置收发时钟链（各节点频率）
	ret = ad9361_set_trx_clock_chain(phy, rx_path_clks, tx_path_clks);
	if (ret < 0)
		return ret;

	// 时钟变化后需重新更新带宽配置（保持原带宽值）
	ret = ad9361_update_rf_bandwidth(phy, phy->current_rx_bw_Hz,
					 phy->current_tx_bw_Hz);

	return ret;
}

/**
 * Get the RX and TX path rates.
 * @param phy The AD9361 state structure.
 * @param rx_path_clks RX path rates buffer.
 * @param tx_path_clks TX path rates buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询收发链路各节点的当前时钟频率，用于调试时钟同步问题。
int32_t ad9361_get_trx_path_clks(struct ad9361_rf_phy *phy,
				 uint32_t *rx_path_clks,
				 uint32_t *tx_path_clks)
{
	// 调用底层函数读取收发时钟链各节点频率
	return ad9361_get_trx_clock_chain(phy, rx_path_clks, tx_path_clks);
}

/**
 * Set the number of channels mode.
 * @param phy The AD9361 state structure.
 * Note: This function also resets the device, some additional
 *       configurations might be necessary，会重置芯片，需重新配置其他参数
 * @param ch_mode Number of channels mode (MODE_1x1, MODE_2x2).
 * 				  Accepted values:
 * 				   MODE_1x1 (1)
 * 				   MODE_2x2 (2)
 * @return 0 in case of success, negative error code otherwise.
 */
// 配置通道模式（1收1发或2收2发）
int32_t ad9361_set_no_ch_mode(struct ad9361_rf_phy *phy, uint8_t no_ch_mode)
{
	switch (no_ch_mode) {
	case 1:// 1x1模式（1收1发
		phy->pdata->rx2tx2 = 0;
		break;
	case 2: // 2x2模式（2收2发）
		phy->pdata->rx2tx2 = 1;
		break;
	default:
		return -EINVAL;
	}
	// 以下为芯片重置和重新初始化流程：
#ifndef AXI_ADC_NOT_PRESENT
	// 更新ADC芯片信息（适配1x1/2x2模式）
	phy->adc_conv->chip_info = &axiadc_chip_info_tbl[phy->pdata->rx2tx2 ?
						      ID_AD9361 : ID_AD9364];
#endif
	ad9361_reset(phy);// 重置芯片
	// 软件复位寄存器
	ad9361_spi_write(phy->spi, REG_SPI_CONF, SOFT_RESET | _SOFT_RESET);
	ad9361_spi_write(phy->spi, REG_SPI_CONF, 0x0);

	// 清除状态结构体
	ad9361_clear_state(phy);

	// 重新计算并更新所有时钟链节点频率（适配新通道模式）
	phy->clks[TX_REFCLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[TX_REFCLK], phy->clk_refin->rate);
	phy->clks[TX_REFCLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[TX_REFCLK], phy->clk_refin->rate);
	phy->clks[RX_REFCLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[RX_REFCLK], phy->clk_refin->rate);
	phy->clks[BB_REFCLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[BB_REFCLK], phy->clk_refin->rate);
	phy->clks[BBPLL_CLK]->rate = ad9361_bbpll_recalc_rate(
					     phy->ref_clk_scale[BBPLL_CLK], phy->clks[BB_REFCLK]->rate);
	phy->clks[ADC_CLK]->rate = ad9361_clk_factor_recalc_rate(
					   phy->ref_clk_scale[ADC_CLK], phy->clks[BBPLL_CLK]->rate);
	phy->clks[R2_CLK]->rate = ad9361_clk_factor_recalc_rate(
					  phy->ref_clk_scale[R2_CLK], phy->clks[ADC_CLK]->rate);
	phy->clks[R1_CLK]->rate = ad9361_clk_factor_recalc_rate(
					  phy->ref_clk_scale[R1_CLK], phy->clks[R2_CLK]->rate);
	phy->clks[CLKRF_CLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[CLKRF_CLK], phy->clks[R1_CLK]->rate);
	phy->clks[RX_SAMPL_CLK]->rate = ad9361_clk_factor_recalc_rate(
						phy->ref_clk_scale[RX_SAMPL_CLK], phy->clks[CLKRF_CLK]->rate);
	phy->clks[DAC_CLK]->rate = ad9361_clk_factor_recalc_rate(
					   phy->ref_clk_scale[DAC_CLK], phy->clks[ADC_CLK]->rate);
	phy->clks[T2_CLK]->rate = ad9361_clk_factor_recalc_rate(
					  phy->ref_clk_scale[T2_CLK], phy->clks[DAC_CLK]->rate);
	phy->clks[T1_CLK]->rate = ad9361_clk_factor_recalc_rate(
					  phy->ref_clk_scale[T1_CLK], phy->clks[T2_CLK]->rate);
	phy->clks[CLKTF_CLK]->rate = ad9361_clk_factor_recalc_rate(
					     phy->ref_clk_scale[CLKTF_CLK], phy->clks[T1_CLK]->rate);
	phy->clks[TX_SAMPL_CLK]->rate = ad9361_clk_factor_recalc_rate(
						phy->ref_clk_scale[TX_SAMPL_CLK], phy->clks[CLKTF_CLK]->rate);
	phy->clks[RX_RFPLL_INT]->rate = ad9361_rfpll_int_recalc_rate(
						phy->ref_clk_scale[RX_RFPLL_INT], phy->clks[RX_REFCLK]->rate);
	phy->clks[TX_RFPLL_INT]->rate = ad9361_rfpll_int_recalc_rate(
						phy->ref_clk_scale[TX_RFPLL_INT], phy->clks[TX_REFCLK]->rate);
	phy->clks[RX_RFPLL_DUMMY]->rate = ad9361_rfpll_dummy_recalc_rate(
			phy->ref_clk_scale[RX_RFPLL_DUMMY]);
	phy->clks[TX_RFPLL_DUMMY]->rate = ad9361_rfpll_dummy_recalc_rate(
			phy->ref_clk_scale[TX_RFPLL_DUMMY]);
	phy->clks[RX_RFPLL]->rate = ad9361_rfpll_recalc_rate(
					    phy->ref_clk_scale[RX_RFPLL]);
	phy->clks[TX_RFPLL]->rate = ad9361_rfpll_recalc_rate(
					    phy->ref_clk_scale[TX_RFPLL]);

#ifndef AXI_ADC_NOT_PRESENT
	axiadc_init(phy);// 初始化ADC
#endif
	ad9361_setup(phy);// 重新配置AD9361
#ifndef AXI_ADC_NOT_PRESENT
	/* platform specific wrapper to call ad9361_post_setup() */
	axiadc_post_setup(phy);// 平台相关后处理
#endif

	return 0;
}

/**
 * Do multi chip synchronization.// 实现多AD9361芯片同步
 * @param phy_master The AD9361 Master state structure.// 主芯片状态句柄
 * @param phy_slave The AD9361 Slave state structure.// 从芯片状态句柄
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//实现多片 AD9361 芯片的同步（如 MIMO 系统中多射频通道的时钟和数据对齐），通过同步延迟寄存器和分步校准确保各芯片时序一致。
int32_t ad9361_do_mcs(struct ad9361_rf_phy *phy_master,
		      struct ad9361_rf_phy *phy_slave)
{
	uint32_t ensm_mode;
	int32_t step;
	int32_t reg;

	// AD9363A不支持多芯片同步，若主/从芯片为AD9363A则返回错误
	if ((phy_master->dev_sel == ID_AD9363A) ||
	    (phy_slave->dev_sel == ID_AD9363A)) {
		printf("%s : MCS is not supported by AD9363!\n", __func__);
		return -1;
	}

	// 同步RX/TX的时钟和数据延迟寄存器（主芯片配置同步到从芯片）
	reg = ad9361_spi_read(phy_master->spi, REG_RX_CLOCK_DATA_DELAY);
	ad9361_spi_write(phy_slave->spi, REG_RX_CLOCK_DATA_DELAY, reg);
	reg = ad9361_spi_read(phy_master->spi, REG_TX_CLOCK_DATA_DELAY);
	ad9361_spi_write(phy_slave->spi, REG_TX_CLOCK_DATA_DELAY, reg);

	// 保存主芯片当前的ENSM模式（后续恢复用）
	ad9361_get_en_state_machine_mode(phy_master, &ensm_mode);

	// 将主从芯片的ENSM模式切换到ALERT模式（同步准备状态）
	ad9361_set_en_state_machine_mode(phy_master, ENSM_MODE_ALERT);
	ad9361_set_en_state_machine_mode(phy_slave, ENSM_MODE_ALERT);

	// 执行多芯片同步步骤（0-5共6步）
	for (step = 0; step <= 5; step++) {
		ad9361_mcs(phy_slave, step);
		ad9361_mcs(phy_master, step);
		mdelay(100);
	}

	// 恢复主从芯片的ENSM模式到同步前的状态
	ad9361_set_en_state_machine_mode(phy_master, ensm_mode);
	ad9361_set_en_state_machine_mode(phy_slave, ensm_mode);

	return 0;
}

/**
 * Enable/disable the TRX FIR filters.
 * @param phy The AD9361 current state structure.
 * @param en_dis The option (ENABLE, DISABLE).
 * 				 Accepted values:
 * 				  ENABLE (1)
 * 				  DISABLE (0)
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//一次性启用或禁用接收和发射链路的 FIR 滤波器（适用于需要同时开关滤波功能的场景，如系统复位或模式切换）
int32_t ad9361_set_trx_fir_en_dis (struct ad9361_rf_phy *phy,
				   uint8_t en_dis)
{
	int32_t ret = 0;

	// 若当前RX和TX FIR状态已与目标一致，直接返回
	if ((phy->bypass_rx_fir == phy->bypass_tx_fir) &&
	    (phy->bypass_rx_fir == !en_dis))
		return ret;

	phy->bypass_rx_fir = !en_dis;
	phy->bypass_tx_fir = !en_dis;
	ret = ad9361_validate_enable_fir(phy);
	if (ret < 0) {
		phy->bypass_rx_fir = true;
		phy->bypass_tx_fir = true;
	}

	return ret;
}

/**
 * Set the OSR rate governor.
 * @param phy The AD9361 current state structure.
 * @param rate_gov OSR rate governor (highest, nominal).
 * 				   Accepted values:
 * 					HIGHEST_OSR (0 - highest OSR)//(0 - 最高过采样率，优先抗混叠)
 * 					NOMINAL_OSR (1 - nominal)//(1 - 标称过采样率，平衡性能与功耗)
 * @return 0 in case of success, negative error code otherwise.
 */
//选择过采样率的优化策略 —— 最高过采样率（提升抗混叠性能，适合高精度场景）或标称过采样率（降低功耗，适合低功耗场景）
int32_t ad9361_set_trx_rate_gov (struct ad9361_rf_phy *phy, uint32_t rate_gov)
{
	// 更新过采样率策略标志（0=最高，1=标称）
	if (rate_gov == 0)
		phy->rate_governor = 0;
	else
		phy->rate_governor = 1;

	return 0;
}

/**
 * Get the OSR rate governor.
 * @param phy The AD9361 current state structure.
 * @param rate_gov Option buffer.
 * @return 0 in case of success, negative error code otherwise.
 */
//查询当前使用的过采样率策略，确认系统在性能与功耗之间的平衡方式
int32_t ad9361_get_trx_rate_gov (struct ad9361_rf_phy *phy, uint32_t *rate_gov)
{
	*rate_gov = phy->rate_governor;

	return 0;
}

/**
 * Perform the selected calibration.// 执行指定类型的校准
 * @param phy The AD9361 state structure. // AD9361状态句柄
 * @param cal The selected calibration (TX_QUAD_CAL, RFDC_CAL).// 支持TX正交校准和RF直流校准
 * 			  Accepted values:
 * 			   TX_QUAD_CAL(TX正交失衡校准)
 * 			   RFDC_CAL (RF直流偏移校准)
 * @param arg For TX_QUAD_CAL - the optional RX phase value overwrite (set to zero).可选RX相位覆盖值（通常设为0）
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//手动触发特定校准流程 —— 如 TX 正交失衡校准（减少 I/Q 通道幅度 / 相位误差）或 RF 直流偏移校准（消除射频链路直流分量），提升信号质量
int32_t ad9361_do_calib(struct ad9361_rf_phy *phy, uint32_t cal, int32_t arg)
{
	// 调用底层校准函数执行指定校准
	return ad9361_do_calib_run(phy, cal, arg);
}

/**
 * Load and enable TRX FIR filters configurations.
 * @param phy The AD9361 current state structure.
 * @param rx_fir_cfg RX FIR filter configuration.
 * @param tx_fir_cfg TX FIR filter configuration.
 * @return 0 in case of success, negative error code otherwise.
 *
 * Note: This function will/may affect the data path.
 */
//一站式完成收发 FIR 滤波器的配置加载和启用
//（整合set_tx_fir_config、set_rx_fir_config和set_trx_fir_en_dis的功能），简化滤波配置流程
int32_t ad9361_trx_load_enable_fir(struct ad9361_rf_phy *phy,
				   AD9361_RXFIRConfig rx_fir_cfg,
				   AD9361_TXFIRConfig tx_fir_cfg)
{
	int32_t rtx = -1, rrx = -1;// 标志：TX/RX配置是否有效

	// 初始化滤波器相关状态
	phy->filt_rx_bw_Hz = 0;// RX滤波带宽
	phy->filt_tx_bw_Hz = 0;// TX滤波带宽
	phy->filt_valid = false; // 配置有效性标志

	// 若TX FIR配置包含采样时钟信息，保存到状态结构体
	if (tx_fir_cfg.tx_path_clks[TX_SAMPL_FREQ]) {
		memcpy(phy->filt_tx_path_clks, tx_fir_cfg.tx_path_clks,
		       sizeof(phy->filt_tx_path_clks));
		rtx = 0;
	}

	// 若RX FIR配置包含采样时钟信息，保存到状态结构体
	if (rx_fir_cfg.rx_path_clks[RX_SAMPL_FREQ]) {
		memcpy(phy->filt_rx_path_clks, rx_fir_cfg.rx_path_clks,
		       sizeof(phy->filt_rx_path_clks));
		rrx = 0;
	}

	// 保存TX/RX滤波带宽（若配置中提供）
	if (tx_fir_cfg.tx_bandwidth) {
		phy->filt_tx_bw_Hz = tx_fir_cfg.tx_bandwidth;
	}

	if (rx_fir_cfg.rx_bandwidth) {
		phy->filt_rx_bw_Hz = rx_fir_cfg.rx_bandwidth;
	}

	// 应用TX和RX FIR配置（加载系数、增益等）
	ad9361_set_tx_fir_config(phy, tx_fir_cfg);
	ad9361_set_rx_fir_config(phy, rx_fir_cfg);

	// 若TX和RX配置均有效，标记整体配置有效
	if (!(rrx | rtx))
		phy->filt_valid = true;

	ad9361_set_trx_fir_en_dis(phy, 1);

	return 0;
}

/**
 * Do DCXO coarse tuning.// 对DCXO（数字控制晶体振荡器）进行粗调
 * @param phy The AD9361 current state structure.
 * @param coarse The DCXO coarse tuning value.// 粗调值（范围由硬件定义）
 * @return 0 in case of success, negative error code otherwise.
 */
//调整 DCXO 的粗调值（大范围调整振荡器频率），用于快速将时钟频率校准到目标附近
int32_t ad9361_do_dcxo_tune_coarse(struct ad9361_rf_phy *phy,
				   uint32_t coarse)
{
	// 保存粗调值到状态结构体
	phy->pdata->dcxo_coarse = coarse;

	// 调用底层函数应用粗调+当前细调值（DCXO需要粗调和细调结合）
	return ad9361_set_dcxo_tune(phy, phy->pdata->dcxo_coarse,
				    phy->pdata->dcxo_fine);
}

/**
 * Do DCXO fine tuning.// 对DCXO进行细调
 * @param phy The AD9361 current state structure.
 * @param fine The DCXO fine tuning value.// 细调值（小范围精确调整）
 * @return 0 in case of success, negative error code otherwise.
 */
//调整 DCXO 的细调值（小范围精确调整），在粗调基础上进一步优化时钟频率精度
int32_t ad9361_do_dcxo_tune_fine(struct ad9361_rf_phy *phy,
				 uint32_t fine)
{
	phy->pdata->dcxo_fine = fine;

	// 调用底层函数应用当前粗调+新细调值（精确校准频率）
	return ad9361_set_dcxo_tune(phy, phy->pdata->dcxo_coarse,
				    phy->pdata->dcxo_fine);
}

/**
 * Get the temperature.
 * @param phy The AD9361 current state structure.
 * @param temp The temperature (degrees C * 1000).
 * @return 0 in case of success, negative error code otherwise.
 */
//获取 AD9361 芯片的内部温度（精度 0.001℃），用于监控芯片工作状态（温度过高可能影响性能或寿命）
int32_t ad9361_get_temperature(struct ad9361_rf_phy *phy,
			       int32_t *temp)
{
	// 调用底层函数读取温度，并转换为毫摄氏度
	*temp = ad9361_get_temp(phy);

	return 0;
}
