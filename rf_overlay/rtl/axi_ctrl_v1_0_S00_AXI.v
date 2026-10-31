
`timescale 1 ns / 1 ps

	module axi_lite_v1_0_S00_AXI #
	(
		// Do not modify the parameters beyond this line
		// Width of S_AXI data bus
		parameter integer C_S_AXI_DATA_WIDTH	= 32,
		// Width of S_AXI address bus
		parameter integer C_S_AXI_ADDR_WIDTH	= 6
	)
	(
		// Users to add ports here
		output wire [6:0] idelay_en,
		output wire [4:0] idelay_tap,
		output wire [11:0] phase_inc,
		output wire           phy_mode,
		output wire           wave_sel,
		// TX sample buffer (BRAM) configuration (reg5/reg6) and its read
		// position (reg7, read-only).  src_sel replaces the BD constant that
		// used to hold `mode` at 0.
		output wire           src_sel,
		output wire [12:0]    smp_len,
		output wire           smp_wrap,
		output wire           smp_commit,
		input  wire [12:0]    smp_pos,
		// RX capture configuration (reg9/reg11/reg12) and status (reg13).
		output wire           cfg_arm,
		output wire           cfg_cont,
		output wire           cfg_commit,
		output wire [2:0]     cfg_ratio,
		output wire [12:0]    cfg_len,
		input  wire [12:0]    cap_pos,
		input  wire           cap_done,
		input  wire           data_clk,
		input  wire           rst_source_ok,
		// User ports ends
		// Do not modify the ports beyond this line

		// Global Clock Signal
		input wire  S_AXI_ACLK,
		// Global Reset Signal. This Signal is Active LOW
		input wire  S_AXI_ARESETN,
		// Write address (issued by master, acceped by Slave)
		input wire [C_S_AXI_ADDR_WIDTH-1 : 0] S_AXI_AWADDR,
		// Write channel Protection type. This signal indicates the
    		// privilege and security level of the transaction, and whether
    		// the transaction is a data access or an instruction access.
		input wire [2 : 0] S_AXI_AWPROT,
		// Write address valid. This signal indicates that the master signaling
    		// valid write address and control information.
		input wire  S_AXI_AWVALID,
		// Write address ready. This signal indicates that the slave is ready
    		// to accept an address and associated control signals.
		output wire  S_AXI_AWREADY,
		// Write data (issued by master, acceped by Slave) 
		input wire [C_S_AXI_DATA_WIDTH-1 : 0] S_AXI_WDATA,
		// Write strobes. This signal indicates which byte lanes hold
    		// valid data. There is one write strobe bit for each eight
    		// bits of the write data bus.    
		input wire [(C_S_AXI_DATA_WIDTH/8)-1 : 0] S_AXI_WSTRB,
		// Write valid. This signal indicates that valid write
    		// data and strobes are available.
		input wire  S_AXI_WVALID,
		// Write ready. This signal indicates that the slave
    		// can accept the write data.
		output wire  S_AXI_WREADY,
		// Write response. This signal indicates the status
    		// of the write transaction.
		output wire [1 : 0] S_AXI_BRESP,
		// Write response valid. This signal indicates that the channel
    		// is signaling a valid write response.
		output wire  S_AXI_BVALID,
		// Response ready. This signal indicates that the master
    		// can accept a write response.
		input wire  S_AXI_BREADY,
		// Read address (issued by master, acceped by Slave)
		input wire [C_S_AXI_ADDR_WIDTH-1 : 0] S_AXI_ARADDR,
		// Protection type. This signal indicates the privilege
    		// and security level of the transaction, and whether the
    		// transaction is a data access or an instruction access.
		input wire [2 : 0] S_AXI_ARPROT,
		// Read address valid. This signal indicates that the channel
    		// is signaling valid read address and control information.
		input wire  S_AXI_ARVALID,
		// Read address ready. This signal indicates that the slave is
    		// ready to accept an address and associated control signals.
		output wire  S_AXI_ARREADY,
		// Read data (issued by slave)
		output wire [C_S_AXI_DATA_WIDTH-1 : 0] S_AXI_RDATA,
		// Read response. This signal indicates the status of the
    		// read transfer.
		output wire [1 : 0] S_AXI_RRESP,
		// Read valid. This signal indicates that the channel is
    		// signaling the required read data.
		output wire  S_AXI_RVALID,
		// Read ready. This signal indicates that the master can
    		// accept the read data and response information.
		input wire  S_AXI_RREADY
	);

	// AXI4LITE signals
	reg [C_S_AXI_ADDR_WIDTH-1 : 0] 	axi_awaddr;
	reg  	axi_awready;
	reg  	axi_wready;
	reg [1 : 0] 	axi_bresp;
	reg  	axi_bvalid;
	reg [C_S_AXI_ADDR_WIDTH-1 : 0] 	axi_araddr;
	reg  	axi_arready;
	reg [C_S_AXI_DATA_WIDTH-1 : 0] 	axi_rdata;
	reg [1 : 0] 	axi_rresp;
	reg  	axi_rvalid;

	// Example-specific design signals
	// local parameter for addressing 32 bit / 64 bit C_S_AXI_DATA_WIDTH
	// ADDR_LSB is used for addressing 32/64 bit registers/memories
	// ADDR_LSB = 2 for 32 bits (n downto 2)
	// ADDR_LSB = 3 for 64 bits (n downto 3)
	localparam integer ADDR_LSB = (C_S_AXI_DATA_WIDTH/32) + 1;
	localparam integer OPT_MEM_ADDR_BITS = 3;
	//----------------------------------------------
	//-- Signals for user logic register space example
	//------------------------------------------------
	//-- Number of Slave Registers 16
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg0;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg1;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg2;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg3;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg5;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg6;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg7;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg8;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg9;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg10;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg11;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg12;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg13;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg14;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_reg15;
	wire	 slv_reg_rden;
	wire	 slv_reg_wren;
	reg [C_S_AXI_DATA_WIDTH-1:0]	 reg_data_out;
	wire [C_S_AXI_DATA_WIDTH-1:0]	 status_word;
	integer	 byte_index;
	reg	 aw_en;

	// I/O Connections assignments

	assign S_AXI_AWREADY	= axi_awready;
	assign S_AXI_WREADY	= axi_wready;
	assign S_AXI_BRESP	= axi_bresp;
	assign S_AXI_BVALID	= axi_bvalid;
	assign S_AXI_ARREADY	= axi_arready;
	assign S_AXI_RDATA	= axi_rdata;
	assign S_AXI_RRESP	= axi_rresp;
	assign S_AXI_RVALID	= axi_rvalid;
	// Implement axi_awready generation
	// axi_awready is asserted for one S_AXI_ACLK clock cycle when both
	// S_AXI_AWVALID and S_AXI_WVALID are asserted. axi_awready is
	// de-asserted when reset is low.

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_awready <= 1'b0;
	      aw_en <= 1'b1;
	    end 
	  else
	    begin    
	      if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en)
	        begin
	          // slave is ready to accept write address when 
	          // there is a valid write address and write data
	          // on the write address and data bus. This design 
	          // expects no outstanding transactions. 
	          axi_awready <= 1'b1;
	          aw_en <= 1'b0;
	        end
	        else if (S_AXI_BREADY && axi_bvalid)
	            begin
	              aw_en <= 1'b1;
	              axi_awready <= 1'b0;
	            end
	      else           
	        begin
	          axi_awready <= 1'b0;
	        end
	    end 
	end       

	// Implement axi_awaddr latching
	// This process is used to latch the address when both 
	// S_AXI_AWVALID and S_AXI_WVALID are valid. 

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_awaddr <= 0;
	    end 
	  else
	    begin    
	      if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en)
	        begin
	          // Write Address latching 
	          axi_awaddr <= S_AXI_AWADDR;
	        end
	    end 
	end       

	// Implement axi_wready generation
	// axi_wready is asserted for one S_AXI_ACLK clock cycle when both
	// S_AXI_AWVALID and S_AXI_WVALID are asserted. axi_wready is 
	// de-asserted when reset is low. 

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_wready <= 1'b0;
	    end 
	  else
	    begin    
	      if (~axi_wready && S_AXI_WVALID && S_AXI_AWVALID && aw_en )
	        begin
	          // slave is ready to accept write data when 
	          // there is a valid write address and write data
	          // on the write address and data bus. This design 
	          // expects no outstanding transactions. 
	          axi_wready <= 1'b1;
	        end
	      else
	        begin
	          axi_wready <= 1'b0;
	        end
	    end 
	end       

	// Implement memory mapped register select and write logic generation
	// The write data is accepted and written to memory mapped registers when
	// axi_awready, S_AXI_WVALID, axi_wready and S_AXI_WVALID are asserted. Write strobes are used to
	// select byte enables of slave registers while writing.
	// These registers are cleared when reset (active low) is applied.
	// Slave register write enable is asserted when valid address and data are available
	// and the slave is ready to accept the write address and write data.
	assign slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      slv_reg0 <= 0;
	      slv_reg1 <= 0;
	      slv_reg2 <= 0;
	      slv_reg3 <= 0;
	      slv_reg5 <= 0;
	      slv_reg6 <= 0;
	      slv_reg7 <= 0;
	      slv_reg8 <= 0;
	      slv_reg9 <= 0;
	      slv_reg10 <= 0;
	      slv_reg11 <= 0;
	      slv_reg12 <= 0;
	      slv_reg13 <= 0;
	      slv_reg14 <= 0;
	      slv_reg15 <= 0;
	    end 
	  else begin
	    if (slv_reg_wren)
	      begin
	        case ( axi_awaddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	          4'h0:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 0
	                slv_reg0[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h1:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 1
	                slv_reg1[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h2:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 2
	                slv_reg2[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h3:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 3
	                slv_reg3[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h5:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes
	                // Slave register 5
	                slv_reg5[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h6:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 6
	                slv_reg6[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          // 4'h7 deliberately has no write arm.  reg7 reads the TX reader's
	          // position, so a write arm would store a value that the read mux
	          // never returns -- a silent write-then-read-back disagreement of
	          // exactly the kind reg4 avoids by having no register at all.

	          4'h8:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 8
	                slv_reg8[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'h9:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 9
	                slv_reg9[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'hA:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 10
	                slv_reg10[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'hB:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 11
	                slv_reg11[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'hC:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 12
	                slv_reg12[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          // 4'hD has no write arm either: reg13 reads the RX capture status
	          // (done + position).  See 4'h7.

	          4'hE:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 14
	                slv_reg14[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          4'hF:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 ) begin
	                // Respective byte enables are asserted as per write strobes 
	                // Slave register 15
	                slv_reg15[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	              end  
	          default : begin
	                      slv_reg0 <= slv_reg0;
	                      slv_reg1 <= slv_reg1;
	                      slv_reg2 <= slv_reg2;
	                      slv_reg3 <= slv_reg3;
	                      slv_reg5 <= slv_reg5;
	                      slv_reg6 <= slv_reg6;
	                      slv_reg7 <= slv_reg7;
	                      slv_reg8 <= slv_reg8;
	                      slv_reg9 <= slv_reg9;
	                      slv_reg10 <= slv_reg10;
	                      slv_reg11 <= slv_reg11;
	                      slv_reg12 <= slv_reg12;
	                      slv_reg13 <= slv_reg13;
	                      slv_reg14 <= slv_reg14;
	                      slv_reg15 <= slv_reg15;
	                    end
	        endcase
	      end
	  end
	end    

	// Implement write response logic generation
	// The write response and response valid signals are asserted by the slave 
	// when axi_wready, S_AXI_WVALID, axi_wready and S_AXI_WVALID are asserted.  
	// This marks the acceptance of address and indicates the status of 
	// write transaction.

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_bvalid  <= 0;
	      axi_bresp   <= 2'b0;
	    end 
	  else
	    begin    
	      if (axi_awready && S_AXI_AWVALID && ~axi_bvalid && axi_wready && S_AXI_WVALID)
	        begin
	          // indicates a valid write response is available
	          axi_bvalid <= 1'b1;
	          axi_bresp  <= 2'b0; // 'OKAY' response 
	        end                   // work error responses in future
	      else
	        begin
	          if (S_AXI_BREADY && axi_bvalid) 
	            //check if bready is asserted while bvalid is high) 
	            //(there is a possibility that bready is always asserted high)   
	            begin
	              axi_bvalid <= 1'b0; 
	            end  
	        end
	    end
	end   

	// Implement axi_arready generation
	// axi_arready is asserted for one S_AXI_ACLK clock cycle when
	// S_AXI_ARVALID is asserted. axi_awready is 
	// de-asserted when reset (active low) is asserted. 
	// The read address is also latched when S_AXI_ARVALID is 
	// asserted. axi_araddr is reset to zero on reset assertion.

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_arready <= 1'b0;
	      axi_araddr  <= 32'b0;
	    end 
	  else
	    begin    
	      if (~axi_arready && S_AXI_ARVALID)
	        begin
	          // indicates that the slave has acceped the valid read address
	          axi_arready <= 1'b1;
	          // Read address latching
	          axi_araddr  <= S_AXI_ARADDR;
	        end
	      else
	        begin
	          axi_arready <= 1'b0;
	        end
	    end 
	end       

	// Implement axi_arvalid generation
	// axi_rvalid is asserted for one S_AXI_ACLK clock cycle when both 
	// S_AXI_ARVALID and axi_arready are asserted. The slave registers 
	// data are available on the axi_rdata bus at this instance. The 
	// assertion of axi_rvalid marks the validity of read data on the 
	// bus and axi_rresp indicates the status of read transaction.axi_rvalid 
	// is deasserted on reset (active low). axi_rresp and axi_rdata are 
	// cleared to zero on reset (active low).  
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_rvalid <= 0;
	      axi_rresp  <= 0;
	    end 
	  else
	    begin    
	      if (axi_arready && S_AXI_ARVALID && ~axi_rvalid)
	        begin
	          // Valid read data is available at the read data bus
	          axi_rvalid <= 1'b1;
	          axi_rresp  <= 2'b0; // 'OKAY' response
	        end   
	      else if (axi_rvalid && S_AXI_RREADY)
	        begin
	          // Read data is accepted by the master
	          axi_rvalid <= 1'b0;
	        end                
	    end
	end    

	// Implement memory mapped register select and read logic generation
	// Slave register read enable is asserted when valid address is available
	// and the slave is ready to accept the read address.
	// ---- Diagnostic readback from the DATA_CLK domain (reg7 and reg13).
	//
	// These are POSITION COUNTERS.  They exist so software can see that the
	// datapath is actually moving -- reg7 proves the TX reader is fetching, and
	// reg13 gives the capture handshake (arm, watch done, then read the buffer).
	//
	// They are crossed with plain two-stage chains, which is NOT a correct bus
	// crossing in general: each bit resolves independently, so a read taken while
	// the counter is changing can return a value that never existed (off by one,
	// or worse if several bits turn over together).  That is tolerable for a
	// diagnostic and is written down here so it is not mistaken for a
	// measurement.  If any criterion ever depends on the exact position, this
	// must become a Gray-coded crossing -- the DATA_CLK edge counter below already
	// is one -- or a proper handshake.
	//
	// This sits outside and ABOVE the read process.  Declaring it after its use
	// would make the identifier an implicit wire at the point of use, and putting
	// it inside the always block was rejected outright -- both are the same
	// hazard class as the reader's `primed`.
	reg [12:0] smp_pos_m,  smp_pos_sync;
	reg [12:0] cap_pos_m,  cap_pos_sync;
	reg [1:0]  cap_done_sync;
	always @(posedge S_AXI_ACLK or negedge S_AXI_ARESETN) begin
		if (!S_AXI_ARESETN) begin
			smp_pos_m <= 13'd0; smp_pos_sync <= 13'd0;
			cap_pos_m <= 13'd0; cap_pos_sync <= 13'd0;
			cap_done_sync <= 2'b00;
		end else begin
			smp_pos_m    <= smp_pos;
			smp_pos_sync <= smp_pos_m;
			cap_pos_m    <= cap_pos;
			cap_pos_sync <= cap_pos_m;
			cap_done_sync <= {cap_done_sync[0], cap_done};
		end
	end

	wire [31:0] smp_pos_word = { 19'd0, smp_pos_sync };
	wire [31:0] cap_status   = { 18'd0, cap_done_sync[1], cap_pos_sync };

	assign slv_reg_rden = axi_arready & S_AXI_ARVALID & ~axi_rvalid;
	always @(*)
	begin
	      // Address decoding for reading registers
	      case ( axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	        4'h0   : reg_data_out <= slv_reg0;
	        4'h1   : reg_data_out <= slv_reg1;
	        4'h2   : reg_data_out <= slv_reg2;
	        4'h3   : reg_data_out <= slv_reg3;
	        4'h4   : reg_data_out <= status_word;
	        4'h5   : reg_data_out <= slv_reg5;
	        4'h6   : reg_data_out <= slv_reg6;
	        4'h7   : reg_data_out <= smp_pos_word;
	        4'h8   : reg_data_out <= slv_reg8;
	        4'h9   : reg_data_out <= slv_reg9;
	        4'hA   : reg_data_out <= slv_reg10;
	        4'hB   : reg_data_out <= slv_reg11;
	        4'hC   : reg_data_out <= slv_reg12;
	        4'hD   : reg_data_out <= cap_status;
	        4'hE   : reg_data_out <= slv_reg14;
	        4'hF   : reg_data_out <= slv_reg15;
	        default : reg_data_out <= 0;
	      endcase
	end

	// Output register or memory read data
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_rdata  <= 0;
	    end 
	  else
	    begin    
	      // When there is a valid read address (S_AXI_ARVALID) with 
	      // acceptance of read address by the slave (axi_arready), 
	      // output the read dada 
	      if (slv_reg_rden)
	        begin
	          axi_rdata <= reg_data_out;     // register read data
	        end   
	    end
	end    

	// Add user logic here
	assign  idelay_en  = slv_reg0[6:0];
	assign  idelay_tap = slv_reg1[4:0];
	assign  phase_inc  = slv_reg2[11:0];
	assign  phy_mode   = slv_reg3[0];
	// reg10 is unused by the example and sits at offset 0x28.  It was chosen over
	// a spare bit of reg0-reg3 on purpose: phy_mode's write discipline requires
	// its whole word to be written while DATA_CLK is stopped, so sharing that
	// register would make every waveform change a framing change too.
	assign  wave_sel   = slv_reg10[0];

	// ---- TX sample buffer.  reg5/reg6 are the configuration, reg7 is status.
	//
	// src_sel sits in reg5 rather than sharing reg3's byte with phy_mode: phy_mode
	// may only be written while DATA_CLK is STOPPED (tools/impl1_clock_groups.tcl
	// is the authoritative statement), and putting an unrelated bit in the same
	// register invites a read-modify-write that touches it by accident.
	assign  src_sel    = slv_reg5[0];
	assign  smp_wrap   = slv_reg5[1];
	assign  smp_commit = slv_reg5[2];
	assign  smp_len    = slv_reg6[12:0];

	// ---- RX capture.  reg9/reg11/reg12 configure it, reg13 reports status.
	//
	// cfg_commit is a toggle, not a level: the atomically-latched consumers see
	// an edge, so software can commit two identical configurations in a row and
	// both take effect.  A level would make the second one a no-op.
	assign  cfg_arm    = slv_reg9[0];
	assign  cfg_cont   = slv_reg9[1];
	assign  cfg_commit = slv_reg9[2];
	assign  cfg_ratio  = slv_reg11[2:0];
	assign  cfg_len    = slv_reg12[12:0];

	// ---------------------------------------------------------------------
	// Read-only STATUS (offset 0x10).
	//
	// Answers "is the AD9361's DATA_CLK actually reaching the FPGA, and how
	// fast?" without any software timing.  A DATA_CLK-domain Gray counter is
	// sampled by a self-running window on FCLK0; software reads the count
	// together with a rolling sequence number, so it never reads a torn
	// window.
	//
	// The counter MUST be clocked by data_clk, not sampled by FCLK0: FCLK0 is
	// 50 MHz and the candidate DATA_CLK rates are 30/60 MHz, so sampling would
	// alias (drop edges) while still reading something plausible.  Gray coding
	// only makes the multi-bit crossing safe -- it does not recover lost edges.
	// ---------------------------------------------------------------------

	// DATA_CLK-domain free-running counter.  Reset is async-asserted from the
	// AXI reset (so the value is defined even when data_clk never ticks) and
	// sync-released into the DATA_CLK domain by the 2-FF chain below.
	reg [1:0] dclk_rst_sync;
	always @(posedge data_clk or negedge S_AXI_ARESETN) begin
		if (!S_AXI_ARESETN) dclk_rst_sync <= 2'b00;
		else                dclk_rst_sync <= {dclk_rst_sync[0], 1'b1};
	end
	wire dclk_rst_n = dclk_rst_sync[1];

	reg [15:0] dclk_bin;
	reg [15:0] dclk_gray;
	always @(posedge data_clk or negedge dclk_rst_n) begin
		if (!dclk_rst_n) begin
			dclk_bin  <= 16'd0;
			dclk_gray <= 16'd0;
		end else begin
			dclk_bin  <= dclk_bin + 16'd1;
			dclk_gray <= (dclk_bin + 16'd1) ^ ((dclk_bin + 16'd1) >> 1);
		end
	end

	// 3-deep sync of the Gray word into FCLK0.  Any sample is the old or the
	// new value, never a mix -- that is what Gray coding buys.
	reg [15:0] gray_s0, gray_s1, gray_s2;
	always @(posedge S_AXI_ACLK or negedge S_AXI_ARESETN) begin
		if (!S_AXI_ARESETN) {gray_s0, gray_s1, gray_s2} <= 48'd0;
		else                {gray_s0, gray_s1, gray_s2} <= {dclk_gray, gray_s0, gray_s1};
	end

	function [15:0] gray2bin;
		input [15:0] g;
		integer i;
		begin
			gray2bin[15] = g[15];
			for (i = 14; i >= 0; i = i - 1)
				gray2bin[i] = gray2bin[i+1] ^ g[i];
		end
	endfunction

	wire [15:0] dclk_now     = gray2bin(gray_s2);
	// A Gray increment changes exactly one bit, so "s1 != s2" means an edge
	// arrived -- this is the liveness indicator, not a data comparison.
	wire        gray_changed = (gray_s1 != gray_s2);

	// Self-running measurement window: 2^WIN_LOG2 FCLK0 cycles = 655.36 us.
	// After each window the FSM idles for WIN_GAP cycles with the results
	// published (win_active=0, win_done=1).  Software (and the TB) reads
	// win_active/win_done as LEVELS right after win_seq changes; restarting
	// the next window one cycle later would make them single-cycle pulses
	// that no AXI read could ever catch.  The measurement itself stays
	// exactly 2^WIN_LOG2 cycles; only the publish period gains the gap.
	localparam [3:0]  WIN_LOG2   = 4'd15;
	localparam [15:0] WIN_CYCLES = (16'd1 << WIN_LOG2) - 16'd1;
	localparam [15:0] WIN_GAP    = 16'd255;

	reg [15:0] win_cnt;
	reg        win_active;
	reg [3:0]  win_seq;
	reg        win_done;
	reg [15:0] win_base;
	reg [15:0] win_cycles;
	reg        clk_seen;
	reg [15:0] win_gap_cnt;

	always @(posedge S_AXI_ACLK or negedge S_AXI_ARESETN) begin
		if (!S_AXI_ARESETN) begin
			win_cnt    <= 16'd0;
			win_active <= 1'b0;
			win_seq    <= 4'd0;
			win_done   <= 1'b0;
			win_base   <= 16'd0;
			win_cycles <= 16'd0;
			clk_seen   <= 1'b0;
			win_gap_cnt <= 16'd0;
		end else begin
			if (gray_changed) clk_seen <= 1'b1;
			if (!win_active && !win_done) begin
				// first window after reset
				win_active <= 1'b1;
				win_base   <= dclk_now;
				win_cnt    <= 16'd0;
				win_done   <= 1'b0;
				win_gap_cnt <= 16'd0;
			end else if (win_active) begin
				if (win_cnt == WIN_CYCLES) begin
					win_cycles <= dclk_now - win_base;
					win_seq    <= win_seq + 4'd1;
					win_done   <= 1'b1;
					win_active <= 1'b0;
					win_gap_cnt <= 16'd0;
				end else begin
					win_cnt <= win_cnt + 16'd1;
				end
			end else begin
				// publish period: results held, next window deferred by WIN_GAP
				if (win_gap_cnt == WIN_GAP) begin
					win_active <= 1'b1;
					win_base   <= dclk_now;
					win_cnt    <= 16'd0;
					win_done   <= 1'b0;
					win_gap_cnt <= 16'd0;
				end else begin
					win_gap_cnt <= win_gap_cnt + 16'd1;
				end
			end
		end
	end

	// rst_source_ok: a DATA_CLK-domain level.  It means "rf_tx_source's own
	// reset chain has filled", NOT "the phy is ready" -- see the plan's D1.
	reg [1:0] rso_sync;
	always @(posedge S_AXI_ACLK or negedge S_AXI_ARESETN) begin
		if (!S_AXI_ARESETN) rso_sync <= 2'b00;
		else                rso_sync <= {rso_sync[0], rst_source_ok};
	end

	assign status_word = { 4'h0,          // [31:28] reserved
	                       WIN_LOG2,      // [27:24] window is 2^15 FCLK0 cycles
	                       win_cycles,    // [23:8]  DATA_CLK edges in the window
	                       win_seq,       // [7:4]   rolling window index
	                       win_active,    // [3]     1 = window in progress
	                       rso_sync[1],   // [2]     rst_source_ok (synced level)
	                       win_done,      // [1]     last window completed
	                       clk_seen };    // [0]     sticky: an edge was seen
	// User logic ends

	endmodule
