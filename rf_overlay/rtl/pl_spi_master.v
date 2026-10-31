`timescale 1 ns / 1 ps
//////////////////////////////////////////////////////////////////////////////////
// pl_spi_master -- minimal SPI master for the AD9361 control port.
//
// The board's PYNQ boot image leaves PS SPI0 unusable (device-tree node disabled,
// APER clock gated off, MIO1..6 muxed to QSPI), and Overlay() never applies PS7
// settings, so the AD9361 control port is driven from the PL instead.  This is the
// same pattern the project already uses for the GT911 (a register-level master in
// RTL, driven from userspace over MMIO).
//
// Protocol matches the vendor example that ran on this board (sw/ref/platform.c,
// spi_init(SPI_DEVICE_ID, 1, 0) -> CPHA=1 / CPOL=0, i.e. SPI mode 1):
//
//   * mode 1: MOSI is set while SCLK is low and changes on the rising edge;
//     MISO is sampled on the falling edge.
//   * a transfer is one continuous CSN-low burst of  2 + N_TX + N_RX  bytes:
//       byte 0 = INSTR[15:8], byte 1 = INSTR[7:0],
//       then N_TX data bytes, then N_RX bytes clocked in.
//   * the 16-bit instruction word is the AD9361's own format:
//       {RW[15], CNT[14:12], ADDR[9:0]}   -- see ad9361.h
//
// Register map (word addresses, C_S_AXI_ADDR_WIDTH = 5):
//   0x00 CTRL   [7:0] N_TX data bytes to send, [15:8] N_RX bytes to receive,
//               [16]  START (write 1 to launch; self-clearing)
//   0x04 INSTR  [15:0] instruction word
//   0x08 TXD0   tx data bytes 0..3
//   0x0C TXD1   tx data bytes 4..7
//   0x10 RXD0   rx data bytes 0..3   (read)
//   0x14 RXD1   rx data bytes 4..7   (read)
//   0x18 STATUS [0] BUSY, [1] DONE (sticky until the next START)
//   0x1C DIV    SCLK half-period in aclk cycles (min 1)
//////////////////////////////////////////////////////////////////////////////////

module pl_spi_master #(
	parameter integer C_S_AXI_DATA_WIDTH = 32,
	parameter integer C_S_AXI_ADDR_WIDTH = 5
)(
	(* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET aresetn" *)
	(* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
	input  wire                                aclk,
	(* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
	(* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
	input  wire                                aresetn,

	// AXI4-Lite slave
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
	input  wire [C_S_AXI_ADDR_WIDTH-1:0]       s_axi_awaddr,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
	input  wire [2:0]                          s_axi_awprot,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
	input  wire                                s_axi_awvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
	output wire                                s_axi_awready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
	input  wire [C_S_AXI_DATA_WIDTH-1:0]       s_axi_wdata,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
	input  wire [(C_S_AXI_DATA_WIDTH/8)-1:0]   s_axi_wstrb,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
	input  wire                                s_axi_wvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
	output wire                                s_axi_wready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
	output wire [1:0]                          s_axi_bresp,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
	output wire                                s_axi_bvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
	input  wire                                s_axi_bready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
	input  wire [C_S_AXI_ADDR_WIDTH-1:0]       s_axi_araddr,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
	input  wire [2:0]                          s_axi_arprot,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
	input  wire                                s_axi_arvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
	output wire                                s_axi_arready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
	output wire [C_S_AXI_DATA_WIDTH-1:0]       s_axi_rdata,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
	output wire [1:0]                          s_axi_rresp,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
	output wire                                s_axi_rvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
	input  wire                                s_axi_rready,

	// SPI pins (to the FMC connector). Deliberately left as plain wires: they are
	// routed straight to top-level ports in the BD, and tagging spi_sclk as a
	// clock would make the BD invent a clock domain for it.
	output wire                                spi_sclk,
	output wire                                spi_mosi,
	input  wire                                spi_miso,
	output wire                                spi_csn
);

	localparam integer ADDR_LSB = 2;

	// ------------------------------------------------------------------ SPI FSM
	localparam S_IDLE      = 3'd0,
	           S_LOW_PREP  = 3'd1,
	           S_LOW_WAIT  = 3'd2,
	           S_HIGH_WAIT = 3'd3,
	           S_DONE      = 3'd4;

	reg [2:0]  state;
	wire       busy = (state != S_IDLE);

	// ---------------------------------------------------------------- registers
	reg [7:0]  n_tx;
	reg [7:0]  n_rx;
	reg        start_req;
	reg [15:0] instr;
	reg [31:0] txd0, txd1;
	reg [7:0]  rx_mem [0:7];
	reg [31:0] div;
	reg        done_r;

	wire [63:0] txd_full = { txd1, txd0 };
	wire [63:0] rxd_full = { rx_mem[7], rx_mem[6], rx_mem[5], rx_mem[4],
	                         rx_mem[3], rx_mem[2], rx_mem[1], rx_mem[0] };

	// ------------------------------------------------------------ AXI write side
	wire wr_en = s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid;
	reg  bvalid_r;

	assign s_axi_awready = wr_en;
	assign s_axi_wready  = wr_en;
	assign s_axi_bvalid  = bvalid_r;
	assign s_axi_bresp   = 2'b00;

	// ------------------------------------------------------------- AXI read side
	wire rd_en = s_axi_arvalid && !s_axi_rvalid;
	reg  rvalid_r;
	reg [C_S_AXI_DATA_WIDTH-1:0] rdata_r;

	assign s_axi_arready = rd_en;
	assign s_axi_rvalid  = rvalid_r;
	assign s_axi_rresp   = 2'b00;
	assign s_axi_rdata   = rdata_r;

	always @(posedge aclk) begin
		if (!aresetn) begin
			bvalid_r <= 1'b0;
			rvalid_r <= 1'b0;
			rdata_r  <= {C_S_AXI_DATA_WIDTH{1'b0}};
		end else begin
			if (wr_en) bvalid_r <= 1'b1;
			else if (bvalid_r && s_axi_bready) bvalid_r <= 1'b0;

			if (rd_en) begin
				rvalid_r <= 1'b1;
				case (s_axi_araddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB])
					5'h00: rdata_r <= {15'b0, start_req, n_rx, n_tx};
					5'h01: rdata_r <= {16'b0, instr};
					5'h02: rdata_r <= txd0;
					5'h03: rdata_r <= txd1;
					5'h04: rdata_r <= rxd_full[31:0];
					5'h05: rdata_r <= rxd_full[63:32];
					5'h06: rdata_r <= {30'b0, done_r, busy};
					5'h07: rdata_r <= div;
					default: rdata_r <= 32'hDEADBEEF;
				endcase
			end else if (rvalid_r && s_axi_rready) begin
				rvalid_r <= 1'b0;
			end
		end
	end

	// ----------------------------------------------------------- register writes
	wire wr_ctrl  = wr_en && (s_axi_awaddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB] == 5'h00);
	wire wr_instr = wr_en && (s_axi_awaddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB] == 5'h01);
	wire wr_txd0  = wr_en && (s_axi_awaddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB] == 5'h02);
	wire wr_txd1  = wr_en && (s_axi_awaddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB] == 5'h03);
	wire wr_div   = wr_en && (s_axi_awaddr[C_S_AXI_ADDR_WIDTH-1:ADDR_LSB] == 5'h07);

	// ----------------------------------------------------------- MISO input sync
	reg [1:0] miso_sync_r;
	always @(posedge aclk) begin
		if (!aresetn) miso_sync_r <= 2'b00;
		else          miso_sync_r <= {miso_sync_r[0], spi_miso};
	end
	wire miso_sync = miso_sync_r[1];

	// ------------------------------------------------------------------ SPI FSM
	reg        sclk_r, mosi_r, csn_r;
	reg [4:0]  byte_cnt;      // 0 .. total-1   (total <= 2+8+8 = 18)
	reg [2:0]  bit_cnt;       // 7 .. 0
	reg [31:0] half_cnt;
	reg [7:0]  lat_n_tx, lat_n_rx;

	wire [4:0] total_bytes = 5'd2 + lat_n_tx[4:0] + lat_n_rx[4:0];
	wire [4:0] rx_start    = 5'd2 + lat_n_tx[4:0];
	wire [31:0] half        = (div == 32'd0) ? 32'd1 : div;

	// byte currently being shifted out (or 0x00 during the receive-only tail)
	reg [7:0] tx_byte;
	always @(*) begin
		if (byte_cnt == 5'd0)                     tx_byte = instr[15:8];
		else if (byte_cnt == 5'd1)                tx_byte = instr[7:0];
		else if (byte_cnt < 5'd2 + lat_n_tx[4:0]) tx_byte = txd_full[(byte_cnt - 5'd2)*8 +: 8];
		else                                      tx_byte = 8'h00;
	end

	always @(posedge aclk) begin
		if (!aresetn) begin
			state     <= S_IDLE;
			sclk_r    <= 1'b0;
			mosi_r    <= 1'b0;
			csn_r     <= 1'b1;
			byte_cnt  <= 5'd0;
			bit_cnt   <= 3'd7;
			half_cnt  <= 32'd0;
			lat_n_tx  <= 8'd0;
			lat_n_rx  <= 8'd0;
		end else begin
			case (state)
				S_IDLE: begin
					csn_r <= 1'b1;
					sclk_r <= 1'b0;
					if (start_req) begin
						csn_r     <= 1'b0;
						lat_n_tx  <= n_tx;
						lat_n_rx  <= n_rx;
						byte_cnt  <= 5'd0;
						bit_cnt   <= 3'd7;
						state     <= S_LOW_PREP;
					end
				end

				// SCLK low: present the bit, then hold for half a period
				S_LOW_PREP: begin
					sclk_r   <= 1'b0;
					mosi_r   <= tx_byte[bit_cnt];
					half_cnt <= half;
					state    <= S_LOW_WAIT;
				end

				S_LOW_WAIT: begin
					if (half_cnt <= 32'd1) begin
						sclk_r   <= 1'b1;          // rising edge: MOSI already stable
						half_cnt <= half;
						state    <= S_HIGH_WAIT;
					end else begin
						half_cnt <= half_cnt - 32'd1;
					end
				end

				// SCLK high; on the falling edge the slave's MISO is sampled
				S_HIGH_WAIT: begin
					if (half_cnt <= 32'd1) begin
						sclk_r <= 1'b0;
						if (byte_cnt >= rx_start)
							rx_mem[byte_cnt - rx_start][bit_cnt] <= miso_sync;

						if (bit_cnt == 3'd0) begin
							bit_cnt <= 3'd7;
							if (byte_cnt + 5'd1 == total_bytes) begin
								state <= S_DONE;
							end else begin
								byte_cnt <= byte_cnt + 5'd1;
								state    <= S_LOW_PREP;
							end
						end else begin
							bit_cnt <= bit_cnt - 3'd1;
							state   <= S_LOW_PREP;
						end
					end else begin
						half_cnt <= half_cnt - 32'd1;
					end
				end

				S_DONE: begin
					csn_r  <= 1'b1;
					sclk_r <= 1'b0;
					state  <= S_IDLE;
				end

				default: state <= S_IDLE;
			endcase
		end
	end

	// START is a one-shot: it is consumed as soon as the FSM leaves IDLE.
	always @(posedge aclk) begin
		if (!aresetn)                              start_req <= 1'b0;
		else if (wr_ctrl && s_axi_wdata[16])       start_req <= 1'b1;
		else if (state != S_IDLE)                  start_req <= 1'b0;
	end

	// DONE is sticky until the next START, so software can poll STATUS.
	always @(posedge aclk) begin
		if (!aresetn)                        done_r <= 1'b0;
		else if (wr_ctrl && s_axi_wdata[16]) done_r <= 1'b0;
		else if (state == S_DONE)            done_r <= 1'b1;
	end

	// ------------------------------------------------------------ register store
	always @(posedge aclk) begin
		if (!aresetn) begin
			n_tx  <= 8'd0;
			n_rx  <= 8'd0;
			instr <= 16'd0;
			txd0  <= 32'd0;
			txd1  <= 32'd0;
			div   <= 32'd25;          // 50 MHz / (2*25) = 1 MHz SCLK
		end else if (wr_en) begin
			if (wr_ctrl)  begin n_tx <= s_axi_wdata[7:0]; n_rx <= s_axi_wdata[15:8]; end
			if (wr_instr) instr <= s_axi_wdata[15:0];
			if (wr_txd0)  txd0  <= s_axi_wdata;
			if (wr_txd1)  txd1  <= s_axi_wdata;
			if (wr_div)   div   <= s_axi_wdata;
		end
	end

	assign spi_sclk = sclk_r;
	assign spi_mosi = mosi_r;
	assign spi_csn  = csn_r;

endmodule