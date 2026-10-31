`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_sample_capture -- captures decimated RX samples into a BRAM for the PS.
//
// This module owns the WHOLE capture configuration path on purpose: the
// clock-domain crossing for ratio/length/continuous, the atomic latch, and the
// decimator instance.  There is exactly one place a capture configuration is
// committed, which is what makes "no capture mixes two configurations" a
// structural property rather than a discipline someone has to remember.
//
// WHY THE CONFIG IS LATCHED ON A TOGGLE RATHER THAN CROSSED DIRECTLY
// ------------------------------------------------------------------------------
// ratio/length/continuous come from FCLK0 and are multi-bit.  A plain two-flop
// synchroniser per bit is not safe for a bus: the bits can arrive on different
// DATA_CLK edges, so the capture could latch a length from one write and a ratio
// from the next -- and every sample in the buffer would then belong to a
// configuration that never existed.  Here software writes the values, waits, and
// then toggles cfg_commit; the toggle is 2-FF synchronised and the bus is
// sampled only on the resulting edge, by which time it has been stable for many
// cycles.  Same mechanism the AWG side uses for its tap table.
//
// A ratio change mid-group is not merely a wrong number: half a group at the old
// D and half at the new one produces an output that belongs to neither ratio and
// looks like a perfectly ordinary sample.  Latching atomically is what prevents
// it.  rf_decimate's header carries the same warning from the other side.
//
// NOT READING A BUFFER WHILE IT IS BEING OVERWRITTEN
// ------------------------------------------------------------------------------
// In continuous mode the write pointer wraps and keeps going, so the PS must not
// read while busy is high.  done is sticky for a one-shot capture and clears when
// arm is released, which gives software the handshake: arm, poll done, then read.
// A one-shot capture stops at the end and leaves the buffer intact.
//
// WORD FORMAT
// ------------------------------------------------------------------------------
// [15:0] = I, [31:16] = Q, each sign-extended from 12 bits.  Sign extension
// rather than zero padding so that software can read a plain int16 per
// component without a separate widening step -- and because the choice has to be
// stated somewhere (criterion C10): a reader that assumed zero padding and got
// sign extension would see large positive numbers for every negative sample.
//////////////////////////////////////////////////////////////////////////////////

module rf_sample_capture (
	input  wire        clk,          // DATA_CLK domain
	input  wire        rst_n,

	// sample input, from the AD9361 RX deserialiser
	input  wire        ce,           // adc_d1q1_valid: one cycle per sample
	input  wire [11:0] in_i,
	input  wire [11:0] in_q,

	// configuration and control, FCLK0 domain
	input  wire [2:0]  cfg_ratio,    // 0..4 -> D = 1,2,4,8,16
	input  wire [12:0] cfg_len,      // DECIMATED samples to capture (>= 1)
	input  wire        cfg_cont,     // 1 = circular, keep overwriting
	input  wire        cfg_commit,   // toggle: latch ratio/len/cont
	input  wire        cfg_arm,      // level: capture while high

	// BRAM port B, write side
	output wire        bram_en,
	output wire [3:0]  bram_we,
	output wire [31:0] bram_addr,    // BYTE address (word << 2)
	output wire [31:0] bram_wdata,

	// status
	output wire [12:0] pos,
	output wire        done,
	output wire        busy
);

	// ------------------------------------------------------- CDC and atomic latch
	reg [2:0]  commit_s;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) commit_s <= 2'b00;
		else        commit_s <= {commit_s[0], cfg_commit};
	end
	wire commit_edge = commit_s[1] ^ commit_s[0];

	reg [2:0]  ratio_s;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) ratio_s <= 2'b00;
		else        ratio_s <= {ratio_s[0], cfg_ratio[0]};
	end

	reg [2:0]  ratio_lat;
	reg [12:0] len_lat;
	reg        cont_lat;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			ratio_lat <= 3'd0;
			len_lat   <= 13'd1;
			cont_lat  <= 1'b0;
		end else if (commit_edge) begin
			// The whole bus is sampled on the one edge the synchronised toggle
			// produces.  cfg_* has been stable since well before it.
			ratio_lat <= cfg_ratio;
			len_lat   <= (cfg_len == 13'd0) ? 13'd1 : cfg_len;   // clamp: len 0 must not underflow
			cont_lat  <= cfg_cont;
		end
	end

	// arm is a single bit, so a plain 2-FF chain is correct for it.
	reg [1:0] arm_s;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) arm_s <= 2'b00;
		else        arm_s <= {arm_s[0], cfg_arm};
	end
	wire armed = arm_s[1];

	// ------------------------------------------------------------------ decimator
	wire        dec_ce;
	wire [11:0] dec_i, dec_q;

	rf_decimate u_dec (
		.clk(clk), .rst_n(rst_n), .ce(ce), .ratio_sel(ratio_lat),
		.in_i(in_i), .in_q(in_q),
		.out_ce(dec_ce), .out_i(dec_i), .out_q(dec_q)
	);

	// --------------------------------------------------------------------- store
	reg [12:0] pos_r;
	reg        done_r;
	// A one-shot capture that has finished must stop writing, or it would keep
	// overwriting the buffer the PS is about to read.
	wire       running = armed && !(done_r && !cont_lat);
	wire       write   = running && dec_ce;

	// len_lat - 1 is REGISTERED, for the same reason the decimator's last index
	// is: left combinational it puts a 13-bit subtract in front of the compare
	// that gates pos_r, and at 4.167 ns that path measured -0.064 ns.  len_lat
	// changes only on a commit, and the capture is not running through a commit,
	// so the single cycle of lag is not observable.
	//
	// Declared BEFORE the expressions that use it.  Verilog turns an undeclared
	// identifier into an implicit 1-bit wire, so writing this below would leave
	// nxt/at_end reading a one-bit net that has nothing to do with this register.
	// Third time in this file set -- the reader's `primed` and axi_ctrl's
	// `smp_pos_word` were the same slip.
	reg [12:0] last_r;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) last_r <= 13'd0;
		else        last_r <= (len_lat == 13'd0) ? 13'd0 : (len_lat - 13'd1);
	end

	wire [12:0] nxt    = (pos_r >= last_r) ? 13'd0 : (pos_r + 13'd1);
	wire        at_end = (pos_r >= last_r);

	assign bram_en    = write;
	assign bram_we    = write ? 4'b1111 : 4'b0000;
	assign bram_addr  = {18'd0, pos_r, 2'b00};
	assign bram_wdata = {{4{dec_q[11]}}, dec_q, {4{dec_i[11]}}, dec_i};

	assign pos  = pos_r;
	assign done = done_r;
	assign busy = running;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			pos_r  <= 13'd0;
			done_r <= 1'b0;
		end else if (!armed) begin
			// Releasing arm rewinds and clears done, so the next arm is a fresh
			// capture rather than a resume part-way into the old one.
			pos_r  <= 13'd0;
			done_r <= 1'b0;
		end else if (write) begin
			if (at_end) begin
				if (cont_lat) begin
					pos_r  <= 13'd0;
				end else begin
					pos_r  <= pos_r;      // hold at the last written word
					done_r <= 1'b1;
				end
			end else begin
				pos_r <= nxt;
			end
		end
	end

endmodule
