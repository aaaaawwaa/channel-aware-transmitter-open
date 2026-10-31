`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// pl_spi_master_tb -- self-checking testbench for the PL SPI master.
//
// Focus: the AD9361 control-port contract taken from the vendor example
// (sw/ref/platform.c / ad9361.h):
//   * SPI mode 1 (CPOL=0, CPHA=1) -- MOSI changes only while SCLK is low,
//     MISO is sampled on the falling edge.
//   * one continuous CSN-low burst of 2 + N_TX + N_RX bytes holding the 16-bit
//     instruction word {RW[15], CNT[14:12], ADDR[9:0]} followed by the data.
//
// A behavioural SPI slave watches MOSI and drives MISO so both a write
// (0x8037 -> data 0x0A, i.e. "write reg 0x037") and a read (0x0037 ->
// MISO 0xA5) can be checked end to end.
//////////////////////////////////////////////////////////////////////////////////

module pl_spi_master_tb;

	localparam integer AW = 5;

	reg         aclk   = 1'b0;
	reg         aresetn = 1'b0;

	// AXI-Lite
	reg  [AW-1:0] awaddr  = 0;
	reg           awvalid = 0;
	wire          awready;
	reg  [31:0]   wdata   = 0;
	reg  [3:0]    wstrb   = 4'hF;
	reg           wvalid  = 0;
	wire          wready;
	wire  [1:0]   bresp;
	wire          bvalid;
	reg           bready  = 1;
	reg  [AW-1:0] araddr  = 0;
	reg           arvalid = 0;
	wire          arready;
	wire  [31:0]  rdata;
	wire  [1:0]   rresp;
	wire          rvalid;
	reg           rready  = 1;

	// SPI
	wire spi_sclk, spi_mosi, spi_csn;
	wire spi_miso;

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

	always #10 aclk = ~aclk;   // 50 MHz

	// ------------------------------------------------------------------ DUT
	pl_spi_master #(.C_S_AXI_DATA_WIDTH(32), .C_S_AXI_ADDR_WIDTH(AW)) dut (
		.aclk(aclk), .aresetn(aresetn),
		.s_axi_awaddr(awaddr), .s_axi_awprot(3'b0), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
		.s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
		.s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
		.s_axi_araddr(araddr), .s_axi_arprot(3'b0), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
		.s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
		.spi_sclk(spi_sclk), .spi_mosi(spi_mosi), .spi_miso(spi_miso), .spi_csn(spi_csn)
	);

	// ----------------------------------------------------------- AXI helpers
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

	// ------------------------------------------- SPI bus monitor + slave model
	reg [7:0]  mosi_bytes [0:15];   // MOSI byte stream captured this burst
	integer    mosi_byte_idx;
	reg [7:0]  mosi_cur;
	integer    mosi_bit_in_byte;
	reg        mode1_violation;

	initial begin
		mosi_byte_idx = 0; mosi_bit_in_byte = 0;
		mode1_violation = 1'b0; mosi_cur = 8'h00;
	end

	// Mode 1: MOSI is stable through the sampling (falling) edge and changes only
	// while SCLK is low.  Any MOSI change while SCLK is high would break it.
	always @(spi_mosi) begin
		if (spi_csn == 1'b0 && spi_sclk == 1'b1)
			mode1_violation = 1'b1;
	end

	// Assemble MOSI into bytes on the rising edge (MSB first).
	always @(posedge spi_sclk) begin
		if (spi_csn == 1'b0) begin
			mosi_cur = { mosi_cur[6:0], spi_mosi };
			if (mosi_bit_in_byte == 7) begin
				mosi_bytes[mosi_byte_idx] = mosi_cur;
				mosi_byte_idx = mosi_byte_idx + 1;
				mosi_bit_in_byte = 0;
			end else begin
				mosi_bit_in_byte = mosi_bit_in_byte + 1;
			end
		end
	end

	// Behavioural slave.  In mode 1 MISO changes on the leading (rising) edge and
	// is sampled by the master on the trailing (falling) edge.  The first rising
	// edge presents bit 7, the second bit 6, and so on -- so the byte the master
	// recovers is miso_byte, MSB first.
	reg [7:0] miso_byte;
	reg [7:0] miso_sr;
	reg [3:0] miso_n;
	initial begin
		miso_byte = 8'h00; miso_sr = 8'h00; miso_n = 4'd0;
	end

	always @(negedge spi_csn) begin
		miso_sr = 8'h00;
		miso_n  = 4'd0;
	end

	always @(posedge spi_sclk) begin
		if (spi_csn == 1'b0) begin
			if (miso_n == 4'd0) miso_sr = miso_byte;          // bit 7 out
			else                miso_sr = { miso_sr[6:0], 1'b0 };
			miso_n = miso_n + 4'd1;
		end
	end

	assign spi_miso = miso_sr[7];

	// The master samples MISO on the falling edge.  Track what it recovers.
	reg [7:0] miso_captured;
	integer   miso_cap_bits;
	initial begin
		miso_captured = 8'h00; miso_cap_bits = 0;
	end
	always @(negedge spi_sclk) begin
		if (spi_csn == 1'b0 && miso_cap_bits < 8) begin
			miso_captured = { miso_captured[6:0], spi_miso };
			miso_cap_bits = miso_cap_bits + 1;
		end
	end

	task burst_reset;
		begin
			mosi_byte_idx    = 0;
			mosi_bit_in_byte = 0;
			mosi_cur         = 8'h00;
			miso_captured    = 8'h00;
			miso_cap_bits    = 0;
			mode1_violation  = 1'b0;
		end
	endtask

	task wait_done;
		reg [31:0] st;
		integer    guard;
		begin
			guard = 0;
			st = 32'h0;
			while (!st[1] && guard < 500) begin
				axi_read(5'h18, st);
				guard = guard + 1;
			end
		end
	endtask

	// -------------------------------------------------------------------- test
	reg [31:0] rd;

	initial begin
		$display("=== pl_spi_master_tb ===");

		aresetn = 1'b0;
		repeat (5) @(posedge aclk);
		aresetn = 1'b1;
		repeat (2) @(posedge aclk);

		// SCLK half period = 10 aclk -> 2.5 MHz SCLK.  Must stay wide enough that
		// the DUT's 2-FF MISO synchroniser settles inside the SCLK high phase.
		axi_write(5'h1C, 32'd10);

		// ------------------------------------------------------------------
		// 1. WRITE: reg 0x037 <- 0x0A
		//    instr = AD_WRITE|AD_CNT(1)|ADDR(0x37).  AD_CNT(x) is
		//    (((x)-1)&0x7)<<12, so a ONE-byte transfer encodes CNT=0 -> 0x8037.
		// ------------------------------------------------------------------
		burst_reset();
		miso_byte = 8'hFF;   // a write must ignore MISO

		axi_write(5'h08, 32'h0000_000A);         // TXD0 = data byte 0x0A
		axi_write(5'h04, 32'h0000_8037);         // INSTR
		axi_write(5'h00, (32'd0 << 8) | 32'd1 | (32'd1 << 16)); // N_TX=1,N_RX=0,START
		wait_done();

		chk("write: exactly 3 bytes on MOSI",    mosi_byte_idx == 3);
		chk("write: byte0 == 0x80 (AD_WRITE,AD_CNT(1)=0)", mosi_bytes[0] == 8'h80);
		chk("write: byte1 == 0x37 (addr 0x037)", mosi_bytes[1] == 8'h37);
		chk("write: byte2 == 0x0A (data)",       mosi_bytes[2] == 8'h0A);
		chk("write: CSN released at end",        spi_csn == 1'b1);
		chk("write: SCLK idles low",             spi_sclk == 1'b0);
		chk("write: mode 1 respected",           mode1_violation == 1'b0);

		// ------------------------------------------------------------------
		// 2. READ: reg 0x037 -> 0xA5
		//    instr = AD_READ|AD_CNT(1)|ADDR(0x37) = 0x0037 (CNT field 0 either way).
		// ------------------------------------------------------------------
		burst_reset();
		miso_byte = 8'hA5;

		axi_write(5'h04, 32'h0000_0037);         // INSTR (read)
		axi_write(5'h00, (32'd1 << 8) | 32'd0 | (32'd1 << 16)); // N_TX=0,N_RX=1,START
		wait_done();

		axi_read(5'h10, rd);
		chk("read: RXD0[7:0] == 0xA5 (master sampled MISO)", rd[7:0] == 8'hA5);
		chk("read: byte0 == 0x00 (AD_READ,AD_CNT(1)=0)", mosi_bytes[0] == 8'h00);
		chk("read: byte1 == 0x37 (addr 0x037)",    mosi_bytes[1] == 8'h37);
		chk("read: 2 tx + 1 rx = 3 bytes clocked", mosi_byte_idx == 3);
		chk("read: mode 1 respected",              mode1_violation == 1'b0);
		chk("read: CSN released",                  spi_csn == 1'b1);

		// ------------------------------------------------------------------
		// 3. Multi-byte read (the AD9361 also does multi-byte reads).
		//    3 bytes = AD_CNT(3) = 2 -> 0x2037 ; expect 3 bytes in RXD0[23:0].
		// ------------------------------------------------------------------
		burst_reset();
		miso_byte = 8'h11;    // only byte 0 is exercised by this simple slave model
		axi_write(5'h04, 32'h0000_2037);
		axi_write(5'h00, (32'd3 << 8) | 32'd0 | (32'd1 << 16));
		wait_done();
		chk("read3: instr = 0x20 0x37 (AD_CNT(3)=2)", mosi_bytes[0] == 8'h20 && mosi_bytes[1] == 8'h37);
		chk("read3: 5 bytes clocked (2+3)",        mosi_byte_idx == 5);
		chk("read3: mode 1 respected",             mode1_violation == 1'b0);

		// ------------------------------------------------------------------
		// 4. DONE clears on the next START, and STATUS reports idle after.
		// ------------------------------------------------------------------
		axi_read(5'h18, rd);
		chk("status: DONE set, BUSY clear",        rd[1] == 1'b1 && rd[0] == 1'b0);

		$display("=== %0d PASS, %0d FAIL ===", pass_cnt, fail_cnt);
		if (fail_cnt == 0) $display("PASS pl_spi_master_tb");
		$finish;
	end

	initial begin
		#200000;
		$display("FAIL timeout");
		$finish;
	end

endmodule