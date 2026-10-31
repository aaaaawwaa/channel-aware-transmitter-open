`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_sample_reader -- presents I/Q samples out of a BRAM to the AD9361 TX path.
//
// WHY THERE IS NO RATE CONVERTER HERE
// ------------------------------------------------------------------------------
// The BRAM's port A belongs to the AXI BRAM controller, so the PS writes samples
// into it at whatever rate userspace manages.  Port B is read here, on DATA_CLK.
// The two are independent: the reader is the rate master and the ratio is exactly
// 1.  That is the whole reason this path needs no 5/6 fractional resampler -- see
// docs/superpowers/plans/2026-09-19-ad9361-tx-sample-path.md section 1.6.  The
// 5:6 mismatch belongs to the migration stage, where a live 50 MHz AWG stream is
// the source, not to a buffer that is simply read out.
//
// THE SAMPLE STROBE IS SHARED WITH THE DDS, DELIBERATELY
// ------------------------------------------------------------------------------
// `ce` is rf_tx_source's dds_ce: one cycle per sample period, which is 2 DATA_CLK
// cycles in 1R1T and 4 in 2R2T.  Sharing it is the point.  If this reader derived
// its own divider the two could disagree about where a sample boundary sits, and
// the symptom -- a tone at the wrong frequency in one phy_mode only -- reads like
// the part behaving differently in that mode rather than like an RTL bug.  That
// exact misreading is recorded at spec section 6.2 point 3.
//
// ADDRESS RULE
// ------------------------------------------------------------------------------
// The BD-embedded blk_mem_gen is configured with Enable_32bit_Address = true
// (without it the IP silently synthesises to an empty netlist carrying zero RAMB
// primitives -- see CLAUDE.md), so its port A is BYTE addressed.  Port B must
// match, hence bram_addr = word << 2.  Getting this wrong writes and reads
// different locations with no error anywhere.
//
// PIPELINE: WHY THE ADDRESS LEADS
// ------------------------------------------------------------------------------
// A block RAM read has one cycle of latency: the address applied in cycle N
// presents data in cycle N+1.  In 1R1T the sample period is only two cycles, so
// there is room for exactly one cycle of latency and no more.  The address
// therefore switches to the *next* word on the same cycle `ce` fires, so its data
// lands the very next cycle and is loaded before the following `ce`.  Issuing the
// read one cycle later would present each sample one period late and drop one
// sample per period -- which on a scope looks like a frequency error, not a
// dropped sample.
//
// WORD FORMAT
// ------------------------------------------------------------------------------
// [15:0] = I, [31:16] = Q, each sign-extended from 12 bits by the writer.  Only
// the low 12 bits of each field are read here, so this end does not care whether
// the upper four bits are sign extension or zero -- but the writer must pick one
// and the capture contract must say which (criterion C10).
//////////////////////////////////////////////////////////////////////////////////

module rf_sample_reader (
	input  wire        clk,          // DATA_CLK domain
	input  wire        rst_n,        // async reset, active low
	input  wire        ce,           // sample strobe (= dds_ce), 1 cycle per sample
	input  wire        enable,       // 1 = drive the TX path from BRAM
	input  wire [12:0] length,       // samples in the buffer, 1..4096
	input  wire        wrap,         // 1 = restart at 0 after the last, 0 = hold

	output wire        bram_en,
	output wire [31:0] bram_addr,    // BYTE address (word << 2)
	input  wire [31:0] bram_rdata,

	output reg  [11:0] sample_i,
	output reg  [11:0] sample_q,
	output wire [12:0] pos,          // address of the sample being presented
	output reg         active        // 1 = samples are being driven
);

	// length-1, clamped: a zero length would otherwise underflow the wrap test
	// and park the reader at 8191.
	wire [12:0] len_m1 = (length == 13'd0) ? 13'd0 : (length - 13'd1);

	reg [12:0] fetch_word;   // word to present at the next ce
	reg [12:0] pos_r;
	reg [31:0] rd_data;

	wire [12:0] next_word = (fetch_word >= len_m1) ? (wrap ? 13'd0 : len_m1)
	                                              : (fetch_word + 13'd1);

	// next_word is REGISTERED before it reaches the BRAM address pin.
	//
	// Unregistered, the address path was len_lat -> 13-bit subtract -> 13-bit
	// compare -> 13-bit increment -> two muxes -> 32-bit address -> the BRAM's
	// ADDRBWRADDR, and it measured 4.438 ns against a 4.167 ns period at 60 MSPS
	// in 2R2T (WNS -0.814).  Over half of that was routing, because the BRAM
	// lands at the far end of the die from the reader.
	//
	// Registering it costs nothing in behaviour: fetch_word only changes on a ce
	// edge, so it has been stable for at least a cycle by the time a ce arrives,
	// and next_word_r is therefore exactly next_word during the ce cycle that
	// uses it.  The compare and the increment move onto a register-to-register
	// path with a whole period to settle, and the BRAM sees a mux of two
	// registers instead of the end of a long chain.
	reg [12:0] next_word_r;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n)        next_word_r <= 13'd0;
		else if (enable)   next_word_r <= next_word;
	end

	// `enable` arrives from FCLK0 already synchronised (rf_tx_source owns that
	// crossing).  Two cycles of settling here give rd_data a valid word before
	// the first ce is honoured -- without this the first sample presented would
	// be whatever the BRAM happened to drive at power-up.
	//
	// Declared BEFORE the address logic that uses it.  Spelling it out below
	// instead would make `primed` an implicit 1-bit wire at the point of use,
	// silently disconnected from this one.
	reg [1:0] prime;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) prime <= 2'b00;
		else        prime <= {prime[0], enable};
	end
	wire primed = prime[1];

	// The address leads: on the ce cycle it already points at the *next* word,
	// so that word's data arrives one cycle later and is loaded well before the
	// next ce.  See the header.
	//
	// It must lead only on a ce that is actually HONOURED.  `ce` is free-running
	// from rf_tx_source and is high long before `enable` arrives, so leading on
	// a bare `ce` lets the fetch address run ahead of the first sample that is
	// really taken -- and playback then starts one word late.  Measured on the
	// bench: the first captured sample came out as 1 instead of 0.  A one-word
	// offset is the kind of defect that survives inspection and surfaces as a
	// mysterious phase error, so the guard is spelled out rather than inferred.
	wire honoured = ce && primed && enable;
	assign bram_addr = {18'd0, (honoured ? next_word_r : fetch_word), 2'b00};
	assign bram_en   = enable;
	assign pos       = pos_r;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			fetch_word <= 13'd0;
			pos_r      <= 13'd0;
			rd_data    <= 32'd0;
			active     <= 1'b0;
			sample_i   <= 12'd0;
			sample_q   <= 12'd0;
		end else if (!enable) begin
			// Rewind on disable so that re-enabling always starts at word 0.  A
			// reader that resumed mid-buffer would make the first capture after
			// a source change start at an arbitrary phase, which is the kind of
			// thing that gets diagnosed as a data error.
			fetch_word <= 13'd0;
			pos_r      <= 13'd0;
			active     <= 1'b0;
		end else begin
			// One cycle behind the address, so this is the word the address
			// pointed at last cycle.
			rd_data <= bram_rdata;

			if (ce && primed) begin
				sample_i   <= rd_data[11:0];
				sample_q   <= rd_data[27:16];
				pos_r      <= fetch_word;
				active     <= 1'b1;
				fetch_word <= next_word;
			end
		end
	end

endmodule
