`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// rf_tx_source_tb -- checks that the DDS tone survives phy_tx intact.
//
// rf_tx_source drives dac_valid/dac_data_* into phy_tx, which serialises one
// 12-bit I/Q pair over two DATA_CLK cycles.  The danger this TB exists for: the
// DDS advances once per sample, and phy_tx re-latches its inputs on *every*
// cycle dac_valid is high.  If the DDS step lands between the two cycles of a
// pair, the pair gets I[11:6] from one sample and I[5:0] from the next, and the
// tone comes out with a full-scale glitch every other cycle.
//
// The TB reassembles the wire back into 12-bit I samples and requires the
// sequence to be *exactly* the DDS's ideal output.  That one assertion covers
// every way the pairing can go wrong: a half-sample splice produces a word that
// is no DDS sample at all, and a DDS that advances twice per pair (or once per
// cycle) produces a different sequence.  A slope threshold alone would not do:
// the two halves of adjacent samples can be close enough to look smooth.
//
// Wire protocol, measured off phy_tx rather than assumed: the LOW half (I[5:0])
// rides the cycle whose tx_frame_out is 0, and the HIGH half (I[11:6]) the
// following cycle whose tx_frame_out is 1.  The first version of this TB had
// the two halves the other way round and reported a splice on correct RTL --
// reassembling a pair as (frame=1, then frame=0) pairs one sample's high half
// with the next sample's low half, which is exactly the bug being hunted.
//
// Elaborate with `xelab glbl rf_tx_source_tb -L unisims_ver`.
//////////////////////////////////////////////////////////////////////////////////

module rf_tx_source_tb;

	reg         rst_n = 1'b0;
	reg         dclk = 1'b0;          // 30 MHz DATA_CLK

	reg         mode = 1'b0;          // 0 = DDS tone
	reg         phy_mode = 1'b1;      // shared with phy_tx: one net in the BD
	reg  [11:0] phase_inc = 12'd128;  // example value -> fs/32 = 937.5 kHz

	wire        dac_valid;
	wire [11:0] dac_d1, dac_q1, dac_d2, dac_q2;
	wire        rst_source_ok;

	wire        tx_clk_p, tx_clk_n, tx_fr_p, tx_fr_n;
	wire [5:0]  tx_d_p, tx_d_n;

	integer pass_cnt = 0, fail_cnt = 0;

	task chk;
		// 96 bytes, not 32.  A packed vector holds a fixed number of bytes, and
		// XSIM's %0s right-aligns the label in it -- so at [255:0] every label
		// longer than 32 characters was silently truncated FROM THE LEFT:
		// "1R1T: dds_ce period is 2 DATA_CLK cycles" printed as
		// "s_ce period is 2 DATA_CLK cycles", losing exactly the mode tag the
		// new 2R2T checks are identified by.  The check still ran and still
		// judged correctly; the log just stopped saying which one it was, which
		// is how a passing log becomes unreadable evidence.  (-1 for the NUL,
		// so 95 usable characters -- no label here comes close.)
		input [8*96-1:0] name;
		input         cond;
		begin
			if (cond) begin
				pass_cnt = pass_cnt + 1;
				$display("PASS %0s", name);
			end else begin
				fail_cnt = fail_cnt + 1;
				$display("FAIL %0s", name);
			end
		end
	endtask

	// ------------------------------------------------------- period measurement
	// Both tasks are deliberately BOUNDED.  A period that is wrong must come back
	// as a number that fails an assertion, never as a loop that spins until the
	// file-level watchdog prints a bare "FAIL timeout" with no diagnostic.

	// DATA_CLK cycles from one dds_ce high to the next.  Read off the DUT's
	// internal strobe (src.dds_ce), because the obvious external observable does
	// not work here: in DDS mode dac_valid is tied high, so "count cycles while
	// dac_valid is high" never terminates -- which is what the plan's snippet did.
	// Returns -1 if dds_ce never rose, or the 16-cycle cap if it never rose again.
	task measure_ce_period;
		output integer n;
		integer k;
		begin
			n = -1;
			k = 0;
			@(posedge dclk); #1;
			while (src.dds_ce !== 1'b1 && k < 32) begin
				@(posedge dclk); #1;
				k = k + 1;
			end
			if (src.dds_ce === 1'b1) begin
				n = 0; k = 0;
				while (k < 16) begin
					@(posedge dclk); #1;
					n = n + 1;
					k = k + 1;
					if (src.dds_ce === 1'b1) k = 16;
				end
			end
		end
	endtask

	// DATA_CLK cycles between two rising edges of the frame line at the pin --
	// the phy's own framing as it actually leaves the device.  1R1T drives
	// 1,0,1,0; 2R2T drives 1,1,0,0, so this reads back 2 and 4 respectively.
	// Having it as well as the strobe check is what catches a phy_mode that
	// reached the tone source but not the phy (or vice versa): the strobe would
	// be right and the wire would still be off by 2x.
	task measure_frame_period;
		output integer n;
		integer k;
		reg     prev;
		reg     found;
		begin
			n = -1;
			@(posedge dclk); #1;
			prev  = tx_fr_p;
			found = 1'b0;
			k     = 0;
			while (k < 32 && !found) begin
				@(posedge dclk); #1;
				k = k + 1;
				if (tx_fr_p === 1'b1 && prev === 1'b0) found = 1'b1;
				prev = tx_fr_p;
			end
			if (found) begin
				n = 0; k = 0; found = 1'b0;
				while (k < 16 && !found) begin
					@(posedge dclk); #1;
					n = n + 1;
					k = k + 1;
					if (tx_fr_p === 1'b1 && prev === 1'b0) found = 1'b1;
					prev = tx_fr_p;
				end
			end
		end
	endtask

	always #16.6665 dclk = ~dclk;

	// src_sel = 0 keeps this TB on the DDS path, which is what it tests.  The
	// buffer path has its own TB (rf_sample_reader_tb).
	//
	// wave_sel is driven explicitly, and it used to not be: the port was added to
	// rf_tx_source by the square-wave change and never added here.  That was
	// checked rather than assumed -- leaving it unconnected makes no difference to
	// this TB, because XSIM defaults an unconnected input to 0 and 0 is sine.
	// So the TB was not broken, it was lucky: the benign value happened to be the
	// default.  Had the polarity been the other way the DDS output would have gone
	// X and every check here would have failed.  Driving it removes the dependence
	// on which way round the default falls.
	reg  [12:0] smp_len = 13'd1;
	reg         smp_wrap = 1'b1;
	reg         smp_commit = 1'b0;
	wire        bram_en;
	wire [31:0] bram_addr;
	wire [12:0] smp_pos;

	rf_tx_source src (
		.tx_clk(dclk),
		.rst_n(rst_n),
		.src_sel(mode),
		.phy_mode(phy_mode),
		.phase_inc(phase_inc),
		.wave_sel(1'b0),                 // sine; the square case is dds_inc's
		.smp_len(smp_len), .smp_wrap(smp_wrap), .smp_commit(smp_commit),
		.bram_en(bram_en), .bram_addr(bram_addr), .bram_rdata(32'd0),
		.smp_pos(smp_pos),
		.dac_valid(dac_valid),
		.dac_data_d1(dac_d1), .dac_data_q1(dac_q1),
		.dac_data_d2(dac_d2), .dac_data_q2(dac_q2),
		.rst_source_ok(rst_source_ok)
	);

	phy_tx tx (
		.rst_n(rst_n), .data_clk(dclk),
		.tx_clk_out_p(tx_clk_p), .tx_clk_out_n(tx_clk_n),
		.tx_frame_out_p(tx_fr_p), .tx_frame_out_n(tx_fr_n),
		.tx_data_out_p(tx_d_p), .tx_data_out_n(tx_d_n),
		.dac_valid(dac_valid),
		.dac_data_d1(dac_d1), .dac_data_q1(dac_q1),
		.dac_data_d2(dac_d2), .dac_data_q2(dac_q2),
		// Driven from the same reg as src's phy_mode: in the BD both come off the
		// same net (axi_ctrl_0/phy_mode), so any other combination is a state the
		// hardware cannot reach and would be measuring nothing.
		.phy_mode(phy_mode)
	);

	// --------------------------------------------------------------- expected
	// phase_inc=128 on a 12-bit accumulator steps the ROM index by 8 per emitted
	// sample, so sample k is ROM[8k mod 256]<<4 and the sequence repeats every 32
	// samples.  Read the values out of the same ROM the DUT uses, once at startup,
	// so the expectation cannot drift from the table.
	reg  [7:0]  ideal_addr;
	wire [7:0]  ideal_dout;
	sine_rom ideal_rom (.addr(ideal_addr), .dout(ideal_dout));
	wire signed [7:0]  ideal_s8  = $signed(ideal_dout);
	wire signed [11:0] ideal_now = ideal_s8 <<< 4;   // same scaling as dds_inc

	reg  [11:0] ideal [0:31];
	integer     i;

	initial begin
		for (i = 0; i < 32; i = i + 1) begin
			ideal_addr = i * 8;
			#1;
			ideal[i] = ideal_now;
		end
		ideal_addr = 8'd0;
	end

	// ---------------------------------------------------------- wire capture
	// The wire is read one time step after the edge: reading in the same delta as
	// the ODDR's own update returns the previous half.
	reg  [5:0]  lo_half;
	reg         have_lo;
	reg  [11:0] v;

	integer     n_samples;      // completed pairs since reset
	integer     settle;         // pairs to ignore while the pipeline warms up

	parameter   BUF = 64;
	reg  [11:0] smp [0:BUF-1];
	integer     smp_wr;
	integer     smp0_n;         // n_samples when smp[0] was stored
	integer     off;            // rotation into `ideal`, locked once BUF is full
	integer     seq_errs;

	integer     max_jump;
	reg         jmp_ready;
	reg signed [11:0] prev;
	integer     vmax, vmin;

	integer     ce_1r1t, fr_1r1t, ce_2r2t, fr_2r2t;   // period measurements

	always @(posedge dclk) begin
		#1;
		if (rst_n) begin
			if (tx_fr_p === 1'b0) begin
				lo_half <= tx_d_p;       // frame=0 cycle carries I[5:0]
				have_lo <= 1'b1;
			end else if (have_lo) begin
				v = {tx_d_p, lo_half};   // frame=1 cycle carries I[11:6]
				have_lo <= 1'b0;
				n_samples = n_samples + 1;

				if (n_samples > settle) begin
					if (smp_wr < BUF) begin
						if (smp_wr == 0) smp0_n = n_samples;
						smp[smp_wr] = v;
						smp_wr = smp_wr + 1;
					end else if (off >= 0) begin
						if (v !== ideal[(n_samples + off) % 32])
							seq_errs = seq_errs + 1;
						if (jmp_ready) begin
							if ($signed(v) - prev > max_jump) max_jump = $signed(v) - prev;
							if (prev - $signed(v) > max_jump) max_jump = prev - $signed(v);
						end
						if ($signed(v) > vmax) vmax = $signed(v);
						if ($signed(v) < vmin) vmin = $signed(v);
						prev = $signed(v);
						jmp_ready = 1'b1;
					end
				end
			end
		end
	end

	// ------------------------------------------------------------- sequencing
	integer j, o;
	reg     ok;

	initial begin
		$display("=== rf_tx_source_tb ===");

		n_samples = 0; have_lo = 1'b0; smp_wr = 0; off = -1; smp0_n = 0;
		seq_errs = 0; max_jump = 0; jmp_ready = 1'b0; prev = 12'sd0;
		vmax = -4096; vmin = 4096; settle = 2;

		rst_n = 1'b0;
		repeat (8) @(posedge dclk);
		rst_n = 1'b1;

		// Collect the alignment run, then find which rotation of the ideal period
		// the wire is on.  Matching a run rather than a single value matters: the
		// ideal period repeats values (ROM[56] and ROM[72] are both 0x7D), so one
		// sample cannot pin the phase.
		repeat (2 * (BUF + settle + 2)) @(posedge dclk);

		off = -1;
		for (o = 0; o < 32; o = o + 1) begin
			ok = 1'b1;
			for (j = 0; j < BUF; j = j + 1)
				if (smp[j] !== ideal[(j + o) % 32]) ok = 1'b0;
			if (ok && off < 0) begin
				// Store the rotation in terms of the *global* sample index so the
				// streaming check below can use n_samples directly: the ideal
				// sample at global index n is ideal[(n + off) % 32].
				off = ((o - smp0_n) % 32 + 32) % 32;
			end
		end

		// Let it stream and check every remaining sample against the expectation.
		repeat (4000) @(posedge dclk);
		#1;

		chk("rst_source_ok asserts once DATA_CLK runs", rst_source_ok === 1'b1);
		chk("dac_valid stays high in DDS mode",         dac_valid === 1'b1);
		chk("samples are produced",                     n_samples > 100);
		$display("  n_samples=%0d", n_samples);

		chk("reassembled sequence locks onto the ideal DDS period", off >= 0);
		$display("  off=%0d", off);

		// The exact splice test: every pair must reassemble into a word that is
		// precisely the DDS sample the sequence is at, in order.  Requires a lock
		// (off >= 0): without one the streaming comparison never ran, and letting
		// it pass on an empty comparison would make a spliced design look clean.
		chk("every reassembled pair is exactly a DDS sample", off >= 0 && seq_errs == 0);
		$display("  seq_errs=%0d", seq_errs);

		// Secondary net: the steepest step between adjacent DDS samples is
		// 25 LSB of the 8-bit table (25*16 = 400) around the zero crossings, so
		// anything larger is a splice or a dropped pair.
		chk("no full-scale jump between consecutive samples", max_jump <= 400);
		$display("  max_jump=%0d", max_jump);

		// The reassembled tone must swing full scale, which also proves the two
		// halves are being paired the right way round (a swap would cap the
		// excursion near zero).
		chk("reassembled tone reaches full scale", vmax > 1900 && vmin < -1900);
		$display("  vmax=%0d vmin=%0d", vmax, vmin);

		// Q is unused for a real tone and must stay at zero.
		chk("Q stays zero in DDS mode", dac_q1 === 12'sd0 && dac_q2 === 12'sd0);

		// ---------------------------------------------- dds_ce period vs phy_mode
		// phy_tx frames one I/Q sample per *two* DATA_CLK cycles in 1R1T and per
		// *four* in 2R2T, so the DDS strobe divider has to follow phy_mode.  If it
		// does not, TX1's tone comes out at twice the intended frequency in 2R2T --
		// a symptom that reads as "the part behaves differently in the new mode"
		// rather than as an RTL bug, which is the expensive way to be wrong.
		//
		// This block runs AFTER every check above on purpose: in 2R2T the wire
		// pattern becomes 1,1,0,0, so the reassembly above would pair a high half
		// with a low half and its sequence check would become meaningless noise.
		// The 1R1T half is the regression that matters most (stage 1 was already
		// validated there), so it runs first and on the unchanged configuration.
		phy_mode = 1'b1;                       // 1R1T regression: period must still be 2

		rst_n = 1'b0; repeat (8) @(posedge dclk); rst_n = 1'b1;
		measure_ce_period(ce_1r1t);
		chk("1R1T: dds_ce period is 2 DATA_CLK cycles", ce_1r1t == 2);
		$display("  ce_1r1t=%0d", ce_1r1t);

		repeat (24) @(posedge dclk);
		measure_frame_period(fr_1r1t);
		chk("1R1T: wire frame period is 2 DATA_CLK cycles", fr_1r1t == 2);
		$display("  fr_1r1t=%0d", fr_1r1t);

		phy_mode = 1'b0;                       // 2R2T: period must follow to 4
		rst_n = 1'b0; repeat (8) @(posedge dclk); rst_n = 1'b1;
		measure_ce_period(ce_2r2t);
		chk("2R2T: dds_ce period is 4 DATA_CLK cycles", ce_2r2t == 4);
		$display("  ce_2r2t=%0d", ce_2r2t);

		repeat (24) @(posedge dclk);
		measure_frame_period(fr_2r2t);
		chk("2R2T: wire frame period is 4 DATA_CLK cycles", fr_2r2t == 4);
		$display("  fr_2r2t=%0d", fr_2r2t);

		phy_mode = 1'b1;                       // leave the DUT where stage 1 found it

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS rf_tx_source_tb");
		$finish;
	end

	initial begin
		#2000000;
		$display("FAIL timeout");
		$finish;
	end

endmodule