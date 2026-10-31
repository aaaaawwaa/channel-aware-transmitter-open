`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_decimate -- non-overlapping boxcar decimator, ratio 1/2/4/8/16.
//
// WHY THE RATIO IS A REGISTER AND NOT A CONSTANT
// ------------------------------------------------------------------------------
// The receiver has to answer two questions that pull the capture window in
// opposite directions.  Wideband noise and pulsed interference need band, so
// they want D=1: 4096 complex samples at 60 MSPS is 68.267 us with a 30 MHz
// Nyquist span.  Narrowband and slow-fading structure needs time, so it wants
// D=16: the same 4096 samples then cover 1.092 ms but only 1.875 MHz.  One fixed
// configuration cannot serve both, so the ratio is a register and software picks
// per capture.
//
// WHAT IT DOES TO THE DATA -- none of this is free
// ------------------------------------------------------------------------------
//   y[m] = (1/D) * sum(k=0..D-1) x[mD + k]
//
// For independent zero-mean white noise and ignoring quantisation the output
// variance is the input's divided by D.  Narrow pulses are widened and weakened.
// Peak-to-average ratio and kurtosis move, because averaging D samples pushes the
// distribution toward Gaussian.  So it is NOT true that only spectral-shape
// features are affected -- of the energy-ish features, average power, PAPR,
// kurtosis, duty cycle and clipping statistics all move too.
//
// A boxcar is also not an anti-alias filter: out-of-band energy folds into the
// observation band, and dividing by sinc afterwards cannot undo folding -- it can
// only flatten passband droop, and near a null it amplifies error.  The practical
// consequence is that the decimation ratio is part of the feature definition:
// features are comparable within one D, and across D they need separate
// calibration or D as an explicit conditioning input.  This is the design's own
// rule ("do not reuse a window's meaning across sample rates") applied here.
//
// Also, `rx_len` in the capture side counts DECIMATED samples.  Getting that
// wrong is a factor of D in every window-length arithmetic.
//
// ROUNDING
// ------------------------------------------------------------------------------
// The divide is an arithmetic right shift, which for negative values floors
// rather than rounding to nearest: a bias of up to 0.5 LSB, worst case at D=16.
// Irrelevant to the features above at 12 bits, but recorded rather than glossed,
// because "it is just an average" would be the wrong description.
//
// CONFIG STABILITY
// ------------------------------------------------------------------------------
// ratio_sel MUST NOT change while a group is in flight.  Half a group at the old
// D and half at the new one produces an output that belongs to neither, and it
// looks like a legitimate sample.  The atomic latch and the clock-domain crossing
// both live in rf_sample_capture, so that there is exactly one place where
// capture configuration is committed.
//////////////////////////////////////////////////////////////////////////////////

module rf_decimate (
	input  wire        clk,
	input  wire        rst_n,
	input  wire        ce,          // input sample strobe
	input  wire [2:0]  ratio_sel,   // 0..4 -> D = 1,2,4,8,16 (stable while running)
	input  wire [11:0] in_i,
	input  wire [11:0] in_q,
	output reg         out_ce,      // one-cycle pulse per output sample
	output reg  [11:0] out_i,
	output reg  [11:0] out_q
);

	// Clamp rather than trust the caller: an out-of-range ratio_sel would make
	// 1 << shift exceed the group counter and the decimator would never emit.
	wire [2:0] shift  = (ratio_sel > 3'd4) ? 3'd4 : ratio_sel;

	reg  [4:0]        cnt;
	reg  signed [16:0] acc_i, acc_q;

	// The group's last index is REGISTERED, so the value the counter compares
	// against is not the far end of ratio_sel -> shifter -> subtract -> compare.
	// ratio_sel is a configuration input and is stable for the whole capture, so
	// registering costs nothing but one cycle after a ratio change, and a ratio
	// change is already required to happen between groups.
	reg [4:0] last_r;
	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) last_r <= 5'd0;
		else        last_r <= (5'd1 << shift) - 5'd1;
	end

	// 12-bit signed input sign-extended to the accumulator width.  The guard
	// bits are log2(D) = 4, which is what the widest sum needs: 16 * -2048 is
	// -32768 and 16 * 2047 is 32752, both inside signed 17 bits.
	wire signed [16:0] in_i_s = {{5{in_i[11]}}, in_i};
	wire signed [16:0] in_q_s = {{5{in_q[11]}}, in_q};

	// The accumulator is zero at the start of every group -- on reset, and again
	// at the instant a group completes -- so the running sum starts at the sample
	// itself without a special case.  The previous form spelled that out as
	// `(cnt == 0) ? in : acc + in`, which was redundant and put cnt into the
	// adder's path; at 4.167 ns that path was measuring -0.021 ns.
	wire signed [16:0] sum_i = acc_i + in_i_s;
	wire signed [16:0] sum_q = acc_q + in_q_s;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			cnt    <= 5'd0;
			acc_i  <= 17'sd0;
			acc_q  <= 17'sd0;
			out_ce <= 1'b0;
			out_i  <= 12'sd0;
			out_q  <= 12'sd0;
		end else begin
			out_ce <= 1'b0;
			if (ce) begin
				if (cnt == last_r) begin
					// Truncating to 12 bits here is exact, not lossy: the mean
					// of D values each within +/-2048 is itself within +/-2048.
					out_i  <= sum_i >>> shift;
					out_q  <= sum_q >>> shift;
					out_ce <= 1'b1;
					cnt    <= 5'd0;
					acc_i  <= 17'sd0;
					acc_q  <= 17'sd0;
				end else begin
					acc_i <= sum_i;
					acc_q <= sum_q;
					cnt   <= cnt + 5'd1;
				end
			end
		end
	end

endmodule
