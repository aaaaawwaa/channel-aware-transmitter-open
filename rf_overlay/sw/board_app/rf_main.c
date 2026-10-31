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

/* Criterion 1.5's ceiling, and the noise level it was derived from (E19).
 *
 * The criterion asks "is there no DATA_CLK yet?" and the answer has to come
 * from the f_DATA_CLK MAGNITUDE -- see the long note at `plcfg` for why the
 * original sticky-bit test could not answer it.  RF_NOCLOCK_MAX_HZ is the
 * acceptance ceiling: at or above it, the channel is seeing something it
 * cannot explain and Step 3 must not be trusted.
 *
 * RF_NOISE_OBSERVED_MAX_HZ is not a second threshold -- it is the highest
 * value actually MEASURED on this board with the BBPLL unprogrammed, across 25
 * windows: 0.023 to 0.266 MHz, varying window to window, which is what noise
 * looks like and what a locked clock does not.  It is an observed maximum, not
 * a bound -- a later reading may exceed it, and that is exactly why the
 * acceptance ceiling is set well above it rather than at it.  A reading between
 * the two is reported as a grey zone instead of quietly accepted, because that
 * is where a real problem would hide.  The ceiling is 3.8x above the highest
 * observed noise and 1/30th of the 30 MHz (= fs/2, LVDS DDR) that MUST appear
 * once the BBPLL is programmed -- 113x between noise and signal. */
#define RF_NOCLOCK_MAX_HZ          1000000ull
#define RF_NOISE_OBSERVED_MAX_HZ    266000ull

/* ---------------------------------------------------------------- PL STATUS */

/* Read the read-only STATUS word and wait for a fresh measurement window.
 *
 * The window is self-running in hardware (2^15 FCLK0 cycles = 655.36 us) and
 * publishes a rolling sequence number, so "is this reading fresh" is answered
 * by win_seq changing -- not by software timing, which cannot work here: one
 * mmap read costs ~25 us while DATA_CLK is ~33 ns, so software can only ever
 * observe "it is toggling", never count edges.
 *
 * *stale is set when the window never advanced.  It has to be reported
 * out-of-band because there is no sentinel value available: every 32-bit pattern
 * is a legal status word, so a caller comparing the return against a sentinel
 * would classify some real reading as a time-out, and a caller not comparing
 * would print a stuck window exactly like a live one.  Task 5's C3 adjudicates
 * fs against f_DATA_CLK by parsing this output and cannot tell the two apart
 * either, so the distinction has to be made here or it is lost. */
static uint32_t status_read_fresh(int *stale)
{
	uint32_t w0 = rf_reg_read(RF_REG_STATUS);
	uint32_t seq0 = RF_STATUS_WIN_SEQ(w0);
	int i;

	*stale = 0;
	for (i = 0; i < 2000; i++) {
		uint32_t w = rf_reg_read(RF_REG_STATUS);
		if (RF_STATUS_WIN_SEQ(w) != seq0 && RF_STATUS_WIN_DONE(w))
			return w;
		udelay(100);                    /* 2000 * 100us = 200 ms max */
	}
	/* Never advanced.  Return the word the caller asked about, but say so. */
	*stale = 1;
	return w0;
}

static void status_report(const char *label, uint64_t expect_hz)
{
	int      stale = 0, n_used = 0;
	uint32_t w = status_read_fresh(&stale);
	uint64_t f;

	printf("--- PL STATUS (%s) ---\n", label);
	printf("  raw        = 0x%08X\n", w);
	printf("  clk_seen   = %u   (sticky: a DATA_CLK edge has been seen)\n",
	       RF_STATUS_CLK_SEEN(w));
	printf("  win_done   = %u\n", RF_STATUS_WIN_DONE(w));
	printf("  win_seq    = %u\n", RF_STATUS_WIN_SEQ(w));
	printf("  win_active = %u\n", RF_STATUS_WIN_ACTIVE(w));
	printf("  rst_source_ok = %u  (LIVENESS of rf_tx_source's own reset chain, "
	       "NOT phy readiness -- see the plan's D1)\n", RF_STATUS_RSO(w));
	printf("  win_cycles = %u  (over 2^%u FCLK0 cycles)\n",
	       RF_STATUS_WIN_CYCLES(w), RF_STATUS_WIN_LOG2(w));
	/* Three mutually exclusive outcomes, and only the last one prints a rate.
	 *
	 * The order matters.  "No clock" is checked FIRST because a window that
	 * cannot advance for want of a clock to count is the expected pre-init
	 * state -- Step 1's negative criterion asserts exactly that state -- and
	 * calling it STALE would report a fault where there is none.  The
	 * distinction that matters is: no clock at all (a measurement, and the
	 * f_DATA_CLK_HZ macro would yield 0 anyway) versus a word that claims a
	 * clock and a completed window while win_seq never moved across 200 ms of
	 * polling (the observation channel failing).
	 *
	 * Neither of the first two prints a number, so a line scanner -- Task 5's
	 * read_f_data_clk_hz parses this output to adjudicate fs against
	 * f_DATA_CLK -- cannot pick up a stale rate and adjudicate on it.  Both
	 * land on its unparseable-reply path, which is already fail-safe. */
	if (RF_STATUS_WIN_CYCLES(w) == 0 || !RF_STATUS_CLK_SEEN(w)) {
		printf("  f_DATA_CLK = NO CLOCK\n");
	} else if (stale) {
		printf("  STALE (window never advanced): the status word reports a "
		       "clock and a completed window, but win_seq did not change in "
		       "200 ms -- the observation channel is not running, so there is "
		       "no current measurement to report\n");
		printf("  f_DATA_CLK = STALE\n");
	} else {
		int      L     = (int)RF_STATUS_WIN_LOG2(w);
		uint64_t step  = rf_f_data_clk_alias_step(w);
		uint64_t base  = (((uint64_t)RF_STATUS_WIN_CYCLES(w)) * 50000000ull) >> L;
		f = rf_f_data_clk_resolve(w, expect_hz, &n_used);
		printf("  aliases    = %u is one of {%.3f, %.3f, %.3f, %.3f} MHz "
		       "(+%.3f MHz each: 16-bit counter, wraps)\n",
		       RF_STATUS_WIN_CYCLES(w),
		       (double)base / 1e6,
		       (double)(base + step) / 1e6,
		       (double)(base + 2 * step) / 1e6,
		       (double)(base + 3 * step) / 1e6,
		       (double)step / 1e6);
		if (expect_hz)
			printf("  selected   = n=%d, nearest the %llu Hz implied by the "
			       "configured interface (corroboration, not independent "
			       "evidence)\n", n_used,
			       (unsigned long long)expect_hz);
		printf("  f_DATA_CLK = %llu Hz  (%.3f MHz)\n",
		       (unsigned long long)f, (double)f / 1e6);
	}
}

/* ------------------------------------------------------------------ driver */

static struct ad9361_rf_phy *phy;

