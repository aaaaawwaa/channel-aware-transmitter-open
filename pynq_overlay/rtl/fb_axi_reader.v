`timescale 1ns / 1ps
// Framebuffer line reader: an AXI4 read master that walks a DDR frame buffer one
// line at a time and pushes each line into a line buffer the display can read.
//
// Deliberately a *sequential* master, not a general one: the access pattern is
// fixed (480 lines of 800 RGB565 pixels, then wrap), one burst is in flight at a
// time, and rready is tied high. That is far less logic, and far less to get
// wrong, than a general descriptor-driven engine -- and the bandwidth is not close
// to being a constraint. A line is 1600 bytes = 200 beats of 64 bits; the line
// takes 1056 pixel clocks (~31.7 us) to display, while the burst takes ~200 AXI
// clocks, so the read side is idle most of the time.
//
// The line buffer stride is padded to 2048 bytes rather than the natural 1600: an
// AXI INCR burst must not cross a 4 KB boundary, and 1600-byte rows do (row 2
// starts at 3200 and a 1600-byte read would run past 4096). At a 2048-byte stride
// every 1600-byte read sits inside its own 2048-byte window.
//
// Cross-domain buffer ownership uses the classic async-FIFO scheme: each side owns
// its own toggle per buffer and compares against the other's synchronised copy, so
// neither ever writes a flop the other is clearing.
module fb_axi_reader #(
    parameter AXI_ADDR_WIDTH = 32,
    parameter AXI_DATA_WIDTH = 64,
    parameter AXI_ID_WIDTH   = 6,
    parameter LINE_BEATS     = 200,     // 1600 B / 8 B
    parameter STRIDE_BYTES   = 2048,    // padded row pitch
    parameter LINES          = 480,
    parameter BUFS           = 3
) (
    input  wire                      clk,          // AXI clock domain
    input  wire                      rst,
    input  wire [AXI_ADDR_WIDTH-1:0] base_addr,
    input  wire                      run,          // 0 = idle and re-align to line 0

    // line buffer write port
    output reg                       wr_en,
    output reg  [1:0]                wr_sel,
    output reg  [7:0]                wr_addr,
    output reg  [AXI_DATA_WIDTH-1:0] wr_data,

    // cross-domain handshake
    output reg  [BUFS-1:0]           wr_tog,
    input  wire [BUFS-1:0]           rd_tog_sync,

    // ---- self-check taps (read-only, not in the pixel path) ----
    // `wr_line` is the line number that goes with the write currently on the line
    // buffer port, so the buffer can be tagged with it. It must be registered
    // alongside `wr_en`/`wr_addr` rather than taken straight from `line`: `line`
    // advances on the same edge as the last beat's write, so sampling it at the
    // last beat would tag the buffer with the *next* line.
    output reg  [8:0]                wr_line,
    output wire [8:0]                dbg_line,
    output reg                       dbg_oor,
    output reg                       dbg_misaligned,
    // Per-line data signature: the sum of every pixel value received for the line, so
    // the *contents* can be checked, not just which line went where. Software writes
    // the buffer, so it can compute the same sum and compare -- a tag check alone
    // would pass even if the AXI read handed back garbage.
    output reg  [31:0]               dbg_sig,
    output reg  [8:0]                dbg_sig_line,

    // AXI4 read master
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_araddr,
    output wire [7:0]                m_axi_arlen,
    output wire [2:0]                m_axi_arsize,
    output wire [1:0]                m_axi_arburst,
    output wire [AXI_ID_WIDTH-1:0]   m_axi_arid,
    output wire                      m_axi_arvalid,
    input  wire                      m_axi_arready,
    input  wire [AXI_DATA_WIDTH-1:0] m_axi_rdata,
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_rid,
    input  wire [1:0]                m_axi_rresp,
    input  wire                      m_axi_rlast,
    input  wire                      m_axi_rvalid,
    output wire                      m_axi_rready
);
    localparam S_IDLE = 2'd0, S_AR = 2'd1, S_DATA = 2'd2;

    reg [1:0]  state;
    reg [8:0]  line;      // 0 .. LINES-1
    reg [7:0]  beat;
    reg [1:0]  wsel;
    reg        arvalid_r;

    // ---- which buffer may we fill? free(b) = our toggle == theirs ----
    wire [2:0] free_b = { wr_tog[2] == rd_tog_sync[2],
                          wr_tog[1] == rd_tog_sync[1],
                          wr_tog[0] == rd_tog_sync[0] };
    // round-robin from the last one we filled. The index must wrap modulo the
    // buffer count: plain `wsel + 1` reaches 3, and indexing a 3-bit vector with 3
    // yields x, which silently stalls the whole reader.
    wire [1:0] cand = (wsel == 2'd2) ? 2'd0 : wsel + 2'd1;
    wire       have_free = free_b[cand];

    assign m_axi_araddr  = base_addr + ({{(AXI_ADDR_WIDTH-9){1'b0}}, line} << 11);
    assign m_axi_arlen    = LINE_BEATS[7:0] - 8'd1;
    assign m_axi_arsize   = 3'd3;      // 8 bytes per beat
    assign m_axi_arburst  = 2'b01;     // INCR
    assign m_axi_arid     = {AXI_ID_WIDTH{1'b0}};
    assign m_axi_arvalid  = arvalid_r;
    assign m_axi_rready   = 1'b1;

    // The reader's own view of which line it is fetching right now. Sticky fault
    // taps: they latch and stay latched until the reader is reset, so a single
    // excursion is still visible long after it happened.
    assign dbg_line = line;

    // Sum of the four RGB565 lanes of one beat = the pixel values of that beat. Adding
    // the 200 beat sums gives the whole line's signature, 32 bits wrapping.
    wire [17:0] beat_sum = {2'b0, m_axi_rdata[15:0]}  + {2'b0, m_axi_rdata[31:16]} +
                           {2'b0, m_axi_rdata[47:32]} + {2'b0, m_axi_rdata[63:48]};
    reg  [31:0] sig_acc;

    always @(posedge clk) begin
        if (rst || !run) begin
            dbg_oor        <= 1'b0;
            dbg_misaligned <= 1'b0;
        end else begin
            // Every row of the frame must stay inside base's 1 MiB page. With the
            // 2048-byte pitch the whole 480-row buffer is 0xF0000 bytes, so leaving
            // the page means the row index ran past the frame (or the base was
            // wrong) and the reader is walking somebody else's memory.
            if (m_axi_araddr[31:20] != base_addr[31:20]) dbg_oor        <= 1'b1;
            // A 1600-byte burst only stays inside one 4 KB window if the base is
            // 4 KB aligned; the API promises that, and this says whether it held.
            if (base_addr[11:0]  != 12'd0)               dbg_misaligned <= 1'b1;
        end
    end

    always @(posedge clk) begin
        if (rst || !run) begin
            state    <= S_IDLE;
            arvalid_r<= 1'b0;
            beat     <= 8'd0;
            line     <= 9'd0;
            wsel     <= 2'd2;      // first candidate is buffer 0, the display's too
            wr_en    <= 1'b0;
            wr_sel   <= 2'd0;
            wr_addr  <= 8'd0;
            wr_data  <= {AXI_DATA_WIDTH{1'b0}};
            wr_tog   <= {BUFS{1'b0}};
            wr_line  <= 9'd0;
            sig_acc     <= 32'd0;
            dbg_sig     <= 32'd0;
            dbg_sig_line<= 9'd0;
        end else begin
            wr_en   <= 1'b0;
            wr_line <= line;
            case (state)
                S_IDLE: if (have_free) begin
                    wsel     <= cand;
                    beat     <= 8'd0;
                    sig_acc  <= 32'd0;
                    arvalid_r<= 1'b1;
                    state    <= S_AR;
                end

                S_AR: if (m_axi_arready) begin
                    arvalid_r <= 1'b0;
                    state     <= S_DATA;
                end

                S_DATA: if (m_axi_rvalid) begin
                    wr_en   <= 1'b1;
                    wr_sel  <= wsel;
                    wr_addr <= beat;
                    wr_data <= m_axi_rdata;
                    sig_acc <= sig_acc + beat_sum;
                    beat    <= beat + 8'd1;
                    if (m_axi_rlast) begin
                        // hand this line over to the display
                        wr_tog[wsel] <= ~wr_tog[wsel];
                        dbg_sig      <= sig_acc + beat_sum;   // include the last beat
                        dbg_sig_line <= line;
                        line <= (line == LINES[8:0] - 9'd1) ? 9'd0 : line + 9'd1;
                        state <= S_IDLE;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
