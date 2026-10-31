`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_tx_source -- picks what feeds the AD9361 TX data path, and holds the rest of
// the PL safely in reset when the RF path is selected off.
//
// Two sources:
//   * DDS tone -- a 12-bit sine (or square) from dds_inc, used by the L3 bring-up
//     test so a real TX tone exists without any PS-side data streaming.
//   * BRAM buffer -- a finite I/Q buffer the PS writes, read out here on DATA_CLK
//     by rf_sample_reader.  Port A of that BRAM belongs to an AXI BRAM controller,
//     so the PS's write rate has no bearing on the playback rate: the reader is
//     the rate master and the ratio is exactly 1.  This is why the path needs no
//     5/6 fractional resampler; the 5:6 mismatch belongs to the migration stage,
//     where a live 50 MHz AWG stream would be the source.
//
// The gating matters: ad9361_phy's data path is clocked by DATA_CLK, which only
// runs once the AD9361 is powered and programmed.  Before that, every flop in
// phy_rx/phy_tx has no clock, so the phy must be held in reset.  rst_source_ok
// reports when the recovered clock is actually present and the reset has settled,
// giving software a positive indication instead of a silent no-op.
//////////////////////////////////////////////////////////////////////////////////

module rf_tx_source (
	input  wire        tx_clk,          // DATA_CLK domain
	input  wire        rst_n,           // async reset, active low

	// configuration, FCLK0 domain
	input  wire        src_sel,         // 0 = DDS tone, 1 = BRAM buffer
	input  wire        phy_mode,        // 1 = 1R1T (2 clk/sample), 0 = 2R2T (4 clk/sample)
	input  wire [11:0] phase_inc,
	input  wire        wave_sel,        // 0 = sine, 1 = square (DDS shape)
	input  wire [12:0] smp_len,         // buffer length in samples, 1..4096
	input  wire        smp_wrap,        // 1 = restart at 0, 0 = hold the last sample
	input  wire        smp_commit,      // toggle: latch smp_len/smp_wrap atomically

	// BRAM port B, read side (port A is the AXI BRAM controller)
	output wire        bram_en,
	output wire [31:0] bram_addr,       // BYTE address (word << 2)
	input  wire [31:0] bram_rdata,

	// status, FCLK0 domain will sample these
	output wire [12:0] smp_pos,         // address of the sample being presented

	output wire        dac_valid,
	output wire [11:0] dac_data_d1,
	output wire [11:0] dac_data_q1,
	output wire [11:0] dac_data_d2,
	output wire [11:0] dac_data_q2,

	output wire        rst_source_ok    // 1 = tx_clk seen, phy out of reset
);

	// ------------------------------------------------------------------ DDS
	wire [11:0] dds_d;

	// dds_inc runs on DATA_CLK like the rest of the phy, so the tone is coherent
	// with the converter's own sample clock (no CDC needed on dac_data_*).
	reg [1:0] dds_rst_sync;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) dds_rst_sync <= 2'b00;
		else        dds_rst_sync <= {dds_rst_sync[0], 1'b1};
	end
	wire dds_rst_n = dds_rst_sync[1];

	// wave_sel comes from the AXI clock (FCLK0) and is sampled here on DATA_CLK,
	// so it gets the same 2-FF treatment as any other control bit entering this
	// domain.  Unlike phy_mode it is deliberately NOT subject to the "write while
	// DATA_CLK is stopped" discipline: it selects a shape, not a framing period,
	// so a mid-stream change costs one sample of the new waveform and buys a
	// same-LO sine/square A/B that a build-time variant could not give.
	reg [1:0] wave_sel_sync;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) wave_sel_sync <= 2'b00;
		else        wave_sel_sync <= {wave_sel_sync[0], wave_sel};
	end

	// phy_tx takes one 12-bit I/Q sample per *two* DATA_CLK cycles in 1R1T and per
	// *four* in 2R2T (it interleaves the sample halves over consecutive cycles, and
	// in 2R2T one of those cycles belongs to the second TX channel), so the DDS
	// must advance one step per sample, not per clock.  dds_ce marks the cycle that
	// starts a new sample, and dac_valid is held high through the whole sample
	// period so the phy re-latches the same value for each half.
	//
	// The period therefore has to follow phy_mode.  Without this, switching to
	// 2R2T makes the DDS advance twice as often per emitted sample and TX1's tone
	// comes out at double the intended frequency -- a symptom that is easily read
	// as "the part behaves differently in the new mode" instead of as an RTL bug,
	// so it must ship in the same rebuild that enables 2R2T.
	//
	// strobe_max is combinational (not registered) because phy_mode arrives from
	// FCLK0 as a configuration signal that is written only while DATA_CLK is
	// STOPPED -- see tools/impl1_clock_groups.tcl:12-14, which is the
	// authoritative statement of the discipline.  Note it is not enough to keep
	// the data quiet: phase_inc = 0 removes edges from the data but does not stop
	// DATA_CLK, and this crossing has no synchroniser, so a write while the clock
	// runs hazards the configuration bit itself.  `init 2r2t` writes it in the
	// window where the part is out of reset but its BBPLL is still unprogrammed,
	// so DATA_CLK has not appeared yet; only then does ad9361_init bring it up.
	// A change mid-stream would glitch the period for a cycle.
	wire [7:0] strobe_max = phy_mode ? 8'd1 : 8'd3;

	reg        dds_ce;
	reg [7:0]  strobe_cnt;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) begin
			dds_ce     <= 1'b0;
			strobe_cnt <= 8'd0;
		end else begin
			dds_ce     <= (strobe_cnt == 8'd0);
			strobe_cnt <= (strobe_cnt == strobe_max) ? 8'd0 : strobe_cnt + 8'd1;
		end
	end

	dds_inc dds_inst (
		.clk(tx_clk),
		.rst_n(dds_rst_n),
		.ce(dds_ce),
		.phase_inc(phase_inc),
		.wave_sel(wave_sel_sync[1]),
		.dout(dds_d)
	);

	// -------------------------------------------------------------- BRAM buffer
	// src_sel is a single bit, so a plain 2-FF chain is correct for it.
	reg [1:0] src_sel_sync;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) src_sel_sync <= 2'b00;
		else        src_sel_sync <= {src_sel_sync[0], src_sel};
	end
	wire src_br = src_sel_sync[1];

	// smp_len and smp_wrap are multi-bit, so they are committed on a toggle
	// rather than crossed bit by bit: independent synchronisers can deliver their
	// bits on different DATA_CLK edges, and the reader would then run with a
	// length from one write and a wrap flag from the next.  Software writes the
	// values, waits, then toggles smp_commit; the bus has been stable for many
	// cycles by the time the synchronised edge arrives.  Same mechanism the
	// capture side uses, and the AWG side uses for its tap table.
	reg [1:0] commit_s;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) commit_s <= 2'b00;
		else        commit_s <= {commit_s[0], smp_commit};
	end
	wire commit_edge = commit_s[1] ^ commit_s[0];

	reg [12:0] len_lat;
	reg        wrap_lat;
	always @(posedge tx_clk or negedge rst_n) begin
		if (!rst_n) begin
			len_lat  <= 13'd1;
			wrap_lat <= 1'b1;
		end else if (commit_edge) begin
			// Clamped: a zero length would underflow the reader's wrap compare.
			len_lat  <= (smp_len == 13'd0) ? 13'd1 : smp_len;
			wrap_lat <= smp_wrap;
		end
	end

	wire [11:0] rd_i, rd_q;
	wire [12:0] rd_pos;

	rf_sample_reader u_reader (
		.clk(tx_clk),
		.rst_n(rst_n),
		.ce(dds_ce),              // shared with the DDS: one sample boundary
		.enable(src_br),
		.length(len_lat),
		.wrap(wrap_lat),
		.bram_en(bram_en),
		.bram_addr(bram_addr),
		.bram_rdata(bram_rdata),
		.sample_i(rd_i),
		.sample_q(rd_q),
		.pos(rd_pos),
		.active()
	);

	assign smp_pos = rd_pos;

	// -------------------------------------------------------------- status
	// rst_source_ok answers "is tx_clk alive and has the reset settled", which is
	// what software needs before it writes anything else.  It is deliberately NOT
	// "the phy is ready": that depends on the AD9361, not on this module.
	assign rst_source_ok = dds_rst_n;

	// ------------------------------------------------------------------ mux
	// dac_valid stays high either way: the DDS always has a sample, and the reader
	// always has one to present (it presents its last word if it has not been
	// primed yet, which is a defined value rather than an X).
	assign dac_valid   = 1'b1;
	assign dac_data_d1 = src_br ? rd_i : dds_d;
	assign dac_data_q1 = src_br ? rd_q : 12'sd0;   // the tone is real (I only)
	assign dac_data_d2 = src_br ? rd_i : 12'sd0;   // TX2 gets the same buffer
	assign dac_data_q2 = src_br ? rd_q : 12'sd0;

endmodule
