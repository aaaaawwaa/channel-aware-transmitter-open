`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// dds_inc_tb -- checks the TX tone generator's frequency and scaling.
//
// dds_inc mirrors the vendor example's AD9361 internal DDS arithmetic:
//   f_out = phase_inc / 2^12 * f_clk
// so with the example's DDS_PHASE_INC = 128 and a 30 MHz DATA_CLK the tone must
// come out at fs/32 = 937.5 kHz.  The TB counts zero crossings over a known
// window rather than trusting the accumulator width, because that is the number
// that shows up on a spectrum analyser.
//
// The `ce` input is the sample strobe: the phase may only advance on the cycles
// where it is high, which is how the module is driven in 1R1T (one sample per
// two DATA_CLK cycles).
//////////////////////////////////////////////////////////////////////////////////

module dds_inc_tb;

	reg         clk = 1'b0;
	reg         rst_n = 1'b0;
	reg         ce = 1'b1;
	reg  [11:0] phase_inc = 12'd128;

	wire [11:0] dout;

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

	always #16.6665 clk = ~clk;      // 30 MHz

	dds_inc dut (
		.clk(clk),
		.rst_n(rst_n),
		.ce(ce),
		.phase_inc(phase_inc),
		.dout(dout)
	);

	// Count rising zero crossings (negative -> non-negative) over a window of
	// exactly 300000 clocks = 10 ms.  fs/32 = 937.5 kHz means 9375 cycles in
	// 10 ms, and each cycle contributes one such crossing.
	integer crossings;
	reg prev_neg;
	always @(posedge clk) begin
		if (rst_n && $signed(dout) >= 0 && prev_neg) crossings = crossings + 1;
		prev_neg <= $signed(dout) < 0;
	end

	// Peak and trough, to check the ±2048 scaling of the 8-bit table.
	integer vmax, vmin;
	reg [11:0] held;
	always @(posedge clk) begin
		if (rst_n) begin
			if ($signed(dout) > vmax) vmax = $signed(dout);
			if ($signed(dout) < vmin) vmin = $signed(dout);
		end
	end

	initial begin
		$display("=== dds_inc_tb ===");

		crossings = 0; prev_neg = 1'b0; vmax = -4096; vmin = 4096;

		rst_n = 1'b0;
		repeat (4) @(posedge clk);
		rst_n = 1'b1;

		// 300000 clocks at 30 MHz = 10 ms.
		repeat (300000) @(posedge clk);

		// 128/4096 * 30 MHz = 937.5 kHz -> 9375 crossings in 10 ms.  Allow a
		// couple of edges of slack for the window boundary.
		chk("128 @ 30 MHz gives 937.5 kHz (+/- 1 cycle)",
		    crossings >= 9374 && crossings <= 9376);
		$display("  crossings=%0d (expect 9375)", crossings);

		chk("output spans a full-scale sine (peak near +2032)",
		    vmax > 2000 && vmax <= 2047);
		chk("output spans a full-scale sine (trough near -2048)",
		    vmin < -2000 && vmin >= -2048);
		$display("  vmax=%0d vmin=%0d", vmax, vmin);

		// The strobe must gate the accumulator: with ce low the phase is frozen,
		// so the output must stop moving.
		ce = 1'b0;
		repeat (8) @(posedge clk);
		#1;
		held = dout;
		repeat (64) @(posedge clk);
		#1;
		chk("ce low freezes the phase", dout === held);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS dds_inc_tb");
		$finish;
	end

	initial begin
		#20000000;
		$display("FAIL timeout");
		$finish;
	end

endmodule