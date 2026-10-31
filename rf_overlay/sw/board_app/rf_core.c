/* rf_main.c -- the on-board AD9361 TX bring-up program.
 *
 * Subcommands rather than one big main, because spec 6.1's steps have to be
 * stoppable: step 1's negative criterion (f_DATA_CLK must read as "no clock"
 * before the BBPLL is programmed) is only meaningful if it runs BEFORE
 * ad9361_init, and its result has to be recorded before anything else happens.
 *
 *   board_app selftest            -- spec 6.1 Step 0, before any vendor code
 *   board_app status              -- read the PL STATUS word and decode it
 *   board_app plcfg               -- spec 6.1 Step 1: PL config + negative check
 *   board_app plstate             -- read the PL config without writing it
 *   board_app init [trace.txt] [atten <mdb>] [lo <hz>] [dump] [2r2t]
 *                                 -- spec 6.1 Step 2: ad9361_init + parameters,
 *                                    with the phy-dependent knobs folded in.
 *                                    `2r2t` starts the part in 2R2T (stage two)
 *                                    instead of the default 1R1T
 *   board_app 2r2t                -- stage two, PL side of the 1R1T -> 2R2T
 *                                    switch only; see the subcommand for why
 *                                    it cannot also move the AD9361
 *   board_app phase_inc <n>       -- change the DDS phase increment (4.2)
 *   board_app audit-begin         -- spec 6.1 Step 5: open the raw read path
 *   board_app audit <addr>        --   re-read one register (raw SPI)
 *   board_app audit-end           --   close the audit pass
 *
 * atten / lo / dump are ARGUMENTS to `init`, not subcommands.  They need the
 * live phy handle, and that handle is process-local: `init` is the only place
 * ad9361_init() ever runs, so a separate invocation would find phy == NULL.
 * Folding them in (controller ruling R7) is what makes "set the value and read
 * it back in the same run" possible, which is what criterion 2.4 and the
 * 4.4/4.5 measurement points require.
 */
#include "rf_core.h"
#include "platform_rf.h"
#include "platform.h"
#include "ad9361.h"
#include "ad9361_api.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <math.h>

/* Amplitude for the generated I/Q waveforms, as a fraction of the 12-bit
 * full scale of 2047.  Deliberately not full scale: the AD9361's TX FIR is
 * enabled (register 0x002 reads 0x52, bits[1:0] = 2, interpolation by 2) and
 * a 128-tap filter overshoots on a stepped input, so a full-scale table would
 * clip inside the part -- and clipping is invisible from here while it shows
 * up as spectral regrowth that looks like a source defect. */
#define RF_SAMPLES_AMP 1500

int rf_selftest(void);

/* The sample rate this project runs at, requested from the driver by
 * `init` (ref/main.c:503-504).  Named once so the two setter calls and
 * criterion 2.5's comparison cannot drift apart. */
#define RF_SAMPLE_RATE_HZ 60000000u

/* Criterion 1.5's ceiling and the noise level it was derived from are part of
 * the public contract now -- the CLI formats the grey-zone note from them -- so
 * they are defined once, in rf_core.h, with the reasoning behind them. */

/* ---------------------------------------------------------------- PL STATUS */

/* Read a completed, newly published measurement window.  A sticky CLK_SEEN bit
 * and a zero count from an old window are not evidence that DATA_CLK is stopped:
 * both can remain true after the clock changes.  Every safety decision therefore
 * waits for WIN_SEQ to advance and WIN_DONE to assert. */
static int status_read_fresh(uint32_t *out)
{
	uint32_t first, seq0, now;
	int i;

	if (!out)
		return RF_ERR_INVALID_ARGUMENT;
	first = rf_reg_read(RF_REG_STATUS);
	if (first == 0xDEADBEEFu)
		return RF_ERR_HARDWARE_IO;
	seq0 = RF_STATUS_WIN_SEQ(first);
	for (i = 0; i < 2000; ++i) {
		now = rf_reg_read(RF_REG_STATUS);
		if (now == 0xDEADBEEFu)
			return RF_ERR_HARDWARE_IO;
		if (RF_STATUS_WIN_SEQ(now) != seq0 && RF_STATUS_WIN_DONE(now)) {
			*out = now;
			return RF_ERR_OK;
		}
		udelay(100);
	}
	/* The last word read is still the best description of the PL that exists,
	 * and the one caller that only REPORTS it (the CLI `status`) needs it to
	 * print the STALE line with the real register contents.  Callers that must
	 * not proceed on a stale window get RF_ERR_TIMEOUT and stop. */
	*out = now;
	return RF_ERR_TIMEOUT;
}

/* ------------------------------------------------------------------ driver */

struct rf_core {
	struct ad9361_rf_phy *vendor_phy;
	struct spi_device spi;
	AD9361_InitParam init_param;
	rf_core_open_args_t open_args;
	rf_state_t state;
	int opened;
	int initialized;
	bool initialization_attempted; /* Lifetime history, unaffected by shutdown/open. */
	int tx_prepared;
	int rx_prepared;
	size_t tx_count;
	size_t rx_count;
	bool rx_continuous;
	bool tx_wrap;
	uint32_t last_loopback;
	uint64_t lo_hz;
	uint32_t sample_rate_hz;
	uint32_t attenuation_mdB;
	uint32_t rx_bandwidth_hz;
	uint32_t tx_bandwidth_hz;
#ifdef PLATFORM_HOST
	struct {
		uint64_t rx_lo_hz;
		uint64_t tx_lo_hz;
		uint32_t rx_sample_rate_hz;
		uint32_t tx_sample_rate_hz;
		uint32_t rx_bandwidth_hz;
		uint32_t tx_bandwidth_hz;
		uint32_t attenuation_mdB;
		uint8_t tx_fir_enabled;
		uint8_t rx_fir_enabled;
		AD9361_TXFIRConfig tx_fir;
		AD9361_RXFIRConfig rx_fir;
		int readback_fault;
		int headroom_tx2_fault;
	} fake_vendor;
#endif
	const char *trace_path;
};

/* The PL, GPIO, SPI and vendor callback layer is physically singleton.  Keep
 * ownership at the core boundary so a second core cannot disturb the first. */
static rf_core_t *rf_owner;

/* Parameters.  Per the user's direction for this round, the REFERENCE EXAMPLE
 * is authoritative: where the spec and the example disagree, the example wins,
 * and anything we deliberately do differently is recorded here as a deviation
 * to revisit later.  The example's struct has rf_tx_bandwidth_hz = 20 MHz and
 * tx_synthesizer_frequency_hz = 2400 MHz and then overrides both after
 * ad9361_init returns (ad9361_set_tx_rf_bandwidth(phy, 30000000),
 * ad9361_set_tx_lo_freq(phy, 80000000)); we set the fields directly so there is
 * one place where each value lives.
 *
 * Deviations from the example, all recorded in docs/rf/boardtest.md:
 *
 *   LO = 80 MHz, not the example's struct value of 2.4 GHz.  Not really a
 *   deviation -- the example sets 80 MHz too, just later.  The DSOX1102A's
 *   100 MHz front end cannot see 2.4 GHz at all, so 80 MHz is what makes any
 *   measurement possible; it is above the AD9361's 70 MHz floor.
 *
 *   TX attenuation starts at 0 mdB, not 10000, for the B-layer measurement.
 *   LO leakage is the only signal B-layer has (spec 0.2 -- there is no
 *   chip-internal tone source), and 10 dB of attenuation spent up front is
 *   10 dB of visibility thrown away.  C5 restores the 10000 -> 30000 sweep
 *   afterwards, as a DIFFERENCE so the scope's front-end response cancels.
 *
 * Everything else follows the example: 1R1T, FDD, fs = 60 MSPS, RF bandwidth
 * 30 MHz, the six path clocks, the AGC settings, and the TX FIR ENABLED with the
 * example's own 128-tap coefficients.  The FIR was going to be bypassed; it is
 * not, because matching the configuration that already ran on this board is
 * worth more this round than the cleaner tone a bypass would give.  See the
 * C3 note in scope_tx_check.py for what that costs and how it is handled.
 *
 * Where fs = 60 MSPS comes from is NOT the path clocks filled in below.  Those
 * are the example's PRE-recompute chain, and ad9361_init programs the chain
 * straight from this struct (vendor/ad9361.c:5329-5330), so on their own they
 * leave the part at the 40 MSPS they encode.  60 MSPS is recomputed by the two
 * ad9361_set_*_sampling_freq calls in `init` -- see the long comment there
 * before removing them.  Filling the struct correctly is NOT sufficient.
 *
 * Filled field by field rather than as one big initialiser on purpose: a
 * positional literal for a ~90-field vendor struct is unreadable and silently
 * breaks when the vendor reorders it.  Assigning by name makes "what we
 * changed" a few diffable lines.
 */
