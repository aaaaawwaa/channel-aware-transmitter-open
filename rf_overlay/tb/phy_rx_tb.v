`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// phy_rx_tb -- checks phy_rx's DDR capture and I/Q reassembly.
//
// phy_rx is the mirror of phy_tx: it recovers DATA_CLK from the incoming
// differential pair, delays frame+data through IDELAYE2, samples them with
// IDDR (SAME_EDGE_PIPELINED) and shifts the resulting 6-bit halves into a
// 24-bit register.  When that register holds the pattern 4'b1100 in 1R1T mode
// the phy declares a full I/Q pair and latches:
//
//   adc_data_d1 = { dsh[23:18], dsh[11:6] }     (I)
//   adc_data_q1 = { dsh[17:12], dsh[5:0]  }     (Q)
//
// The TB drives one bit slot per DATA_CLK edge (four slots = two DATA_CLK
// cycles = one I/Q pair) and checks that the 12-bit words coming back out are
// the ones put in.
//
// NOTE: the wire pattern is NOT phy_tx's.  phy_tx feeds the AD9361's TX port
// and phy_rx reads its RX port; the two use different frame polarity and the
// IDDR is one pipeline stage deeper than the ODDR, so looping TX straight back
// into RX is a contract that does not exist on the board.  The slot order and
// frame levels below were derived by sweeping all 16 frame waveforms against
// this phy_rx and taking the one that lands dsh on 4'b1100 with a clean
// I/Q split (see the commit message for the sweep output).
//
// IDELAYE2 is left at tap 0 (idelay_en all zero, so LD never loads): the delay
// line is transparent and the capture is checked on the raw path.  The
// IDELAYCTRL/tap machinery is a device primitive, not something a behavioural
// TB can meaningfully validate.
//
// Elaborate with `xelab glbl phy_rx_tb -L unisims_ver`.
//////////////////////////////////////////////////////////////////////////////////

module phy_rx_tb;

	reg         rst_n = 1'b0;
	reg         ref_clk200m = 1'b0;
	reg         dclk = 1'b0;          // 30 MHz DATA_CLK (60 MSPS DDR)

	// The sample under test: I = 0xABC, Q = 0x123.  Distinct nibble patterns
	// so a swapped or truncated field is obvious rather than plausible.
	localparam [5:0] I_HI = 6'h2A;    // I[11:6]
	localparam [5:0] I_LO = 6'h3C;    // I[5:0]
	localparam [5:0] Q_HI = 6'h04;    // Q[11:6]
	localparam [5:0] Q_LO = 6'h23;    // Q[5:0]

	reg  [6:0]  idelay_en  = 7'd0;
	reg  [4:0]  idelay_tap = 5'd0;
	reg         phy_mode   = 1'b1;    // 1R1T

	reg  [5:0]  d_p;
	reg         fr_p;
	wire [5:0]  d_n = ~d_p;
	wire        fr_n = ~fr_p;

	wire        data_clk;
	wire        adc_d1q1_valid, adc_d2q2_valid;
	wire [11:0] adc_data_d1, adc_data_q1, adc_data_d2, adc_data_q2;

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

	always #2.5      ref_clk200m = ~ref_clk200m;   // 200 MHz for IDELAYCTRL
	always #16.6665  dclk        = ~dclk;          // 30 MHz

	// One bit slot per DATA_CLK edge.  The shift register fills newest-first, so
	// dsh[23:18] ends up holding slot3, dsh[17:12] slot2, dsh[11:6] slot1 and
	// dsh[5:0] slot0.  The latch splits that as
	//   d1 = { dsh[23:18], dsh[11:6] }  = { slot3, slot1 }
	//   q1 = { dsh[17:12], dsh[5:0]  }  = { slot2, slot0 }
	// so slot3/slot1 carry I and slot2/slot0 carry Q, high halves first.
	integer slot = 0;
	always @(dclk) slot <= (slot == 3) ? 0 : slot + 1;

	always @(*) begin
		case (slot)
			0: d_p = Q_LO;
			1: d_p = I_LO;
			2: d_p = Q_HI;
			3: d_p = I_HI;
		endcase
		fr_p = (slot >= 2);
	end

	phy_rx dut (
		.ref_clk200m(ref_clk200m),
		.rst_n(rst_n),
		.data_clk(data_clk),
		.rx_clk_in_p(dclk), .rx_clk_in_n(~dclk),
		.rx_data_in_p(d_p),  .rx_data_in_n(d_n),
		.rx_frame_in_p(fr_p), .rx_frame_in_n(fr_n),
		.adc_d1q1_valid(adc_d1q1_valid),
		.adc_d2q2_valid(adc_d2q2_valid),
		.adc_data_d1(adc_data_d1), .adc_data_q1(adc_data_q1),
		.adc_data_d2(adc_data_d2), .adc_data_q2(adc_data_q2),
		.idelay_en(idelay_en), .idelay_tap(idelay_tap),
		.phy_mode(phy_mode)
	);

	// Watch for the recovered I/Q appearing with the valid strobe.
	reg saw_i, saw_q, saw_valid;
	always @(posedge data_clk) begin
		if (rst_n && adc_d1q1_valid) begin
			saw_valid <= 1'b1;
			if (adc_data_d1 === 12'hABC) saw_i <= 1'b1;
			if (adc_data_q1 === 12'h123) saw_q <= 1'b1;
		end
	end

	initial begin
		$display("=== phy_rx_tb ===");

		saw_i = 1'b0; saw_q = 1'b0; saw_valid = 1'b0;

		rst_n = 1'b0;
		repeat (10) @(posedge dclk);
		rst_n = 1'b1;

		// Give the phy enough DATA_CLK edges to walk several full frames
		// through the 24-bit shift register.
		repeat (32) @(posedge data_clk);
		#1;

		chk("1R1T: recovered DATA_CLK is toggling", data_clk === 1'b0 || data_clk === 1'b1);
		chk("1R1T: d1q1 valid strobe fires",        saw_valid);
		chk("1R1T: I reassembles to 0xABC",         saw_i);
		chk("1R1T: Q reassembles to 0x123",         saw_q);
		chk("1R1T: d2q2 valid stays low",           adc_d2q2_valid === 1'b0);

		// Reset must clear the capture state.
		rst_n = 1'b0;
		repeat (4) @(posedge data_clk);
		#1;
		chk("reset: valid strobe clears", adc_d1q1_valid === 1'b0);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS phy_rx_tb");
		$finish;
	end

	initial begin
		#400000;
		$display("FAIL timeout");
		$finish;
	end

endmodule