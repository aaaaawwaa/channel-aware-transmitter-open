`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// dds_inc -- phase accumulator + 8-bit sine ROM, mirroring the vendor example's
// DDS_PHASE_INC register.
//
// The example's own PL-side DDS is programmed with a phase increment
// (reg2 of axi_ctrl_v1_0, "DDS_PHASE_INC", 128 in the example) and documents the
// result as fs/2^12 * phase_inc -- fs/32 at 128.  This module reproduces that
// arithmetic in the PL so the same register value means the same tone, and so
// the TX data path has a real source (the example's top level leaves it
// dangling; see the plan's note on that).
//
//   f_out = phase_inc / 2^12 * f_data_clk
//   128 at 30 MHz DATA_CLK -> 937.5 kHz
//
// The ROM is the same 256-entry sine the AWG side uses (src/rtl/sine_rom.v),
// so no new table is introduced.  It is already signed and +/-127; scaling by
// 16 fills the +/-2032 the 12-bit converter expects.
//////////////////////////////////////////////////////////////////////////////////

module dds_inc (
	input  wire        clk,
	input  wire        rst_n,
	input  wire        ce,            // advance one sample when high
	input  wire [11:0] phase_inc,
	input  wire        wave_sel,      // 0 = sine ROM, 1 = square
	output reg  [11:0] dout
);

	// 12-bit accumulator: phase_inc is the per-sample phase step over one full
	// turn of 4096, which is what makes 128 come out as fs/32.
	reg [11:0] acc;

	// 256-entry table indexed by the top 8 bits of the accumulator, so the tone
	// is quantised to fs/256 steps of phase but stays continuous in frequency.
	wire [7:0] s8;
	sine_rom rom_inst (
		.addr(acc[11:4]),
		.dout(s8)
	);

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) acc <= 12'd0;
		else if (ce) acc <= acc + phase_inc;
	end

	// sine_rom stores a signed two's-complement sine (its second half is
	// 0xFD, 0xFA, ... i.e. -3, -6, ...), amplitude +/-127.  Reading it back as
	// signed and scaling by 16 fills the +/-2032 the 12-bit converter wants.
	wire signed [7:0] s_signed = $signed(s8);

	// Square is taken from the accumulator's top bit rather than from a second
	// ROM: acc[11] is high over the second half of every full turn, so it is a
	// 50% square at exactly the same f_out the sine has, and it needs no table
	// and no knowledge of how sine_rom encodes zero.  Same peak (+/-2032), so a
	// sine/square A/B at one LO is a level change only in RMS (sqrt(2)/2 x and
	// its harmonics), not in Vpp.
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n)        dout <= 12'sd0;
		else if (wave_sel) dout <= acc[11] ? 12'sd2032 : -12'sd2032;
		else               dout <= s_signed <<< 4;
	end

endmodule
