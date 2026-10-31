`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rf_sample_capture_tb -- the write sequence, the config latch, and the format.
//
// Three things here fail silently in hardware and are therefore worth a test
// rather than an inspection:
//
//   * the byte address.  The monitor decodes addr[13:2] as the word index, so a
//     capture that handed out word addresses would write every sample to the
//     wrong place and the buffer would look shuffled rather than empty.
//   * the sign extension.  I and Q are sign-extended from 12 to 16 bits, so a
//     captured -1 reads 0xFFFF.  Zero padding would read 0x0FFF -- a large
//     positive number where a small negative one belongs, in every negative
//     sample, with nothing anywhere reporting an error.
//   * the atomic config latch.  A ratio change that takes effect without commit
//     is the failure mode rf_sample_capture exists to prevent: the group being
//     accumulated spans two ratios and the result belongs to neither.
//
// The stimulus is clocked, not driven from the test sequence.  Driving `ce` from
// a task with blocking assignments races the DUT's own clocked sampling -- that
// cost a round of false failures in rf_decimate_tb.
//////////////////////////////////////////////////////////////////////////////////

module rf_sample_capture_tb;

	reg         clk = 1'b0;
	reg         rst_n = 1'b0;

	reg  [2:0]  cfg_ratio = 3'd0;
	reg  [12:0] cfg_len = 13'd4;
	reg         cfg_cont = 1'b0;
	reg         cfg_commit = 1'b0;
	reg         cfg_arm = 1'b0;

	wire        bram_en;
	wire [3:0]  bram_we;
	wire [31:0] bram_addr;
	wire [31:0] bram_wdata;
	wire [12:0] pos;
	wire        done;
	wire        busy;

	integer pass_cnt = 0, fail_cnt = 0;

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

	always #5 clk = ~clk;

	// ------------------------------------------------------------------ stimulus
	integer period = 2;
	integer tick = 0;
	reg     ce = 1'b0;
	integer pulse_limit = 0;      // stop after this many input samples
	integer pulse_n = 0;

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) begin
			tick <= 0; ce <= 1'b0;
		end else if (pulse_n >= pulse_limit) begin
			ce <= 1'b0; tick <= 0;
		end else if (tick == 0) begin
			ce <= 1'b1; tick <= period - 1;
		end else begin
			ce <= 1'b0; tick <= tick - 1;
		end
	end

	always @(posedge clk or negedge rst_n) begin
		if (!rst_n) pulse_n <= 0;
		else if (ce && pulse_n < pulse_limit) pulse_n <= pulse_n + 1;
	end

	// A fixed negative sample, so the sign extension is checkable: -1 must come
	// back as 0xFFFF, not 0x0FFF.
	reg [11:0] in_i = 12'hFFF;
	reg [11:0] in_q = 12'hFFF;

	rf_sample_capture dut (
		.clk(clk), .rst_n(rst_n),
		.ce(ce), .in_i(in_i), .in_q(in_q),
		.cfg_ratio(cfg_ratio), .cfg_len(cfg_len), .cfg_cont(cfg_cont),
		.cfg_commit(cfg_commit), .cfg_arm(cfg_arm),
		.bram_en(bram_en), .bram_we(bram_we),
		.bram_addr(bram_addr), .bram_wdata(bram_wdata),
		.pos(pos), .done(done), .busy(busy)
	);

	// -------------------------------------------------------------- write monitor
	integer n_wr = 0;
	integer wr_word [0:255];      // addr[13:2], the word the write landed on
	integer wr_data [0:255];

	always @(posedge clk) begin
		if (rst_n && bram_en && (bram_we != 4'b0000) && n_wr < 256) begin
			wr_word[n_wr] = bram_addr >> 2;
			wr_data[n_wr] = bram_wdata;
			n_wr = n_wr + 1;
		end
	end

	task cfg_latch;                // write the config, then commit it
		begin
			@(posedge clk);
			cfg_commit = ~cfg_commit;
			repeat (4) @(posedge clk);
		end
	endtask

	task start;                    // clear counters and arm
		input integer nsamp;
		begin
			cfg_arm = 1'b0;
			repeat (4) @(posedge clk);
			n_wr = 0;
			pulse_n = 0;
			pulse_limit = nsamp;
			cfg_arm = 1'b1;
		end
	endtask

	integer i, ok;
	initial begin
		$display("=== rf_sample_capture_tb ===");

		// Release reset first.  The DUT has an async reset and this TB declares
		// rst_n as 0 at time zero; without this line the whole run happens with
		// the capture held in reset and every write check fails with no writes
		// at all -- which reads like a completely dead datapath rather than a
		// missing line in the test.
		rst_n = 1'b0;
		repeat (4) @(posedge clk);
		rst_n = 1'b1;
		repeat (4) @(posedge clk);

		// ---------------------------------------------- one-shot, D=1, 4 samples
		cfg_ratio = 3'd0; cfg_len = 13'd4; cfg_cont = 1'b0;
		cfg_latch();
		start(4);
		repeat (30) @(posedge clk);

		chk("one-shot: exactly len writes", n_wr == 4);
		chk("one-shot: done asserts", done === 1'b1);
		chk("one-shot: busy deasserts", busy === 1'b0);
		ok = 1;
		for (i = 0; i < 4; i = i + 1) if (wr_word[i] !== i) ok = 0;
		chk("one-shot: words are 0,1,2,3 (byte address / 4)", ok);
		if (!ok) $display("  got %0d %0d %0d %0d", wr_word[0], wr_word[1], wr_word[2], wr_word[3]);
		chk("one-shot: last write is at word len-1", n_wr >= 1 && wr_word[n_wr-1] == 3);

		// -------------------------------------------------------------- the format
		// -1 sign-extended: 0xFFFF in both halves.  Zero padding gives 0x0FFF.
		chk("format: -1 sign-extends to 0xFFFF in both I and Q",
		    n_wr >= 1 && wr_data[0][15:0] === 16'hFFFF && wr_data[0][31:16] === 16'hFFFF);
		if (n_wr >= 1 && wr_data[0][15:0] !== 16'hFFFF)
			$display("  I read 0x%04X (zero padded would be 0x0FFF)", wr_data[0][15:0]);

		// ------------------------------------------- NV: writing stops after done
		// A one-shot capture that kept writing would keep overwriting the buffer
		// the PS is about to read, and the corruption would be silent.
		pulse_n = 0; pulse_limit = 32;      // more samples arrive, arm still high
		repeat (40) @(posedge clk);
		chk("NV: no further writes after done", n_wr == 4);

		// ---------------------------------------------------------- releasing arm
		cfg_arm = 1'b0;
		repeat (6) @(posedge clk);
		chk("releasing arm rewinds pos to 0", pos == 13'd0);
		chk("releasing arm clears done", done === 1'b0);

		// ------------------------------------------------------- continuous mode
		cfg_cont = 1'b1; cfg_len = 13'd4; cfg_ratio = 3'd0;
		cfg_latch();
		start(10);
		repeat (50) @(posedge clk);
		chk("continuous: more writes than length", n_wr >= 9);
		chk("continuous: it wrapped back to 0",
		    n_wr >= 6 && wr_word[4] == 0 && wr_word[5] == 1);
		chk("continuous: done never asserts", done === 1'b0);

		// ------------------------------------------ the config latch, and its NV
		// D=4 via commit.  Then change cfg_ratio to D=16 WITHOUT committing: the
		// decimation must carry on at D=4.  That is the whole point of the latch.
		cfg_cont = 1'b0; cfg_len = 13'd64; cfg_ratio = 3'd2;   // D = 4
		cfg_latch();
		start(8);
		repeat (30) @(posedge clk);
		chk("D=4 latched: 8 input samples give 2 writes", n_wr == 2);

		cfg_arm = 1'b0;
		repeat (4) @(posedge clk);
		n_wr = 0;
		cfg_arm = 1'b1;
		pulse_n = 0; pulse_limit = 8;

		// Mid-capture: change the ratio on the bus, but do NOT commit.
		@(posedge clk);
		cfg_ratio = 3'd4;                    // would be D=16 if it took effect
		repeat (30) @(posedge clk);
		chk("NV: uncommitted ratio change does NOT take effect (still 2 writes)",
		    n_wr == 2);
		if (n_wr != 2) $display("  got %0d writes -- the latch is not holding", n_wr);

		// Now commit it.  The next capture must be at D=16.
		cfg_arm = 1'b0;
		repeat (4) @(posedge clk);
		cfg_latch();
		n_wr = 0;
		pulse_n = 0; pulse_limit = 16;
		cfg_arm = 1'b1;
		repeat (50) @(posedge clk);
		chk("after commit the new ratio takes effect (16 samples -> 1 write)",
		    n_wr == 1);

		// ------------------------------------------------------- NV: len clamped
		// length 0 would underflow the wrap compare; clamped to 1 the capture
		// writes exactly one sample and stops.
		cfg_arm = 1'b0;
		repeat (4) @(posedge clk);
		cfg_ratio = 3'd0; cfg_len = 13'd0; cfg_cont = 1'b0;
		cfg_latch();
		n_wr = 0; pulse_n = 0; pulse_limit = 8;
		cfg_arm = 1'b1;
		repeat (40) @(posedge clk);
		chk("NV: len 0 is clamped to 1 (one write, then done)",
		    n_wr == 1 && done === 1'b1);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS rf_sample_capture_tb");
		$finish;
	end

	initial begin
		#400000;
		$display("FAIL timeout");
		$finish;
	end

endmodule