static void rf_init_param_fill(rf_core_t *core)
{
	memset(&core->init_param, 0, sizeof(core->init_param));

	core->init_param.dev_sel          = ID_AD9361;
	core->init_param.id_no            = 0;
	core->init_param.reference_clk_rate = 40000000UL;   /* 40 MHz on this FMC card */

	/* Base configuration: 1R1T, FDD. */
	core->init_param.two_rx_two_tx_mode_enable      = 0;
	core->init_param.one_rx_one_tx_mode_use_rx_num  = 0;
	core->init_param.one_rx_one_tx_mode_use_tx_num  = 0;
	core->init_param.frequency_division_duplex_mode_enable = 1;

	/* DC offset tracking -- reference values. */
	core->init_param.dc_offset_tracking_update_event_mask = 5;
	core->init_param.dc_offset_attenuation_high_range     = 6;
	core->init_param.dc_offset_attenuation_low_range      = 5;
	core->init_param.dc_offset_count_high_range           = 0x28;
	core->init_param.dc_offset_count_low_range            = 0x32;

	core->init_param.trx_synthesizer_target_fref_overwrite_hz = MAX_SYNTH_FREF;

	/* LO: 80 MHz, not the reference struct's 2.4 GHz.  The DSOX1102A has a
	 * 100 MHz front end, so a 1 GHz carrier is invisible to the only
	 * instrument this round has; 80 MHz is above the AD9361's 70 MHz floor,
	 * visible on the scope, and a frequency the reference example already ran
	 * on this board.  Set here rather than by a later set_tx_lo_freq() call so
	 * the initialisation and the operating point are the same number. */
	core->init_param.rx_synthesizer_frequency_hz = 80000000UL;
	core->init_param.tx_synthesizer_frequency_hz = 80000000UL;
	core->init_param.tx_lo_powerdown_managed_enable = 1;

	/* Sample rate chain, reference values.  These are the example's
	 * PRE-recompute numbers: ad9361_init programs the chain straight from this
	 * struct (vendor/ad9361.c:5329-5330), so filling them correctly leaves the
	 * part at the 40 MSPS they encode, NOT at the 60 MSPS this project runs.
	 * 60 MSPS is recomputed by the ad9361_set_*_sampling_freq calls in `init`
	 * (ref/main.c:503-504).  Do not delete those calls as redundant. */
	{
		static const uint32_t rx_clks[6] =
			{ 1280000000, 320000000, 160000000, 80000000, 40000000, 40000000 };
		static const uint32_t tx_clks[6] =
			{ 1280000000, 160000000, 160000000, 80000000, 40000000, 40000000 };
		int i;
		for (i = 0; i < 6; i++) {
			core->init_param.rx_path_clock_frequencies[i] = rx_clks[i];
			core->init_param.tx_path_clock_frequencies[i] = tx_clks[i];
		}
	}

	/* RF bandwidth 30 MHz.  Note the reference STRUCT says 20 MHz and then
	 * calls ad9361_set_tx_rf_bandwidth(phy, 30000000) afterwards -- 30 MHz is
	 * what actually applies there, so 30 MHz is what we put here. */
	core->init_param.rf_rx_bandwidth_hz = 30000000UL;
	core->init_param.rf_tx_bandwidth_hz = 30000000UL;

	/* TX attenuation: 0 mdB for the B-layer look at LO leakage; the C3 sweep
	 * restores 10000 -> 30000 later. */
	core->init_param.tx_attenuation_mdB = 0;

	core->init_param.dcxo_coarse_and_fine_tune[0] = 8;
	core->init_param.dcxo_coarse_and_fine_tune[1] = 5920;

	/* RX gain control: slow-attack AGC on both channels (reference values). */
	core->init_param.gc_rx1_mode = 2;   /* RF_GAIN_SLOWATTACK_AGC */
	core->init_param.gc_rx2_mode = 2;
	core->init_param.gc_adc_large_overload_thresh = 58;
	core->init_param.gc_adc_ovr_sample_size       = 4;
	core->init_param.gc_adc_small_overload_thresh = 47;
	core->init_param.gc_dec_pow_measurement_duration = 8192;
	core->init_param.gc_dig_gain_enable           = 0;
	core->init_param.gc_lmt_overload_high_thresh  = 800;
	core->init_param.gc_lmt_overload_low_thresh   = 704;
	core->init_param.gc_low_power_thresh          = 24;
	core->init_param.gc_max_dig_gain              = 15;

	core->init_param.mgc_dec_gain_step = 2;
	core->init_param.mgc_inc_gain_step = 2;

	core->init_param.agc_adc_large_overload_exceed_counter = 10;
	core->init_param.agc_adc_large_overload_inc_steps      = 2;
	core->init_param.agc_adc_small_overload_exceed_counter = 10;
	core->init_param.agc_dig_gain_step_size                = 4;
	core->init_param.agc_dig_saturation_exceed_counter     = 3;
	core->init_param.agc_gain_update_interval_us           = 1000;
	core->init_param.agc_inner_thresh_high                 = 10;
	core->init_param.agc_inner_thresh_high_dec_steps       = 1;
	core->init_param.agc_inner_thresh_low                  = 12;
	core->init_param.agc_inner_thresh_low_inc_steps        = 1;
	core->init_param.agc_lmt_overload_large_exceed_counter = 10;
	core->init_param.agc_lmt_overload_large_inc_steps      = 2;
	core->init_param.agc_lmt_overload_small_exceed_counter = 10;
	core->init_param.agc_outer_thresh_high                 = 5;
	core->init_param.agc_outer_thresh_high_dec_steps       = 2;
	core->init_param.agc_outer_thresh_low                  = 18;
	core->init_param.agc_outer_thresh_low_inc_steps        = 2;
	core->init_param.agc_attack_delay_extra_margin_us      = 1;

	core->init_param.fagc_dec_pow_measuremnt_duration = 64;
	core->init_param.fagc_state_wait_time_ns          = 260;
	core->init_param.fagc_lp_thresh_increment_time    = 5;
	core->init_param.fagc_lp_thresh_increment_steps   = 1;
	core->init_param.fagc_lock_level_lmt_gain_increase_en = 1;
	core->init_param.fagc_lock_level_gain_increase_upper_limit = 5;
	core->init_param.fagc_lpf_final_settling_steps    = 1;
	core->init_param.fagc_lmt_final_settling_steps    = 1;
	core->init_param.fagc_final_overrange_count       = 3;

	/* ---- Rest of the reference's non-zero fields.
	 *
	 * The brief's parameter list stops at fagc_final_overrange_count, and the
	 * brief's cross-check note says "every field not in the list is zero, which
	 * is deliberate -- they match items that are also zero in the reference
	 * example", followed by: if the cross-check finds a reference field that is
	 * non-zero and missing, ADD IT and note it here.  The cross-check found
	 * exactly that: the example's literal is positional and the brief's list
	 * stops 41 fields short of its end, so everything below is non-zero in the
	 * example (main.c, `default_init_param`) and would have been silently
	 * zeroed by the memset above.
	 *
	 * Values are the example's, transcribed field by field; the order follows
	 * the vendor struct.  Nothing here is invented, and nothing here changes a
	 * value the brief does fix.  Two notes:
	 *
	 *   lvds_mode_enable = 1 is the example's value and is correct for this
	 *   board: the FMC bank is wired LVDS_25 (bank 34 VCCO measured 2.5 V, see
	 *   docs/rf/compatibility.md).
	 *
	 *   temp_sense_offset_signed is int8_t and the example writes 0xCE, which
	 *   does not fit; -50 is 0xCE truncated to int8_t, written as a negative
	 *   literal so the compiler does not flag the overflow.
	 */
	core->init_param.fagc_use_last_lock_level_for_set_gain_en  = 1;
	core->init_param.fagc_rst_gla_stronger_sig_thresh_exceeded_en = 1;
	core->init_param.fagc_optimized_gain_offset                = 5;
	core->init_param.fagc_rst_gla_stronger_sig_thresh_above_ll = 10;
	core->init_param.fagc_rst_gla_engergy_lost_sig_thresh_exceeded_en = 1;
	core->init_param.fagc_rst_gla_engergy_lost_goto_optim_gain_en = 1;
	core->init_param.fagc_rst_gla_engergy_lost_sig_thresh_below_ll = 10;
	core->init_param.fagc_energy_lost_stronger_sig_gain_lock_exit_cnt = 8;
	core->init_param.fagc_rst_gla_large_adc_overload_en        = 1;
	core->init_param.fagc_rst_gla_large_lmt_overload_en        = 1;
	core->init_param.fagc_power_measurement_duration_in_state5 = 64;

	core->init_param.rssi_delay        = 1;
	core->init_param.rssi_duration     = 1000;
	core->init_param.rssi_restart_mode = 3;
	core->init_param.rssi_wait         = 1;

	core->init_param.aux_adc_decimation        = 256;
	core->init_param.aux_adc_rate              = 40000000UL;
	core->init_param.aux_dac_manual_mode_enable = 1;

	core->init_param.temp_sense_decimation              = 256;
	core->init_param.temp_sense_measurement_interval_ms = 1000;
	core->init_param.temp_sense_offset_signed           = -50;   /* example: 0xCE */
	core->init_param.temp_sense_periodic_measurement_enable = 1;

	core->init_param.ctrl_outs_enable_mask = 0xFF;

	core->init_param.pp_tx_swap_enable          = 1;
	core->init_param.pp_rx_swap_enable          = 1;
	core->init_param.rx_frame_pulse_mode_enable = 1;
	core->init_param.lvds_mode_enable           = 1;

	core->init_param.rx_data_delay     = 4;
	core->init_param.tx_fb_clock_delay = 7;
	core->init_param.lvds_bias_mV      = 150;
	core->init_param.lvds_rx_onchip_termination_enable = 1;
	core->init_param.lvds_invert1_control = 0xFF;
	core->init_param.lvds_invert2_control = 0x0F;

	core->init_param.low_high_gain_threshold_mdB = 37000;
	core->init_param.high_gain_dB                = 24;
	core->init_param.tx_mon_delay                = 511;
	core->init_param.tx_mon_duration             = 8192;
	core->init_param.tx1_mon_front_end_gain      = 2;
	core->init_param.tx2_mon_front_end_gain      = 2;
	core->init_param.tx1_mon_lo_cm               = 48;
	core->init_param.tx2_mon_lo_cm               = 48;
}

/* FIR coefficient tables, copied verbatim from the reference example
 * (main.c:336-430).  Both are 128 taps and both are symmetric about the centre,
 * which is a useful sanity check when transcribing: the second half of each table
 * is the first half reversed.
 *
 * The example enables both FIRs (main.c:495-496).  The plan originally bypassed
 * them for a cleaner tone; the user's direction for this round is that the
 * example's configuration wins where the two disagree, so they are enabled and
 * these are the example's own coefficients.
 *
 * Consequence, handled in scope_tx_check.py: with the FIR in the path, the DDS's
 * phase increment no longer maps to an output frequency by the bare
 * df = inc/2^12 * fs relation -- the interpolating FIR has its own passband.
 * C3 therefore checks the RATIO between two increments (which the FIR's passband
 * does not disturb, since both tones sit in it) before naming a candidate. */

static const AD9361_TXFIRConfig rf_tx_fir_config = {
	3,      /* tx */
	0,      /* tx_gain */
	2,      /* tx_int */
	{
	-23, -44, -92, -124, -144, -114, -47, 46, 119, 141, 93, 4,
	-79, -98, -37, 73, 164, 170, 75, -69, -171, -154, -13, 171,
	275, 215, 9, -218, -311, -187, 97, 363, 421, 203, -177, -471,
	-464, -118, 368, 670, 550, 33, -571, -846, -551, 190, 918, 1114,
	550, -498, -1364, -1394, -402, 1107, 2154, 1863, 84, -2324, -3793, -2826,
	1125, 7188, 13311, 17146, 17146, 13311, 7188, 1125, -2826, -3793, -2324, 84,
	1863, 2154, 1107, -402, -1394, -1364, -498, 550, 1114, 918, 190, -551,
	-846, -571, 33, 550, 670, 368, -118, -464, -471, -177, 203, 421,
	363, 97, -187, -311, -218, 9, 215, 275, 171, -13, -154, -171,
	-69, 75, 170, 164, 73, -37, -98, -79, 4, 93, 141, 119,
	46, -47, -114, -144, -124, -92, -44, -23
	},      /* tx_coef[128] */
	128,    /* tx_coef_size */
	{ 0, 0, 0, 0, 0, 0 },   /* tx_path_clks[6] */
	0       /* tx_bandwidth */
};

static const AD9361_RXFIRConfig rf_rx_fir_config_search = {
	3,      /* rx */
	-6,     /* rx_gain */
	1,      /* rx_dec */
	{
	0, 0, -1, 1, -2, 3, -4, 4, -4, 4, -2, 0,
	3, -8, 13, -19, 26, -32, 38, -43, 46, -46, 44, -39,
	29, -16, 0, 20, -42, 66, -91, 115, -136, 154, -167, 172,
	-169, 156, -133, 99, -55, 0, 63, -133, 208, -284, 358, -426,
	483, -527, 550, -550, 520, -457, 354, -204, 0, 272, -633, 1125,
	-1838, 3009, -5521, 17341, 17341, -5521, 3009, -1838, 1125, -633, 272, 0,
	-204, 354, -457, 520, -550, 550, -527, 483, -426, 358, -284, 208,
	-133, 63, 0, -55, 99, -133, 156, -169, 172, -167, 154, -136,
	115, -91, 66, -42, 20, 0, -16, 29, -39, 44, -46, 46,
	-43, 38, -32, 26, -19, 13, -8, 3, 0, -2, 4, -4,
	4, -4, 3, -2, 1, -1, 0, 0
	},      /* rx_coef[128] */
	128,    /* rx_coef_size */
	{ 0, 0, 0, 0, 0, 0 },   /* rx_path_clks[6] */
	30000000 /* rx_bandwidth */
};

/* ---------------------------------------------------------------- shared core API */


static int core_fail(rf_core_t *core, int error)
{
	if (core)
		core->state = RF_STATE_FAULT;
	return error;
}

static int core_ready(rf_core_t *core)
{
	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->state != RF_STATE_READY)
		return RF_ERR_INVALID_STATE;
	return RF_ERR_OK;
}

/* The commit bit is an EDGE, not a level.
 *
 * smp_len/smp_wrap and cap_ratio/cap_len/cap_cont cross into the DATA_CLK
 * domain as buses.  The reader and the capture latch them on a synchronised
 * TRANSITION of this bit -- `commit_edge = commit_s[1] ^ commit_s[0]` in
 * rf_tx_source.v:144 and rf_sample_capture.v:77 -- which is what stops a
 * length from one write being paired with a wrap flag from the next.  A level
 * would make every commit after the first a no-op; the RTL comment at
 * axi_ctrl_v1_0_S00_AXI.v:598 says exactly that.  Baseline toggled the bit
 * (`v ^= RF_SMP_COMMIT`); the core must too.
 *
 * `reg_value` is the register's current contents, whose commit bit is the level
 * to invert.  `base` supplies every other bit; its own commit bit is ignored,
 * so callers can pass a value they have already masked or edited.
 *
 * Only the *configure* commands commit (smpcfg, capcfg).  Select/arm/start/stop
 * commands (srcsel, caparm, ...) are plain level writes: baseline left the bit
 * alone, and so must these -- an accidental toggle there would re-latch a
 * half-written configuration. */
static uint32_t commit_toggle(uint32_t reg_value, uint32_t base, uint32_t commit_bit)
{
	return (base & ~commit_bit) | (~reg_value & commit_bit);
}

