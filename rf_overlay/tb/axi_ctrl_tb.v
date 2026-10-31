`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// axi_ctrl_tb -- self-checking testbench for the read-only STATUS register.
//
// The register answers one question: is the AD9361's DATA_CLK actually reaching
// the FPGA, and how fast?  A DATA_CLK-domain Gray counter is sampled by a
// self-running 2^WIN_LOG2-cycle FCLK0 window; software reads the count together
// with a rolling sequence number so it never reads a torn window.
//
// The decisive test is the NEGATIVE one: with data_clk held still the register
// must report zero cycles and clk_seen=0.  A counter accidentally clocked by
// FCLK0 would pass every positive test and fail this one -- so data_clk is
// deliberately driven at a DIFFERENT rate from aclk (33.333 ns vs 20 ns).
//////////////////////////////////////////////////////////////////////////////////

module axi_ctrl_tb;

	localparam integer AW       = 6;
	localparam real    ACLK_NS  = 20.0;      // FCLK0 = 50 MHz
	localparam real    DCLK_NS  = 33.333;    // DATA_CLK = 30 MHz (candidate A)
	localparam integer WIN_LOG2 = 15;
	localparam real    WIN_NS   = 32768.0 * ACLK_NS;          // 655.36 us
	localparam real    EXP_EDGES = WIN_NS / DCLK_NS;          // ~19660.8

	reg aclk = 1'b0, aresetn = 1'b0;
	reg data_clk = 1'b0;
	reg dclk_en  = 1'b0;
	reg rst_source_ok = 1'b0;

	// AXI-Lite
	reg  [AW-1:0] awaddr = 0;
	reg           awvalid = 0;
	wire          awready;
	reg  [31:0]   wdata = 0;
	reg  [3:0]    wstrb = 4'hF;
	reg           wvalid = 0;
	wire          wready;
	wire  [1:0]   bresp;
	wire          bvalid;
	reg           bready = 1;
	reg  [AW-1:0] araddr = 0;
	reg           arvalid = 0;
	wire          arready;
	wire  [31:0]  rdata;
	wire  [1:0]   rresp;
	wire          rvalid;
	reg           rready = 1;

	wire [6:0]  idelay_en;
	wire [4:0]  idelay_tap;
	wire [11:0] phase_inc;
	wire        phy_mode;
	wire        wave_sel;
	wire        src_sel;
	wire [12:0] smp_len;
	wire        smp_wrap;
	wire        smp_commit;
	wire        cfg_arm;
	wire        cfg_cont;
	wire        cfg_commit;
	wire [2:0]  cfg_ratio;
	wire [12:0] cfg_len;

	// Driven by this TB so the read-only registers have something to read.
	// Leaving them undriven would default to 0 in XSIM and the reg7/reg13 checks
	// would pass for the wrong reason -- a zero read back from a register whose
	// whole point is that it does not return what was written.
	reg  [12:0] smp_pos = 13'd0;
	reg  [12:0] cap_pos = 13'd0;
	reg         cap_done = 1'b0;

	integer pass_cnt = 0, fail_cnt = 0;

	task chk;
		// 128 characters, not 32.  A packed vector holds a fixed number of bytes
		// and %0s right-aligns the label in it, so at [255:0] every label longer
		// than 32 characters is silently truncated FROM THE LEFT -- the check
		// still runs and still judges correctly, the log just stops saying which
		// one it was.  (-1 for the NUL.)
		input [8*128-1:0] name;
		input         cond;
		begin
			if (cond) begin pass_cnt = pass_cnt + 1; $display("PASS %0s", name); end
			else      begin fail_cnt = fail_cnt + 1; $display("FAIL %0s", name); end
		end
	endtask

	always #(ACLK_NS/2.0) aclk = ~aclk;

	// DATA_CLK: stopped (held low) when dclk_en=0, otherwise toggling.
	initial forever begin
		#(DCLK_NS/2.0);
		data_clk = dclk_en ? ~data_clk : 1'b0;
	end

	axi_ctrl_v1_0 #(.C_S00_AXI_DATA_WIDTH(32), .C_S00_AXI_ADDR_WIDTH(AW)) dut (
		.idelay_en(idelay_en), .idelay_tap(idelay_tap),
		.phase_inc(phase_inc), .phy_mode(phy_mode),
		.wave_sel(wave_sel),
		.src_sel(src_sel), .smp_len(smp_len), .smp_wrap(smp_wrap),
		.smp_commit(smp_commit), .smp_pos(smp_pos),
		.cfg_arm(cfg_arm), .cfg_cont(cfg_cont), .cfg_commit(cfg_commit),
		.cfg_ratio(cfg_ratio), .cfg_len(cfg_len),
		.cap_pos(cap_pos), .cap_done(cap_done),
		.data_clk(data_clk), .rst_source_ok(rst_source_ok),
		.s00_axi_aclk(aclk), .s00_axi_aresetn(aresetn),
		.s00_axi_awaddr(awaddr), .s00_axi_awprot(3'b0),
		.s00_axi_awvalid(awvalid), .s00_axi_awready(awready),
		.s00_axi_wdata(wdata), .s00_axi_wstrb(wstrb),
		.s00_axi_wvalid(wvalid), .s00_axi_wready(wready),
		.s00_axi_bresp(bresp), .s00_axi_bvalid(bvalid), .s00_axi_bready(bready),
		.s00_axi_araddr(araddr), .s00_axi_arprot(3'b0),
		.s00_axi_arvalid(arvalid), .s00_axi_arready(arready),
		.s00_axi_rdata(rdata), .s00_axi_rresp(rresp),
		.s00_axi_rvalid(rvalid), .s00_axi_rready(rready)
	);

	task axi_write;
		input [AW-1:0] addr;
		input [31:0]   data;
		begin
			@(posedge aclk);
			awaddr <= addr; wdata <= data; awvalid <= 1'b1; wvalid <= 1'b1;
			@(posedge aclk);
			while (!(awready && wready)) @(posedge aclk);
			awvalid <= 1'b0; wvalid <= 1'b0;
			@(posedge aclk);
			while (!bvalid) @(posedge aclk);
		end
	endtask

	task axi_read;
		input  [AW-1:0] addr;
		output [31:0]   data;
		begin
			@(posedge aclk);
			araddr <= addr; arvalid <= 1'b1;
			@(posedge aclk);
			while (!arready) @(posedge aclk);
			arvalid <= 1'b0;
			@(posedge aclk);
			while (!rvalid) @(posedge aclk);
			data = rdata;
		end
	endtask

	// STATUS bit fields (offset 0x10)
	function [3:0]  f_wlog2;  input [31:0] w; begin f_wlog2  = w[27:24]; end endfunction
	function [15:0] f_cycles; input [31:0] w; begin f_cycles = w[23:8];  end endfunction
	function [3:0]  f_seq;    input [31:0] w; begin f_seq    = w[7:4];   end endfunction
	function        f_active; input [31:0] w; begin f_active = w[3];     end endfunction
	function        f_rso;    input [31:0] w; begin f_rso    = w[2];     end endfunction
	function        f_done;   input [31:0] w; begin f_done   = w[1];     end endfunction
	function        f_seen;   input [31:0] w; begin f_seen   = w[0];     end endfunction

	// Wait until win_seq changes, then return the freshly completed window.
	task wait_new_window;
		output [31:0] st;
		reg [31:0] s0, s1;
		integer    guard;
		begin
			axi_read(6'h10, s0);
			guard = 0;
			s1 = s0;
			while (f_seq(s1) == f_seq(s0) && guard < 200000) begin
				axi_read(6'h10, s1);
				guard = guard + 1;
			end
			if (guard >= 200000) $display("WARN wait_new_window timed out");
			st = s1;
		end
	endtask

	reg [31:0] st, st2;

	initial begin
		$display("=== axi_ctrl_tb ===");

		aresetn = 1'b0;
		dclk_en = 1'b0;             // DATA_CLK absent
		rst_source_ok = 1'b0;
		repeat (5) @(posedge aclk);
		aresetn = 1'b1;
		repeat (2) @(posedge aclk);

		// ---------------------------------------------------------------
		// Group 1 -- NEGATIVE criterion: no DATA_CLK => zero, not "unknown".
		// ---------------------------------------------------------------
		wait_new_window(st);
		chk("dead: win_cycles == 0",        f_cycles(st) == 16'd0);
		chk("dead: clk_seen == 0",          f_seen(st)   == 1'b0);
		chk("dead: rst_source_ok == 0",     f_rso(st)    == 1'b0);
		chk("dead: WIN_LOG2 field == 15",   f_wlog2(st)  == 4'd15);
		chk("dead: window FSM still runs",  f_seq(st)    != f_seq(32'h0) || f_done(st) == 1'b1);

		// ---------------------------------------------------------------
		// Group 2 -- POSITIVE criterion: DATA_CLK at 30 MHz.
		// ---------------------------------------------------------------
		dclk_en = 1'b1;
		rst_source_ok = 1'b1;
		wait_new_window(st);
		wait_new_window(st);        // discard the first (partial) window
		chk("alive: clk_seen == 1",         f_seen(st) == 1'b1);
		chk("alive: rst_source_ok == 1",    f_rso(st)  == 1'b1);
		chk("alive: win_cycles in 19660 +/- 2",
		    f_cycles(st) >= 16'd19658 && f_cycles(st) <= 16'd19663);
		chk("alive: win_active low at read", f_active(st) == 1'b0);
		chk("alive: win_done == 1",          f_done(st)   == 1'b1);

		// ---------------------------------------------------------------
		// Group 3 -- slv_reg0..3 must not regress.
		// ---------------------------------------------------------------
		axi_write(6'h00, 32'h0000_007F);
		axi_write(6'h04, 32'h0000_0015);
		axi_write(6'h08, 32'h0000_0ABC);
		axi_write(6'h0C, 32'h0000_0001);
		axi_read(6'h00, st); chk("reg0 == 0x7F",  st[6:0] == 7'h7F);
		axi_read(6'h04, st); chk("reg1 == 0x15",  st[4:0] == 5'h15);
		axi_read(6'h08, st); chk("reg2 == 0xABC", st[11:0] == 12'hABC);
		axi_read(6'h0C, st); chk("reg3 == 1",     st[0] == 1'b1);

		// ------------------------------------------------- the sample-buffer regs
		// These are the path the PS uses to configure and watch the new datapath.
		// Each one is checked at the OUTPUT, not by reading the register back:
		// a read-back would only prove the register file works, which is the
		// tautology this project has been bitten by before.
		axi_write(6'h14, 32'h0000_0007);   // reg5: src_sel | smp_wrap | smp_commit
		axi_write(6'h18, 32'h0000_0123);   // reg6: smp_len
		axi_write(6'h24, 32'h0000_0005);   // reg9: cfg_arm | cfg_commit
		axi_write(6'h2C, 32'h0000_0003);   // reg11: cfg_ratio = 3 (D=8)
		axi_write(6'h30, 32'h0000_0456);   // reg12: cfg_len
		repeat (3) @(posedge aclk);
		chk("reg5 reaches src_sel/smp_wrap/smp_commit",
		    src_sel === 1'b1 && smp_wrap === 1'b1 && smp_commit === 1'b1);
		chk("reg6 reaches smp_len", smp_len == 13'h0123);
		chk("reg9 reaches cfg_arm/cfg_cont/cfg_commit (cont stays 0)",
		    cfg_arm === 1'b1 && cfg_cont === 1'b0 && cfg_commit === 1'b1);
		chk("reg11 reaches cfg_ratio", cfg_ratio == 3'd3);
		chk("reg12 reaches cfg_len",   cfg_len == 13'h0456);

		// -------------------------------------- the read-only regs, and their NV
		// reg7 and reg13 have no write arm.  A write to either must NOT come back
		// as what was written: that is the property, and its failure mode is a
		// write that appears to succeed while the read returns something else.
		smp_pos  = 13'h1ABC;
		cap_pos  = 13'h0DEF;
		cap_done = 1'b1;
		repeat (6) @(posedge aclk);                 // let the 2-stage crossing settle
		axi_write(6'h1C, 32'hFFFF_FFFF);            // reg7: must be ignored
		axi_write(6'h34, 32'hFFFF_FFFF);            // reg13: must be ignored
		axi_read(6'h1C, st);
		chk("reg7 reads smp_pos, not the value written",
		    st[12:0] == 13'h1ABC && st != 32'hFFFF_FFFF);
		if (!(st[12:0] == 13'h1ABC)) $display("  reg7 read 0x%08X", st);
		axi_read(6'h34, st);
		chk("reg13 reads {cap_done,cap_pos}, not the value written",
		    st[12:0] == 13'h0DEF && st[13] === 1'b1 && st != 32'hFFFF_FFFF);
		if (!(st[12:0] == 13'h0DEF)) $display("  reg13 read 0x%08X", st);

		// NV on the NV: with the sources at zero the read must be zero, which
		// proves the checks above were reading the source and not a stuck value.
		smp_pos = 13'd0; cap_pos = 13'd0; cap_done = 1'b0;
		repeat (6) @(posedge aclk);
		axi_read(6'h1C, st);
		chk("reg7 follows its source back down to 0", st[12:0] == 13'd0);
		axi_read(6'h34, st);
		chk("reg13 follows its source back down to 0",
		    st[12:0] == 13'd0 && st[13] === 1'b0);

		// ---------------------------------------------------------------
		// Group 4 -- STATUS must be READ-ONLY: writing 0x10 changes nothing.
		// ---------------------------------------------------------------
		axi_write(6'h10, 32'hFFFF_FFFF);
		wait_new_window(st);
		chk("read-only: WIN_LOG2 still 15",  f_wlog2(st) == 4'd15);
		chk("read-only: cycles still sane",
		    f_cycles(st) >= 16'd19658 && f_cycles(st) <= 16'd19663);
		chk("read-only: reserved bits zero", st[31:28] == 4'h0);
		// win_seq must keep rolling forward (proves the FSM was not clobbered)
		axi_read(6'h10, st2);
		chk("read-only: seq still rolling",  f_seq(st2) != 4'h0 || f_done(st2) == 1'b1);

		// ---------------------------------------------------------------
		// Group 5 -- rst_source_ok is a synchronised LEVEL, not a latch.
		// ---------------------------------------------------------------
		rst_source_ok = 1'b0;
		wait_new_window(st);
		wait_new_window(st);
		chk("rso: follows low",  f_rso(st) == 1'b0);
		rst_source_ok = 1'b1;
		wait_new_window(st);
		wait_new_window(st);
		chk("rso: follows high", f_rso(st) == 1'b1);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS axi_ctrl_tb");
		$finish;
	end

	initial begin
		#20_000_000;
		$display("FAIL timeout");
		$finish;
	end

endmodule
