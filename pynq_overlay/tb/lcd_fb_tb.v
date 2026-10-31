`timescale 1ns / 1ps
// Framebuffer path acceptance: a behavioural AXI4 read slave stands in for DDR and
// serves a synthetic frame, and the testbench checks that the pixels the panel is
// fed are exactly that frame.
//
// The slave answers by *computing* the pixel from the address rather than storing a
// frame, so a mis-addressed burst, a wrong stride, a lane mix-up or an off-by-one
// line all show up as mismatched pixels instead of as plausible garbage.
//
// Two clocks on purpose: the reader runs on aclk (75 MHz), the display on pclk
// (33.33 MHz), which is what the real design does -- the line buffers are the only
// place the two domains meet, so this is where a handshake mistake would surface.
module lcd_fb_tb;
    localparam PCLK_HALF = 15;      // 33.33 MHz
    localparam ACLK_HALF = 7;       // ~71 MHz
    localparam BASE      = 32'h1000_0000;

    localparam H_ACTIVE = 800, V_ACTIVE = 480;
    localparam LINE_BEATS = 200;

    reg aclk = 1'b0, pclk = 1'b0;
    always #ACLK_HALF aclk = ~aclk;
    always #PCLK_HALF pclk = ~pclk;

    reg areset = 1'b1;
    reg locked = 1'b0;
    reg fb_en  = 1'b0;

    wire        lcd_pclk, lcd_de, lcd_hs, lcd_vs, lcd_bl;
    wire [15:0] lcd_rgb;

    // self-check taps coming back out of the DUT
    wire [31:0] dbg0, dbg1, dbg2, dbg3;
    wire        dbg_oor        = dbg0[27];
    wire        dbg_misaligned = dbg0[28];
    wire        dbg_tag_bad    = dbg1[27];
    wire        dbg_stall      = dbg1[28];
    wire [31:0] dbg_sig        = dbg2;
    wire [8:0]  dbg_sig_line   = dbg3[8:0];

    wire [31:0] m_araddr;
    wire [7:0]  m_arlen;
    wire [2:0]  m_arsize;
    wire [1:0]  m_arburst;
    wire [5:0]  m_arid;
    wire        m_arvalid;
    wire        m_arready;
    wire [63:0] m_rdata;
    wire [5:0]  m_rid;
    wire [1:0]  m_rresp;
    wire        m_rlast;
    wire        m_rvalid;
    wire        m_rready;

    lcd_fb #(
        .AXI_ADDR_WIDTH (32),
        .LINE_BEATS     (LINE_BEATS),
        .LINES          (V_ACTIVE)
    ) dut (
        .pclk       (pclk),
        .pll_locked (locked),
        .fb_en      (fb_en),
        .lcd_pclk   (lcd_pclk),
        .lcd_de     (lcd_de),
        .lcd_hs     (lcd_hs),
        .lcd_vs     (lcd_vs),
        .lcd_rgb    (lcd_rgb),
        .lcd_bl     (lcd_bl),
        .aclk       (aclk),
        .areset     (areset),
        .fb_base    (BASE),
        .dbg0       (dbg0),
        .dbg1       (dbg1),
        .dbg2       (dbg2),
        .dbg3       (dbg3),
        .m_axi_araddr (m_araddr),
        .m_axi_arlen  (m_arlen),
        .m_axi_arsize (m_arsize),
        .m_axi_arburst(m_arburst),
        .m_axi_arid   (m_arid),
        .m_axi_arvalid(m_arvalid),
        .m_axi_arready(m_arready),
        .m_axi_rdata  (m_rdata),
        .m_axi_rid    (m_rid),
        .m_axi_rresp  (m_rresp),
        .m_axi_rlast  (m_rlast),
        .m_axi_rvalid (m_rvalid),
        .m_axi_rready (m_rready)
    );

    // ---------------- behavioural DDR ----------------
    // frame(line, col) is deliberately asymmetric in both arguments so a swapped
    // line/column or a shifted lane cannot accidentally match.
    function [15:0] frame_px;
        input integer ln;
        input integer col;
        begin
            frame_px = ((ln[7:0] << 8) | (col[7:0] & 8'hFF)) ^ 16'h5A5A;
        end
    endfunction

    function [63:0] frame_word;
        input integer ln;
        input integer w;
        begin
            frame_word = { frame_px(ln, w*4 + 3), frame_px(ln, w*4 + 2),
                           frame_px(ln, w*4 + 1), frame_px(ln, w*4 + 0) };
        end
    endfunction

    reg [31:0] s_araddr;
    reg [7:0]  s_len;
    reg [8:0]  s_cnt;
    reg        s_active;
    integer    serr = 0;

    assign m_arready = ~s_active;

    always @(posedge aclk) begin
        if (areset) begin
            s_active <= 1'b0; s_cnt <= 9'd0; s_len <= 8'd0; s_araddr <= 32'd0;
        end else if (!s_active) begin
            if (m_arvalid) begin
                s_araddr <= m_araddr;
                s_len    <= m_arlen;
                s_cnt    <= 9'd0;
                s_active <= 1'b1;
                // a legal burst: INCR, 8-byte beats, and none straddling 4 KB
                if (m_arburst !== 2'b01 || m_arsize !== 3'd3) begin
                    $display("FAIL burst attr addr=%h burst=%b size=%d", m_araddr, m_arburst, m_arsize);
                    serr = serr + 1;
                end
                if (((m_araddr & 32'hFFF) + (m_arlen + 1) * 8) > 32'h1000) begin
                    $display("FAIL 4KB crossing addr=%h len=%d", m_araddr, m_arlen);
                    serr = serr + 1;
                end
            end
        end else if (m_rvalid && m_rready) begin
            if (s_cnt == {1'b0, s_len}) s_active <= 1'b0;
            s_cnt <= s_cnt + 9'd1;
        end
    end

    assign m_rvalid = s_active;
    assign m_rlast  = s_active && (s_cnt[7:0] == s_len);
    assign m_rid    = 6'd0;
    assign m_rresp  = 2'b00;
    wire [31:0] beat_addr = s_araddr + {s_cnt[7:0], 3'b000};
    wire [8:0]  beat_ln   = (beat_addr - BASE) >> 11;      // 2048-byte stride
    wire [7:0]  beat_w    = beat_addr[10:3];
    assign m_rdata = frame_word(beat_ln, beat_w);

    // ---------------- display-side capture ----------------
    integer errors = 0, de_run_bad = 0;
    integer de_x = 0, lines_seen = 0, de_lines = 0;
    integer frame_no = 0;
    reg     counting = 1'b0;
    localparam SKIP_FRAMES = 4;       // let the ring prime and the frame align
    localparam MEAS_FRAMES = 2;

    reg vs_d = 1'b1;
    always @(posedge pclk) begin
        vs_d <= lcd_vs;
        if (counting) begin
            if (lcd_de) begin
                if (lcd_rgb !== frame_px(lines_seen, de_x)) begin
                    if (errors < 6)
                        $display("FAIL px line=%0d x=%0d got=%h want=%h",
                                 lines_seen, de_x, lcd_rgb, frame_px(lines_seen, de_x));
                    errors = errors + 1;
                end
                de_x = de_x + 1;
            end else if (de_x != 0) begin
                if (de_x != H_ACTIVE) de_run_bad = de_run_bad + 1;
                de_lines = de_lines + 1;
                lines_seen = lines_seen + 1;
                de_x = 0;
            end
        end
        if (vs_d && !lcd_vs) begin          // VS falling = frame is starting
            // Must key off the falling edge: VS is active low and stays low for the
            // first two lines, so a rising edge lands on line 2 and every expected
            // pixel would be read from the wrong row.
            frame_no = frame_no + 1;
            if (frame_no == SKIP_FRAMES + 1) begin
                counting = 1'b1;
                lines_seen = 0; de_lines = 0; de_x = 0;
            end else if (frame_no == SKIP_FRAMES + MEAS_FRAMES + 1) begin
                counting = 1'b0;
            end else if (counting) begin
                // rows restart with each frame; the DUT's ring does too
                lines_seen = 0;
                de_x = 0;
            end
        end
    end

    integer bl_low = 0;
    always @(posedge pclk) if (!lcd_bl) bl_low = bl_low + 1;

    // The reader's per-line data signature must equal the sum of the pixels the DDR
    // model actually served for that line. Checked on two lines, once per frame.
    reg [8:0]  sig_chk_line = 9'h1FF;
    integer    sig_err = 0, c;
    reg [31:0] exp_sig;
    always @(posedge aclk) begin
        if (dbg_sig_line !== sig_chk_line) begin
            sig_chk_line <= dbg_sig_line;
            if (dbg_sig_line == 9'd100 || dbg_sig_line == 9'd479) begin
                exp_sig = 32'd0;
                for (c = 0; c < H_ACTIVE; c = c + 1)
                    exp_sig = exp_sig + frame_px(dbg_sig_line, c);
                if (dbg_sig !== exp_sig) begin
                    sig_err = sig_err + 1;
                    if (sig_err < 4)
                        $display("FAIL line signature line=%0d got=%h want=%h",
                                 dbg_sig_line, dbg_sig, exp_sig);
                end
            end
        end
    end

    // ---------------- stimulus ----------------
    integer checks_failed = 0;
    task check;
        input [255:0] name;
        input integer got;
        input integer want;
        begin
            if (got === want) $display("PASS %0s", name);
            else begin
                checks_failed = checks_failed + 1;
                $display("FAIL %0s got=%0d want=%0d", name, got, want);
            end
        end
    endtask

    initial begin
        $display("== lcd_fb_tb: DDR framebuffer -> 800x480 ==");
        repeat (8) @(posedge pclk);
        locked = 1'b1;
        repeat (4) @(posedge aclk);
        areset = 1'b0;
        repeat (20) @(posedge pclk);
        fb_en = 1'b1;

        // SKIP_FRAMES + MEAS_FRAMES frames, then report
        while (frame_no <= SKIP_FRAMES + MEAS_FRAMES + 1) @(posedge pclk);
        repeat (100) @(posedge pclk);

        $display("  [dbg] de_lines=%0d frames=%0d", de_lines, frame_no);

        check("axi_burst_errors",   serr,        0);
        check("pixel_errors",       errors,      0);
        check("de_run_length_bad",  de_run_bad,  0);
        check("de_lines_total", de_lines, V_ACTIVE * MEAS_FRAMES);
        check("backlight_low_pclk", bl_low,      0);
        // The DUT's own self-check: every visible row must carry the line tag it is
        // supposed to, and the display must never have run out of buffers. These are
        // the same taps the board build exposes, so this pins their meaning down.
        check("selfcheck_tag_bad",  dbg_tag_bad, 0);
        check("selfcheck_stall",    dbg_stall,   0);
        check("selfcheck_reader_oor", dbg_oor,   0);
        check("selfcheck_base_align", dbg_misaligned, 0);
        check("selfcheck_line_signature", sig_err, 0);

        if (checks_failed == 0 && serr == 0 && errors == 0)
            $display("PASS lcd_fb_tb");
        else
            $display("FAIL lcd_fb_tb (checks=%0d serr=%0d px=%0d)", checks_failed, serr, errors);
        $finish;
    end

    initial begin
        #200000000;                     // 200 ms: >6 frames at 16.7 ms
        $display("FAIL lcd_fb_tb timeout (frames=%0d de_lines=%0d)", frame_no, de_lines);
        $finish;
    end
endmodule