static AD9361_InitParam rf_init_param;

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
static void rf_init_param_fill(void)
{
	memset(&rf_init_param, 0, sizeof(rf_init_param));

	rf_init_param.dev_sel          = ID_AD9361;
	rf_init_param.id_no            = 0;
	rf_init_param.reference_clk_rate = 40000000UL;   /* 40 MHz on this FMC card */

	/* Base configuration: 1R1T, FDD. */
	rf_init_param.two_rx_two_tx_mode_enable      = 0;
	rf_init_param.one_rx_one_tx_mode_use_rx_num  = 0;
	rf_init_param.one_rx_one_tx_mode_use_tx_num  = 0;
	rf_init_param.frequency_division_duplex_mode_enable = 1;

	/* DC offset tracking -- reference values. */
	rf_init_param.dc_offset_tracking_update_event_mask = 5;
	rf_init_param.dc_offset_attenuation_high_range     = 6;
	rf_init_param.dc_offset_attenuation_low_range      = 5;
	rf_init_param.dc_offset_count_high_range           = 0x28;
	rf_init_param.dc_offset_count_low_range            = 0x32;

	rf_init_param.trx_synthesizer_target_fref_overwrite_hz = MAX_SYNTH_FREF;

	/* LO: 80 MHz, not the reference struct's 2.4 GHz.  The DSOX1102A has a
	 * 100 MHz front end, so a 1 GHz carrier is invisible to the only
	 * instrument this round has; 80 MHz is above the AD9361's 70 MHz floor,
	 * visible on the scope, and a frequency the reference example already ran
	 * on this board.  Set here rather than by a later set_tx_lo_freq() call so
	 * the initialisation and the operating point are the same number. */
	rf_init_param.rx_synthesizer_frequency_hz = 80000000UL;
	rf_init_param.tx_synthesizer_frequency_hz = 80000000UL;
	rf_init_param.tx_lo_powerdown_managed_enable = 1;

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
			rf_init_param.rx_path_clock_frequencies[i] = rx_clks[i];
			rf_init_param.tx_path_clock_frequencies[i] = tx_clks[i];
		}
	}

	/* RF bandwidth 30 MHz.  Note the reference STRUCT says 20 MHz and then
	 * calls ad9361_set_tx_rf_bandwidth(phy, 30000000) afterwards -- 30 MHz is
	 * what actually applies there, so 30 MHz is what we put here. */
	rf_init_param.rf_rx_bandwidth_hz = 30000000UL;
	rf_init_param.rf_tx_bandwidth_hz = 30000000UL;

	/* TX attenuation: 0 mdB for the B-layer look at LO leakage; the C3 sweep
	 * restores 10000 -> 30000 later. */
	rf_init_param.tx_attenuation_mdB = 0;

	rf_init_param.dcxo_coarse_and_fine_tune[0] = 8;
	rf_init_param.dcxo_coarse_and_fine_tune[1] = 5920;

	/* RX gain control: slow-attack AGC on both channels (reference values). */
	rf_init_param.gc_rx1_mode = 2;   /* RF_GAIN_SLOWATTACK_AGC */
	rf_init_param.gc_rx2_mode = 2;
	rf_init_param.gc_adc_large_overload_thresh = 58;
	rf_init_param.gc_adc_ovr_sample_size       = 4;
	rf_init_param.gc_adc_small_overload_thresh = 47;
	rf_init_param.gc_dec_pow_measurement_duration = 8192;
	rf_init_param.gc_dig_gain_enable           = 0;
	rf_init_param.gc_lmt_overload_high_thresh  = 800;
	rf_init_param.gc_lmt_overload_low_thresh   = 704;
	rf_init_param.gc_low_power_thresh          = 24;
	rf_init_param.gc_max_dig_gain              = 15;

	rf_init_param.mgc_dec_gain_step = 2;
	rf_init_param.mgc_inc_gain_step = 2;

	rf_init_param.agc_adc_large_overload_exceed_counter = 10;
	rf_init_param.agc_adc_large_overload_inc_steps      = 2;
	rf_init_param.agc_adc_small_overload_exceed_counter = 10;
	rf_init_param.agc_dig_gain_step_size                = 4;
	rf_init_param.agc_dig_saturation_exceed_counter     = 3;
	rf_init_param.agc_gain_update_interval_us           = 1000;
	rf_init_param.agc_inner_thresh_high                 = 10;
	rf_init_param.agc_inner_thresh_high_dec_steps       = 1;
	rf_init_param.agc_inner_thresh_low                  = 12;
	rf_init_param.agc_inner_thresh_low_inc_steps        = 1;
	rf_init_param.agc_lmt_overload_large_exceed_counter = 10;
	rf_init_param.agc_lmt_overload_large_inc_steps      = 2;
	rf_init_param.agc_lmt_overload_small_exceed_counter = 10;
	rf_init_param.agc_outer_thresh_high                 = 5;
	rf_init_param.agc_outer_thresh_high_dec_steps       = 2;
	rf_init_param.agc_outer_thresh_low                  = 18;
	rf_init_param.agc_outer_thresh_low_inc_steps        = 2;
	rf_init_param.agc_attack_delay_extra_margin_us      = 1;

	rf_init_param.fagc_dec_pow_measuremnt_duration = 64;
	rf_init_param.fagc_state_wait_time_ns          = 260;
	rf_init_param.fagc_lp_thresh_increment_time    = 5;
	rf_init_param.fagc_lp_thresh_increment_steps   = 1;
	rf_init_param.fagc_lock_level_lmt_gain_increase_en = 1;
	rf_init_param.fagc_lock_level_gain_increase_upper_limit = 5;
	rf_init_param.fagc_lpf_final_settling_steps    = 1;
	rf_init_param.fagc_lmt_final_settling_steps    = 1;
	rf_init_param.fagc_final_overrange_count       = 3;

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
	rf_init_param.fagc_use_last_lock_level_for_set_gain_en  = 1;
	rf_init_param.fagc_rst_gla_stronger_sig_thresh_exceeded_en = 1;
	rf_init_param.fagc_optimized_gain_offset                = 5;
	rf_init_param.fagc_rst_gla_stronger_sig_thresh_above_ll = 10;
	rf_init_param.fagc_rst_gla_engergy_lost_sig_thresh_exceeded_en = 1;
	rf_init_param.fagc_rst_gla_engergy_lost_goto_optim_gain_en = 1;
	rf_init_param.fagc_rst_gla_engergy_lost_sig_thresh_below_ll = 10;
	rf_init_param.fagc_energy_lost_stronger_sig_gain_lock_exit_cnt = 8;
	rf_init_param.fagc_rst_gla_large_adc_overload_en        = 1;
	rf_init_param.fagc_rst_gla_large_lmt_overload_en        = 1;
	rf_init_param.fagc_power_measurement_duration_in_state5 = 64;

	rf_init_param.rssi_delay        = 1;
	rf_init_param.rssi_duration     = 1000;
	rf_init_param.rssi_restart_mode = 3;
	rf_init_param.rssi_wait         = 1;

	rf_init_param.aux_adc_decimation        = 256;
	rf_init_param.aux_adc_rate              = 40000000UL;
	rf_init_param.aux_dac_manual_mode_enable = 1;

	rf_init_param.temp_sense_decimation              = 256;
	rf_init_param.temp_sense_measurement_interval_ms = 1000;
	rf_init_param.temp_sense_offset_signed           = -50;   /* example: 0xCE */
	rf_init_param.temp_sense_periodic_measurement_enable = 1;

	rf_init_param.ctrl_outs_enable_mask = 0xFF;

	rf_init_param.pp_tx_swap_enable          = 1;
	rf_init_param.pp_rx_swap_enable          = 1;
	rf_init_param.rx_frame_pulse_mode_enable = 1;
	rf_init_param.lvds_mode_enable           = 1;

	rf_init_param.rx_data_delay     = 4;
	rf_init_param.tx_fb_clock_delay = 7;
	rf_init_param.lvds_bias_mV      = 150;
	rf_init_param.lvds_rx_onchip_termination_enable = 1;
	rf_init_param.lvds_invert1_control = 0xFF;
	rf_init_param.lvds_invert2_control = 0x0F;

	rf_init_param.low_high_gain_threshold_mdB = 37000;
	rf_init_param.high_gain_dB                = 24;
	rf_init_param.tx_mon_delay                = 511;
	rf_init_param.tx_mon_duration             = 8192;
	rf_init_param.tx1_mon_front_end_gain      = 2;
	rf_init_param.tx2_mon_front_end_gain      = 2;
	rf_init_param.tx1_mon_lo_cm               = 48;
	rf_init_param.tx2_mon_lo_cm               = 48;
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

/* -------------------------------------------------------------------- main */

/* The register audit (spec 6.1 Step 5) reads through a zeroed struct
 * spi_device rather than the driver's own, so it needs no phy and can run as
 * its own process.  ad9361_spi_read is public (vendor/ad9361.h) and our
 * platform layer ignores the spi pointer -- spi_write_then_read does
 * `(void)spi;` -- so the zeroed device is a legitimate handle for the one PL
 * SPI master, which the whole SoC shares.  Nothing is torn down on exit.
 *
 * spi_init writes the PL SPI divisor and probes PL STATUS.  It sends nothing
 * to the AD9361, so the part stays exactly as `init` left it -- which is what
 * makes this pass independent of the driver instance without re-initialising
 * the thing under audit.
 *
 * `audit` calls this too, even though `audit-begin` already did.  That is
 * deliberate: BoardCmd.cmd() runs one `sudo board_app <args>` per call, so
 * `audit-begin` is a NEW PROCESS from the first `audit` and cannot have left a
 * path open for it.  Re-opening is the only thing that works in that shape,
 * and it is idempotent.  `audit-begin` keeps its own call so that a dead
 * transport is reported once, up front, instead of once per register. */
static struct spi_device audit_spi;

static int audit_open(void)
{
	if (spi_init(0, 1, 0) != 0) {
		fprintf(stderr,
		        "audit: spi_init failed -- refusing to print a register "
		        "value, because a plausible-looking 00 would be "
		        "indistinguishable from a real read\n");
		return -1;
	}
	memset(&audit_spi, 0, sizeof(audit_spi));
	return 0;
}

/* ------------------------------------------------------- CLI numeric values
 *
 * `init`'s atten/lo values are parsed with these rather than with a bare
 * strtoul.  A bare strtoul accepts three things this interface must not:
 *
 *   - a value with trailing junk, because the endptr is discarded.  `init lo
 *     atten` then parses "atten" as 0, prints "LO set to 0 Hz", and exits 0.
 *   - a leading '-', which strtoul SILENTLY NEGATES.  `init atten -5` becomes
 *     4294967295 mdB, not -5 and not an error.
 *   - an out-of-range magnitude, which saturates to ULONG_MAX and sets errno
 *     rather than failing.
 *
 * All three are the same shape as the rest of this file's defect class: the
 * command reports success for something the operator did not ask for.  They
 * exit 2 instead, the code the rest of the parser already uses for a bad
 * argument -- Task 5 drives `init` from a script, where a typo like any of
 * these would otherwise pass unnoticed.
 *
 * Returns 0 on success, -1 on any of the above. */
static int parse_dec_u64(const char *s, uint64_t *out)
{
	char *end;
	unsigned long long v;

	if (s == NULL || *s == '\0' || *s == '-')
		return -1;
	errno = 0;
	v = strtoull(s, &end, 10);
	if (errno == ERANGE || *end != '\0')
		return -1;
	*out = (uint64_t)v;
	return 0;
}

