`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_decimate_tb -- grouping, the divide, the sign of the rounding, and rate.
//
// STIMULUS IS CLOCKED, NOT DRIVEN FROM THE TEST SEQUENCE
// ------------------------------------------------------------------------------
// The first version of this TB pulsed `ce` from a task with blocking
// assignments, immediately after @(posedge clk).  That races the DUT, which
// samples `ce` in its own @(posedge clk) block at the same simulation time: when
// the task's `ce = 0` won the scheduling order, the DUT saw ce already low and
// missed the sample, and every captured value came out one feed late.  The
// symptom looked like a DUT bug -- "got 0 0 1 2" for a ramp -- and was not one.
// `ce` and the data are now produced by clocked logic, so there is no window in
// which the test can move them under the DUT.
//
// The ramp makes every group mean an exact known value: for D=4 over samples
// 0..15 the means are 1.5, 5.5, 9.5, 13.5, which an arithmetic right shift
// floors to 1, 5, 9, 13.
//
// The rounding direction is checked on purpose.  For the negated ramp the true
// means are -1.5, -5.5, -9.5, -13.5 and the three plausible rules differ:
//   floor (arithmetic shift) -> -2 -6 -10 -14   <- what this design documents
//   round to nearest         -> -1 -5  -9 -13
//   round toward zero        -> -1 -5  -9 -13
// So the check below passes for exactly one of them and can fail.
//////////////////////////////////////////////////////////////////////////////////

module rf_decimate_tb;

	reg         clk = 1'b0;
	reg         rst_n = 1'b0;
	reg  [2:0]  ratio_sel = 3'd0;

	wire        out_ce;
	wire [11:0] out_i, out_q;

	// ------------------------------------------------------------------ stimulus
	integer period = 2;        // clocks between ce pulses
	integer tick = 0;
	reg     ce = 1'b0;

	// ce stops after exactly `feed_limit` pulses.  Without this the strobe runs
	// free and the window used by the test decides how many groups are produced
	// -- so an exact output count becomes a property of the wait, not of the
	// decimator.  (The first version had that bug: the values were all correct
	// and the count checks failed anyway.)
	integer feed_limit = 0;
	integer pulses = 0;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			tick <= 0;
			ce   <= 1'b0;
		end else if (pulses >= feed_limit) begin
			ce   <= 1'b0;
			tick <= 0;
		end else if (tick == 0) begin
			ce   <= 1'b1;
			tick <= period - 1;
		end else begin
			ce   <= 1'b0;
			tick <= tick - 1;
		end
	end

	// feed_mode 0: 0,1,2,...   1: 0,-1,-2,...   2: 2047 then zeros
	integer feed_mode = 0;
	integer feed_n = 0;
	reg [11:0] in_i = 12'd0;
	reg [11:0] in_q = 12'd0;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) feed_n <= 0;
		else if (ce) feed_n <= feed_n + 1;
	end

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) pulses <= 0;
		else if (ce && pulses < feed_limit) pulses <= pulses + 1;
	end

	// Combinational from feed_n, so the value is already settled on the cycle ce
	// is high and the DUT samples both together.
	always @(*) begin
		case (feed_mode)
			1:       begin in_i = -feed_n[11:0]; in_q = -feed_n[11:0]; end
			2:       begin in_i = (feed_n == 0) ? 12'd2047 : 12'd0;
			               in_q = in_i; end
			default: begin in_i = feed_n[11:0];  in_q = feed_n[11:0]; end
		endcase
	end

	integer pass_cnt = 0, fail_cnt = 0;
	integer n_out = 0;
	integer cap_i [0:127];
	integer cap_q [0:127];

	task chk;
		input [8*128-1:0] name;
		input             cond;
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

	always #5 clk = ~clk;      // 100 MHz; only the ce rate matters

	rf_decimate dut (
		.clk(clk), .rst_n(rst_n), .ce(ce), .ratio_sel(ratio_sel),
		.in_i(in_i), .in_q(in_q),
		.out_ce(out_ce), .out_i(out_i), .out_q(out_q)
	);

	// out_ce and out_* are assigned at the same edge, so they both hold their
	// new values on the cycle after that edge -- which is where this reads them.
	always @(posedge clk) begin
		if (rst_n && out_ce && n_out < 128) begin
			// $signed, because out_i is an unsigned [11:0] and an integer capture
			// would hold 4094 rather than -2 for the negated ramp.  The values
			// were right all along; the comparison was not.
			cap_i[n_out] = $signed(out_i);
			cap_q[n_out] = $signed(out_q);
			n_out = n_out + 1;
		end
	end

	integer k;
	task run;                       // reset, feed exactly `nsamp` samples, settle
		input integer mode;
		input integer nsamp;
		begin
			rst_n = 1'b0;
			repeat (3) @(posedge clk);
			feed_mode  = mode;
			feed_n     = 0;
			feed_limit = nsamp;
			n_out      = 0;
			rst_n = 1'b1;
			repeat ((nsamp + 2) * period + 8) @(posedge clk);
		end
	endtask

	initial begin
		$display("=== rf_decimate_tb ===");

		// ------------------------------------------------------------ D = 1 bypass
		ratio_sel = 3'd0;
		run(0, 8);
		chk("D=1: one output per input (bypass)", n_out == 8);
		begin : d1
			integer ok;
			ok = (n_out == 8);
			for (k = 0; k < 8; k = k + 1) if (cap_i[k] !== k) ok = 0;
			chk("D=1: output equals input exactly", ok);
			if (!ok) $display("  got %0d %0d %0d %0d %0d", cap_i[0], cap_i[1], cap_i[2], cap_i[3], cap_i[4]);
		end

		// -------------------------------------------------------- D = 4, positive
		ratio_sel = 3'd2;
		run(0, 16);
		chk("D=4: 16 inputs give 4 outputs", n_out == 4);
		chk("D=4: means 1,5,9,13 (floor of 1.5,5.5,9.5,13.5)",
		    n_out == 4 && cap_i[0]==1 && cap_i[1]==5 && cap_i[2]==9 && cap_i[3]==13);
		chk("D=4: I and Q treated identically",
		    n_out == 4 && cap_q[0]==1 && cap_q[3]==13);

		// ------------------------------------------- D = 4, NEGATIVE: the rounding
		run(1, 16);
		chk("D=4: negative means floor to -2,-6,-10,-14 (not -1,-5,-9,-13)",
		    n_out == 4 && cap_i[0]==-2 && cap_i[1]==-6 && cap_i[2]==-10 && cap_i[3]==-14);
		if (n_out == 4 && !(cap_i[0]==-2 && cap_i[3]==-14))
			$display("  got %0d %0d %0d %0d", cap_i[0], cap_i[1], cap_i[2], cap_i[3]);

		// ------------------------------------------------------------- D = 2 and 8
		ratio_sel = 3'd1;
		run(0, 8);
		chk("D=2: means 0,2,4,6 (floor of 0.5,2.5,4.5,6.5)",
		    n_out == 4 && cap_i[0]==0 && cap_i[1]==2 && cap_i[2]==4 && cap_i[3]==6);

		ratio_sel = 3'd3;
		run(0, 16);
		chk("D=8: 16 inputs give 2 outputs, means 3 and 11",
		    n_out == 2 && cap_i[0]==3 && cap_i[1]==11);

		// ------------------------------------------------------------- D = 16 rate
		ratio_sel = 3'd4;
		run(0, 32);
		chk("D=16: 32 inputs give 2 outputs", n_out == 2);
		chk("D=16: means 7 and 23 (floor of 7.5 and 23.5)",
		    n_out == 2 && cap_i[0]==7 && cap_i[1]==23);

		// ------------------------------------------- the ce PERIOD must not matter
		// Groups are counted in ce pulses, not in clock cycles, so a 2R2T-style
		// strobe (four cycles per sample) has to give exactly the same means as
		// 1R1T's two.  A decimator that counted clocks instead would silently
		// halve its ratio the moment phy_mode changed.
		period = 4;
		ratio_sel = 3'd2;
		run(0, 16);
		chk("D=4 with a 4-cycle ce period gives the same means 1,5,9,13",
		    n_out == 4 && cap_i[0]==1 && cap_i[1]==5 && cap_i[2]==9 && cap_i[3]==13);
		period = 2;

		// --------------------------------------------- NV: a pulse is NOT preserved
		// The claim that matters for the pulsed-interference label: a single
		// full-scale sample is divided by D.  At D=16 it comes out as 127.  If
		// this ever returns 2047, the decimator is not in the path.
		//
		// ratio_sel is re-set here rather than inherited: the ce-period case
		// above leaves it at D=4, and inheriting it made this check read 511 --
		// a real result for the wrong ratio, which is the kind of thing that
		// looks like a decimator fault.
		ratio_sel = 3'd4;
		run(2, 16);
		chk("NV: a single 2047 spike at D=16 comes out as 127, not 2047",
		    n_out >= 1 && cap_i[0] == 127);
		if (n_out >= 1 && cap_i[0] != 127) $display("  got %0d", cap_i[0]);

		// --------------------------------------------------------- NV: ce gates it
		// ce is driven by clocked logic now, so the test cannot hold it down by
		// hand; zero pulses is the equivalent and is the way it is actually
		// stopped in hardware.
		ratio_sel = 3'd0;
		run(0, 0);
		chk("NV: no ce pulses produces no output", n_out == 0);

		// ------------------------------------------------- NV: ratio_sel clamped
		// An out-of-range ratio must not make the group counter unreachable.
		// Clamped to 16, 16 inputs still yield exactly one output.
		ratio_sel = 3'd7;
		run(0, 16);
		chk("NV: ratio_sel=7 is clamped to 16 and still emits",
		    n_out == 1 && cap_i[0] == 7);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS rf_decimate_tb");
		$finish;
	end

	initial begin
		#400000;
		$display("FAIL timeout");
		$finish;
	end

endmodule
