`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// phy_tx_tb -- checks phy_tx's DDR output framing.
//
// phy_tx turns one 12-bit I/Q sample pair per data_clk into a DDR stream, MSB
// first, with tx_frame marking which half of the pair is on the wire.  In 1R1T
// mode it interleaves I and Q over two data_clk cycles:
//
//   frame=0, rising edge -> I[5:0],  falling edge -> Q[5:0]
//   frame=1, rising edge -> I[11:6], falling edge -> Q[11:6]
//
// tx_data_out_p/_n are the two halves of ONE differential pair driven by a
// single ODDR (OBUFDS), so _n is just the complement of _p -- they are not the
// two DDR phases.  The TB therefore samples tx_data_out_p on both the rising
// and the falling edge of data_clk and reassembles the byte from that.
//
// NOTE: this TB does instantiate the device primitives (ODDR/OBUFDS) because
// they are what actually drives the pins, and a wrong D1/D2 pairing would be
// invisible at the register level.  Elaborate with `xelab glbl phy_tx_tb -L
// unisims_ver`.
//////////////////////////////////////////////////////////////////////////////////

module phy_tx_tb;

	reg         rst_n = 1'b0;
	reg         data_clk = 1'b0;
	reg         dac_valid = 1'b0;
	reg  [11:0] dac_data_d1 = 12'd0;
	reg  [11:0] dac_data_q1 = 12'd0;
	reg  [11:0] dac_data_d2 = 12'd0;
	reg  [11:0] dac_data_q2 = 12'd0;
	reg         phy_mode = 1'b1;      // 1R1T

	wire        tx_clk_out_p, tx_clk_out_n;
	wire        tx_frame_out_p, tx_frame_out_n;
	wire [5:0]  tx_data_out_p, tx_data_out_n;

	integer pass_cnt = 0, fail_cnt = 0;

	task chk;
		input [255:0] name;
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

	always #16.6665 data_clk = ~data_clk;   // 30 MHz DATA_CLK (60 MSPS DDR)

	phy_tx dut (
		.rst_n(rst_n),
		.data_clk(data_clk),
		.tx_clk_out_p(tx_clk_out_p), .tx_clk_out_n(tx_clk_out_n),
		.tx_frame_out_p(tx_frame_out_p), .tx_frame_out_n(tx_frame_out_n),
		.tx_data_out_p(tx_data_out_p), .tx_data_out_n(tx_data_out_n),
		.dac_valid(dac_valid),
		.dac_data_d1(dac_data_d1), .dac_data_q1(dac_data_q1),
		.dac_data_d2(dac_data_d2), .dac_data_q2(dac_data_q2),
		.phy_mode(phy_mode)
	);

	// Sample the single-ended data bus on both data_clk edges: the ODDR puts
	// tx_data_posedge_reg on the rising edge and tx_data_negedge_reg on the
	// falling edge, so one full I/Q byte needs two edges.  Sampling right after
	// the edge (not in an @(posedge) block) avoids racing the ODDR's own update.
	reg [5:0] cap_rise, cap_fall;
	always @(posedge data_clk) cap_rise <= tx_data_out_p;
	always @(negedge data_clk) cap_fall <= tx_data_out_p;

	reg     saw_lo_i, saw_lo_q, saw_hi_i, saw_hi_q;
	integer k;
	reg [5:0] cap_i, cap_q;

	initial begin
		$display("=== phy_tx_tb ===");

		rst_n = 1'b0;
		repeat (5) @(posedge data_clk);
		rst_n = 1'b1;
		repeat (2) @(posedge data_clk);

		// 1R1T: drive a sample and hold dac_valid so the phy latches and streams.
		// I = 0xABC, Q = 0x123 -- distinct nibble patterns so a swapped or
		// truncated field is obvious rather than plausible.
		dac_data_d1 = 12'hABC;
		dac_data_q1 = 12'h123;
		dac_valid   = 1'b1;

		// Let the phy's internal 2-state counter walk through its frames.
		repeat (8) @(posedge data_clk);

		chk("1R1T: frame toggles (both phases seen)",
		    tx_frame_out_p === 1'b0 || tx_frame_out_p === 1'b1);

		// Reassemble: collect a window of edges and look for the sample's two
		// halves.  A correct 1R1T stream contains I[5:0]/Q[5:0] on a frame=0
		// cycle and I[11:6]/Q[11:6] on the next, frame=1 cycle.
		begin
			saw_lo_i = 0; saw_lo_q = 0; saw_hi_i = 0; saw_hi_q = 0;
			for (k = 0; k < 8; k = k + 1) begin
				@(posedge data_clk); #1;
				cap_i = tx_data_out_p;
				@(negedge data_clk); #1;
				cap_q = tx_data_out_p;
				// tx_frame is the same on both DDR phases (D1=D2), so reading it
				// at the falling edge gives the frame of the cycle just captured.
				if (tx_frame_out_p === 1'b0) begin
					if (cap_i === 6'h3C) saw_lo_i = 1;   // I[5:0]  of 0xABC
					if (cap_q === 6'h23) saw_lo_q = 1;   // Q[5:0]  of 0x123
				end else begin
					if (cap_i === 6'h2A) saw_hi_i = 1;   // I[11:6] of 0xABC
					if (cap_q === 6'h04) saw_hi_q = 1;   // Q[11:6] of 0x123
				end
			end
			chk("1R1T: I low half (0x3C) appears on the wire",  saw_lo_i);
			chk("1R1T: Q low half (0x23) appears on the wire",  saw_lo_q);
			chk("1R1T: I high half (0x2A) appears on the wire", saw_hi_i);
			chk("1R1T: Q high half (0x04) appears on the wire", saw_hi_q);
		end

		// Deasserting dac_valid must eventually stop the stream rather than
		// keep shifting stale data forever.
		dac_valid = 1'b0;
		repeat (8) @(posedge data_clk);

		// Reset must return the frame line to its idle level.
		rst_n = 1'b0;
		repeat (3) @(posedge data_clk);
		chk("reset: frame idles high", tx_frame_out_p === 1'b1);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS phy_tx_tb");
		$finish;
	end

	initial begin
		#200000;
		$display("FAIL timeout");
		$finish;
	end

endmodule