static void usage(void)
{
	fprintf(stderr,
	        "usage: board_app <subcommand>\n"
	        "  selftest               transport self-check (no vendor code)\n"
	        "  status                 read the PL STATUS word\n"
	        "  plcfg                  spec 6.1 Step 1: PL config + NEGATIVE check\n"
	        "  plstate                read the PL config WITHOUT writing it\n"
	        "                         (plcfg sets phy_mode = 1 before it judges\n"
	        "                         anything, so it cannot be used to ASK what\n"
	        "                         mode the PL is in -- only to set it)\n"
	        "  init [trace.txt] [atten <mdB>] [lo <Hz>] [dump] [2r2t]\n"
	        "                         spec 6.1 Step 2: ad9361_init + parameters\n"
	        "                         atten/lo/dump/2r2t must be given to THIS\n"
	        "                         invocation -- the phy handle does not\n"
	        "                         outlive the process, so there is no\n"
	        "                         separate `atten`/`lo`/`dump` subcommand\n"
	        "                         2r2t starts the part in 2R2T (stage two)\n"
	        "                         instead of the default 1R1T, and writes\n"
	        "                         PL phy_mode = 0 to match.  It stops the\n"
	        "                         tone first and LEAVES phase_inc = 0, so\n"
	        "                         restore it with `phase_inc <n>` before\n"
	        "                         measuring\n"
	        "  2r2t                   PL side of the 1R1T -> 2R2T switch ONLY, and\n"
	        "                         DIAGNOSTIC ONLY: it cannot move the AD9361\n"
	        "                         (the phy handle does not outlive `init`), and\n"
	        "                         it writes phy_mode without being able to stop\n"
	        "                         DATA_CLK, which is the discipline\n"
	        "                         tools/impl1_clock_groups.tcl:12-14 requires.\n"
	        "                         Use `init 2r2t` for the real switch\n"
	        "  phase_inc <n>          set the PL DDS phase increment (12 bit)\n"
	        "  wavesel <0|1>          set the PL DDS waveform: 0 = sine (default),\n"
	        "                         1 = square.  Takes effect immediately, with\n"
	        "                         no clock-stopped window required -- it does\n"
	        "                         not touch phy_mode's register or its\n"
	        "                         discipline.  Use `plstate` to read it back\n"
	        "  idelay <en> <tap>      load the RX pin deserialiser's input delay:\n"
	        "                         en is 7 bit (one bit per IDELAYE2: six data\n"
	        "                         lanes + frame), tap is 5 bit.  en = 0x7F\n"
	        "                         makes every primitive take the tap.  Needed\n"
	        "                         because `plcfg` is the ONLY other writer and\n"
	        "                         it forces phy_mode = 1 first, so a 2R2T run\n"
	        "                         that refuses `plcfg` never loads IDELAY at\n"
	        "                         all.  Touches reg0/reg1 only -- phy_mode is\n"
	        "                         in reg3 and is never written here\n"
	        "  audit-begin            open the raw SPI path for the register audit\n"
	        "  audit <addr>           re-read one AD9361 register (hex address)\n"
	        "  audit-end              close the audit pass\n"
	        "\n"
	        "  --- sample path: TX buffer (RF_BRAM_TX_BASE, 4096 x 32 bit) ---\n"
	        "  srcsel <0|1>           0 = DDS tone (default), 1 = the sample buffer\n"
	        "  smpcfg <len> [wrap]    buffer length in samples (1..4096, default\n"
	        "                         wrap=1) and COMMIT it.  The commit is a\n"
	        "                         toggle, so committing twice works; len/wrap\n"
	        "                         are latched together, never bit by bit\n"
	        "  smpstat [n]            read the reader's position n times.  The\n"
	        "                         SEQUENCE is the evidence: advancing while\n"
	        "                         src_sel=1, still while src_sel=0\n"
	        "  samples iq   <n> <k>   fill with A*cos + j*A*sin, k cycles in n\n"
	        "                         points.  Mixed up to RF this puts ONE\n"
	        "                         sideband at LO + k*60e6/n\n"
	        "  samples real <n> <k>   the same with Q = 0: TWO sidebands,\n"
	        "                         symmetric about LO.  The positive control\n"
	        "                         for 'is Q alive'\n"
	        "  samples zeros <n>      all zero.  The positive control for 'is the\n"
	        "                         buffer path actually driving the TX'\n"
	        "  samples write <word> <value>\n"
	        "  samples dump  <first> <count>\n"
	        "\n"
	        "  --- sample path: RX capture (RF_BRAM_RX_BASE) ---\n"
	        "  capcfg <ratio> <len> [cont]\n"
	        "                         ratio 0..4 -> D = 1,2,4,8,16; len counts\n"
	        "                         DECIMATED samples, so the window is\n"
	        "                         len*D/60e6 seconds.  Both fields commit\n"
	        "                         together\n"
	        "  caparm <0|1>           arm / release.  Releasing rewinds and clears\n"
	        "                         done, so the next arm is a fresh capture\n"
	        "  capstat [n]            read {done, position} n times: arm, wait for\n"
	        "                         done = 1, and only then read the buffer.  A\n"
	        "                         capture read while it is still running is a\n"
	        "                         mixture of two captures that looks ordinary\n"
	        "  capdump <first> <count>\n"
	        "\n"
	        "  --- raw AD9361 register write (same path as audit; no phy) ---\n"
	        "  regwrite <addr> <value> [mask]\n"
	        "                         one SPI write.  WITH a mask it is a\n"
	        "                         read-modify-write of just those bits; WITHOUT\n"
	        "                         one it writes the whole byte.  The mask is not\n"
	        "                         optional decoration -- 0x3F5 also carries\n"
	        "                         OBSERVATION_POINT, CHANNEL and RX_MASK, so\n"
	        "                         regwrite 0x3F5 1 1 and regwrite 0x3F5 1 are\n"
	        "                         different commands\n"
	        "                         regwrite 0x3F5 1 1 turns ON the on-chip TX->RX\n"
	        "                         data-port loopback (0x3F5 bit0); 0 1 turns it\n"
	        "                         off.  It is the only loopback mode that works\n"
	        "                         here: ad9361_bist_loopback(mode 2) calls a\n"
	        "                         stub and silently does nothing\n");
}

