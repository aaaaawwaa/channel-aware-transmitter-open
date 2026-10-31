`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_sample_reader_tb -- playback order, the byte-address rule, wrap, and ce.
//
// HOW THE ADDRESS RULE IS MADE TESTABLE
// ------------------------------------------------------------------------------
// The word stored at index k is {16'(k), 16'(k)}, so the sample value IS the
// index.  The model indexes its array with bram_addr[13:2] -- the byte address
// divided by four, which is what a byte-addressed block RAM port does.  A reader
// that forgot the shift and handed out a WORD address would therefore read
// mem[k>>2] and present k/4: a sequence of four-fold repeats instead of 0,1,2,3.
// That is the failure the design notes call silent, and here it is visible.
//
// The check is not "the sequence looks right" but "the largest address the BRAM
// saw was (length-1)*4".  With length 16 that is 60 for a byte address and 15
// for a word address, so the two cannot be confused.
//////////////////////////////////////////////////////////////////////////////////

module rf_sample_reader_tb;

	localparam LEN = 16;

	reg         clk = 1'b0;
	reg         rst_n = 1'b0;
	reg         ce = 1'b0;
	reg         enable = 1'b0;
	reg  [12:0] length = LEN;
	reg         wrap = 1'b1;

	wire        bram_en;
	wire [31:0] bram_addr;
	wire [31:0] bram_rdata;
	wire [11:0] sample_i, sample_q;
	wire [12:0] pos;
	wire        active;

	integer pass_cnt = 0, fail_cnt = 0;

	// 8*128 bits = 128 characters.  The 256-bit port the existing TBs use holds
	// only 32, so any longer check name comes out truncated from the LEFT in the
	// log -- "1R1T: it wrapped" printed as "ped (a 17th sample...".  The count
	// still works (the line still starts with PASS) but a truncated name is the
	// part you need when it FAILS.
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

	always #4 clk = ~clk;      // 125 MHz; the rate is irrelevant, the ce ratio is not

	// ---------------------------------------------------------------- BRAM model
	// 4096 x 32, one cycle of read latency, port B byte addressed.
	reg [31:0] mem [0:4095];
	reg [31:0] rdata_r = 32'd0;
	integer    max_addr = 0;
	integer    bad_align = 0;
	integer    k;

	always @(posedge clk) begin
		if (bram_en) begin
			rdata_r <= mem[bram_addr[13:2]];
			if (bram_addr > max_addr) max_addr = bram_addr;
			if (bram_addr[1:0] != 2'b00) bad_align = bad_align + 1;
		end
	end
	assign bram_rdata = rdata_r;

	rf_sample_reader dut (
		.clk(clk), .rst_n(rst_n), .ce(ce), .enable(enable),
		.length(length), .wrap(wrap),
		.bram_en(bram_en), .bram_addr(bram_addr), .bram_rdata(bram_rdata),
		.sample_i(sample_i), .sample_q(sample_q), .pos(pos), .active(active)
	);

	// ------------------------------------------------------------------ capture
	// sample_* are registered on the ce edge, so they hold their new value on the
	// cycle AFTER ce.  Capture there, not on ce itself.
	reg ce_d = 1'b0;
	always @(posedge clk) ce_d <= ce;

	integer n_cap = 0;
	integer cap_i [0:63];
	integer cap_q [0:63];
	always @(posedge clk) begin
		if (rst_n && ce_d && n_cap < 64) begin
			cap_i[n_cap] = sample_i;
			cap_q[n_cap] = sample_q;
			n_cap = n_cap + 1;
		end
	end

	// Drive the sample strobe at a fixed ratio: 2 cycles per sample (1R1T) or
	// 4 (2R2T).  `period` is the number of clocks between rising ce edges.
	integer period = 2;
	integer tick = 0;
	always @(posedge clk) begin
		if (!rst_n) begin
			tick <= 0; ce <= 1'b0;
		end else begin
			if (tick == 0) begin
				ce   <= 1'b1;
				tick <= period - 1;
			end else begin
				ce   <= 1'b0;
				tick <= tick - 1;
			end
		end
	end

	integer i;
	integer order_ok;
	task reset_and_prime;
		begin
			rst_n = 1'b0; enable = 1'b0;
			repeat (4) @(posedge clk);
			rst_n = 1'b1;
			repeat (2) @(posedge clk);
			enable = 1'b1;
			repeat (4) @(posedge clk);   // let the prime settle
			n_cap = 0;
		end
	endtask

	initial begin
		$display("=== rf_sample_reader_tb ===");

		for (k = 0; k < 4096; k = k + 1)
			mem[k] = {k[15:0], k[15:0]};

		// ---------------------------------------------------------------- 1R1T
		period = 2;
		reset_and_prime();
		repeat (40) @(posedge clk);

		chk("1R1T: 16 samples captured", n_cap >= 16);
		// A flag, not `if (i == 16)` after the loop: i is 16 on normal completion
		// too, so that form cannot tell "all matched" from "broke out early" and
		// would report PASS on a mismatch.
		order_ok = 1;
		for (i = 0; i < 16; i = i + 1) begin
			if (cap_i[i] !== (i % LEN) || cap_q[i] !== (i % LEN)) begin
				if (order_ok)
					$display("  first mismatch at %0d: i=%0d q=%0d (expected %0d)",
					         i, cap_i[i], cap_q[i], i % LEN);
				order_ok = 0;
			end
		end
		chk("1R1T: sample order is 0..15 then wraps", order_ok);

		chk("1R1T: it wrapped (a 17th sample is back at 0)",
		    n_cap >= 18 && cap_i[16] == 0 && cap_i[17] == 1);
		chk("byte address reached (len-1)*4 = 60, not (len-1) = 15",
		    max_addr == (LEN - 1) * 4);
		chk("every address was word aligned", bad_align == 0);
		$display("  max_addr=%0d (byte=%0d word=%0d)  bad_align=%0d",
		         max_addr, (LEN-1)*4, LEN-1, bad_align);

		// ------------------------------------------------- ce gates the advance
		ce = 1'b0;
		repeat (20) @(posedge clk);
		chk("ce low: the reader does not advance",
		    sample_i === cap_i[n_cap-1]);
		chk("ce low: active stays as it was", active === 1'b1);

		// ------------------------------------------------------------ 2R2T ratio
		// Same buffer, four cycles per sample.  The order must be identical --
		// only the wall-clock spacing changes.  A reader with its own divider
		// instead of the shared ce would drift here.
		period = 4;
		reset_and_prime();
		repeat (60) @(posedge clk);
		chk("2R2T: still 0,1,2,3,...",
		    n_cap >= 8 &&
		    cap_i[0]==0 && cap_i[1]==1 && cap_i[2]==2 && cap_i[3]==3 &&
		    cap_i[4]==4 && cap_i[5]==5 && cap_i[6]==6 && cap_i[7]==7);

		// --------------------------------------------------------------- no wrap
		period = 2;
		wrap = 1'b0;
		reset_and_prime();
		repeat (60) @(posedge clk);
		chk("wrap=0: holds the last sample instead of restarting",
		    n_cap >= 20 &&
		    cap_i[15] == LEN-1 && cap_i[16] == LEN-1 && cap_i[19] == LEN-1);
		wrap = 1'b1;

		// ------------------------------------------------------------- NV: disable
		// The negative case the design demands: with enable low the reader must
		// not be driving the TX path at all.  Without this, "it played the right
		// samples" is compatible with a reader that keeps running when it should
		// have stopped -- and the TX path would emit stale data forever.
		enable = 1'b0;
		repeat (10) @(posedge clk);
		chk("NV: enable low -> active deasserts", active === 1'b0);
		chk("NV: enable low -> bram_en deasserts", bram_en === 1'b0);

		// Re-enabling must start from word 0, not resume mid-buffer.
		reset_and_prime();
		repeat (40) @(posedge clk);
		chk("re-enable restarts at word 0", n_cap >= 4 && cap_i[0] == 0 && cap_i[1] == 1);

		// --------------------------------------------------------------- NV: length
		// A zero length must not underflow the wrap compare and park the reader
		// at 8191.  Clamped, it should behave as length 1: every sample is word 0.
		enable = 1'b0;
		repeat (4) @(posedge clk);
		length = 13'd0;
		max_addr = 0;          // the monitor is not self-resetting; clear it here
		enable = 1'b1;
		repeat (6) @(posedge clk);
		n_cap = 0;
		repeat (20) @(posedge clk);
		chk("NV: length 0 is clamped, every sample is word 0",
		    n_cap >= 4 && cap_i[0] == 0 && cap_i[3] == 0 && max_addr < 8);
		length = LEN;

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS rf_sample_reader_tb");
		$finish;
	end

	initial begin
		#500000;
		$display("FAIL timeout");
		$finish;
	end

endmodule
