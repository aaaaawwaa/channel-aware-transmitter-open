module phy_rx
(
	//clk reset
	input	wire 			ref_clk200m,//200m idelay ctrl ref clock
	input	wire 			rst_n,
	output	wire 			data_clk,
	//rx phy port
	input	wire 			rx_clk_in_p,
	input	wire 			rx_clk_in_n,
	input	wire	[5:0] 	rx_data_in_p,
	input	wire 	[5:0]	rx_data_in_n,
	input	wire 			rx_frame_in_p,
	input	wire 			rx_frame_in_n,
	
	//adc result data
	output	wire 			adc_d1q1_valid,
	output	wire 			adc_d2q2_valid,
	output	wire 	[11:0]	adc_data_d1,
	output	wire 	[11:0]	adc_data_q1,
	output	wire 	[11:0]	adc_data_d2,
	output	wire 	[11:0]	adc_data_q2,
	//mode/idelay tap/en
	input	wire	[6:0]	idelay_en,
	input	wire 	[4:0]	idelay_tap,
	input 	wire 	        phy_mode //0 2R2T 1 1R1T
);
parameter PHY_MODE_1R1T=1;
parameter PHY_MODE_2R2T=0;

wire data_clk_in;
wire rx_frame;
wire rx_frame_idelay;
wire rx_frame_iddr_p;
wire rx_frame_iddr_n;
wire [5:0] rx_data;
wire [5:0] rx_data_idelay;
wire [5:0] rx_data_iddr_p;
wire [5:0] rx_data_iddr_n;
//CLK
   IBUFDS #(
      .DIFF_TERM("FALSE"),       // Differential Termination
      .IBUF_LOW_PWR("TRUE"),     // Low power="TRUE", Highest performance="FALSE" 
      .IOSTANDARD("DEFAULT")     // Specify the input I/O standard
   ) IBUFDS_data_clk_inst (
      .O(data_clk_in),  // Buffer output
      .I(rx_clk_in_p),  // Diff_p buffer input (connect directly to top-level port)
      .IB(rx_clk_in_n) // Diff_n buffer input (connect directly to top-level port)
   );
     BUFG BUFG_inst (
      .O(data_clk), // 1-bit output: Clock output
      .I(data_clk_in)  // 1-bit input: Clock input
   );
//FRAME
   IBUFDS #(
      .DIFF_TERM("FALSE"),       // Differential Termination
      .IBUF_LOW_PWR("TRUE"),     // Low power="TRUE", Highest performance="FALSE" 
      .IOSTANDARD("DEFAULT")     // Specify the input I/O standard
   ) IBUFDS_rx_frame_inst (
      .O(rx_frame),  // Buffer output
      .I(rx_frame_in_p),  // Diff_p buffer input (connect directly to top-level port)
      .IB(rx_frame_in_n) // Diff_n buffer input (connect directly to top-level port)
   );