int main(int argc, char **argv)
{
	if (argc < 2) { usage(); return 2; }

	/* ---- selftest: MUST come before any vendor call ---- */
	if (!strcmp(argv[1], "selftest"))
		return rf_selftest();

	/* ---- plcfg: spec 6.1 Step 1.  Write the PL config, then read STATUS
	 *      and REQUIRE that it does not report a clock.  The BBPLL is not
	 *      programmed yet, so DATA_CLK does not exist; an observation channel
	 *      that says "clock is running" in this state is BROKEN, and every
	 *      later reading from it would be untrustworthy.  Falsify the
	 *      instrument before suspecting the device.
	 *
	 * THE TEST IS THE f_DATA_CLK MAGNITUDE.  That is a correction, not the
	 * original form (plan defect E19).  The first cut tested
	 * `CLK_SEEN(w) || WIN_CYCLES(w) != 0`, and BOTH of those terms count
	 * NOISE rather than clock:
	 *   - clk_seen is a STICKY bit set by any change in the synchronised gray
	 *     counter (axi_ctrl_v1_0_S00_AXI.v:621, and only reset clears it at
	 *     :618 -- the RTL's own comment at :669 says "sticky");
	 *   - win_cycles counts those same edges.
	 * DATA_CLK arrives on an UNTERMINATED differential pair (rf.xdc:99 gives
	 * rx_clk_in_p/n no PULLTYPE and no DIFF_TERM), so an undriven pair couples
	 * noise and both terms trip on it.  A sticky edge flag answers "is this
	 * line wiggling", not "is there a clock" -- the wrong instrument.
	 *
	 * Measured on this board with the BBPLL unprogrammed: twelve consecutive
	 * windows gave f_DATA_CLK <= 0.252 MHz, against the 30 MHz that MUST
	 * appear once the BBPLL is programmed -- 119x apart, and wildly variable
	 * window to window, which is what noise looks like and what a locked clock
	 * does not.  The channel is honest.  So the original criterion failed a
	 * HEALTHY instrument, and its prescribed response -- stop and investigate
	 * the channel -- would have halted the round on a good board.
	 *
	 * PASS below the ceiling, FAIL at or above it.  A reading in the grey zone
	 * between the measured noise floor and the ceiling is REPORTED, never
	 * silently accepted, and the threshold is not to be moved to fit it: that
	 * is the failure mode this project keeps catching, and the plan says so. ---- */
	if (!strcmp(argv[1], "plcfg")) {
		uint32_t w;
		rf_reg_write(RF_REG_PHY_MODE,  1);      /* 1R1T, matching AD9361 */
		rf_reg_write(RF_REG_IDELAY_EN, 0x7F);
		rf_reg_write(RF_REG_IDELAY_TAP, 15);
		rf_reg_write(RF_REG_PHASE_INC, 128);

		printf("--- PL config written ---\n");
		printf("  phy_mode   readback = %u (expect 1)\n",
		       rf_reg_read(RF_REG_PHY_MODE));
		printf("  idelay_en  readback = 0x%02X (expect 0x7F)\n",
		       rf_reg_read(RF_REG_IDELAY_EN));
		printf("  idelay_tap readback = %u (expect 15)\n",
		       rf_reg_read(RF_REG_IDELAY_TAP));
		printf("  phase_inc  readback = %u (expect 128)\n",
		       rf_reg_read(RF_REG_PHASE_INC));

		w = rf_reg_read(RF_REG_STATUS);
		printf("  STATUS = 0x%08X\n", w);

		if (RF_STATUS_F_DATA_CLK_HZ(w) >= RF_NOCLOCK_MAX_HZ) {
			printf("RESULT FAIL (negative criterion): the STATUS channel "
			       "reports %.3f MHz of DATA_CLK while the BBPLL is "
			       "unprogrammed, against the highest noise measured on "
			       "this board (%.3f MHz across 25 windows). The "
			       "observation channel is broken; do not trust "
			       "Step 3.\n",
			       (double)RF_STATUS_F_DATA_CLK_HZ(w) / 1e6,
			       (double)RF_NOISE_OBSERVED_MAX_HZ / 1e6);
			return 1;
		}
		if (RF_STATUS_F_DATA_CLK_HZ(w) >= RF_NOISE_OBSERVED_MAX_HZ) {
			printf("NOTE: f_DATA_CLK = %.3f MHz is in the grey zone -- "
			       "above the highest noise observed here (%.3f MHz) "
			       "but below the %.3f MHz that means a real clock. "
			       "Reported rather than smoothed over; treat the "
			       "observation channel as SUSPECT and say so in the "
			       "record.\n",
			       (double)RF_STATUS_F_DATA_CLK_HZ(w) / 1e6,
			       (double)RF_NOISE_OBSERVED_MAX_HZ / 1e6,
			       (double)RF_NOCLOCK_MAX_HZ / 1e6);
		}
		printf("RESULT PASS (negative criterion): f_DATA_CLK = %.3f MHz, "
		       "below the %.3f MHz ceiling -- no DATA_CLK, as required\n",
		       (double)RF_STATUS_F_DATA_CLK_HZ(w) / 1e6,
		       (double)RF_NOCLOCK_MAX_HZ / 1e6);
		return 0;
	}

	/* ---- plstate: read the PL config WITHOUT writing it ----
	 *
	 * plcfg writes before it evaluates anything -- it sets phy_mode = 1 as its
	 * first act, then checks that STATUS reports no clock (rf_main.c:578-624).
	 * On a cold device that ordering is harmless.  On a board deliberately left
	 * in 2R2T it is not: `plcfg` throws the PL back to 1R1T and only then
	 * reports the failure its caller was going to see anyway, so a stage-2
	 * measurement that runs plcfg first measures a board whose PL and AD9361
	 * disagree -- and the number it produces looks exactly like a passing
	 * stage-1 run.  There was no way to ASK what phy_mode is, only to set it.
	 *
	 * This subcommand is read-only by construction: no rf_reg_write appears in
	 * it.  It asserts nothing, because "which mode should this be" is the
	 * caller's question, not the transport's. ---- */
	if (!strcmp(argv[1], "plstate")) {
		printf("  idelay_en   = 0x%02X\n", rf_reg_read(RF_REG_IDELAY_EN));
		printf("  idelay_tap  = %u\n",   rf_reg_read(RF_REG_IDELAY_TAP));
		printf("  phase_inc   = %u\n",   rf_reg_read(RF_REG_PHASE_INC));
		{
			uint32_t ws = rf_reg_read(RF_REG_WAVE_SEL);
			printf("  wave_sel    = %u  (%s)\n", ws & 1u,
			       (ws & 1u) ? "square" : "sine");
		}
		{
			uint32_t pm = rf_reg_read(RF_REG_PHY_MODE);
			printf("  phy_mode    = %u  (%s)\n", pm,
			       pm ? "1R1T" : "2R2T");
		}
		printf("  STATUS      = 0x%08X\n", rf_reg_read(RF_REG_STATUS));
		return 0;
	}

	/* ---- status ---- */
	if (!strcmp(argv[1], "status")) {
		/* phy_mode is readable, so the expected DATA_CLK follows from the
		 * state the PL is actually in rather than from a guess: 1R1T
		 * (phy_mode 1) drives 2*fs, 2R2T drives 4*fs. */
		uint32_t pm = rf_reg_read(RF_REG_PHY_MODE);
		status_report("current",
		              (uint64_t)(pm ? 2u : 4u) * RF_SAMPLE_RATE_HZ);
		return 0;
	}

	/* ---- init: spec 6.1 Step 2, with the phy-dependent knobs folded in ----
	 *
	 * R7: atten / lo / dump happen HERE, in the one process that ran
	 * ad9361_init.  They were separate subcommands and could not work: phy is
	 * process-local, so `board_app atten 0` in its own process found phy NULL
	 * and the vendor setter dereferenced it.  Folding them in is also what
	 * makes the readback evidence come from the run that set the value, which
	 * criterion 2.4 ("atten reads back equal to what was written") and the
	 * 4.4/4.5 measurement points require.  The null-phy guards that used to
	 * report this are gone with the subcommands -- there is now no way to
	 * reach a phy-dependent call from a process that lacks one. ---- */
	if (!strcmp(argv[1], "init")) {
		const char *trace_path = NULL;
		int      want_dump = 0, atten_set = 0, lo_set = 0, want_2r2t = 0;
		uint32_t atten_mdb = 0;
		uint64_t lo_hz = 0;
		int32_t  ret;
		int      i;

		/* argv[2] is the trace path ONLY if it is not one of the keywords,
		 * so `init atten 30000` means "no trace", not "a trace file called
		 * atten".  Everything after that is an order-independent
		 * keyword/value pair, with `dump` and `2r2t` bare flags.  A bare
		 * `init` keeps the default trace filename it has always used. */
		i = 2;
		if (argc > 2 && strcmp(argv[2], "atten") && strcmp(argv[2], "lo") &&
		    strcmp(argv[2], "dump") && strcmp(argv[2], "2r2t")) {
			trace_path = argv[2];
			i = 3;
		}
		for (; i < argc; i++) {
			uint64_t v = 0;

			if (!strcmp(argv[i], "dump")) {
				want_dump = 1;
			} else if (!strcmp(argv[i], "2r2t")) {
				/* A bare flag, like `dump`, so the parser gains no new
				 * shape and `init 2r2t atten 0` / `init atten 0 2r2t`
				 * both mean the same thing.  Keyword spelled the same as
				 * the subcommand on purpose: one vocabulary for one
				 * concept, and the subcommand's own text points here. */
				want_2r2t = 1;
			} else if (!strcmp(argv[i], "atten")) {
				if (++i >= argc) {
					fprintf(stderr, "init: atten needs a value in mdB\n");
					return 2;
				}
				if (parse_dec_u64(argv[i], &v) != 0 || v > 0xFFFFFFFFull) {
					fprintf(stderr, "init: atten needs a value in "
					        "mdB, got '%s'\n", argv[i]);
					return 2;
				}
				atten_mdb = (uint32_t)v;
				atten_set = 1;
			} else if (!strcmp(argv[i], "lo")) {
				if (++i >= argc) {
					fprintf(stderr, "init: lo needs a value in Hz\n");
					return 2;
				}
				if (parse_dec_u64(argv[i], &v) != 0) {
					fprintf(stderr, "init: lo needs a value in Hz, "
					        "got '%s'\n", argv[i]);
					return 2;
				}
				lo_hz = v;
				lo_set = 1;
			} else {
				fprintf(stderr, "init: unexpected argument '%s'\n", argv[i]);
				usage();
				return 2;
			}
		}

		gpio_init(0);
		gpio_set_value(RF_GPIO_TXNRX,  1);
		gpio_set_value(RF_GPIO_ENABLE, 1);
		gpio_set_value(RF_GPIO_RESETB, 0);
		gpio_set_value(RF_GPIO_SYNC,   0);
		gpio_set_value(RF_GPIO_EN_AGC, 0);
		gpio_set_value(RF_GPIO_CTL0, 0);
		gpio_set_value(RF_GPIO_CTL1, 0);
		gpio_set_value(RF_GPIO_CTL2, 0);
		gpio_set_value(RF_GPIO_CTL3, 0);

		if (spi_init(0, 1, 0) != 0) return 1;

		/* The trace is opened BEFORE ad9361_init and flushed AFTER it: a
		 * file write inside the VCO calibration's timing window would
		 * perturb the very thing being measured (spec 5.3 / R6). */
		rf_trace_open(trace_path ? trace_path : "trace_init.txt");

		gpio_set_value(RF_GPIO_RESETB, 1);
		msleep_interruptible(20);

		/* The PL's framing and the part's mode must MATCH, so assert phy_mode
		 * rather than assuming it -- a mismatch here corrupts the data bus
		 * silently (spec 4(d) / R5).  Which value is right depends on the
		 * switch, and so does who writes it:
		 *
		 *   1R1T (default) keeps the original contract exactly: `plcfg`
		 *   (Step 1) ran first and wrote phy_mode = 1, and init fails if it
		 *   did not.  Nothing here writes phy_mode on this path.
		 *
		 *   2R2T is deliberately self-sufficient, because `plcfg` CANNOT have
		 *   left phy_mode = 0 -- 1R1T is its fixed default and stage one
		 *   depends on that.  So `init 2r2t` performs the PL side of the
		 *   switch itself: phase_inc to 0 first, so the data path carries no
		 *   edges while the framing changes (R10 -- it reaches rf_tx_source's
		 *   strobe divider combinationally), then phy_mode = 0.  The write is
		 *   not taken on trust; the readback below is the same assertion as
		 *   the 1R1T path, with a different expected value. */
		if (want_2r2t) {
			rf_reg_write(RF_REG_PHASE_INC, 0);
			udelay(100);
			rf_reg_write(RF_REG_PHY_MODE, 0);
		}
		if (rf_reg_read(RF_REG_PHY_MODE) != (want_2r2t ? 0u : 1u)) {
			fprintf(stderr, "phy_mode is not %u -- "
			        "%s\n", want_2r2t ? 0u : 1u,
			        want_2r2t ? "run `plcfg` first, then re-run `init 2r2t`"
			                  : "run `plcfg` first");
			return 1;
		}

		rf_init_param_fill();
		/* The 2R2T switch overrides the 1R1T default that rf_init_param_fill()
		 * just wrote (rf_main.c:201).  Overridden HERE rather than inside the
		 * fill so that the default stays stated in exactly one place -- a
		 * second assignment to the field would make "what does a bare `init`
		 * do" a two-line question.  A bare `init` still gets 1R1T. */
		if (want_2r2t) {
			rf_init_param.two_rx_two_tx_mode_enable = 1;
			printf("2R2T: PL phy_mode -> 0 (readback confirmed above) and "
			       "two_rx_two_tx_mode_enable -> 1.  phase_inc was left at "
			       "0; restore it with `board_app phase_inc <n>` before "
			       "measuring.\n");
		}
		ret = ad9361_init(&phy, &rf_init_param);
		/* The flush's return is NOT discarded.  rf_trace_flush returns records
		 * written or -1, and on -1 it prints only to stderr
		 * (platform_rf.c:204-206) -- so ignoring it lets `init` print RESULT
		 * PASS and exit 0 while Task 6's input file silently does not exist.
		 * That is the same "plausible-looking success for a failed operation"
		 * shape R6 exists to close, and the trace is a declared deliverable of
		 * this task.  The plan's own code block discards this return too; this
		 * is a deliberate improvement on the plan, not a deviation from it. */
		if (rf_trace_flush() < 0) {
			fprintf(stderr, "rf_trace_flush failed -- Task 6's input "
			        "does not exist\n");
			return 1;
		}
		rf_trace_overflow_report();

		printf("ad9361_init returned %d (criterion 2.1: expect 0)\n", (int)ret);
		if (ret != 0) {
			printf("RESULT FAIL: the vendor driver reported its own error; "
			       "its diagnosis is more trustworthy than ours\n");
			return 1;
		}

		/* FIR: enabled with the EXAMPLE's own coefficients, exactly as the
		 * example does it (main.c:493-496).  The plan originally bypassed
		 * both FIRs to get a cleaner tone; the user's direction for this
		 * round is that the example's configuration wins where the two
		 * disagree, so the example's path is followed and the consequence
		 * (C3's interpretation) is handled in scope_tx_check.py.
		 *
		 * The coefficients are the example's tx_fir_config (128 taps,
		 * tx_gain=0, tx_int=2) and rx_fir_config_search (rx_dec=1,
		 * rx_gain=-6, 128 taps) -- copied from main.c:336-430, NOT invented.
		 * They are 128-entry tables; see the plan's Task 4 Step 1a. */
		ad9361_set_tx_fir_config(phy, rf_tx_fir_config);
		ad9361_set_rx_fir_config(phy, rf_rx_fir_config_search);
		ad9361_set_tx_fir_en_dis(phy, 1);
		ad9361_set_rx_fir_en_dis(phy, 1);

		/* ===== fs = 60 MSPS IS PRODUCED HERE, NOT BY THE STRUCT =====
		 *
		 * ref/main.c:503-504 calls exactly these two, in this position (after
		 * ad9361_init and after the FIR enable).  They are not redundant with
		 * the rx/tx_path_clock_frequencies filled in rf_init_param_fill():
		 *
		 *   - ad9361_init programs the chain straight from the struct
		 *     (vendor/ad9361.c:5329-5330, ad9361_set_trx_clock_chain(phy,
		 *     pd->rx_path_clks, pd->tx_path_clks)), so the struct's values are
		 *     what the part runs after init.
		 *   - Those struct values are the example's PRE-recompute chain
		 *     (index 5 = 40 MHz), so they alone leave the part at 40 MSPS.
		 *   - ad9361_set_{rx,tx}_sampling_freq are the ONLY code that recompute
		 *     the chain from a requested rate, via
		 *     ad9361_calculate_rf_clock_chain() (vendor/ad9361_api.c:852 and
		 *     :1556).
		 *
		 * So deleting these two calls as "already set in the struct" silently
		 * drops the part to 40 MSPS -- a wrong sample rate with no error
		 * anywhere.  docs/rf/example_project_notes.md:166 and :172 record this
		 * (the struct clocks are annotated "（API 会重算）").
		 *
		 * Both calls internally re-apply ad9361_update_rf_bandwidth with
		 * phy->current_*_bw_Hz, which after init is our struct's 30 MHz, so
		 * that part is a no-op.  The LO is untouched by them, so the `lo`
		 * override below keeps the example's order (sampling, then LO). */
		ret = ad9361_set_tx_sampling_freq(phy, RF_SAMPLE_RATE_HZ);
		if (ret != 0) {
			fprintf(stderr, "ad9361_set_tx_sampling_freq(%u) failed: %d\n",
			        (unsigned)RF_SAMPLE_RATE_HZ, (int)ret);
			return 1;
		}
		ret = ad9361_set_rx_sampling_freq(phy, RF_SAMPLE_RATE_HZ);
		if (ret != 0) {
			fprintf(stderr, "ad9361_set_rx_sampling_freq(%u) failed: %d\n",
			        (unsigned)RF_SAMPLE_RATE_HZ, (int)ret);
			return 1;
		}
		printf("sampling rate requested = %u Hz on both paths "
		       "(ref/main.c:503-504)\n", (unsigned)RF_SAMPLE_RATE_HZ);

		/* Overrides, applied to the LIVE phy and read back by the criteria
		 * below, in this same process.  Both channels, so stage two's TX2
		 * sees the same treatment. */
		if (atten_set) {
			ad9361_set_tx_attenuation(phy, 0, atten_mdb);
			ad9361_set_tx_attenuation(phy, 1, atten_mdb);
			printf("override: TX atten set to %u mdB on both channels\n",
			       atten_mdb);
		}
		if (lo_set) {
			ad9361_set_tx_lo_freq(phy, lo_hz);
			ad9361_set_rx_lo_freq(phy, lo_hz);
			printf("override: TX/RX LO set to %llu Hz\n",
			       (unsigned long long)lo_hz);
		}

		/* Second flush.  rf_trace_flush is the only writer of the trace file
		 * (platform_rf.c:196-218) and the first one ran right after
		 * ad9361_init -- so without this, everything written since is absent
		 * from the file Task 6 enumerates its audit address list from.  That
		 * would omit precisely the writes this round most needs re-read: the
		 * sampling-frequency chain (above), the FIR registers, TX_ATTEN and
		 * the LO registers.  Spec 5.3's second pass exists to re-read what the
		 * first pass wrote, so a trace that stops before them defeats it.
		 *
		 * This does NOT violate the "no read between writes" rule: that rule
		 * is about the VCO calibration window INSIDE ad9361_init, which
		 * returned above; this flush is outside it.  rf_trace_flush reopens
		 * with "w" and rewrites the whole buffer, so the resulting file is the
		 * complete history rather than an appended-to one. */
		if (rf_trace_flush() < 0) {
			fprintf(stderr, "rf_trace_flush failed -- Task 6's input "
			        "does not exist\n");
			return 1;
		}
		rf_trace_overflow_report();

		/* Criterion 2.2: the ENSM must have reached an enabled state. */
		{
			uint32_t mode = 0;
			ad9361_get_en_state_machine_mode(phy, &mode);
			printf("ENSM mode = 0x%02X (criterion 2.2: expect FDD/RX, "
			       "not SLEEP/ALERT)\n", (unsigned)mode);
		}

		/* Criterion 2.3: LO read-back.  This is the driver's own arithmetic
		 * from the register file -- it proves register self-consistency, NOT
		 * PLL lock.  Lock can only be shown by the scope at Step 4.
		 * When `lo <hz>` was given above, this line IS that override's
		 * readback, and it comes from the run that set it. */
		{
			uint64_t lo = 0;
			ad9361_get_tx_lo_freq(phy, &lo);
			printf("TX LO readback = %llu Hz (criterion 2.3, "
			       "register self-consistency only)\n",
			       (unsigned long long)lo);
		}

		/* Criterion 2.4.  Same as 2.3: when `atten <mdB>` was given above,
		 * this line is that override's readback, from the run that set it. */
		{
			uint32_t atten = 0;
			ad9361_get_tx_attenuation(phy, 0, &atten);
			printf("TX1 atten readback = %u mdB (criterion 2.4)\n", atten);
		}

		/* Criterion 2.5, self-verifying: "the rate the example asks for was
		 * actually achieved", not "these particular dividers appeared".
		 *
		 * ad9361_get_{rx,tx}_sampling_freq return
		 * clk_get_rate(phy, phy->ref_clk_scale[..._SAMPL_CLK])
		 * (vendor/ad9361_api.c:873) -- the LIVE clock, not a cached request --
		 * so what is printed here is a genuine readback, and the match/mismatch
		 * token is computed from it at run time.
		 *
		 * The old text asserted the six divider values.  That expectation was
		 * the PRE-recompute chain and became wrong the moment the
		 * ad9361_set_*_sampling_freq calls above were added, which is why it
		 * is gone rather than updated: asserting a particular divider set is
		 * asserting one way to reach 60 MSPS, not the criterion.  The divider
		 * row is still printed, as raw detail. */
		{
			uint32_t rx[6] = {0}, tx[6] = {0}, rx_fs = 0, tx_fs = 0;
			int i;

			ad9361_get_rx_sampling_freq(phy, &rx_fs);
			ad9361_get_tx_sampling_freq(phy, &tx_fs);
			printf("RX sampling rate readback = %u Hz (criterion 2.5, "
			       "requested %u: %s)\n", rx_fs, (unsigned)RF_SAMPLE_RATE_HZ,
			       rx_fs == RF_SAMPLE_RATE_HZ ? "MATCH" : "MISMATCH");
			printf("TX sampling rate readback = %u Hz (criterion 2.5, "
			       "requested %u: %s)\n", tx_fs, (unsigned)RF_SAMPLE_RATE_HZ,
			       tx_fs == RF_SAMPLE_RATE_HZ ? "MATCH" : "MISMATCH");

			ad9361_get_trx_path_clks(phy, rx, tx);
			printf("RX path clocks (raw detail -- the recomputed chain, NOT "
			       "an expectation):\n   ");
			for (i = 0; i < 6; i++) printf(" %u", rx[i]);
			printf("\nTX path clocks (raw detail):\n   ");
			for (i = 0; i < 6; i++) printf(" %u", tx[i]);
			printf("\n");
		}

		/* Step 3: DATA_CLK exists now.  Same channel, opposite direction --
		 * Step 1 required "no clock", this requires a specific number. */
		status_report("after init (spec 6.1 Step 3)",
		              (uint64_t)(want_2r2t ? 4 : 2) * RF_SAMPLE_RATE_HZ);

		/* `dump`: the driver's own diagnostics (spec 5.4), for free because
		 * the phy is already live.  No second status_report here -- Step 3's
		 * has just run, and printing it twice would only add noise. */
		if (want_dump) {
			uint32_t mode = 0, atten = 0, fs = 0, rx[6] = {0}, tx[6] = {0};
			uint64_t lo = 0;
			int j;
			ad9361_get_en_state_machine_mode(phy, &mode);
			ad9361_get_tx_lo_freq(phy, &lo);
			ad9361_get_tx_attenuation(phy, 0, &atten);
			ad9361_get_tx_sampling_freq(phy, &fs);
			ad9361_get_trx_path_clks(phy, rx, tx);
			printf("ENSM=0x%02X LO=%llu atten=%u fs=%u\n", (unsigned)mode,
			       (unsigned long long)lo, atten, fs);
			printf("RX clocks:");
			for (j = 0; j < 6; j++) printf(" %u", rx[j]);
			printf("\nTX clocks:");
			for (j = 0; j < 6; j++) printf(" %u", tx[j]);
			printf("\n");
		}

		printf("RESULT PASS (init returned 0; inspect the numbers above "
		       "against criteria 2.1-2.5)\n");
		return 0;
	}

	/* ---- 2r2t: stage two, PL side of the switch ----
	 *
	 * Why this exists at all: in 1R1T TX2 is OFF, so "no signal on CH2" means
	 * nothing -- a healthy off channel and a broken one look identical.  The
	 * part has to be in 2R2T before TX2 is judgeable.
	 *
	 * ORDER MATTERS (R10): phy_mode is written with DATA_CLK STOPPED, not merely
	 * with the data path quiet.  The authoritative statement is
	 * rf_overlay/tools/impl1_clock_groups.tcl:12-14: this crossing has no
	 * synchroniser, so an unsynchronised configuration bit written while the
	 * recovered clock runs is a metastability hazard on the bit itself --
	 * phase_inc = 0 removes edges from the DATA, it does not stop the CLOCK.
	 * `init 2r2t` is the sequence that satisfies that (RESETB low, release, wait
	 * out the part's boot, then write phy_mode with the BBPLL still unprogrammed
	 * so DATA_CLK has not appeared, then let ad9361_init bring it up).  phy_mode
	 * also reaches rf_tx_source's strobe divider combinationally, so a
	 * mid-stream change would glitch one sample period on top of that.
	 *
	 * ⚠ DIAGNOSTIC ONLY.  It cannot satisfy the discipline above, and it cannot
	 * move the AD9361 at all.  On the ordering: this subcommand is a separate
	 * process invocation that may well be issued while the part is programmed
	 * and DATA_CLK is running, which is exactly the case impl1_clock_groups.tcl
	 * forbids -- it cannot check, because it has no way to stop the clock.  Use
	 * `init 2r2t`, which does the write inside the window where the clock is
	 * still absent.  Kept because it is the only way to exercise the PL half in
	 * isolation when diagnosing, and because the readback below is useful
	 * evidence -- but it is NOT a step of the stage-two procedure.
	 *
	 * ⚠ WHAT THIS SUBCOMMAND CANNOT DO, and why it says so out loud: `phy` is
	 * process-local.  BoardCmd.cmd() runs one `sudo board_app <args>` per
	 * call, so this is a NEW process in which ad9361_init has never run and
	 * phy is NULL (R7 -- the same fact that folded atten/lo/dump into init).
	 * The `if (phy)` branch below therefore does NOT run in the shape the
	 * command is actually invoked in, and the AD9361 stays in 1R1T while the
	 * PL frames 2R2T.  An unconditional "RESULT PASS: readback is 0" would
	 * read as though the part had been switched too, so the readback is
	 * qualified as PL-only and the route that DOES move the part is named:
	 * `board_app init 2r2t [atten <mdB>]`, which switches both in the one
	 * process that has a phy, from a hardware reset. ---- */
	if (!strcmp(argv[1], "2r2t")) {
		uint32_t rb;

		rf_reg_write(RF_REG_PHASE_INC, 0);
		udelay(100);

		rf_reg_write(RF_REG_PHY_MODE, 0);      /* PL side: 2R2T framing */

		if (phy) {
			/* Unreachable from the CLI as it is invoked today (see the
			 * note above); kept because it is the plan's stated route and
			 * it is what a future in-process caller would want.  It is
			 * structurally safe to call ad9361_init twice: every vendor
			 * allocation is fresh per call (ad9361_api.c:86-116, and the
			 * clks at ad9361.c:7190+) and the driver keeps no mutable
			 * file-scope state, so the second call neither double-frees
			 * nor reuses the first instance.  The first instance LEAKS --
			 * acceptable in a CLI process, and recorded in
			 * docs/rf/boardtest.md.  Note also that ad9361_reset()
			 * (ad9361.c:1002) does NOT pulse RESETB here: our
			 * rf_init_param_fill leaves gpio_resetb at 0 and gpio_is_valid
			 * accepts 0, so it toggles TXNRX instead.  That is pre-existing
			 * and identical on the first init, which is why it is not
			 * changed here -- but it means a re-init reprograms a RUNNING
			 * part rather than a reset one, which is exactly why
			 * `init 2r2t` exists as the supported route. */
			rf_init_param.two_rx_two_tx_mode_enable = 1;
			printf("re-initialising with two_rx_two_tx_mode_enable = 1\n");
			if (ad9361_init(&phy, &rf_init_param) != 0) {
				printf("RESULT FAIL: 2R2T re-init failed\n");
				return 1;
			}
			/* Same FIR configuration as the 1R1T path, from the same
			 * tables -- stage two must differ from stage one ONLY in
			 * phy_mode, or a CH2 difference has two explanations. */
			ad9361_set_tx_fir_config(phy, rf_tx_fir_config);
			ad9361_set_rx_fir_config(phy, rf_rx_fir_config_search);
			ad9361_set_tx_fir_en_dis(phy, 1);
			ad9361_set_rx_fir_en_dis(phy, 1);
		} else {
			printf("NOTE: the AD9361 was NOT touched -- phy is NULL in "
			       "this process.  init is the only command that runs "
			       "ad9361_init and the handle does not outlive it, so "
			       "this invocation can only move the PL.\n");
			printf("      The PL now frames 2R2T while the part is still in "
			       "1R1T.  Switch the PART with `board_app init 2r2t "
			       "[atten <mdB>]` (one init, from a hardware reset), and "
			       "do NOT measure CH2 until the two sides agree.\n");
		}

		rb = rf_reg_read(RF_REG_PHY_MODE);
		printf("phy_mode readback = %u (expect 0)\n", rb);
		if (rb != 0) {
			printf("RESULT FAIL: phy_mode read back %u, not 0 -- the PL "
			       "did not take the write\n", rb);
			return 1;
		}
		printf("RESULT PASS (PL side only): phy_mode == 0, the PL frames "
		       "2R2T.  phase_inc was left at 0; restore it with `board_app "
		       "phase_inc <n>` before measuring.\n");
		return 0;
	}

	/* ---- phase_inc: the one knob that stays its own subcommand ----
	 *
	 * Still valid per-process even though the phy does not survive, because it
	 * writes a PL register rather than a chip register, and because THE AD9361
	 * KEEPS TRANSMITTING AFTER THE init PROCESS EXITS -- it is hardware, and
	 * only the driver's in-RAM state dies with the process.  So a later
	 * `phase_inc` sweeps the DDS on a part that is still running, which is
	 * exactly what criterion 4.2 measures.  atten/lo could not do this: they
	 * go through the driver, which needs the handle. */
	if (!strcmp(argv[1], "phase_inc")) {
		uint32_t inc;
		if (argc < 3) { usage(); return 2; }
		inc = (uint32_t)strtoul(argv[2], NULL, 0);
		rf_reg_write(RF_REG_PHASE_INC, inc & 0xFFFu);
		printf("phase_inc set to %u (readback %u)\n",
		       inc & 0xFFFu, rf_reg_read(RF_REG_PHASE_INC));
		return 0;
	}

	/* ---- wavesel: PL DDS waveform shape (reg10[0] at 0x28).  Like phase_inc
	 *      this is a pure PL register, so it needs no phy handle and can be
	 *      toggled on a running part -- which is the whole point: a sine/square
	 *      A/B at one LO separates "the scope reads this level because the
	 *      waveform changed" from "because the frequency changed".
	 *
	 *      It is deliberately its own subcommand rather than a bit packed into
	 *      plcfg's register writes: plcfg sets phy_mode = 1, and phy_mode may
	 *      only be written while DATA_CLK is stopped (impl1_clock_groups.tcl
	 *      :12-14).  Sharing a register would silently turn every waveform
	 *      change into a framing change. ---- */
	if (!strcmp(argv[1], "wavesel")) {
		uint32_t sel;
		if (argc < 3) { usage(); return 2; }
		sel = (uint32_t)strtoul(argv[2], NULL, 0);
		if (sel > 1u) {
			fprintf(stderr, "wavesel: 0 (sine) or 1 (square), got %u\n",
			        sel);
			return 2;
		}
		rf_reg_write(RF_REG_WAVE_SEL, sel);
		printf("wave_sel set to %u (%s) (readback %u)\n", sel,
		       sel ? "square" : "sine", rf_reg_read(RF_REG_WAVE_SEL) & 1u);
		return 0;
	}

	/* ---- idelay: load the RX pin deserialiser's input delay ----
	 *
	 * WHY THIS EXISTS.  `plcfg` is the only writer of IDELAY, and its FIRST act
	 * is `rf_reg_write(RF_REG_PHY_MODE, 1)` -- 1R1T is its fixed default and
	 * stage one depends on that.  A 2R2T run therefore cannot call `plcfg` (it
	 * would throw the PL back to 1R1T), and `init` never writes IDELAY.  So on
	 * the 2R2T path `idelay_en` stays 0, `LD(idelay_en[i])` never asserts
	 * (phy_rx.v:89/143), and every IDELAYE2 sits at its power-on tap.
	 *
	 * That matters because the on-chip loopback is the AD9361's OWN data-port
	 * loopback (SPI 0x3F5 bit0), so the return path crosses the physical LVDS
	 * DDR pins and is sampled by those same IDELAYE2s.  Measured A/B on this
	 * board, same bitstream, minutes apart: 1R1T with en = 0x7F is bit-exact,
	 * whereas en = 0 leaves 9 of 64 samples off with a worst error of 46 --
	 * including a sample that should be zero reading (36,40).  A 2R2T capture
	 * with no IDELAY load has NO exact solution at all, and its per-criterion
	 * best mappings disagree with each other, which is the signature of
	 * "nothing is aligning" rather than of a different mapping.
	 *
	 * This subcommand exists so the 2R2T path can load the delay WITHOUT going
	 * through `plcfg`.  It writes reg0/reg1 only; phy_mode lives in reg3 and
	 * has its own write discipline (impl1_clock_groups.tcl:12-14 -- its whole
	 * word may only be written while DATA_CLK is stopped), so sharing a
	 * register with it would make every delay change a framing change.  The
	 * register choice is not decoration: it is what keeps the two apart.
	 *
	 * A constant 1 in `en` means LD is held asserted and the primitive tracks
	 * `idelay_tap` continuously, so the tap can be swept on a running part with
	 * no stop/start -- which is what the sweep in loopback_tap_sweep.py does.
	 * en = 0 is accepted and is a legal, meaningful value: it is the state the
	 * 2R2T path was unknowingly in, so it is the control for the A/B above.
	 *
	 * The readback is asserted, not printed for looks: this project has a
	 * documented case of a PL register write that silently did not take (the
	 * BD revision that left AXI BRAM writes inert), so "wrote it" is not
	 * evidence that it arrived. ---- */
	if (!strcmp(argv[1], "idelay")) {
		uint32_t en, tap, en_rb, tap_rb;
		if (argc < 4) { usage(); return 2; }
		en  = (uint32_t)strtoul(argv[2], NULL, 0);
		tap = (uint32_t)strtoul(argv[3], NULL, 0);
		if (en > 0x7Fu) {
			fprintf(stderr, "idelay: en is 7 bit (0x00..0x7F), got 0x%X\n",
			        en);
			return 2;
		}
		if (tap > 31u) {
			fprintf(stderr, "idelay: tap is 5 bit (0..31), got %u\n", tap);
			return 2;
		}
		rf_reg_write(RF_REG_IDELAY_EN,  en);
		rf_reg_write(RF_REG_IDELAY_TAP, tap);
		en_rb  = rf_reg_read(RF_REG_IDELAY_EN);
		tap_rb = rf_reg_read(RF_REG_IDELAY_TAP);
		printf("  idelay_en  <- 0x%02X (readback 0x%02X)\n",  en,  en_rb);
		printf("  idelay_tap <- %u (readback %u)\n",          tap, tap_rb);
		if (en_rb != en || tap_rb != tap) {
			fprintf(stderr, "idelay: readback does not match the write -- "
			        "the PL register did not take the value\n");
			return 1;
		}
		printf("RESULT PASS (PL side only): IDELAY loaded.  phy_mode was not "
		       "written by this subcommand (it reads %u).\n",
		       rf_reg_read(RF_REG_PHY_MODE));
		return 0;
	}

	/* ================= sample path: TX buffer =================
	 *
	 * These are the first PL registers on this overlay that a userspace
	 * program can reach as memory rather than as one SPI command at a time.
	 * Everything here is plain MMIO through platform_rf.c.
	 */

	/* srcsel <0|1>: 0 = the DDS tone, 1 = the sample buffer.
	 *
	 * Read-modify-write, which is safe here because reg5 IS readable -- the
	 * read mux returns slv_reg5.  reg7 and reg13 are the two that are not. */
	if (!strcmp(argv[1], "srcsel")) {
		uint32_t on;
		if (argc < 3) { usage(); return 2; }
		on = (uint32_t)strtoul(argv[2], NULL, 0);
		if (on > 1u) {
			fprintf(stderr, "srcsel: 0 (DDS) or 1 (buffer), got %u\n", on);
			return 2;
		}
		{
			uint32_t v = rf_reg_read(RF_REG_SRC_CFG);
			v = on ? (v | RF_SRC_SEL) : (v & ~RF_SRC_SEL);
			rf_reg_write(RF_REG_SRC_CFG, v);
		}
		printf("src_sel = %u (%s)  reg5 = 0x%08X\n", on,
		       on ? "buffer" : "DDS", rf_reg_read(RF_REG_SRC_CFG));
		return 0;
	}

	/* smpcfg <len> [wrap]: set the buffer length and commit it.
	 *
	 * The commit is a TOGGLE, not a level.  smp_len and smp_wrap cross into the
	 * DATA_CLK domain as a bus, so the reader latches them on the synchronised
	 * edge of this bit rather than sampling them bit by bit -- two independent
	 * per-bit chains could otherwise deliver a length from one write and a wrap
	 * flag from the next.  Toggling also means committing the same values twice
	 * works, where a level would make the second one a no-op. */
	if (!strcmp(argv[1], "smpcfg")) {
		uint32_t len, wrap, v;
		if (argc < 3) { usage(); return 2; }
		len  = (uint32_t)strtoul(argv[2], NULL, 0);
		wrap = (argc > 3) ? (uint32_t)strtoul(argv[3], NULL, 0) : 1u;
		if (len < 1u || len > RF_BRAM_WORDS) {
			fprintf(stderr, "smpcfg: len 1..%u, got %u\n",
			        RF_BRAM_WORDS, len);
			return 2;
		}
		rf_reg_write(RF_REG_SMP_LEN, len);
		v = rf_reg_read(RF_REG_SRC_CFG);
		v = wrap ? (v | RF_SMP_WRAP) : (v & ~RF_SMP_WRAP);
		v ^= RF_SMP_COMMIT;                 /* toggle = commit */
		rf_reg_write(RF_REG_SRC_CFG, v);
		printf("smp_len = %u wrap = %u committed (reg5 = 0x%08X)\n",
		       len, wrap ? 1u : 0u, rf_reg_read(RF_REG_SRC_CFG));
		return 0;
	}

	/* smpstat [samples]: read the reader's position repeatedly.
	 *
	 * The point is the SEQUENCE, not one value.  A single reading of smp_pos
	 * says nothing -- it is a position, and every position looks plausible.
	 * Watching it advance while src_sel = 1, and stay put while src_sel = 0, is
	 * what makes it evidence (criterion C17's negative case). */
	if (!strcmp(argv[1], "smpstat")) {
		unsigned n = (argc > 2) ? (unsigned)strtoul(argv[2], NULL, 0) : 6u;
		unsigned j;
		printf("src_sel = %u  smp_len = %u\n",
		       rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL,
		       rf_reg_read(RF_REG_SMP_LEN) & 0x1FFFu);
		for (j = 0; j < n; j++) {
			printf("  smp_pos = %u\n", rf_reg_read(RF_REG_SMP_POS) & 0x1FFFu);
		}
		return 0;
	}

	/* samples <mode> ...: fill the TX buffer.
	 *
	 *   samples iq   <n> <k>   I = A cos, Q = A sin, k cycles in n points
	 *   samples real <n> <k>   I = A cos, Q = 0        (the C4 negative case)
	 *   samples zeros <n>      all zero                (the C1 negative case)
	 *   samples write <word> <value>
	 *   samples dump  <first> <count>
	 *
	 * `iq` puts a SINGLE sideband at LO + k*fs/n once the AD9361 mixes it up,
	 * where `real` puts TWO, symmetric about LO.  That difference is the
	 * strongest evidence in this suite that Q is alive: it is a one-bit
	 * observation that cannot be produced by an I-only signal, and unlike an
	 * amplitude or an eye diagram it has no measurement margin to argue about. */
	if (!strcmp(argv[1], "samples")) {
		const char *mode;
		uint32_t n, k;
		unsigned j;

		if (argc < 3) { usage(); return 2; }
		mode = argv[2];

		if (!strcmp(mode, "write")) {
			uint32_t w, val;
			if (argc < 5) { usage(); return 2; }
			w   = (uint32_t)strtoul(argv[3], NULL, 0);
			val = (uint32_t)strtoul(argv[4], NULL, 0);
			if (w >= RF_BRAM_WORDS) {
				fprintf(stderr, "samples write: word 0..%u\n",
				        RF_BRAM_WORDS - 1u);
				return 2;
			}
			rf_bram_write(RF_BRAM_TX_BASE, w, val);
			printf("TX[%u] = 0x%08X (readback 0x%08X)\n", w, val,
			       rf_bram_read(RF_BRAM_TX_BASE, w));
			return 0;
		}

		if (!strcmp(mode, "dump")) {
			uint32_t first, count;
			if (argc < 5) { usage(); return 2; }
			first = (uint32_t)strtoul(argv[3], NULL, 0);
			count = (uint32_t)strtoul(argv[4], NULL, 0);
			for (j = 0; j < count && (first + j) < RF_BRAM_WORDS; j++) {
				uint32_t w = rf_bram_read(RF_BRAM_TX_BASE, first + j);
				printf("  TX[%u] = 0x%08X  I=%d Q=%d\n",
				       first + j, w, rf_iq_i(w), rf_iq_q(w));
			}
			return 0;
		}

		if (argc < 4) { usage(); return 2; }
		n = (uint32_t)strtoul(argv[3], NULL, 0);
		if (n < 1u || n > RF_BRAM_WORDS) {
			fprintf(stderr, "samples: n 1..%u, got %u\n",
			        RF_BRAM_WORDS, n);
			return 2;
		}

		if (!strcmp(mode, "zeros")) {
			for (j = 0; j < n; j++)
				rf_bram_write(RF_BRAM_TX_BASE, j, 0u);
			printf("TX buffer: %u zero words\n", n);
			return 0;
		}

		if (strcmp(mode, "iq") && strcmp(mode, "real")) {
			fprintf(stderr, "samples: mode iq|real|zeros|write|dump\n");
			return 2;
		}
		if (argc < 5) { usage(); return 2; }
		k = (uint32_t)strtoul(argv[4], NULL, 0);

		for (j = 0; j < n; j++) {
			/* The phase is built in double and rounded once.  An integer
			 * accumulator would drift over the run and put a kink in the
			 * waveform that the spectrum would show as a skirt -- which
			 * looks like a source defect and is not one. */
			double ph = 6.28318530717958647692 * (double)k * (double)j
			          / (double)n;
			int ii = (int)(RF_SAMPLES_AMP * cos(ph));
			int qq = strcmp(mode, "real") ? (int)(RF_SAMPLES_AMP * sin(ph)) : 0;
			rf_bram_write(RF_BRAM_TX_BASE, j, rf_iq_pack(ii, qq));
		}
		{
			uint32_t w0 = rf_bram_read(RF_BRAM_TX_BASE, 0);
			uint32_t w1 = rf_bram_read(RF_BRAM_TX_BASE, 1 % n);
			printf("TX buffer: %u points, k=%u, amp=%d, mode=%s\n",
			       n, k, RF_SAMPLES_AMP, mode);
			printf("  TX[0] = I=%d Q=%d   TX[1] = I=%d Q=%d\n",
			       rf_iq_i(w0), rf_iq_q(w0), rf_iq_i(w1), rf_iq_q(w1));
			printf("  played at 60 MSPS this is LO %+d Hz\n",
			       (int)((int64_t)k * 60000000 / (int64_t)n));
		}
		return 0;
	}

	/* ================= sample path: RX capture ================= */

	/* capcfg <ratio> <len> [cont]: the whole capture configuration, committed
	 * atomically.  Both fields go over the same commit toggle on purpose --
	 * committing them separately could leave the decimator running at one ratio
	 * with a length chosen for another, and every sample in the buffer would
	 * then belong to a configuration nobody asked for. */
	if (!strcmp(argv[1], "capcfg")) {
		uint32_t ratio, len, cont, v;
		if (argc < 4) { usage(); return 2; }
		ratio = (uint32_t)strtoul(argv[2], NULL, 0);
		len   = (uint32_t)strtoul(argv[3], NULL, 0);
		cont  = (argc > 4) ? (uint32_t)strtoul(argv[4], NULL, 0) : 0u;
		if (ratio > 4u) {
			fprintf(stderr, "capcfg: ratio 0..4 (D = 1,2,4,8,16), got %u\n",
			        ratio);
			return 2;
		}
		if (len < 1u || len > RF_BRAM_WORDS) {
			fprintf(stderr, "capcfg: len 1..%u, got %u\n",
			        RF_BRAM_WORDS, len);
			return 2;
		}
		rf_reg_write(RF_REG_CAP_RATIO, ratio);
		rf_reg_write(RF_REG_CAP_LEN, len);
		v = rf_reg_read(RF_REG_CAP_CFG);
		v = cont ? (v | RF_CAP_CONT) : (v & ~RF_CAP_CONT);
		v ^= RF_CAP_COMMIT;
		rf_reg_write(RF_REG_CAP_CFG, v);
		{
			/* len counts DECIMATED samples, so the wall-clock window is
			 * len*D/fs.  Stating it here keeps the factor of D from being
			 * silently dropped every time someone works it out again. */
			uint32_t d = 1u << ratio;
			uint64_t ns = (uint64_t)len * d * 1000000000ull / 60000000ull;
			printf("capture: D=%u len=%u cont=%u committed\n", d, len, cont);
			printf("  window = %u x %u samples = %llu ns\n",
			       len, d, (unsigned long long)ns);
			/* Both figures used to be 1000x out: the rate was computed in
			 * kSPS (60000/D) and labelled MSPS, and the Nyquist edge in
			 * kHz and labelled MHz.  A reader checking C13's D-sweep
			 * against "60000 MSPS" has no way to tell that from a real
			 * defect, so the units are spelled out with the value. */
			printf("  output rate = %.4f MSPS (%.1f kSPS), "
			       "Nyquist = +/-%.4f MHz\n",
			       60.0 / (double)d, 60000.0 / (double)d,
			       30.0 / (double)d);
		}
		return 0;
	}

	if (!strcmp(argv[1], "caparm")) {
		uint32_t on, v;
		if (argc < 3) { usage(); return 2; }
		on = (uint32_t)strtoul(argv[2], NULL, 0);
		v = rf_reg_read(RF_REG_CAP_CFG);
		v = on ? (v | RF_CAP_ARM) : (v & ~RF_CAP_ARM);
		rf_reg_write(RF_REG_CAP_CFG, v);
		printf("cap_arm = %u\n", on ? 1u : 0u);
		return 0;
	}

	/* capstat [n]: the done/position handshake, repeatedly.
	 *
	 * A one-shot capture must be armed, allowed to finish, and only then read.
	 * done is what says it finished; reading while busy means reading a buffer
	 * that is still being overwritten, which produces a plausible-looking
	 * result that is a mixture of two captures. */
	if (!strcmp(argv[1], "capstat")) {
		unsigned n = (argc > 2) ? (unsigned)strtoul(argv[2], NULL, 0) : 6u;
		unsigned j;
		for (j = 0; j < n; j++) {
			uint32_t s = rf_reg_read(RF_REG_CAP_STATUS);
			printf("  cap_pos = %u  done = %u\n",
			       RF_CAP_POS(s), RF_CAP_DONE(s));
		}
		return 0;
	}

	if (!strcmp(argv[1], "capdump")) {
		uint32_t first, count;
		unsigned j;                 /* its own scope: the `samples` block's j
		                             * does not reach here */
		if (argc < 4) { usage(); return 2; }
		first = (uint32_t)strtoul(argv[2], NULL, 0);
		count = (uint32_t)strtoul(argv[3], NULL, 0);
		printf("RX buffer:");
		for (j = 0; j < count && (first + j) < RF_BRAM_WORDS; j++) {
			uint32_t w = rf_bram_read(RF_BRAM_RX_BASE, first + j);
			printf(" %d%+dj", rf_iq_i(w), rf_iq_q(w));
		}
		printf("\n");
		return 0;
	}

	/* regwrite <addr> <value> [mask]: one raw AD9361 SPI write.
	 *
	 * Uses the same path as `audit` -- spi_init plus the vendor SPI accessors,
	 * with no phy handle.  That is what makes the on-chip TX->RX loopback
	 * reachable at all: ad9361_bist_loopback() would work here (its one
	 * hardware dependency, ad9361_hdl_loopback, is a stub returning -ENODEV
	 * because config.h defines AXI_ADC_NOT_PRESENT), but the loopback's whole
	 * content is one bit in REG_OBSERVE_CONFIG, and writing that bit directly
	 * skips a function whose other branches are dead on this platform.
	 *
	 * 0x3F5 bit0 = DATA_PORT_LOOP_TEST_ENABLE: 1 loops TX->RX inside the part.
	 *
	 * MASK MATTERS, and the syntax makes it optional rather than implied.
	 * Without a mask the whole byte is written, which is what the first version
	 * of this command did while its comment claimed otherwise.  0x3F5 is not a
	 * one-bit register: it also carries OBSERVATION_POINT[4:1], CHANNEL[5] and
	 * RX_MASK[6], so `regwrite 0x3F5 1` clears all of those.  They happen to
	 * read 0x00 on this board today, so writing 1 is exactly the intended
	 * read-modify-write here -- but that is a property of the current state, not
	 * of the command, and reasoning about it after the fact is how a comment
	 * ends up describing code that does something else.
	 *
	 *   regwrite 0x3F5 1 1     set bit 0, leave every other bit alone
	 *   regwrite 0x3F5 0 1     clear bit 0
	 *   regwrite 0x3F5 0x40    write the whole byte */
	if (!strcmp(argv[1], "regwrite")) {
		uint32_t addr, val, mask;
		int32_t  before, after;
		int      masked;
		if (argc < 4) { usage(); return 2; }
		if (audit_open() != 0) return 1;
		addr = (uint32_t)strtoul(argv[2], NULL, 16);
		val  = (uint32_t)strtoul(argv[3], NULL, 0);
		masked = (argc > 4);
		mask = masked ? (uint32_t)strtoul(argv[4], NULL, 0) : 0xFFu;
		if ((val & ~0xFFu) != 0u || (mask & ~0xFFu) != 0u) {
			fprintf(stderr, "regwrite: value and mask are bytes\n");
			return 2;
		}
		/* Read first, and refuse on failure: writing a plausible value on top
		 * of a broken SPI path is worse than not writing, because the write
		 * would then also report success. */
		before = ad9361_spi_read(&audit_spi, addr);
		if (before < 0) {
			fprintf(stderr, "regwrite 0x%03X: pre-read failed (%d)\n",
			        addr, (int)before);
			return 1;
		}
		if (masked) {
			val = ((uint32_t)(before & 0xFF) & ~mask) | (val & mask);
		}
		if (ad9361_spi_write(&audit_spi, addr, val) != 0) {
			fprintf(stderr, "regwrite 0x%03X: write failed\n", addr);
			return 1;
		}
		after = ad9361_spi_read(&audit_spi, addr);
		/* before -> what was actually written, with the readback beside it.
		 * Printing the requested value twice would look like a verification
		 * and verify nothing. */
		printf("regwrite 0x%03X%s: 0x%02X -> 0x%02X (readback 0x%02X)\n",
		       addr, masked ? " (masked)" : "",
		       (unsigned)(before & 0xFF), (unsigned)val,
		       (after < 0) ? 0u : (unsigned)(after & 0xFF));
		return 0;
	}

	/* ---- audit: spec 6.1 Step 5.  A second, separate pass over an address
	 *      list enumerated from the trace before measurement (spec 5.3).
	 *      Deliberately not a read-back after every write: that would double
	 *      the SPI traffic inside the VCO calibration's timing window, which
	 *      is the very thing being measured.  See audit_open() for why this
	 *      needs no phy and why each invocation opens the path itself. ---- */
	if (!strcmp(argv[1], "audit-begin")) {
		if (audit_open() != 0) return 1;
		printf("audit-begin: raw SPI path open (no driver instance involved)\n");
		return 0;
	}

	if (!strcmp(argv[1], "audit")) {
		uint32_t addr;
		int32_t  val;
		if (argc < 3) { usage(); return 2; }
		if (audit_open() != 0) return 1;
		addr = (uint32_t)strtoul(argv[2], NULL, 16);
		val  = ad9361_spi_read(&audit_spi, addr);
		/* A failed read must NOT print as 0x00.  "Reads 0" is already the
		 * hardest verdict to trust -- a register written with 0 inside its
		 * mask looks identical to a write that never landed -- so a silent
		 * zero here would be indistinguishable from a real read and would
		 * quietly launder a broken SPI path into a plausible value.  Exit
		 * non-zero instead; audit_report.py classifies that READ-FAILED. */
		if (val < 0) {
			fprintf(stderr, "audit 0x%03X: read failed (%d)\n",
			        addr, (int)val);
			return 1;
		}
		printf("audit 0x%03X = 0x%02X\n", addr, (unsigned)(val & 0xFF));
		return 0;
	}

	if (!strcmp(argv[1], "audit-end")) {
		printf("audit-end\n");
		return 0;
	}

	usage();
	return 2;
}
