
`timescale 1 ns / 1 ps

	module axi_ctrl_v1_0 #
	(
		// Users to add parameters here
		
		// Parameters of Axi Slave Bus Interface S00_AXI
		parameter integer C_S00_AXI_DATA_WIDTH	= 32,
		parameter integer C_S00_AXI_ADDR_WIDTH	= 6
	)
	(
		// Users to add ports here
		output wire [6:0] idelay_en,
		output wire [4:0] idelay_tap,
		output wire [11:0] phase_inc,
		output wire           phy_mode,
		output wire           wave_sel,
		// TX sample buffer (BRAM): configuration out, read position in.
		output wire           src_sel,
		output wire [12:0]    smp_len,
		output wire           smp_wrap,
		output wire           smp_commit,
		input  wire [12:0]    smp_pos,
		// RX capture: configuration out, done/position in.
		output wire           cfg_arm,
		output wire           cfg_cont,
		output wire           cfg_commit,
		output wire [2:0]     cfg_ratio,
		output wire [12:0]    cfg_len,
		input  wire [12:0]    cap_pos,
		input  wire           cap_done,
		// Read-only status: the recovered DATA_CLK and the TX source's reset
		// liveness.  Both are in the DATA_CLK domain; the status register
		// synchronises them into the AXI clock itself.
		input  wire           data_clk,
		input  wire           rst_source_ok,
		// User ports ends
		// Do not modify the ports beyond this line


		// Ports of Axi Slave Bus Interface S00_AXI
	(* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S00_ACLK, ASSOCIATED_BUSIF S00_AXI, ASSOCIATED_RESET s00_axi_aresetn" *)
	(* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S00_ACLK CLK" *)
		input wire  s00_axi_aclk,
	(* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S00_ARESETN RST" *)
	(* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S00_ARESETN, POLARITY ACTIVE_LOW" *)
		input wire  s00_axi_aresetn,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI AWADDR" *)
		input wire [C_S00_AXI_ADDR_WIDTH-1 : 0] s00_axi_awaddr,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI AWPROT" *)
		input wire [2 : 0] s00_axi_awprot,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI AWVALID" *)
		input wire  s00_axi_awvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI AWREADY" *)
		output wire  s00_axi_awready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI WDATA" *)
		input wire [C_S00_AXI_DATA_WIDTH-1 : 0] s00_axi_wdata,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI WSTRB" *)
		input wire [(C_S00_AXI_DATA_WIDTH/8)-1 : 0] s00_axi_wstrb,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI WVALID" *)
		input wire  s00_axi_wvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI WREADY" *)
		output wire  s00_axi_wready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI BRESP" *)
		output wire [1 : 0] s00_axi_bresp,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI BVALID" *)
		output wire  s00_axi_bvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI BREADY" *)
		input wire  s00_axi_bready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI ARADDR" *)
		input wire [C_S00_AXI_ADDR_WIDTH-1 : 0] s00_axi_araddr,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI ARPROT" *)
		input wire [2 : 0] s00_axi_arprot,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI ARVALID" *)
		input wire  s00_axi_arvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI ARREADY" *)
		output wire  s00_axi_arready,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI RDATA" *)
		output wire [C_S00_AXI_DATA_WIDTH-1 : 0] s00_axi_rdata,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI RRESP" *)
		output wire [1 : 0] s00_axi_rresp,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI RVALID" *)
		output wire  s00_axi_rvalid,
	(* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S00_AXI RREADY" *)
		input wire  s00_axi_rready
	);
// Instantiation of Axi Bus Interface S00_AXI
	axi_lite_v1_0_S00_AXI # ( 
		.C_S_AXI_DATA_WIDTH(C_S00_AXI_DATA_WIDTH),
		.C_S_AXI_ADDR_WIDTH(C_S00_AXI_ADDR_WIDTH)
	) axi_lite_v1_0_S00_AXI_inst (
		.idelay_en(idelay_en),
		.idelay_tap(idelay_tap),
		.phase_inc(phase_inc),
		.phy_mode(phy_mode),
		.wave_sel(wave_sel),
		.src_sel(src_sel),
		.smp_len(smp_len),
		.smp_wrap(smp_wrap),
		.smp_commit(smp_commit),
		.smp_pos(smp_pos),
		.cfg_arm(cfg_arm),
		.cfg_cont(cfg_cont),
		.cfg_commit(cfg_commit),
		.cfg_ratio(cfg_ratio),
		.cfg_len(cfg_len),
		.cap_pos(cap_pos),
		.cap_done(cap_done),
		.data_clk(data_clk),
		.rst_source_ok(rst_source_ok),

		.S_AXI_ACLK(s00_axi_aclk),
		.S_AXI_ARESETN(s00_axi_aresetn),
		.S_AXI_AWADDR(s00_axi_awaddr),
		.S_AXI_AWPROT(s00_axi_awprot),
		.S_AXI_AWVALID(s00_axi_awvalid),
		.S_AXI_AWREADY(s00_axi_awready),
		.S_AXI_WDATA(s00_axi_wdata),
		.S_AXI_WSTRB(s00_axi_wstrb),
		.S_AXI_WVALID(s00_axi_wvalid),
		.S_AXI_WREADY(s00_axi_wready),
		.S_AXI_BRESP(s00_axi_bresp),
		.S_AXI_BVALID(s00_axi_bvalid),
		.S_AXI_BREADY(s00_axi_bready),
		.S_AXI_ARADDR(s00_axi_araddr),
		.S_AXI_ARPROT(s00_axi_arprot),
		.S_AXI_ARVALID(s00_axi_arvalid),
		.S_AXI_ARREADY(s00_axi_arready),
		.S_AXI_RDATA(s00_axi_rdata),
		.S_AXI_RRESP(s00_axi_rresp),
		.S_AXI_RVALID(s00_axi_rvalid),
		.S_AXI_RREADY(s00_axi_rready)
	);

	endmodule