//I DELAY
IDELAYCTRL IDELAYCTRL_inst (
      .RDY(),       // 1-bit output: Ready output
      .REFCLK(ref_clk200m), // 1-bit input: Reference clock input
      .RST(~rst_n)        // 1-bit input: Active high reset input
   );
   IDELAYE2 #(
      .CINVCTRL_SEL("FALSE"),          // Enable dynamic clock inversion (FALSE, TRUE)
      .DELAY_SRC("IDATAIN"),           // Delay input (IDATAIN, DATAIN)
      .HIGH_PERFORMANCE_MODE("FALSE"), // Reduced jitter ("TRUE"), Reduced power ("FALSE")
      .IDELAY_TYPE("VAR_LOAD"),           // FIXED, VARIABLE, VAR_LOAD, VAR_LOAD_PIPE
      .IDELAY_VALUE(0),                // Input delay tap setting (0-31)
      .PIPE_SEL("FALSE"),              // Select pipelined mode, FALSE, TRUE
      .REFCLK_FREQUENCY(200.0),        // IDELAYCTRL clock input frequency in MHz (190.0-210.0, 290.0-310.0).
      .SIGNAL_PATTERN("DATA")          // DATA, CLOCK input signal
   )
   IDELAYE2_rx_frame_inst (
      .CNTVALUEOUT(), // 5-bit output: Counter value output
      .DATAOUT(rx_frame_idelay),         // 1-bit output: Delayed data output
      .C(ref_clk200m),                     // 1-bit input: Clock input
      .CE(1'b0),                   // 1-bit input: Active high enable increment/decrement input
      .CINVCTRL(),       // 1-bit input: Dynamic clock inversion input
      .CNTVALUEIN(idelay_tap),   // 5-bit input: Counter value input
      .DATAIN(),           // 1-bit input: Internal delay data input
      .IDATAIN(rx_frame),         // 1-bit input: Data input from the I/O
      .INC(1'b0),                 // 1-bit input: Increment / Decrement tap delay input
      .LD(idelay_en[6]),                   // 1-bit input: Load IDELAY_VALUE input
      .LDPIPEEN(),       // 1-bit input: Enable PIPELINE register to load data input
      .REGRST(1'b0)            // 1-bit input: Active-high reset tap-delay input
   );
      IDDR #(
      .DDR_CLK_EDGE("SAME_EDGE_PIPELINED"), // "OPPOSITE_EDGE", "SAME_EDGE" 
                                      //    or "SAME_EDGE_PIPELINED" 
      .INIT_Q1(1'b0), // Initial value of Q1: 1'b0 or 1'b1
      .INIT_Q2(1'b0), // Initial value of Q2: 1'b0 or 1'b1
      .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC" 
   ) IDDR_rx_frame_inst (
      .Q1(rx_frame_iddr_p), // 1-bit output for positive edge of clock
      .Q2(rx_frame_iddr_n), // 1-bit output for negative edge of clock
      .C(data_clk),   // 1-bit clock input
      .CE(1'b1), // 1-bit clock enable input
      .D(rx_frame_idelay),   // 1-bit DDR data input
      .R(1'b0),   // 1-bit reset
      .S(1'b0)    // 1-bit set
   );

//RX DATA
genvar i;
generate
	for(i=0;i<6;i=i+1)begin
	   IBUFDS #(
	      .DIFF_TERM("FALSE"),       // Differential Termination
	      .IBUF_LOW_PWR("TRUE"),     // Low power="TRUE", Highest performance="FALSE" 
	      .IOSTANDARD("DEFAULT")     // Specify the input I/O standard
	   ) IBUFDS_data (
	      .O(rx_data[i]),  // Buffer output
	      .I(rx_data_in_p[i]),  // Diff_p buffer input (connect directly to top-level port)
	      .IB(rx_data_in_n[i]) // Diff_n buffer input (connect directly to top-level port)
	   );

	   IDELAYE2 #(
	      .CINVCTRL_SEL("FALSE"),          // Enable dynamic clock inversion (FALSE, TRUE)
	      .DELAY_SRC("IDATAIN"),           // Delay input (IDATAIN, DATAIN)
	      .HIGH_PERFORMANCE_MODE("FALSE"), // Reduced jitter ("TRUE"), Reduced power ("FALSE")
	      .IDELAY_TYPE("VAR_LOAD"),           // FIXED, VARIABLE, VAR_LOAD, VAR_LOAD_PIPE
	      .IDELAY_VALUE(0),                // Input delay tap setting (0-31)
	      .PIPE_SEL("FALSE"),              // Select pipelined mode, FALSE, TRUE
	      .REFCLK_FREQUENCY(200.0),        // IDELAYCTRL clock input frequency in MHz (190.0-210.0, 290.0-310.0).
	      .SIGNAL_PATTERN("DATA")          // DATA, CLOCK input signal
	   )
	   IDELAYE2_data(
	      .CNTVALUEOUT(), // 5-bit output: Counter value output
	      .DATAOUT(rx_data_idelay[i]),         // 1-bit output: Delayed data output
	      .C(ref_clk200m),                     // 1-bit input: Clock input
	      .CE(1'b0),                   // 1-bit input: Active high enable increment/decrement input
	      .CINVCTRL(),       // 1-bit input: Dynamic clock inversion input
	      .CNTVALUEIN(idelay_tap),   // 5-bit input: Counter value input
	      .DATAIN(),           // 1-bit input: Internal delay data input
	      .IDATAIN(rx_data[i]),         // 1-bit input: Data input from the I/O
	      .INC(1'b0),                 // 1-bit input: Increment / Decrement tap delay input
	      .LD(idelay_en[i]),                   // 1-bit input: Load IDELAY_VALUE input
	      .LDPIPEEN(),       // 1-bit input: Enable PIPELINE register to load data input
	      .REGRST(1'b0)            // 1-bit input: Active-high reset tap-delay input
	   );
	    IDDR #(
	      .DDR_CLK_EDGE("SAME_EDGE_PIPELINED"), // "OPPOSITE_EDGE", "SAME_EDGE" 
	                                      //    or "SAME_EDGE_PIPELINED" 
	      .INIT_Q1(1'b0), // Initial value of Q1: 1'b0 or 1'b1
	      .INIT_Q2(1'b0), // Initial value of Q2: 1'b0 or 1'b1
	      .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC" 
	   ) IDDR_data (
	      .Q1(rx_data_iddr_p[i]), // 1-bit output for positive edge of clock
	      .Q2(rx_data_iddr_n[i]), // 1-bit output for negative edge of clock
	      .C(data_clk),   // 1-bit clock input
	      .CE(1'b1), // 1-bit clock enable input
	      .D(rx_data_idelay[i]),   // 1-bit DDR data input
	      .R(1'b0),   // 1-bit reset
	      .S(1'b0)    // 1-bit set
	   );
	end
endgenerate

//rx data parase logical
reg [5:0]       rx_data_iddr_n_r1_reg ;
reg 	         rx_frame_iddr_n_r1_reg;
reg [3:0]       rx_frame_shift_reg;
reg [23:0]	     rx_data_shift_reg;
reg [11:0]	 rx_data_d1_reg;
reg [11:0]	 rx_data_q1_reg;
reg [11:0]	 rx_data_d2_reg;
reg [11:0]	 rx_data_q2_reg;

/*-----------------------rx frame data shift----------------------*/ 
always @(posedge data_clk or negedge rst_n) begin
    if(!rst_n)begin
        rx_frame_iddr_n_r1_reg<='b0;
        rx_data_iddr_n_r1_reg<='d0;
        rx_frame_shift_reg<='d0;
        rx_data_shift_reg<='d0;
    end
    else begin
        rx_frame_iddr_n_r1_reg<=rx_frame_iddr_n;
        rx_data_iddr_n_r1_reg<=rx_data_iddr_n;
        rx_frame_shift_reg<={rx_frame_shift_reg[1:0],rx_frame_iddr_p,rx_frame_iddr_n_r1_reg};
        rx_data_shift_reg<={rx_data_shift_reg[11:0],rx_data_iddr_p,rx_data_iddr_n_r1_reg};
    end
end

/* --------------------rx qd data-------------------------------*/ 
always @(posedge data_clk or negedge rst_n) begin
    if(!rst_n)begin
        rx_data_d1_reg<='d0;
        rx_data_q1_reg<='d0;
        rx_data_d2_reg<='d0;
        rx_data_q2_reg<='d0;
    end
    else begin
        if((rx_frame_shift_reg==4'b1111)&&(phy_mode == PHY_MODE_2R2T)) begin
            rx_data_d1_reg<={rx_data_shift_reg[23:18],rx_data_shift_reg[11:6]};
            rx_data_q1_reg<={rx_data_shift_reg[17:12],rx_data_shift_reg[5:0]};
        end
        else if((rx_frame_shift_reg==4'b0000)&&(phy_mode == PHY_MODE_2R2T)) begin
            rx_data_d2_reg<={rx_data_shift_reg[23:18],rx_data_shift_reg[11:6]};
            rx_data_q2_reg<={rx_data_shift_reg[17:12],rx_data_shift_reg[5:0]};
        end
        else if((rx_frame_shift_reg==4'b1100)&&(phy_mode == PHY_MODE_1R1T)) begin
		      rx_data_d1_reg<={rx_data_shift_reg[23:18],rx_data_shift_reg[11:6]};
		      rx_data_q1_reg<={rx_data_shift_reg[17:12],rx_data_shift_reg[5:0]};
	   end
	end
end

//data dq valid out
wire rx_1r1t_dq1_valid;
wire rx_2r2t_dq1_valid;
wire rx_2r2t_dq2_valid;
assign rx_1r1t_dq1_valid=(phy_mode == PHY_MODE_1R1T)&&(rx_frame_shift_reg==4'b1100);
assign rx_2r2t_dq1_valid=(phy_mode == PHY_MODE_2R2T)&&(rx_frame_shift_reg==4'b1111);
assign rx_2r2t_dq2_valid=(phy_mode == PHY_MODE_2R2T)&&(rx_frame_shift_reg==4'b0000);

assign adc_d1q1_valid=rx_1r1t_dq1_valid||rx_2r2t_dq1_valid;
assign adc_d2q2_valid=rx_2r2t_dq2_valid;
//data dq out
assign adc_data_d1=rx_data_d1_reg;
assign adc_data_q1=rx_data_q1_reg;
assign adc_data_d2=rx_data_d2_reg;
assign adc_data_q2=rx_data_q2_reg;

endmodule