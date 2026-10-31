`timescale 1ns / 1ps
// TFT output with a selectable pixel source: the built-in colour bars, or a DDR
// frame buffer read back through the Zynq HP port.
//
// Two clock domains meet in the line buffers, and that is the only place they
// meet: the reader writes on the AXI clock, the display reads on the pixel clock,
// and the buffers are inferred as true dual-port block RAM. Ownership is handed
// over with a per-buffer toggle (the async-FIFO scheme), so neither side ever
// clears a flag the other owns.
//
// Three buffers is enough: the reader refills a line in ~200 AXI clocks while the
// display takes 1056 pixel clocks to show one, so the ring is nearly always full
// and the display practically never has to hold a line.
module lcd_fb #(
    parameter AXI_ADDR_WIDTH = 32,
    parameter AXI_ID_WIDTH   = 6,       // matches PS S_AXI_HP0's ID width
    parameter LINE_BEATS     = 200,     // 800 px * 2 B / 8 B
    parameter STRIDE_BYTES   = 2048,    // padded row pitch, see fb_axi_reader
    parameter LINES          = 480,
    parameter BUFS           = 3
) (
    // ---- pixel clock domain ----
    input  wire        pclk,
    input  wire        pll_locked,
    input  wire        fb_en,
    output wire        lcd_pclk,
    output wire        lcd_de,
    output wire        lcd_hs,
    output wire        lcd_vs,
    output wire [15:0] lcd_rgb,
    output wire        lcd_bl,

    // ---- AXI clock domain ----
    input  wire                      aclk,
    input  wire                      areset,
    input  wire [AXI_ADDR_WIDTH-1:0] fb_base,

    // ---- self-check taps, read back over the debug GPIO ----
    output wire [31:0]               dbg0,
    output wire [31:0]               dbg1,
    output wire [31:0]               dbg2,
    output wire [31:0]               dbg3,

    // ---- AXI4 read master ----
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_araddr,
    output wire [7:0]                m_axi_arlen,
    output wire [2:0]                m_axi_arsize,
    output wire [1:0]                m_axi_arburst,
    output wire [AXI_ID_WIDTH-1:0]   m_axi_arid,
    output wire                      m_axi_arvalid,
    input  wire                      m_axi_arready,
    input  wire [63:0]               m_axi_rdata,
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_rid,
    input  wire [1:0]                m_axi_rresp,
    input  wire                      m_axi_rlast,
    input  wire                      m_axi_rvalid,
    output wire                      m_axi_rready
);
    // ---- pixel-domain reset from the MMCM lock ----
    (* ASYNC_REG = "TRUE" *) reg lock_s0, lock_s1;
    always @(posedge pclk) begin
        lock_s0 <= pll_locked;
        lock_s1 <= lock_s0;
    end
    wire p_rstn = lock_s1;

    // `fb_en` is written by the PS (clk_fpga_0) and used here in the pixel domain, and
    // the two clock groups are declared asynchronous -- so this crossing is not timed
    // and needs an explicit synchroniser. It feeds the ring's reset, which is the last
    // place you want a value sampled mid-transition. Both the ring and lcd_timing use
    // this one synchronised copy, so they can never disagree about which source is up.
    (* ASYNC_REG = "TRUE" *) reg fen_s0, fen_s1;
    always @(posedge pclk) begin
        fen_s0 <= fb_en;
        fen_s1 <= fen_s0;
    end
    wire fb_en_p = fen_s1;

    // ---- handshake: writer toggles -> display ----
    wire [BUFS-1:0] wr_tog;      // driven by the reader
    (* ASYNC_REG = "TRUE" *) reg [BUFS-1:0] wt_s0, wt_s1;
    always @(posedge pclk) begin
        wt_s0 <= wr_tog;
        wt_s1 <= wt_s0;
    end

    reg  [BUFS-1:0] rd_tog;      // display-owned
    (* ASYNC_REG = "TRUE" *) reg [BUFS-1:0] rt_s0, rt_s1;
    always @(posedge aclk) begin
        rt_s0 <= rd_tog;
        rt_s1 <= rt_s0;
    end
    (* ASYNC_REG = "TRUE" *) reg run_s0, run_s1;
    always @(posedge aclk) begin
        run_s0 <= fb_en;
        run_s1 <= run_s0;
    end

    // ---- the reader ----
    wire        wr_en;
    wire [1:0]  wr_sel;
    wire [7:0]  wr_addr;
    wire [63:0] wr_data;
    wire [8:0]  wr_line;      // line number that belongs to the write on the port
    wire [8:0]  rdr_line;
    wire        rdr_oor, rdr_misaligned;
    wire [31:0] rdr_sig;
    wire [8:0]  rdr_sig_line;

    fb_axi_reader #(
        .AXI_ADDR_WIDTH (AXI_ADDR_WIDTH),
        .AXI_ID_WIDTH   (AXI_ID_WIDTH),
        .LINE_BEATS     (LINE_BEATS),
        .STRIDE_BYTES   (STRIDE_BYTES),
        .LINES          (LINES),
        .BUFS           (BUFS)
    ) u_reader (
        .clk          (aclk),
        .rst          (areset),
        .base_addr    (fb_base),
        .run          (run_s1),
        .wr_en        (wr_en),
        .wr_sel       (wr_sel),
        .wr_addr      (wr_addr),
        .wr_data      (wr_data),
        .wr_tog       (wr_tog),
        .rd_tog_sync  (rt_s1),
        .wr_line      (wr_line),
        .dbg_line     (rdr_line),
        .dbg_oor      (rdr_oor),
        .dbg_misaligned(rdr_misaligned),
        .dbg_sig      (rdr_sig),
        .dbg_sig_line (rdr_sig_line),
        .m_axi_araddr (m_axi_araddr),
        .m_axi_arlen  (m_axi_arlen),
        .m_axi_arsize (m_axi_arsize),
        .m_axi_arburst(m_axi_arburst),
        .m_axi_arid   (m_axi_arid),
        .m_axi_arvalid(m_axi_arvalid),
        .m_axi_arready(m_axi_arready),
        .m_axi_rdata  (m_axi_rdata),
        .m_axi_rid    (m_axi_rid),
        .m_axi_rresp  (m_axi_rresp),
        .m_axi_rlast  (m_axi_rlast),
        .m_axi_rvalid (m_axi_rvalid),
        .m_axi_rready (m_axi_rready)
    );

    // ---- line buffers: true dual port, write on aclk, read on pclk ----
    reg [63:0] buf0 [0:LINE_BEATS-1];
    reg [63:0] buf1 [0:LINE_BEATS-1];
    reg [63:0] buf2 [0:LINE_BEATS-1];

    always @(posedge aclk) if (wr_en) begin
        case (wr_sel)
            2'd0: buf0[wr_addr] <= wr_data;
            2'd1: buf1[wr_addr] <= wr_data;
            default: buf2[wr_addr] <= wr_data;
        endcase
    end

    // ---- self-check: tag every buffer with the line it holds ----
    // The display consumes exactly one buffer per visible line, so its k-th visible
    // line must carry the reader's line k. Tagging the buffers turns that invariant
    // into something the hardware can check by itself: without it, "the panel shows
    // the wrong pixels" can only be argued about from photographs.
    reg [8:0] tag0, tag1, tag2;

    always @(posedge aclk) if (wr_en && wr_addr == LINE_BEATS[7:0] - 8'd1) begin
        case (wr_sel)
            2'd0:    tag0 <= wr_line;
            2'd1:    tag1 <= wr_line;
            default: tag2 <= wr_line;
        endcase
    end

    // ---- display side ----
    wire [10:0] px_nx;
    wire        vactive, vfirst;
    reg  [1:0]  rd_sel;
    reg         started;

    // Ring indices must wrap modulo the buffer count. `rd_sel + 1` is 3 when
    // rd_sel is 2, and indexing a 3-bit vector with 3 gives x.
    wire [1:0] nxt1 = (rd_sel == 2'd2) ? 2'd0 : rd_sel + 2'd1;
    wire [1:0] nxt2 = (rd_sel == 2'd1) ? 2'd0 :
                      (rd_sel == 2'd2) ? 2'd1 : 2'd2;

    // A buffer is ready only when the writer's toggle DIFFERS from ours. Using the
    // raw synchronised toggle (without the xor against rd_tog) consumes buffers
    // that were never filled and steps past ones that were.
    wire [2:0] ready_b = wt_s1 ^ rd_tog;

    // Switch point: one column before the visible window opens, so the buffer mux
    // has settled by the time the first block-RAM address is presented. Only visible
    // lines switch: a switch on every line boundary would consume the ring 525 times
    // a frame against the reader's 480.
    wire at_switch = (px_nx == 11'h7FF) && vactive;
    // `!started` additionally requires the first visible line of a frame, so the
    // display's k-th line is the reader's k-th line rather than being offset by
    // however many buffers happened to be queued when it woke up.
    wire take1 = ready_b[nxt1] && (started || vfirst);
    wire take2 = ready_b[nxt2] && (started || vfirst) && !ready_b[nxt1];
    wire sw    = at_switch && (take1 || take2);
    // Nothing ready: keep this buffer. The writer still treats it as ours because we
    // have not toggled, so it cannot be overwritten under us -- but the panel now
    // shows this line twice, which is exactly the failure the tag check catches.
    // Only counts once the display is running: before `started` it is meant to show
    // black while it waits for the first visible line of a frame.
    wire st    = at_switch && started && !sw;

    // ---- display-side self-check state ----
    reg [8:0] exp_line;     // reader line this visible row must be showing
    reg [8:0] tag_sel;      // line held by the buffer currently selected
    reg       sw_r1, sw_r2; // switch strobe, aligned to when tag_sel shows the new tag
    reg       tag_bad;      // sticky: the ring ever handed over the wrong line
    reg [8:0] bad_row, bad_got;
    reg       disp_stall;   // sticky: the display ever ran out of buffers

    wire [8:0] exp_next = vfirst ? 9'd0 :
                          (exp_line == LINES[8:0] - 9'd1) ? 9'd0 : exp_line + 9'd1;

    always @(posedge pclk) begin
        if (!p_rstn || !fb_en_p) begin
            rd_sel     <= 2'd2;          // next pick is buffer 0, the reader's first
            started    <= 1'b0;
            rd_tog     <= {BUFS{1'b0}};
            exp_line   <= 9'd0;
            sw_r1      <= 1'b0;
            sw_r2      <= 1'b0;
            tag_bad    <= 1'b0;
            bad_row    <= 9'd0;
            bad_got    <= 9'd0;
            disp_stall <= 1'b0;
        end else begin
            sw_r1 <= sw;
            sw_r2 <= sw_r1;
            if (sw) exp_line <= exp_next;
            if (st) disp_stall <= 1'b1;
            // `tag_sel` is registered one stage behind `rd_sel`, so it only carries
            // the newly taken buffer's tag two cycles after the switch -- hence the
            // two-deep strobe. `exp_line` is stable over that window.
            if (sw_r2 && !tag_bad && (tag_sel != exp_line)) begin
                tag_bad <= 1'b1;
                bad_row <= exp_line;
                bad_got <= tag_sel;
            end

            if (at_switch) begin
                if (take1) begin
                    if (started) rd_tog[rd_sel] <= ~rd_tog[rd_sel];
                    rd_sel  <= nxt1;
                    started <= 1'b1;
                end else if (take2) begin
                    if (started) rd_tog[rd_sel] <= ~rd_tog[rd_sel];
                    rd_sel  <= nxt2;
                    started <= 1'b1;
                end
            end
        end
    end

    reg [63:0] rd_word;
    reg [1:0]  lane_r;
    always @(posedge pclk) begin
        case (rd_sel)
            2'd0:    rd_word <= buf0[px_nx[9:2]];
            2'd1:    rd_word <= buf1[px_nx[9:2]];
            default: rd_word <= buf2[px_nx[9:2]];
        endcase
        case (rd_sel)
            2'd0:    tag_sel <= tag0;
            2'd1:    tag_sel <= tag1;
            default: tag_sel <= tag2;
        endcase
        lane_r <= px_nx[1:0];
    end

    reg [15:0] fb_pixel;
    always @(*) begin
        case (lane_r)
            2'd0:    fb_pixel = rd_word[15:0];
            2'd1:    fb_pixel = rd_word[31:16];
            2'd2:    fb_pixel = rd_word[47:32];
            default: fb_pixel = rd_word[63:48];
        endcase
    end

    // ---- timing generator ----
    lcd_timing u_lcd (
        .pclk       (pclk),
        .pll_locked (pll_locked),
        .fb_en      (fb_en_p),
        .src_rgb    (started ? fb_pixel : 16'h0000),
        .px_nx      (px_nx),
        .vactive    (vactive),
        .vfirst     (vfirst),
        .de         (lcd_de),
        .hs         (lcd_hs),
        .vs         (lcd_vs),
        .rgb        (lcd_rgb),
        .bl         (lcd_bl)
    );

    assign lcd_pclk = pclk;

    // ---- debug bus ----
    // The display-domain taps are sticky and never change again once set, so
    // synchronising them bit-wise is safe: by the time the synchronised sticky flag
    // reads back high, the value next to it has been stable for several pclk
    // periods. Read the pair together, twice, and compare.
    (* ASYNC_REG = "TRUE" *) reg       bad_s0, bad_s1, stl_s0, stl_s1;
    (* ASYNC_REG = "TRUE" *) reg [8:0] brw_s0, brw_s1, got_s0, got_s1;

    always @(posedge aclk) begin
        bad_s0 <= tag_bad;        bad_s1 <= bad_s0;
        stl_s0 <= disp_stall;     stl_s1 <= stl_s0;
        brw_s0 <= bad_row;        brw_s1 <= brw_s0;
        got_s0 <= bad_got;        got_s1 <= got_s0;
    end

    //  dbg0  [8:0] reader line being fetched now   [17:9] tag held by buffer 0
    //        [26:18] buffer 1   [35:27]... see below
    //  dbg0: [8:0] rdr_line  [17:9] tag0  [26:18] tag1  [27] oor
    //        [28] base_misaligned  [31:29] spare
    //  dbg1: [8:0] tag2  [17:9] bad_row  [26:18] bad_got  [27] tag_bad
    //        [28] disp_stall  [31:29] spare
    assign dbg0 = { 3'b0, rdr_misaligned, rdr_oor, tag1, tag0, rdr_line };
    assign dbg1 = { 3'b0, stl_s1, bad_s1, got_s1, brw_s1, tag2 };
    // dbg2/dbg3: the line the reader last finished, and the sum of the pixels it got
    // for it. Both live in the AXI domain, so software reads a consistent pair.
    assign dbg2 = rdr_sig;
    assign dbg3 = { 23'b0, rdr_sig_line };
endmodule