static int core_spi_read(rf_core_t *core, uint32_t reg, uint8_t *value)
{
	int32_t result;

	if (!core || !value || !core->opened || core->state == RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;
	result = ad9361_spi_read(&core->spi, reg);
	if (result < 0)
		return RF_ERR_HARDWARE_IO;
	*value = (uint8_t)result;
	return RF_ERR_OK;
}

/* Compare only the fields `ad9361_get_{rx,tx}_fir_config()` actually WRITES.
 *
 * Measured on the board 2026-09-26.  Those getters populate `*_coef_size`,
 * `*_gain`, the channel, the 128 coefficients and `rx_dec`/`tx_int` -- and nothing
 * else.  `*_bandwidth` and `*_path_clks[6]` are never written, so comparing them
 * against the reference structs compares UNINITIALISED STACK against constants
 * (the board reported `rx_bandwidth = 4977032` and
 * `tx_bandwidth = 3068543795` for that reason).  An equality test that includes
 * them can never be true, which is a criterion that cannot pass rather than a
 * check on the hardware.
 *
 * The channel is also normalised: the configs request BOTH channels (3), while
 * the getters write `channel + 1` (1 or 2), so the raw values differ by design.
 *
 * The coefficients ARE compared, and they are the part that matters -- they are
 * what proves the upload took.  Measured identical (`memcmp` == 0 for both
 * directions) on the board. */
static int fir_rx_equal(const AD9361_RXFIRConfig *a,
                         const AD9361_RXFIRConfig *b)
{
	return a->rx_gain == b->rx_gain &&
	       a->rx_dec == b->rx_dec &&
	       a->rx_coef_size == b->rx_coef_size &&
	       memcmp(a->rx_coef, b->rx_coef, sizeof(a->rx_coef)) == 0;
}

static int fir_tx_equal(const AD9361_TXFIRConfig *a,
                        const AD9361_TXFIRConfig *b)
{
	return a->tx_gain == b->tx_gain &&
	       a->tx_coef_size == b->tx_coef_size &&
	       memcmp(a->tx_coef, b->tx_coef, sizeof(a->tx_coef)) == 0;
}

#ifdef PLATFORM_HOST
static void fake_vendor_init(rf_core_t *core, uint64_t lo_hz,
                             uint32_t sample_rate_hz, uint32_t attenuation_mdB)
{
	memset(&core->fake_vendor, 0, sizeof(core->fake_vendor));
	core->fake_vendor.rx_lo_hz = lo_hz;
	core->fake_vendor.tx_lo_hz = lo_hz;
	core->fake_vendor.rx_sample_rate_hz = sample_rate_hz;
	core->fake_vendor.tx_sample_rate_hz = sample_rate_hz;
	core->fake_vendor.rx_bandwidth_hz = 30000000u;
	core->fake_vendor.tx_bandwidth_hz = 30000000u;
	core->fake_vendor.attenuation_mdB = attenuation_mdB;
	core->fake_vendor.tx_fir_enabled = 1;
	core->fake_vendor.rx_fir_enabled = 1;
	/* The fake must model what the REAL getter writes, not what the setter was
	 * given.  `ad9361_get_{rx,tx}_fir_config()` populates the gain, the rate
	 * fields, the coefficient count and the 128 coefficients -- and NOT
	 * `*_bandwidth` or `*_path_clks[6]`, which it never touches (measured on the
	 * board 2026-09-26).  Copying the whole reference struct here, as this used
	 * to, silently agreed with a comparison of those unread fields, which is why
	 * the host suite stayed green while init could never succeed on hardware.
	 * The unread fields are poisoned so that any comparison reintroduced against
	 * them fails here too. */
	core->fake_vendor.tx_fir = rf_tx_fir_config;
	core->fake_vendor.tx_fir.tx_bandwidth = 0xDEADBEEFu;
	{
		int i;
		for (i = 0; i < 6; i++)
			core->fake_vendor.tx_fir.tx_path_clks[i] = 0xDEADBEEFu;
	}
	core->fake_vendor.rx_fir = rf_rx_fir_config_search;
	core->fake_vendor.rx_fir.rx_bandwidth = 0xDEADBEEFu;
	{
		int i;
		for (i = 0; i < 6; i++)
			core->fake_vendor.rx_fir.rx_path_clks[i] = 0xDEADBEEFu;
	}
	/* The real getters write `channel + 1`; with channel 0 that is 1, while the
	 * reference configs request BOTH channels (3).  Reproduced here so the fake
	 * cannot hide a comparison that includes the channel. */
	core->fake_vendor.tx_fir.tx = 1;
	core->fake_vendor.rx_fir.rx = 1;
}
#endif

static int core_fill_readback(rf_core_t *core, rf_readback_t *readback)
{
	uint8_t product = 0;
	uint32_t mode;
	if (!core || !core->opened || core->state == RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;
	if (core_spi_read(core, REG_PRODUCT_ID, &product) != RF_ERR_OK)
		return RF_ERR_HARDWARE_IO;
	mode = rf_reg_read(RF_REG_PHY_MODE);
	if (mode > 1u || mode == 0xDEADBEEFu)
		return RF_ERR_HARDWARE_IO;
	if (!readback)
		return RF_ERR_OK;
#ifdef PLATFORM_HOST
	if (core->fake_vendor.readback_fault)
		return RF_ERR_HARDWARE_IO;
	readback->rx_lo_hz = core->fake_vendor.rx_lo_hz;
	readback->tx_lo_hz = core->fake_vendor.tx_lo_hz;
	readback->lo_hz = readback->tx_lo_hz;
	readback->rx_sample_rate_hz = core->fake_vendor.rx_sample_rate_hz;
	readback->tx_sample_rate_hz = core->fake_vendor.tx_sample_rate_hz;
	readback->sample_rate_hz = readback->tx_sample_rate_hz;
	readback->attenuation_mdB = core->fake_vendor.attenuation_mdB;
	readback->rx_bandwidth_hz = core->fake_vendor.rx_bandwidth_hz;
	readback->tx_bandwidth_hz = core->fake_vendor.tx_bandwidth_hz;
	readback->tx_fir_enabled = core->fake_vendor.tx_fir_enabled;
	readback->rx_fir_enabled = core->fake_vendor.rx_fir_enabled;
	readback->tx_fir_config_verified = fir_tx_equal(&core->fake_vendor.tx_fir,
	                                                &rf_tx_fir_config);
	readback->rx_fir_config_verified = fir_rx_equal(&core->fake_vendor.rx_fir,
	                                                &rf_rx_fir_config_search);
	readback->hardware_verified = false;
#else
	{
		uint32_t rx_bw = 0, tx_bw = 0, fs_rx = 0, fs_tx = 0;
		uint64_t rx_lo = 0, tx_lo = 0;
		uint32_t atten = 0;
		uint8_t tx_en = 0, rx_en = 0;
		AD9361_TXFIRConfig tx_fir;
		AD9361_RXFIRConfig rx_fir;
		/* The vendor handle exists only after rf_core_init() ran
		 * ad9361_init().  "Opened but not initialized" is a real state -- it is
		 * exactly what the daemon hands device.info before the first rf.init,
		 * since the handle is created at startup and the part is brought up by a
		 * separate request -- and every vendor getter below dereferences the
		 * handle without a NULL check (ad9361_get_rx_rf_bandwidth reads
		 * phy->current_rx_bw_Hz).  Checking it here is what keeps the readback
		 * from being a NULL dereference; measured on the board 2026-09-26 as
		 * SIGSEGV via device.info -> rf_core_get_readback -> gdb frame
		 * ad9361_get_rx_rf_bandwidth(phy=0x0).  The PL checks above do NOT
		 * imply initialization: with no part configured the SPI read still
		 * succeeds (measured product=0xFF, PHY_MODE=0), so the guard has to be
		 * about the handle, not about the register contents.
		 *
		 * Not initialized is reported as "the part did not answer", which is the
		 * contract device.info already implements (readback:null plus a reason),
		 * rather than a fabricated zero a reader would take for a measurement. */
		if (!core->vendor_phy)
			return RF_ERR_HARDWARE_IO;
		if (ad9361_get_rx_rf_bandwidth(core->vendor_phy, &rx_bw) != 0 ||
		    ad9361_get_tx_rf_bandwidth(core->vendor_phy, &tx_bw) != 0 ||
		    ad9361_get_rx_sampling_freq(core->vendor_phy, &fs_rx) != 0 ||
		    ad9361_get_tx_sampling_freq(core->vendor_phy, &fs_tx) != 0 ||
		    ad9361_get_rx_lo_freq(core->vendor_phy, &rx_lo) != 0 ||
		    ad9361_get_tx_lo_freq(core->vendor_phy, &tx_lo) != 0 ||
		    ad9361_get_tx_attenuation(core->vendor_phy, 0, &atten) != 0 ||
		    ad9361_get_tx_fir_en_dis(core->vendor_phy, &tx_en) != 0 ||
		    ad9361_get_rx_fir_en_dis(core->vendor_phy, &rx_en) != 0 ||
		    ad9361_get_tx_fir_config(core->vendor_phy, 0, &tx_fir) != 0 ||
		    ad9361_get_rx_fir_config(core->vendor_phy, 0, &rx_fir) != 0)
			return RF_ERR_HARDWARE_IO;
		readback->rx_lo_hz = rx_lo;
		readback->tx_lo_hz = tx_lo;
		readback->lo_hz = tx_lo;
		readback->rx_sample_rate_hz = fs_rx;
		readback->tx_sample_rate_hz = fs_tx;
		readback->sample_rate_hz = fs_tx;
		readback->attenuation_mdB = atten;
		readback->rx_bandwidth_hz = rx_bw;
		readback->tx_bandwidth_hz = tx_bw;
		readback->tx_fir_enabled = tx_en;
		readback->rx_fir_enabled = rx_en;
		readback->tx_fir_config_verified = fir_tx_equal(&tx_fir, &rf_tx_fir_config);
		readback->rx_fir_config_verified = fir_rx_equal(&rx_fir, &rf_rx_fir_config_search);
		readback->hardware_verified = true;
	}
#endif
	readback->phy_mode_1r1t = (uint8_t)mode;
	readback->product_id = product;
	return RF_ERR_OK;
}

int rf_core_create(const rf_core_open_args_t *args, rf_core_t **out_core)
{
	rf_core_t *core;

	if (!out_core)
		return RF_ERR_INVALID_ARGUMENT;
	*out_core = NULL;
	core = (rf_core_t *)calloc(1, sizeof(*core));
	if (!core)
		return RF_ERR_NO_MEMORY;
	core->state = RF_STATE_OFFLINE;
	if (args)
		core->open_args = *args;
	*out_core = core;
	return RF_ERR_OK;
}

void rf_core_destroy(rf_core_t *core)
{
	if (!core)
		return;
	rf_core_close(core);
		free(core);
}

int rf_core_open(rf_core_t *core, const rf_core_open_args_t *args)
{
	int ret;

	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (args)
		core->open_args = *args;
	if (rf_owner && rf_owner != core)
		return RF_ERR_INVALID_STATE;
	if (core->opened)
		return RF_ERR_OK;
	if (core->state != RF_STATE_OFFLINE && core->state != RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;

	/* Claim the physical singleton before touching any process-global platform
	 * callback state.  A second core is rejected rather than being allowed to
	 * overwrite the first core's SPI/GPIO/trace context.
	 *
	 * `open` is PLATFORM bring-up only: it maps the apertures and initialises
	 * the PL SPI master so registers can be read and written.  It must not
	 * write the GPIO aperture at all.  `gpio_init()` alone loads the whole
	 * output register with 0, and bit 2 of that register is RESETB (active low)
	 * while bit 1 is ENABLE -- so even calling it here asserts reset and
	 * disables the part.  The CLI opens a core once per command, so
	 * `status`/`plstate`/`phase_inc`/`srcsel`/`caparm`/`audit` would each drop
	 * the part out of the state `init` left it in, and `plstate` would stop
	 * being read-only.  The pin sequence belongs to `init` (and to the explicit
	 * stop path, rf_core_safe_shutdown) -- the only entry points that are meant
	 * to move the part. */
	rf_owner = core;
	ret = spi_init(0, 1, 0);
	if (ret != 0) {
		/* Nothing was driven, so there is no pin state to make safe -- just
		 * release the singleton so a later attempt can claim it. */
		rf_platform_release();
		rf_owner = NULL;
		core->opened = 0;
		core->state = RF_STATE_OFFLINE;
		return RF_ERR_HARDWARE_IO;
	}
	memset(&core->spi, 0, sizeof(core->spi));
	core->opened = 1;
	core->state = RF_STATE_OFFLINE;
	return RF_ERR_OK;
}

static int phy_mode_window_ready(rf_core_t *core)
{
	uint32_t status;
	uint64_t f_data_clk;
	int ret;

	if (!core || !core->opened || core->state == RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;
	/* The window is defined by the DATA_CLK measurement, not by a pin.  A
	 * RESETB-shadow precondition used to sit here and it proved nothing: the
	 * shadow is process-local, so it cannot testify about the hardware, and
	 * `rf_core_set_phy_mode_safe` asserted RESETB on the line above, so the
	 * condition was satisfied by construction.  What actually has to hold is
	 * that the data path is quiet, and only the count says that. */
	ret = status_read_fresh(&status);
	if (ret != RF_ERR_OK)
		return ret;
	/* A zero edge count is NOT proof that DATA_CLK is stopped.  The window is a
	 * fixed 2^15 FCLK0 cycles and the edge counter is 16 bits, so a 100 MHz
	 * DATA_CLK (fs = 50 MSPS in 1R1T) wraps to exactly 0 -- the same word a dead
	 * line produces.  Fail closed: a clock that might be running is not a quiet
	 * one.  Below the wrap, the n=0 member separates the measured noise floor
	 * (<= 0.266 MHz, and never exactly zero) from a real clock (>= 30 MHz).
	 * expect_hz is deliberately 0 here -- there is no clock to corroborate, so
	 * the lowest alias is the right one once the wrap case is excluded above. */
	if (RF_STATUS_WIN_CYCLES(status) == 0u)
		return RF_ERR_HARDWARE_IO;
	f_data_clk = rf_f_data_clk_resolve(status, 0, NULL);
	if (f_data_clk >= RF_NOCLOCK_MAX_HZ)
		return RF_ERR_HARDWARE_IO;
	return RF_ERR_OK;
}

int rf_core_set_phy_mode_safe(rf_core_t *core, uint8_t phy_mode_1r1t,
                              uint32_t *readback)
{
    uint32_t rb;
    int ret;

    if (!core || phy_mode_1r1t > 1u)
        return RF_ERR_INVALID_ARGUMENT;
    if (!core->opened || core->state == RF_STATE_FAULT)
        return RF_ERR_INVALID_STATE;
    if (core->state != RF_STATE_OFFLINE &&
        core->state != RF_STATE_INITIALIZING)
        return RF_ERR_INVALID_STATE;

    /* Check, write, read back -- and nothing else.  This function used to assert
     * RESETB here, which meant `plcfg` and `2r2t` reset the part: the CLI opens a
     * core per command, and neither command drives a pin at baseline.  The
     * RESETB discipline belongs to `rf_core_init`, which asserts it as part of
     * the one nine-pin sequence that is allowed to move the part. */
    ret = phy_mode_window_ready(core);
    if (ret != RF_ERR_OK)
        return ret;

    rf_reg_write(RF_REG_PHY_MODE, phy_mode_1r1t);
    rb = rf_reg_read(RF_REG_PHY_MODE);
    if (rb == 0xDEADBEEFu || rb != (uint32_t)phy_mode_1r1t)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    if (readback)
        *readback = rb;
    return RF_ERR_OK;
}

/* Move the TX attenuation without re-running the configuration, and verify it.
 *
 * This exists for one reason: the safety path.  When the daemon loses its client
 * with the TX live, or when a cabled loopback is requested, the attenuation has
 * to be forced to a known value -- and `rf_core_configure` cannot serve, because
 * it requires READY (a live TX is not READY) and it rewrites the LO, the sample
 * rates, the bandwidths and both FIRs along the way.  Forcing the attenuation is
 * therefore its own operation, deliberately usable from any live state (READY,
 * TX_ACTIVE, either loopback) and deliberately verified, so there is no path in
 * which "we wrote it" is mistaken for "it is in force". */
int rf_core_set_tx_attenuation_safe(rf_core_t *core, uint32_t attenuation_mdB,
                                    uint32_t *readback)
{
	uint32_t rb = 0;

	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (attenuation_mdB > MAX_TX_ATTENUATION_DB)
		return RF_ERR_INVALID_ARGUMENT;
	if (!core->opened || !core->initialized || core->state == RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;

#ifdef PLATFORM_HOST
	/* Same shape as the configure path: the fake vendor setter is a separate
	 * state that the readback is then taken from, so a mismatch here means the
	 * write did not take, exactly as on the board. */
	if (core->fake_vendor.readback_fault)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->fake_vendor.attenuation_mdB = attenuation_mdB;
	rb = core->fake_vendor.attenuation_mdB;
#else
	{
		uint32_t ch0 = 0, ch1 = 0;
		if (ad9361_set_tx_attenuation(core->vendor_phy, 0, attenuation_mdB) != 0 ||
		    ad9361_set_tx_attenuation(core->vendor_phy, 1, attenuation_mdB) != 0 ||
		    ad9361_get_tx_attenuation(core->vendor_phy, 0, &ch0) != 0 ||
		    ad9361_get_tx_attenuation(core->vendor_phy, 1, &ch1) != 0 ||
		    ch0 != attenuation_mdB || ch1 != attenuation_mdB)
			return core_fail(core, RF_ERR_HARDWARE_IO);
		rb = ch0;
	}
#endif
	if (rb != attenuation_mdB)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->attenuation_mdB = rb;
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_init(rf_core_t *core, const rf_init_request_t *request,
                 rf_readback_t *readback)
{
	uint32_t expected_mode, mode_rb;
	int32_t ret;
	rf_readback_t actual;
	int result;

	if (!core || !request)
		return RF_ERR_INVALID_ARGUMENT;
	if (!request->attenuation_mdB_present || request->attenuation_mdB > 1000000u)
		return RF_ERR_INVALID_ARGUMENT;
	if (request->lo_hz == 0 || request->sample_rate_hz == 0 ||
	    request->phy_mode_1r1t > 1u)
		return RF_ERR_INVALID_ARGUMENT;
	if (!core->opened)
		return RF_ERR_INVALID_STATE;
	if (core->state != RF_STATE_OFFLINE && core->state != RF_STATE_FAULT)
		return RF_ERR_INVALID_STATE;

	core->initialization_attempted = true;
	core->state = RF_STATE_INITIALIZING;
	/* The AD9361 reset/ENABLE sequence.  It lives here, not in `open`, so that
	 * opening a core to read or configure PL registers cannot reset a running
	 * part.  gpio_init() loads the whole output register with 0 first, which is
	 * exactly what `init` wants -- a known state to drive the nine pins from --
	 * and it is safe to call only here, because this is the command that
	 * programs the part.  RESETB is left asserted through the mode write below
	 * and released once the framing bit has been committed. */
	gpio_init(0);
	gpio_set_value(RF_GPIO_TXNRX, 1);
	gpio_set_value(RF_GPIO_ENABLE, 1);
	gpio_set_value(RF_GPIO_RESETB, 0);
	gpio_set_value(RF_GPIO_SYNC, 0);
	gpio_set_value(RF_GPIO_EN_AGC, 0);
	gpio_set_value(RF_GPIO_CTL0, 0);
	gpio_set_value(RF_GPIO_CTL1, 0);
	gpio_set_value(RF_GPIO_CTL2, 0);
	gpio_set_value(RF_GPIO_CTL3, 0);
	expected_mode = request->phy_mode_1r1t ? 1u : 0u;
	/* This is the sole mode transition in the core.  It proves the DATA_CLK
	 * window is quiet (zero-count and fresh) before changing the unsynchronised
	 * framing bit; RESETB being low is asserted by the sequence just above, not
	 * re-proved here -- the window check is the part that can fail.  A failure
	 * here is a hardware fault (unreadable/stale STATUS, or a mode write that
	 * did not read back), not a bad argument, so it must leave the core in
	 * FAULT rather than mid-INITIALIZING -- a state that would refuse a retry
	 * and hide that the part was never programmed. */
	result = rf_core_set_phy_mode_safe(core, (uint8_t)expected_mode, &mode_rb);
	if (result != RF_ERR_OK)
		return core_fail(core, result);

	rf_init_param_fill(core);
	core->init_param.two_rx_two_tx_mode_enable = expected_mode ? 0 : 1;
	core->init_param.rx_synthesizer_frequency_hz = request->lo_hz;
	core->init_param.tx_synthesizer_frequency_hz = request->lo_hz;
	core->init_param.tx_attenuation_mdB = (int32_t)request->attenuation_mdB;
	core->init_param.rf_rx_bandwidth_hz = 30000000UL;
	core->init_param.rf_tx_bandwidth_hz = 30000000UL;
	core->trace_path = core->open_args.trace_path;
	if (core->open_args.trace_enabled)
		rf_trace_open(core->trace_path ? core->trace_path : "trace_init.txt");
	gpio_set_value(RF_GPIO_RESETB, 1);
	msleep_interruptible(20);

#ifdef PLATFORM_HOST
	core->vendor_phy = NULL;
	fake_vendor_init(core, request->lo_hz, request->sample_rate_hz,
	                 request->attenuation_mdB);
	ret = 0;
#else
	ret = ad9361_init(&core->vendor_phy, &core->init_param);
#endif
	if (core->open_args.trace_enabled && rf_trace_flush() < 0)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (ret != 0)
		return core_fail(core, RF_ERR_HARDWARE_IO);

#ifdef PLATFORM_HOST
	/* The fake vendor is stateful and is read by core_fill_readback; it is not
	 * populated from the readback object or from request fields at read time. */
#else
	/* ORDER AND RETURN-CODES, both taken from the baseline this core was
	 * extracted from (`fa25461:rf_main.c:839-896`), because both are load-bearing.
	 *
	 * The order is: ad9361_init (above) -> FIR config -> FIR enable -> sampling
	 * freq -> attenuation -> LO.
	 *
	 * The return codes are NOT uniformly fatal.  `ad9361_set_rx_fir_en_dis()`
	 * (vendor/ad9361_api.c:1128) validates the tap count against the CURRENT
	 * clock chain:
	 *
	 *     max = ((ADC_FREQ / (ADC_FREQ == R2_FREQ ? 1 : 2)) / RX_SAMPL_FREQ) * 16
	 *
	 * At this point the chain is still the `init_param` reference one (40 MSym/s),
	 * because the 60 MSym/s this core runs at only arrives with the
	 * `set_*_sampling_freq` calls below.  max is therefore below the RX filter's
	 * 128 taps, the vendor sets `bypass_rx_fir = true` and returns -EINVAL --
	 * measured on the board 2026-09-26 as
	 * `ad9361_validate_enable_fir: Invalid: ratio ADC/2 / RX_SAMPL * 16 > TAPS (max 64)`.
	 *
	 * The baseline called that setter as a bare statement and ignored the result,
	 * so `init` still succeeded (with the RX FIR bypassed -- `set_rx_fir_en_dis`
	 * restores `bypass_rx_fir = true` on failure, i.e. it fails SAFE).  Folding it
	 * into an `||` chain is what turned a benign, self-reverting condition into
	 * "every rf.init fails with HARDWARE_IO".  So it is called and its result is
	 * deliberately NOT part of the fatal chain; the readback check below
	 * (`rx_fir_config_verified`) is the criterion that still catches a genuinely
	 * absent RX FIR.
	 *
	 * Everything else here keeps its fatal check, as the baseline's own comment
	 * intended: sampling freq, attenuation and LO are what the acceptance
	 * criteria compare against the readback. */
	if (ad9361_set_tx_fir_config(core->vendor_phy, rf_tx_fir_config) != 0 ||
	    ad9361_set_rx_fir_config(core->vendor_phy, rf_rx_fir_config_search) != 0 ||
	    ad9361_set_tx_fir_en_dis(core->vendor_phy, 1) != 0)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	/* Non-fatal by measurement, not by hope: see the comment above. */
	(void)ad9361_set_rx_fir_en_dis(core->vendor_phy, 1);
	if (ad9361_set_tx_sampling_freq(core->vendor_phy, request->sample_rate_hz) != 0 ||
	    ad9361_set_rx_sampling_freq(core->vendor_phy, request->sample_rate_hz) != 0 ||
	    ad9361_set_tx_attenuation(core->vendor_phy, 0, request->attenuation_mdB) != 0 ||
	    ad9361_set_tx_attenuation(core->vendor_phy, 1, request->attenuation_mdB) != 0 ||
	    ad9361_set_tx_lo_freq(core->vendor_phy, request->lo_hz) != 0 ||
	    ad9361_set_rx_lo_freq(core->vendor_phy, request->lo_hz) != 0)
		return core_fail(core, RF_ERR_HARDWARE_IO);
#endif
	result = core_fill_readback(core, &actual);
	if (result != RF_ERR_OK)
		return core_fail(core, result);
	if ((actual.product_id & 0xF8u) != 0x08u ||
	    actual.phy_mode_1r1t != request->phy_mode_1r1t ||
	    actual.rx_lo_hz != request->lo_hz ||
	    actual.tx_lo_hz != request->lo_hz ||
	    actual.rx_sample_rate_hz != request->sample_rate_hz ||
	    actual.tx_sample_rate_hz != request->sample_rate_hz ||
	    actual.lo_hz != request->lo_hz ||
	    actual.sample_rate_hz != request->sample_rate_hz ||
	    actual.attenuation_mdB != request->attenuation_mdB ||
	    actual.rx_bandwidth_hz != 30000000u ||
	    actual.tx_bandwidth_hz != 30000000u ||
	    /* The FIR *configuration* must be the one that was uploaded -- that is a
	     * readback of what this code wrote, and it is the criterion that catches a
	     * dropped or corrupted coefficient load. */
	    !actual.tx_fir_config_verified || !actual.rx_fir_config_verified)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	/* The FIR *enable* flags are REPORTED, not gated (baseline semantics --
	 * `fa25461:rf_main.c:842,940` called `ad9361_set_rx_fir_en_dis` and printed
	 * `enable=%u/%u` without ever failing on it).
	 *
	 * They must not be fatal because the RX FIR cannot be enabled on this board at
	 * all: `ad9361_validate_enable_fir()` caps the taps at
	 * `(ADC/2 / RX_SAMPL) * 16`, which the measured 60 MSym/s chain puts below the
	 * filter's 128 taps, so the enable-setter returns -EINVAL and (failing SAFE)
	 * resets `bypass_rx_fir`.  That is the vendor's design decision for this
	 * configuration, not a fault in this code -- the coefficients still load and
	 * still verify, which `rx_fir_config_verified` above proves. */
	if (actual.tx_fir_enabled != 1u)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->lo_hz = actual.lo_hz;
	core->sample_rate_hz = actual.sample_rate_hz;
	core->attenuation_mdB = actual.attenuation_mdB;
	core->rx_bandwidth_hz = actual.rx_bandwidth_hz;
	core->tx_bandwidth_hz = actual.tx_bandwidth_hz;
	if (readback)
		*readback = actual;
	core->initialized = 1;
	core->state = RF_STATE_READY;
	return RF_ERR_OK;
}

int rf_core_configure(rf_core_t *core, const rf_config_t *config,
                      rf_readback_t *readback)
{
	uint32_t mode;
	rf_readback_t actual;
	int result;

	if (!core || !config)
		return RF_ERR_INVALID_ARGUMENT;
	if (config->lo_hz == 0 || config->sample_rate_hz == 0 ||
	    config->rx_bandwidth_hz == 0 || config->tx_bandwidth_hz == 0 ||
	    config->phy_mode_1r1t > 1u ||
	    config->attenuation_mdB > MAX_TX_ATTENUATION_DB)
		return RF_ERR_INVALID_ARGUMENT;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	mode = rf_reg_read(RF_REG_PHY_MODE);
	if (mode > 1u || mode == 0xDEADBEEFu || mode != config->phy_mode_1r1t)
		return core_fail(core, RF_ERR_HARDWARE_IO);

	/* Idempotence (plan Task 4 Step 3).  A request that asks for exactly what the
	 * part already holds is answered from the part and writes nothing: re-writing
	 * the same values would put register writes on the bus for no change, and
	 * "configure twice" is how a caller re-asserts a configuration it is not sure
	 * about, so it must be free.
	 *
	 * Every field the request controls is compared against the PART -- the readback
	 * (LO, sample rate, both bandwidths, attenuation, the FIR state) plus the two
	 * IDELAY registers read here; the PHY mode was compared from the PL just above.
	 * Comparing a cached copy of the last request instead would skip a write that
	 * was genuinely needed from the moment something else changed the part, which is
	 * exactly the case this phase exists for.  The cost is one extra readback on the
	 * path that does write; the answer is still taken from the part either way. */
	result = core_fill_readback(core, &actual);
	if (result != RF_ERR_OK)
		return core_fail(core, result);
	if (actual.lo_hz == config->lo_hz &&
	    actual.sample_rate_hz == config->sample_rate_hz &&
	    actual.rx_bandwidth_hz == config->rx_bandwidth_hz &&
	    actual.tx_bandwidth_hz == config->tx_bandwidth_hz &&
	    actual.attenuation_mdB == config->attenuation_mdB &&
	    rf_reg_read(RF_REG_IDELAY_EN) == (config->idelay_en ? 0x7Fu : 0u) &&
	    rf_reg_read(RF_REG_IDELAY_TAP) == config->idelay_tap) {
		if (readback)
			*readback = actual;
		return RF_ERR_OK;
	}

	/* Who owns the IDELAY taps (plan Task 8 Step 1).  Phase 1 MEASURED that an
	 * Overlay() reload CLEARS them and that frame alignment depends on them, and the
	 * panel launcher is what loads them after a reload.  That ownership is deliberately
	 * UNCHANGED here: this request programs the taps the caller names, and the daemon
	 * neither re-loads them on its own nor tries to restore them after a reload.  So a
	 * reload can leave the taps at their power-on value until the launcher runs, and
	 * "a reload must not leave the RX path half-armed" is recorded as NOT CRITERION in
	 * the delivery record (section 24) rather than claimed as enforced. */
	rf_reg_write(RF_REG_IDELAY_EN, config->idelay_en ? 0x7Fu : 0u);
	rf_reg_write(RF_REG_IDELAY_TAP, config->idelay_tap);
	if (rf_reg_read(RF_REG_IDELAY_EN) != (config->idelay_en ? 0x7Fu : 0u) ||
	    rf_reg_read(RF_REG_IDELAY_TAP) != config->idelay_tap)
		return core_fail(core, RF_ERR_HARDWARE_IO);

#ifdef PLATFORM_HOST
	/* These are fake vendor setters, not copies into the public readback.  All
	 * subsequent values come from this separate state through core_fill_readback. */
	if (core->fake_vendor.readback_fault)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->fake_vendor.rx_lo_hz = config->lo_hz;
	core->fake_vendor.tx_lo_hz = config->lo_hz;
	core->fake_vendor.rx_sample_rate_hz = config->sample_rate_hz;
	core->fake_vendor.tx_sample_rate_hz = config->sample_rate_hz;
	core->fake_vendor.attenuation_mdB = config->attenuation_mdB;
	core->fake_vendor.rx_bandwidth_hz = config->rx_bandwidth_hz;
	core->fake_vendor.tx_bandwidth_hz = config->tx_bandwidth_hz;
	core->fake_vendor.tx_fir = rf_tx_fir_config;
	core->fake_vendor.rx_fir = rf_rx_fir_config_search;
	core->fake_vendor.tx_fir_enabled = 1;
	core->fake_vendor.rx_fir_enabled = 1;
#else
	{
		uint32_t rx_bw = 0, tx_bw = 0, fs_rx = 0, fs_tx = 0, atten = 0;
		uint64_t rx_lo = 0, tx_lo = 0;
		uint8_t tx_en = 0, rx_en = 0;
		AD9361_TXFIRConfig tx_fir;
		AD9361_RXFIRConfig rx_fir;
		if (ad9361_set_rx_rf_bandwidth(core->vendor_phy, config->rx_bandwidth_hz) != 0 ||
		    ad9361_set_tx_rf_bandwidth(core->vendor_phy, config->tx_bandwidth_hz) != 0 ||
		    ad9361_set_rx_sampling_freq(core->vendor_phy, config->sample_rate_hz) != 0 ||
		    ad9361_set_tx_sampling_freq(core->vendor_phy, config->sample_rate_hz) != 0 ||
		    ad9361_set_rx_lo_freq(core->vendor_phy, config->lo_hz) != 0 ||
		    ad9361_set_tx_lo_freq(core->vendor_phy, config->lo_hz) != 0 ||
		    ad9361_set_tx_attenuation(core->vendor_phy, 0, config->attenuation_mdB) != 0 ||
		    ad9361_set_tx_attenuation(core->vendor_phy, 1, config->attenuation_mdB) != 0 ||
		    ad9361_set_tx_fir_config(core->vendor_phy, rf_tx_fir_config) != 0 ||
		    ad9361_set_rx_fir_config(core->vendor_phy, rf_rx_fir_config_search) != 0 ||
		    ad9361_set_tx_fir_en_dis(core->vendor_phy, 1) != 0 ||
		    ad9361_get_rx_rf_bandwidth(core->vendor_phy, &rx_bw) != 0 ||
		    ad9361_get_tx_rf_bandwidth(core->vendor_phy, &tx_bw) != 0 ||
		    ad9361_get_rx_sampling_freq(core->vendor_phy, &fs_rx) != 0 ||
		    ad9361_get_tx_sampling_freq(core->vendor_phy, &fs_tx) != 0 ||
		    ad9361_get_rx_lo_freq(core->vendor_phy, &rx_lo) != 0 ||
		    ad9361_get_tx_lo_freq(core->vendor_phy, &tx_lo) != 0 ||
		    ad9361_get_tx_attenuation(core->vendor_phy, 0, &atten) != 0 ||
		    ad9361_get_tx_fir_en_dis(core->vendor_phy, &tx_en) != 0 ||
		    ad9361_get_rx_fir_en_dis(core->vendor_phy, &rx_en) != 0 ||
		    ad9361_get_tx_fir_config(core->vendor_phy, 0, &tx_fir) != 0 ||
		    ad9361_get_rx_fir_config(core->vendor_phy, 0, &rx_fir) != 0 ||
		    rx_bw != config->rx_bandwidth_hz || tx_bw != config->tx_bandwidth_hz ||
		    fs_rx != config->sample_rate_hz || fs_tx != config->sample_rate_hz ||
		    rx_lo != config->lo_hz || tx_lo != config->lo_hz ||
		    atten != config->attenuation_mdB || tx_en != 1u ||
		    !fir_tx_equal(&tx_fir, &rf_tx_fir_config) ||
		    !fir_rx_equal(&rx_fir, &rf_rx_fir_config_search))
			return core_fail(core, RF_ERR_HARDWARE_IO);
		/* Read for the record, deliberately not a criterion here (the RX FIR
		 * cannot be enabled at this chain -- see rf_core_init). */
		(void)rx_en;
	}
#endif
	result = core_fill_readback(core, &actual);
	if (result != RF_ERR_OK)
		return core_fail(core, result);
	if (actual.rx_lo_hz != config->lo_hz ||
	    actual.tx_lo_hz != config->lo_hz ||
	    actual.rx_sample_rate_hz != config->sample_rate_hz ||
	    actual.tx_sample_rate_hz != config->sample_rate_hz ||
	    actual.lo_hz != config->lo_hz ||
	    actual.sample_rate_hz != config->sample_rate_hz ||
	    actual.attenuation_mdB != config->attenuation_mdB ||
	    actual.rx_bandwidth_hz != config->rx_bandwidth_hz ||
	    actual.tx_bandwidth_hz != config->tx_bandwidth_hz ||
	    /* Same split as rf_core_init: the FIR CONFIG is a criterion, the RX FIR
	     * ENABLE is reported (see the long note there). */
	    !actual.tx_fir_config_verified || !actual.rx_fir_config_verified)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (actual.tx_fir_enabled != 1u)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->lo_hz = actual.lo_hz;
	core->sample_rate_hz = actual.sample_rate_hz;
	core->attenuation_mdB = actual.attenuation_mdB;
	core->rx_bandwidth_hz = actual.rx_bandwidth_hz;
	core->tx_bandwidth_hz = actual.tx_bandwidth_hz;
	if (readback)
		*readback = actual;
	return RF_ERR_OK;
}

int rf_core_write_tx(rf_core_t *core, const uint32_t *words, size_t count)
{
	size_t i;

	if (!core || (!words && count != 0) || count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	for (i = 0; i < count; ++i) {
		rf_bram_write(RF_BRAM_TX_BASE, (uint32_t)i, words[i]);
		if (rf_bram_read(RF_BRAM_TX_BASE, (uint32_t)i) != words[i])
			return core_fail(core, RF_ERR_HARDWARE_IO);
	}
	core->tx_count = count;
	return RF_ERR_OK;
}

int rf_core_prepare_tx(rf_core_t *core, size_t count, bool wrap)
{
	uint32_t cfg, cfg_rb, len_rb;

	if (!core || count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	rf_reg_write(RF_REG_SMP_LEN, (uint32_t)count);
	/* Preserve src_sel as baseline's `smpcfg` did (it read the register rather
	 * than building the word from scratch), and commit on a TOGGLE. */
	cfg = wrap ? (cfg | RF_SMP_WRAP) : (cfg & ~RF_SMP_WRAP);
	rf_reg_write(RF_REG_SRC_CFG,
	             commit_toggle(cfg, cfg & (RF_SRC_SEL | RF_SMP_WRAP), RF_SMP_COMMIT));
	len_rb = rf_reg_read(RF_REG_SMP_LEN);
	cfg_rb = rf_reg_read(RF_REG_SRC_CFG);
	if (len_rb == 0xDEADBEEFu || cfg_rb == 0xDEADBEEFu ||
	    (len_rb & 0x1FFFu) != count ||
	    (cfg_rb & (RF_SRC_SEL | RF_SMP_WRAP)) != (cfg & (RF_SRC_SEL | RF_SMP_WRAP)))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->tx_count = count;
	core->tx_wrap = wrap;
	core->tx_prepared = 1;
	return RF_ERR_OK;
}

int rf_core_start_tx(rf_core_t *core)
{
	uint32_t cfg;
	uint32_t rb;
	uint32_t p0, p1;

	if (!core || !core->tx_prepared)
		return RF_ERR_INVALID_STATE;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	/* Enabling the reader is a LEVEL write (baseline's `srcsel 1`): set src_sel,
	 * leave the commit bit at whatever level it holds.  Toggling here would
	 * re-latch a configuration the caller did not mean to change. */
	rf_reg_write(RF_REG_SRC_CFG, cfg | RF_SRC_SEL);
	/* Check the RAW word: RF_SRC_SEL is bit 0, and 0xDEADBEEF has bit 0 set, so a
	 * bit-only test would read a failed access as "TX enabled". */
	rb = rf_reg_read(RF_REG_SRC_CFG);
	if (rb == 0xDEADBEEFu || !(rb & RF_SRC_SEL))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	p0 = rf_reg_read(RF_REG_SMP_POS);
	udelay(1000);
	p1 = rf_reg_read(RF_REG_SMP_POS);
	if (core->tx_count > 1 && p0 == p1)
		return core_fail(core, RF_ERR_TIMEOUT);
	core->state = RF_STATE_TX_ACTIVE;
	return RF_ERR_OK;
}

int rf_core_stop_tx(rf_core_t *core, rf_hw_status_t *status)
{
	uint32_t cfg;
	uint32_t p0, p1;

	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->state != RF_STATE_TX_ACTIVE && core->state != RF_STATE_READY)
		return RF_ERR_INVALID_STATE;
	core->state = RF_STATE_STOPPING;
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	/* Level write, as baseline's `srcsel 0`: clear src_sel, leave the commit
	 * bit where it is. */
	rf_reg_write(RF_REG_SRC_CFG, cfg & ~RF_SRC_SEL);
	p0 = rf_reg_read(RF_REG_SMP_POS);
	udelay(1000);
	p1 = rf_reg_read(RF_REG_SMP_POS);
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	if ((cfg & RF_SRC_SEL) || p0 != p1) {
		if (status) {
			status->tx_active = true;
			status->rx_busy = false;
			status->rx_done = false;
		}
		return core_fail(core, RF_ERR_HARDWARE_IO);
	}
	if (status) {
		status->tx_active = false;
		status->rx_busy = false;
		status->rx_done = false;
	}
	core->tx_prepared = 0;
	core->state = RF_STATE_READY;
	return RF_ERR_OK;
}

int rf_core_prepare_rx(rf_core_t *core, uint8_t ratio, size_t count,
                       bool continuous)
{
	uint32_t cfg, ratio_rb, len_rb, cfg_rb;

	if (!core || ratio > 4u || count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_CAP_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	rf_reg_write(RF_REG_CAP_RATIO, ratio);
	rf_reg_write(RF_REG_CAP_LEN, (uint32_t)count);
	/* Commit on a TOGGLE, and force the ARM bit LOW.
         *
         * A completed capture leaves RF_CAP_ARM set -- rf_core_read_rx deliberately
         * does not clear it -- and a prepare_rx that PRESERVED it made the NEXT
         * capture inherit that arm: the capture never re-ran and the reader returned
         * the previous BRAM contents instead.  Measured on the board 2026-09-30 (a
         * 64-sample request came back as the stale 4 words plus 60 never-written
         * zeros).  start_rx is what raises the bit, so the arm is now always a fresh
         * 0->1 transition.  The bus (ratio/len/cont) is written first so it is stable
         * at the edge. */
        cfg = continuous ? (cfg | RF_CAP_CONT) : (cfg & ~RF_CAP_CONT);
        cfg &= ~RF_CAP_ARM;
        rf_reg_write(RF_REG_CAP_CFG,
                     commit_toggle(cfg, cfg & (RF_CAP_ARM | RF_CAP_CONT), RF_CAP_COMMIT));
	ratio_rb = rf_reg_read(RF_REG_CAP_RATIO);
	len_rb = rf_reg_read(RF_REG_CAP_LEN);
	cfg_rb = rf_reg_read(RF_REG_CAP_CFG);
	if (ratio_rb == 0xDEADBEEFu || len_rb == 0xDEADBEEFu || cfg_rb == 0xDEADBEEFu ||
	    (ratio_rb & 7u) != ratio ||
	    (len_rb & 0x1FFFu) != count ||
	    (cfg_rb & RF_CAP_CONT) != (continuous ? RF_CAP_CONT : 0u) ||
     (cfg_rb & RF_CAP_ARM) != 0u)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->rx_count = count;
	core->rx_continuous = continuous;
	core->rx_prepared = 1;
	return RF_ERR_OK;
}

int rf_core_start_rx(rf_core_t *core)
{
	uint32_t cfg;
	uint32_t rb;

	if (!core || !core->rx_prepared)
		return RF_ERR_INVALID_STATE;
	if (core_ready(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_CAP_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	/* Arming is a LEVEL write (baseline's `caparm 1`): cfg_arm goes straight
	 * through to the capture, and the commit bit is left alone. */
	rf_reg_write(RF_REG_CAP_CFG, cfg | RF_CAP_ARM);
	/* RF_CAP_ARM is bit 0 and 0xDEADBEEF has bit 0 set: test the raw word. */
	rb = rf_reg_read(RF_REG_CAP_CFG);
	if (rb == 0xDEADBEEFu || !(rb & RF_CAP_ARM))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->state = RF_STATE_RX_CAPTURING;
	return RF_ERR_OK;
}

int rf_core_poll_rx(rf_core_t *core, rf_capture_status_t *status)
{
	uint32_t raw;
	uint32_t cfg;
	int tries;

	if (!core || !status)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->state != RF_STATE_RX_CAPTURING)
		return RF_ERR_INVALID_STATE;
	for (tries = 0; tries < 100; ++tries) {
		raw = rf_reg_read(RF_REG_CAP_STATUS);
		cfg = rf_reg_read(RF_REG_CAP_CFG);
		/* The commit bit legitimately holds whatever level was last written --
		 * it is an edge, so it is not cleared after use -- and slv_reg9 returns
		 * it (axi_ctrl_v1_0_S00_AXI.v:603).  Masking it out of the shape check
		 * is required; the sentinel is still caught by the unmasked bits. */
		if ((raw & ~((1u << 13) | 0x1FFFu)) != 0u ||
		    (cfg & ~(RF_CAP_ARM | RF_CAP_CONT | RF_CAP_COMMIT)) != 0u)
			return core_fail(core, RF_ERR_HARDWARE_IO);
		status->done = RF_CAP_DONE(raw) != 0;
		status->position = (uint16_t)RF_CAP_POS(raw);
		status->busy = (cfg & RF_CAP_ARM) != 0 && !status->done;
		if (status->done || core->rx_continuous)
			return RF_ERR_OK;
		udelay(1000);
	}
	return core_fail(core, RF_ERR_TIMEOUT);
}

int rf_core_read_rx(rf_core_t *core, uint32_t *words, size_t count)
{
	rf_capture_status_t status;
	size_t i;

	if (!core || !words || count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->state != RF_STATE_RX_CAPTURING)
		return RF_ERR_INVALID_STATE;
	if (core->rx_continuous)
		return RF_ERR_INVALID_STATE;
	if (count > core->rx_count)
		return RF_ERR_INVALID_ARGUMENT;
	if (rf_core_poll_rx(core, &status) != RF_ERR_OK || !status.done)
		return core_fail(core, RF_ERR_TIMEOUT);
	for (i = 0; i < count; ++i)
		words[i] = rf_bram_read(RF_BRAM_RX_BASE, (uint32_t)i);
	core->state = RF_STATE_READY;
	core->rx_prepared = 0;
	return RF_ERR_OK;
}

/* Complete a FINITE capture: freeze the samples, then release the capture.
 *
 * rf_core_read_rx above deliberately leaves RF_CAP_ARM high, because a completed
 * capture that HAS finished really is a full buffer waiting to be drained -- but a
 * host cannot act on that: with the arm still up, "armed and waiting" and "done and
 * drained" are the same word, and a second finite capture used to inherit the arm
 * (measured on the board 2026-09-30: a 64-sample ask came back as the previous
 * frame).  This is the finite counterpart.
 *
 * The ORDER is the contract, not a style choice.  Lowering the arm is what ends the
 * capture, so the samples are copied out of the BRAM FIRST, while the completed
 * capture is still held; releasing first and draining second would make "the frozen
 * frame" and "the capture that produced it" describe two different transactions.
 * rf_core_read_rx does the freeze and the DONE wait in one place, so this reuses it
 * rather than growing a second copy of the poll/drain logic.
 *
 * The release is a LEVEL register write -- the same one rf_core_safe_shutdown uses
 * for the same bit, so there is one idiom for "release the capture" and not two --
 * followed by an explicit readback, rather than a call to rf_core_set_rx_arm.  That
 * setter is gated on core_opened(), which refuses FAULT, and this function's whole
 * job is to be the release that cannot itself be refused: it reaches the write only
 * from a completed capture it has just read back, but a teardown that a gate could
 * decline is the same shape of hole this interface exists to close, and the CLI
 * setter is not the thing being asserted here -- the ARM BIT is.  What the setter
 * would have contributed on top (its own readback check) is done here directly, so
 * the confirmation is this function's own.  A clear that did not take faults the
 * core and the frozen samples are never handed back as a success.  A continuous
 * capture is refused by rf_core_read_rx above and keeps its arm -- that is the
 * continuous semantic and it does not move. */
int rf_core_finish_rx(rf_core_t *core, uint32_t *words, size_t count)
{
	uint32_t cap;
	int rc;

	/* 1. FREEZE.  Waits for DONE, drains the BRAM into the caller's buffer and
	 *    returns the core to READY; a capture that did not complete faults the
	 *    core there and nothing is released. */
	rc = rf_core_read_rx(core, words, count);
	if (rc != RF_ERR_OK)
		return rc;

	/* 2. RELEASE, and verify the real register rather than the intent. */
	cap = rf_reg_read(RF_REG_CAP_CFG);
	if (cap == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	rf_reg_write(RF_REG_CAP_CFG, cap & ~RF_CAP_ARM);
	cap = rf_reg_read(RF_REG_CAP_CFG);
	if (cap == 0xDEADBEEFu || (cap & RF_CAP_ARM) != 0u)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	return RF_ERR_OK;
}

int rf_core_set_data_port_loop(rf_core_t *core, bool enable, uint8_t *readback)
{
        uint8_t before, after;
        int ret;

        if (!core)
                return RF_ERR_INVALID_ARGUMENT;
        ret = core_spi_read(core, REG_OBSERVE_CONFIG, &before);
        if (ret != RF_ERR_OK)
                return core_fail(core, ret);
        if (ad9361_spi_write(&core->spi, REG_OBSERVE_CONFIG,
                     (before & (uint8_t)~DATA_PORT_LOOP_TEST_ENABLE) |
                             (enable ? DATA_PORT_LOOP_TEST_ENABLE : 0u)) != 0)
                return core_fail(core, RF_ERR_HARDWARE_IO);
        ret = core_spi_read(core, REG_OBSERVE_CONFIG, &after);
        if (ret != RF_ERR_OK ||
            (after & (uint8_t)~DATA_PORT_LOOP_TEST_ENABLE) !=
            (before & (uint8_t)~DATA_PORT_LOOP_TEST_ENABLE) ||
            ((after & DATA_PORT_LOOP_TEST_ENABLE) != (enable ? 1u : 0u)))
                return core_fail(core, RF_ERR_HARDWARE_IO);
        if (readback)
                *readback = after;
        core->last_loopback = after;
        return RF_ERR_OK;
}

int rf_core_set_digital_loopback(rf_core_t *core, bool enable,
                                 uint8_t *readback)
{
        int ret;

        if (!core)
                return RF_ERR_INVALID_ARGUMENT;
        if (core->state != RF_STATE_READY && core->state != RF_STATE_DIGITAL_LOOPBACK)
                return RF_ERR_INVALID_STATE;
        ret = rf_core_set_data_port_loop(core, enable, readback);
        if (ret != RF_ERR_OK)
                return ret;
        core->state = enable ? RF_STATE_DIGITAL_LOOPBACK : RF_STATE_READY;
        return RF_ERR_OK;
}


int rf_core_safe_shutdown(rf_core_t *core, rf_hw_status_t *status)
{
	uint32_t src;
	uint32_t cap;
	int bad = 0;

	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->opened && rf_owner != core)
		return RF_ERR_INVALID_STATE;
	if (!core->opened) {
		if (status) {
			status->tx_active = false;
			status->rx_busy = false;
			status->rx_done = false;
		}
		core->state = RF_STATE_OFFLINE;
		return RF_ERR_OK;
	}
	core->state = RF_STATE_STOPPING;
	/* Level writes: clear src_sel and cfg_arm, leave the commit bits at
	 * whatever level they hold (baseline's `srcsel 0` + `caparm 0`). */
	src = rf_reg_read(RF_REG_SRC_CFG);
	rf_reg_write(RF_REG_SRC_CFG, src & ~RF_SRC_SEL);
	cap = rf_reg_read(RF_REG_CAP_CFG);
	rf_reg_write(RF_REG_CAP_CFG, cap & ~RF_CAP_ARM);
	src = rf_reg_read(RF_REG_SRC_CFG);
	cap = rf_reg_read(RF_REG_CAP_CFG);
	if ((src & RF_SRC_SEL) || (cap & RF_CAP_ARM))
		bad = 1;
	if (status) {
		status->tx_active = (src & RF_SRC_SEL) != 0;
		status->rx_busy = (cap & RF_CAP_ARM) != 0;
		status->rx_done = false;
	}
	/* Drive the part to its resting state explicitly: gpio_init() zeroes the
	 * whole output register (TXNRX/ENABLE/RESETB/... all low) so the stop does
	 * not depend on whatever the lazy bring-up in gpio_set_value() would have
	 * produced.  RESETB low is the held-in-reset state, ENABLE low is disabled. */
	gpio_init(0);
	gpio_set_value(RF_GPIO_RESETB, 0);
	gpio_set_value(RF_GPIO_ENABLE, 0);
	if (bad)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->tx_prepared = 0;
	core->rx_prepared = 0;
	core->state = RF_STATE_OFFLINE;
	return RF_ERR_OK;
}

int rf_core_release_part_keep_state(rf_core_t *core, rf_hw_status_t *status)
{
	uint32_t src;
	uint32_t cap;

	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	if (core->opened && rf_owner != core)
		return RF_ERR_INVALID_STATE;
	if (!core->opened) {
		if (status) {
			status->tx_active = false;
			status->rx_busy = false;
			status->rx_done = false;
		}
		return RF_ERR_OK;
	}
	/* Deliberately NOT core->state = RF_STATE_STOPPING: unlike a shutdown, this
	 * must leave a FAULT visible as a FAULT.  Only the register level moves. */
	/* Guarded like the sibling setters: 0xDEADBEEF is the unmapped/failed-read
	 * sentinel, and a value derived from it must never be written back.  An
	 * unreadable register is reported, not masked into a "cleared" write. */
	src = rf_reg_read(RF_REG_SRC_CFG);
	if (src == 0xDEADBEEFu)
		return RF_ERR_HARDWARE_IO;
	rf_reg_write(RF_REG_SRC_CFG, src & ~RF_SRC_SEL);
	cap = rf_reg_read(RF_REG_CAP_CFG);
	if (cap == 0xDEADBEEFu)
		return RF_ERR_HARDWARE_IO;
	rf_reg_write(RF_REG_CAP_CFG, cap & ~RF_CAP_ARM);
	src = rf_reg_read(RF_REG_SRC_CFG);
	cap = rf_reg_read(RF_REG_CAP_CFG);
	if (src == 0xDEADBEEFu || cap == 0xDEADBEEFu)
		return RF_ERR_HARDWARE_IO;
	if (status) {
		status->tx_active = (src & RF_SRC_SEL) != 0;
		status->rx_busy = (cap & RF_CAP_ARM) != 0;
		status->rx_done = false;
	}
	if ((src & RF_SRC_SEL) || (cap & RF_CAP_ARM))
		/* NOT core_fail(): the contract is that this call leaves the state exactly
		 * as it found it, and the caller turns this -1 into an unconfirmed stop.
		 * safe_shutdown() may mark a fault because it owns the transition to
		 * OFFLINE; this must not, or it would be the very thing it exists to
		 * avoid -- erasing or inventing a state it was not asked to change. */
		return RF_ERR_HARDWARE_IO;
	/* The prepare flags describe what the daemon believes it has driven; the part
	 * is no longer driven by either path, so they are cleared.  Leaving them set
	 * is what do_stop warns about: a later stop would have no way to confirm. */
	core->tx_prepared = 0;
	core->rx_prepared = 0;
	return RF_ERR_OK;
}

void rf_core_close(rf_core_t *core)
{
	if (!core)
		return;
	/* A never-opened secondary core must not touch the first core's hardware. */
	if (rf_owner != core) {
		core->opened = 0;
		core->initialized = 0;
		core->state = RF_STATE_OFFLINE;
		return;
	}
	/* `close` releases the process-local platform state (mmap, SPI/GPIO
	 * callbacks, trace buffer) and nothing else.  It deliberately does NOT stop
	 * the part and does NOT drive the control pins: stopping is the explicit job
	 * of rf_core_safe_shutdown().  The CLI opens a core per command, so a stop
	 * here would reset the hardware between every command and make read-only
	 * commands like `plstate` change the part's state. */
	rf_platform_release();
	rf_owner = NULL;
	core->opened = 0;
	core->initialized = 0;
	core->state = RF_STATE_OFFLINE;
}

rf_state_t rf_core_state(const rf_core_t *core)
{
	return core ? core->state : RF_STATE_FAULT;
}


/* ---------------------------------------------------------------- CLI-facing core operations */

static int core_opened(rf_core_t *core)
{
	return core && core->opened && core->state != RF_STATE_FAULT
	       ? RF_ERR_OK : RF_ERR_INVALID_STATE;
}

int rf_core_selftest(void)
{
	return rf_selftest();
}

int rf_core_get_pl_snapshot(rf_core_t *core, rf_pl_snapshot_t *snapshot)
{
	int ret;
	if (!snapshot)
		return RF_ERR_INVALID_ARGUMENT;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	/* A window that never advances is a MEASUREMENT of the observation channel,
	 * not a hardware fault: saying so is the whole purpose of a `status` report,
	 * and a diagnostic that refuses to describe the diagnostic condition is
	 * useless.  Keep the word, clear the fresh flag, and let the caller decide.
	 * The writers are unaffected -- `rf_core_set_phy_mode_safe` goes through
	 * phy_mode_window_ready, which fails closed on this same condition.  A
	 * sentinel read is still a fault: there is no word to report. */
	ret = status_read_fresh(&snapshot->status);
	if (ret == RF_ERR_TIMEOUT)
		snapshot->status_fresh = false;
	else if (ret != RF_ERR_OK)
		return core_fail(core, ret);
	else
		snapshot->status_fresh = true;
	snapshot->phy_mode = rf_reg_read(RF_REG_PHY_MODE);
	snapshot->idelay_en = rf_reg_read(RF_REG_IDELAY_EN);
	snapshot->idelay_tap = rf_reg_read(RF_REG_IDELAY_TAP);
	snapshot->phase_inc = rf_reg_read(RF_REG_PHASE_INC);
	snapshot->wave_sel = rf_reg_read(RF_REG_WAVE_SEL);
	snapshot->src_cfg = rf_reg_read(RF_REG_SRC_CFG);
	snapshot->smp_len = rf_reg_read(RF_REG_SMP_LEN);
	snapshot->smp_pos = rf_reg_read(RF_REG_SMP_POS);
	snapshot->cap_cfg = rf_reg_read(RF_REG_CAP_CFG);
	snapshot->cap_ratio = rf_reg_read(RF_REG_CAP_RATIO);
	snapshot->cap_len = rf_reg_read(RF_REG_CAP_LEN);
	snapshot->cap_status = rf_reg_read(RF_REG_CAP_STATUS);
	if (snapshot->phy_mode > 1u ||
	    snapshot->idelay_en == 0xDEADBEEFu ||
	    snapshot->idelay_tap == 0xDEADBEEFu ||
	    snapshot->phase_inc == 0xDEADBEEFu ||
	    snapshot->wave_sel == 0xDEADBEEFu ||
	    snapshot->src_cfg == 0xDEADBEEFu ||
	    snapshot->smp_len == 0xDEADBEEFu ||
	    snapshot->smp_pos == 0xDEADBEEFu ||
	    snapshot->cap_cfg == 0xDEADBEEFu ||
	    snapshot->cap_ratio == 0xDEADBEEFu ||
	    snapshot->cap_len == 0xDEADBEEFu ||
	    snapshot->cap_status == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	snapshot->status_win_cycles = (uint16_t)RF_STATUS_WIN_CYCLES(snapshot->status);
	snapshot->status_win_log2 = (uint8_t)RF_STATUS_WIN_LOG2(snapshot->status);
	snapshot->status_win_seq = (uint8_t)RF_STATUS_WIN_SEQ(snapshot->status);
	snapshot->status_clk_seen = (uint8_t)RF_STATUS_CLK_SEEN(snapshot->status);
	snapshot->status_win_done = (uint8_t)RF_STATUS_WIN_DONE(snapshot->status);
	snapshot->status_win_active = (uint8_t)RF_STATUS_WIN_ACTIVE(snapshot->status);
	snapshot->status_rst_source_ok = (uint8_t)RF_STATUS_RSO(snapshot->status);
	snapshot->data_clk_base_hz = RF_STATUS_F_DATA_CLK_HZ(snapshot->status);
	snapshot->data_clk_hz = rf_f_data_clk_resolve(
		snapshot->status,
		snapshot->phy_mode ? 2ull * RF_SAMPLE_RATE_HZ
		                   : 4ull * RF_SAMPLE_RATE_HZ,
		&snapshot->data_clk_alias_index);
	snapshot->data_clk_alias_step_hz = rf_f_data_clk_alias_step(snapshot->status);
	return RF_ERR_OK;
}

static int read_interface_regs(rf_core_t *core, rf_interface_status_t *out)
{
    uint8_t r6, r7, r10, r11, r12;

    if (!core || !out)
        return RF_ERR_INVALID_ARGUMENT;
    if (!core->opened || !core->initialized || core->state == RF_STATE_FAULT)
        return RF_ERR_INVALID_STATE;
    memset(out, 0, sizeof *out);
    if (core_spi_read(core, REG_RX_CLOCK_DATA_DELAY, &r6) != RF_ERR_OK ||
        core_spi_read(core, REG_TX_CLOCK_DATA_DELAY, &r7) != RF_ERR_OK ||
        core_spi_read(core, REG_PARALLEL_PORT_CONF_1, &r10) != RF_ERR_OK ||
        core_spi_read(core, REG_PARALLEL_PORT_CONF_2, &r11) != RF_ERR_OK ||
        core_spi_read(core, REG_PARALLEL_PORT_CONF_3, &r12) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    out->valid = true;
    out->reg_006 = r6; out->reg_007 = r7;
    out->reg_010 = r10; out->reg_011 = r11; out->reg_012 = r12;
    out->rx_data_delay = (uint8_t)(r6 & 0x0Fu);
    out->data_clk_delay = (uint8_t)((r6 >> 4) & 0x0Fu);
    out->tx_data_delay = (uint8_t)(r7 & 0x0Fu);
    out->fb_clk_delay = (uint8_t)((r7 >> 4) & 0x0Fu);
    out->pp1 = r10; out->pp2 = r11;
    out->lvds_mode = (r12 & (1u << 4)) != 0u;
    out->single_data_rate = (r12 & (1u << 5)) != 0u;
    out->half_duplex_mode = (r12 & (1u << 3)) != 0u;
    out->single_port_mode = (r12 & (1u << 2)) != 0u;
    out->full_port = (r12 & (1u << 1)) != 0u;
    out->swap_ports = (r12 & (1u << 6)) != 0u;
    out->fdd_alt_word_order = (r11 & (1u << 7)) != 0u;
    out->fdd_rx_rate_2tx_rate = (r12 & (1u << 7)) != 0u;
    return RF_ERR_OK;
}

int rf_core_read_interface_status(rf_core_t *core, rf_interface_status_t *out)
{
    return read_interface_regs(core, out);
}

int rf_core_set_rx_data_delay(rf_core_t *core, uint8_t value,
                              rf_interface_status_t *before,
                              rf_interface_status_t *after)
{
    rf_interface_status_t b, a;
    uint32_t src, cap;
    uint8_t new6;
    int rc;

    if (!core)
        return RF_ERR_INVALID_ARGUMENT;
    if (value > 15u)
        return RF_ERR_INVALID_ARGUMENT;
    if (!core->opened || core->state != RF_STATE_READY)
        return RF_ERR_INVALID_STATE;
    /* Original value must be readable before the first write. */
    rc = read_interface_regs(core, &b);
    if (rc != RF_ERR_OK)
        return rc;
    /* Same quiet guard as every other tap/configuration write. */
    src = rf_reg_read(RF_REG_SRC_CFG);
    cap = rf_reg_read(RF_REG_CAP_CFG);
    if (src == 0xDEADBEEFu || cap == 0xDEADBEEFu)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    if ((src & RF_SRC_SEL) || (cap & RF_CAP_ARM))
        return RF_ERR_INVALID_STATE;
    new6 = (uint8_t)((b.reg_006 & 0xF0u) | value);
    if (ad9361_spi_write(&core->spi, REG_RX_CLOCK_DATA_DELAY, new6) != 0)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    rc = read_interface_regs(core, &a);
    if (rc != RF_ERR_OK)
        return core_fail(core, rc);
    if (a.reg_006 != new6 ||
        a.reg_007 != b.reg_007 || a.reg_010 != b.reg_010 ||
        a.reg_011 != b.reg_011 || a.reg_012 != b.reg_012 ||
        a.rx_data_delay != value ||
        a.data_clk_delay != b.data_clk_delay)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    if (before) *before = b;
    if (after) *after = a;
    return RF_ERR_OK;
}

int rf_core_set_idelay_lanes(rf_core_t *core, uint8_t mask, uint8_t tap,
                             uint32_t *en_readback, uint32_t *tap_readback)
{
    uint32_t src, cap, rb_en, rb_tap;

    if (!core || mask == 0u || (mask & ~0x7Fu) != 0u || tap > 31u)
        return RF_ERR_INVALID_ARGUMENT;
    if (!core->opened || core->state != RF_STATE_READY)
        return RF_ERR_INVALID_STATE;
    /* Refuse before the first write if the data path is not quiet.  Both are
     * ordinary PL reads; a sentinel is a hardware error, not a state guess. */
    src = rf_reg_read(RF_REG_SRC_CFG);
    cap = rf_reg_read(RF_REG_CAP_CFG);
    if (src == 0xDEADBEEFu || cap == 0xDEADBEEFu)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    if ((src & RF_SRC_SEL) || (cap & RF_CAP_ARM))
        return RF_ERR_INVALID_STATE;
    /* LD is a level.  Closing all LDs first is mandatory: writing a new common
     * tap while 0x7F is still asserted would load every lane, not just the
     * requested one.  Lanes not enabled retain their previous loaded value. */
    rf_reg_write(RF_REG_IDELAY_EN, 0u);
    rf_reg_write(RF_REG_IDELAY_TAP, tap);
    rf_reg_write(RF_REG_IDELAY_EN, mask);
    rb_en = rf_reg_read(RF_REG_IDELAY_EN);
    rb_tap = rf_reg_read(RF_REG_IDELAY_TAP);
    if (rb_en == 0xDEADBEEFu || rb_tap == 0xDEADBEEFu ||
        rb_en != mask || rb_tap != tap)
        return core_fail(core, RF_ERR_HARDWARE_IO);
    if (en_readback)
        *en_readback = rb_en;
    if (tap_readback)
        *tap_readback = rb_tap;
    return RF_ERR_OK;
}

int rf_core_configure_pl(rf_core_t *core, uint8_t phy_mode_1r1t,
                         uint8_t idelay_en, uint8_t idelay_tap,
                         uint32_t phase_inc, rf_pl_snapshot_t *snapshot)
{
	int ret;
	if (!core || phy_mode_1r1t > 1u)
		return RF_ERR_INVALID_ARGUMENT;
	ret = rf_core_set_phy_mode_safe(core, phy_mode_1r1t, NULL);
	if (ret != RF_ERR_OK)
		return ret;
	rf_reg_write(RF_REG_IDELAY_EN, idelay_en ? 0x7Fu : 0u);
	rf_reg_write(RF_REG_IDELAY_TAP, idelay_tap);
	rf_reg_write(RF_REG_PHASE_INC, phase_inc);
	if (rf_reg_read(RF_REG_IDELAY_EN) != (idelay_en ? 0x7Fu : 0u) ||
	    rf_reg_read(RF_REG_IDELAY_TAP) != idelay_tap ||
	    rf_reg_read(RF_REG_PHASE_INC) != phase_inc)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	return snapshot ? rf_core_get_pl_snapshot(core, snapshot) : RF_ERR_OK;
}

int rf_core_set_phase_inc(rf_core_t *core, uint32_t phase_inc,
                          uint32_t *readback)
{
	uint32_t rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	rf_reg_write(RF_REG_PHASE_INC, phase_inc & 0xFFFu);
	rb = rf_reg_read(RF_REG_PHASE_INC);
	if (rb == 0xDEADBEEFu || rb != (phase_inc & 0xFFFu))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_set_wave_sel(rf_core_t *core, uint8_t wave_sel, uint32_t *readback)
{
	uint32_t rb;
	if (core_opened(core) != RF_ERR_OK || wave_sel > 1u)
		return wave_sel > 1u ? RF_ERR_INVALID_ARGUMENT : RF_ERR_INVALID_STATE;
	rf_reg_write(RF_REG_WAVE_SEL, wave_sel);
	rb = rf_reg_read(RF_REG_WAVE_SEL);
	if (rb == 0xDEADBEEFu || (rb & 1u) != wave_sel)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}


int rf_core_set_tx_source(rf_core_t *core, bool buffer, uint32_t *readback)
{
	uint32_t cfg, rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	cfg = buffer ? (cfg | RF_SRC_SEL) : (cfg & ~RF_SRC_SEL);
	/* Level write, as baseline's `srcsel`: src_sel is a direct input, and the
	 * commit bit is left where it is rather than re-latching the length. */
	rf_reg_write(RF_REG_SRC_CFG, cfg);
	rb = rf_reg_read(RF_REG_SRC_CFG);
	if (rb == 0xDEADBEEFu ||
	    (rb & RF_SRC_SEL) != (buffer ? RF_SRC_SEL : 0u))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_configure_tx_path(rf_core_t *core, size_t count, bool wrap,
                              uint32_t *readback)
{
	uint32_t cfg, rb, len_rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	if (count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	rf_reg_write(RF_REG_SMP_LEN, (uint32_t)count);
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	cfg = wrap ? (cfg | RF_SMP_WRAP) : (cfg & ~RF_SMP_WRAP);
	rf_reg_write(RF_REG_SRC_CFG,
	             commit_toggle(cfg, cfg & (RF_SRC_SEL | RF_SMP_WRAP), RF_SMP_COMMIT));
	rb = rf_reg_read(RF_REG_SRC_CFG);
	len_rb = rf_reg_read(RF_REG_SMP_LEN);
	if (rb == 0xDEADBEEFu || len_rb == 0xDEADBEEFu ||
	    (len_rb & 0x1FFFu) != count ||
	    (rb & RF_SMP_WRAP) != (wrap ? RF_SMP_WRAP : 0u))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->tx_count = count;
	core->tx_wrap = wrap;
	core->tx_prepared = 1;
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_get_tx_path(rf_core_t *core, uint32_t *src_cfg,
                        uint32_t *length, uint32_t *position)
{
	uint32_t cfg, len, pos;
	if (core_opened(core) != RF_ERR_OK || !src_cfg || !length || !position)
		return !core || !core->opened ? RF_ERR_INVALID_STATE : RF_ERR_INVALID_ARGUMENT;
	cfg = rf_reg_read(RF_REG_SRC_CFG);
	len = rf_reg_read(RF_REG_SMP_LEN);
	pos = rf_reg_read(RF_REG_SMP_POS);
	/* Check the RAW words before masking: 0xDEADBEEF & 0x1FFF is a plausible
	 * length/position, so a masked comparison could never see the sentinel.  A
	 * failed read is a hardware fault, not a status value, so this faults the
	 * core exactly as rf_core_get_pl_snapshot does. */
	if (cfg == 0xDEADBEEFu || len == 0xDEADBEEFu || pos == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	*src_cfg = cfg;
	*length = len & 0x1FFFu;
	*position = pos & 0x1FFFu;
	return RF_ERR_OK;
}

int rf_core_set_rx_config(rf_core_t *core, uint8_t ratio, size_t count,
                          bool continuous, uint32_t *readback)
{
	uint32_t cfg, rb, ratio_rb, len_rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	if (ratio > 4u || count == 0 || count > RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	cfg = rf_reg_read(RF_REG_CAP_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	rf_reg_write(RF_REG_CAP_RATIO, ratio);
	rf_reg_write(RF_REG_CAP_LEN, (uint32_t)count);
	/* Preserve cfg_arm, commit on a TOGGLE (baseline's `capcfg`). */
	cfg = continuous ? (cfg | RF_CAP_CONT) : (cfg & ~RF_CAP_CONT);
	rf_reg_write(RF_REG_CAP_CFG,
	             commit_toggle(cfg, cfg & (RF_CAP_ARM | RF_CAP_CONT), RF_CAP_COMMIT));
	rb = rf_reg_read(RF_REG_CAP_CFG);
	ratio_rb = rf_reg_read(RF_REG_CAP_RATIO);
	len_rb = rf_reg_read(RF_REG_CAP_LEN);
	if (rb == 0xDEADBEEFu || ratio_rb == 0xDEADBEEFu || len_rb == 0xDEADBEEFu ||
	    (ratio_rb & 7u) != ratio ||
	    (len_rb & 0x1FFFu) != count ||
	    (rb & RF_CAP_CONT) != (continuous ? RF_CAP_CONT : 0u))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	core->rx_count = count;
	core->rx_continuous = continuous;
	core->rx_prepared = 1;
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_set_rx_arm(rf_core_t *core, bool arm, uint32_t *readback)
{
	uint32_t cfg, rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	cfg = rf_reg_read(RF_REG_CAP_CFG);
	if (cfg == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	cfg = arm ? (cfg | RF_CAP_ARM) : (cfg & ~RF_CAP_ARM);
	/* Level write, as baseline's `caparm`: cfg_arm is a direct input. */
	rf_reg_write(RF_REG_CAP_CFG, cfg);
	rb = rf_reg_read(RF_REG_CAP_CFG);
	if (rb == 0xDEADBEEFu ||
	    (rb & RF_CAP_ARM) != (arm ? RF_CAP_ARM : 0u))
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_get_rx_path(rf_core_t *core, uint32_t *config, uint32_t *status)
{
	if (core_opened(core) != RF_ERR_OK || !config || !status)
		return !core || !core->opened ? RF_ERR_INVALID_STATE : RF_ERR_INVALID_ARGUMENT;
	*config = rf_reg_read(RF_REG_CAP_CFG);
	*status = rf_reg_read(RF_REG_CAP_STATUS);
	if (*config == 0xDEADBEEFu || *status == 0xDEADBEEFu)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	return RF_ERR_OK;
}


int rf_core_write_tx_word(rf_core_t *core, size_t index, uint32_t word,
                          uint32_t *readback)
{
	uint32_t rb;
	if (core_opened(core) != RF_ERR_OK)
		return RF_ERR_INVALID_STATE;
	if (index >= RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	rf_bram_write(RF_BRAM_TX_BASE, (uint32_t)index, word);
	rb = rf_bram_read(RF_BRAM_TX_BASE, (uint32_t)index);
	if (rb != word)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_read_tx_word(rf_core_t *core, size_t index, uint32_t *word)
{
	if (core_opened(core) != RF_ERR_OK || !word)
		return !core || !core->opened ? RF_ERR_INVALID_STATE : RF_ERR_INVALID_ARGUMENT;
	if (index >= RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	*word = rf_bram_read(RF_BRAM_TX_BASE, (uint32_t)index);
	return RF_ERR_OK;
}

int rf_core_read_rx_word(rf_core_t *core, size_t index, uint32_t *word)
{
	if (core_opened(core) != RF_ERR_OK || !word)
		return !core || !core->opened ? RF_ERR_INVALID_STATE : RF_ERR_INVALID_ARGUMENT;
	if (index >= RF_BRAM_WORDS)
		return RF_ERR_INVALID_ARGUMENT;
	*word = rf_bram_read(RF_BRAM_RX_BASE, (uint32_t)index);
	return RF_ERR_OK;
}

int rf_core_spi_read(rf_core_t *core, uint32_t address, uint8_t *value)
{
	return core_spi_read(core, address, value);
}

int rf_core_spi_write(rf_core_t *core, uint32_t address, uint8_t value,
                      uint8_t *readback)
{
	uint8_t rb;
	if (core_opened(core) != RF_ERR_OK || address > 0x3FFu)
		return address > 0x3FFu ? RF_ERR_INVALID_ARGUMENT : RF_ERR_INVALID_STATE;
	if (ad9361_spi_write(&core->spi, address, value) != 0)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (core_spi_read(core, address, &rb) != RF_ERR_OK)
		return core_fail(core, RF_ERR_HARDWARE_IO);
	if (readback)
		*readback = rb;
	return RF_ERR_OK;
}

int rf_core_get_readback(rf_core_t *core, rf_readback_t *readback)
{
	return core_fill_readback(core, readback);
}

static int gain_observation_path_idle(void)
{
    uint32_t src_cfg = rf_reg_read(RF_REG_SRC_CFG);
    uint32_t cap_cfg = rf_reg_read(RF_REG_CAP_CFG);

    if (src_cfg == 0xDEADBEEFu || cap_cfg == 0xDEADBEEFu)
        return RF_ERR_HARDWARE_IO;
    /* DONE does not make a raised ARM safe to observe. No state-changing getter
     * is used here: observation failures must not fault or stop the core. */
    if ((src_cfg & RF_SRC_SEL) != 0u || (cap_cfg & RF_CAP_ARM) != 0u)
        return RF_ERR_INVALID_STATE;
    return RF_ERR_OK;
}

int rf_core_get_gain_observation(rf_core_t *core, rf_gain_observation_t *observation)
{
    rf_gain_observation_t raw;
    uint8_t index_before, index_after, digital_raw;
    uint8_t rx_after, mode_after, table_after;
    int rc;

    if (observation)
        memset(observation, 0, sizeof *observation);
    if (!core || !observation)
        return RF_ERR_INVALID_ARGUMENT;
    if (!core->opened || !core->initialized || core->state != RF_STATE_READY)
        return RF_ERR_INVALID_STATE;
#ifndef PLATFORM_HOST
    if (!core->vendor_phy)
        return RF_ERR_INVALID_STATE;
#endif
    rc = gain_observation_path_idle();
    if (rc != RF_ERR_OK)
        return rc;
    memset(&raw, 0, sizeof raw);
    if (core_spi_read(core, REG_RX_ENABLE_FILTER_CTRL, &raw.rx_enable_raw) != RF_ERR_OK ||
        core_spi_read(core, REG_AGC_CONFIG_1, &raw.agc_config1_raw) != RF_ERR_OK ||
        core_spi_read(core, REG_AGC_CONFIG_2, &raw.agc_config2_raw) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    if ((raw.rx_enable_raw & RX_CHANNEL_ENABLE(RX_1)) == 0u ||
        (raw.agc_config2_raw & AGC_USE_FULL_GAIN_TABLE) == 0u)
        return RF_ERR_INVALID_STATE;
    raw.mode_rx1 = (uint8_t)((raw.agc_config1_raw >> RX1_GAIN_CTRL_SHIFT) & RX_GAIN_CTL_MASK);
    /* Direct checked SPI reads avoid the vendor full-table getter's unchecked
     * gain-table indexing and its split-table register writes. No dB conversion. */
    if (core_spi_read(core, REG_GAIN_RX1, &index_before) != RF_ERR_OK ||
        core_spi_read(core, REG_GAIN_RX1 + 2u, &digital_raw) != RF_ERR_OK ||
        core_spi_read(core, REG_GAIN_RX1, &index_after) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    raw.gain_index_before = (uint8_t)(index_before & FULL_TABLE_GAIN_INDEX(~0u));
    raw.gain_index_after = (uint8_t)(index_after & FULL_TABLE_GAIN_INDEX(~0u));
    raw.digital_gain_code = (uint8_t)(digital_raw & DIGITAL_GAIN_RX(~0u));
    if (raw.gain_index_before > 76u || raw.gain_index_after > 76u)
        return RF_ERR_HARDWARE_IO;
    /* Bracket the observation with support checks. An index change is reported,
     * but a change to unsupported channel/table state or to another AGC mode
     * cannot be represented by this single set of mode fields. */
    if (core_spi_read(core, REG_RX_ENABLE_FILTER_CTRL, &rx_after) != RF_ERR_OK ||
        core_spi_read(core, REG_AGC_CONFIG_1, &mode_after) != RF_ERR_OK ||
        core_spi_read(core, REG_AGC_CONFIG_2, &table_after) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    if ((rx_after & RX_CHANNEL_ENABLE(RX_1)) == 0u ||
        (table_after & AGC_USE_FULL_GAIN_TABLE) == 0u)
        return RF_ERR_INVALID_STATE;
    if (((mode_after >> RX1_GAIN_CTRL_SHIFT) & RX_GAIN_CTL_MASK) != raw.mode_rx1)
        return RF_ERR_HARDWARE_IO;
    rc = gain_observation_path_idle();
    if (rc != RF_ERR_OK)
        return rc;
    raw.index_stable = raw.gain_index_before == raw.gain_index_after;
    *observation = raw;
    return RF_ERR_OK;
}

int rf_core_read_rx1_gain_index(rf_core_t *core, uint8_t *out)
{
    uint8_t raw = 0;
    uint8_t index;

    if (!core || !out)
        return RF_ERR_INVALID_ARGUMENT;
    /* "Opened but not initialized" is a real state -- the part has not been
     * configured, so an index read off it would be a number a reader could take for
     * a measurement.  Deliberately NOT gated on the idle path: a raised CAP_ARM is
     * the state this entry point exists for. */
    if (!core->opened || !core->initialized)
        return RF_ERR_INVALID_STATE;
    if (core_spi_read(core, REG_GAIN_RX1, &raw) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    index = (uint8_t)(raw & FULL_TABLE_GAIN_INDEX(~0u));
    if (index > 76u)
        return RF_ERR_HARDWARE_IO;
    *out = index;
    return RF_ERR_OK;
}

#ifdef PLATFORM_HOST
int rf_core_host_set_vendor_readback_fault(rf_core_t *core, int on)
{
	if (!core)
		return RF_ERR_INVALID_ARGUMENT;
	core->fake_vendor.readback_fault = on != 0;
	return RF_ERR_OK;
}
#endif

int rf_core_get_headroom_readback(rf_core_t *core,rf_headroom_readback_t *out){int rc;
 if(!out)return RF_ERR_INVALID_ARGUMENT;
 memset(out,0,sizeof *out);
 rc=rf_core_get_readback(core,&out->rf);if(rc!=RF_ERR_OK)return rc;
#ifdef PLATFORM_HOST
 if(core->fake_vendor.headroom_tx2_fault==1)return RF_ERR_HARDWARE_IO;
 out->tx1_attenuation_mdB=core->fake_vendor.attenuation_mdB;
 out->tx2_attenuation_mdB=core->fake_vendor.attenuation_mdB+(core->fake_vendor.headroom_tx2_fault==2?250:0);
#else
 if(!core->vendor_phy||ad9361_get_tx_attenuation(core->vendor_phy,0,&out->tx1_attenuation_mdB)!=0||ad9361_get_tx_attenuation(core->vendor_phy,1,&out->tx2_attenuation_mdB)!=0)return RF_ERR_HARDWARE_IO;
#endif
 return rf_core_get_pl_snapshot(core,&out->pl);
}
#ifdef PLATFORM_HOST
int rf_core_host_set_headroom_tx2_fault(rf_core_t *core,int on){if(!core)return RF_ERR_INVALID_ARGUMENT;core->fake_vendor.headroom_tx2_fault=on;return RF_ERR_OK;}
#endif

bool rf_core_initialization_attempted(const rf_core_t *core)
{
    return core && core->initialization_attempted;
}
