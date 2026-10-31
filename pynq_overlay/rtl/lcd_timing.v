`timescale 1ns / 1ps
// 5" 800x480 TFT timing controller with a vertical colour-bar pattern.
//
// The parameters are the vendor's ch24_ac820_tft800x480_ctrl values, which is the
// panel this carrier ships with: 1056x525 total at 33.3 MHz, so ~59.9 Hz.
// The 525 counts the 480 visible lines *plus* an 8-line border above and below them
// (V_Top_Border / V_Bottom_Border in the vendor's disp_parameter_cfg.v), on top of
// VBP 25 + VSYNC 2 + VFP 2. Dropping those two borders gives 509 lines = 62.0 Hz;
// the panel tolerates it, but it is not the specified mode.
// HSYNC and VSYNC are ACTIVE LOW -- the vendor registers them as
// `hs <= (hcount > H_SYNC-1)`, i.e. low for the first H_SYNC pixels of a line.
//
// Eight bars rather than the vendor's 4x2 quadrant blocks: the bars drive R, G and
// B independently and in a known order, so a stuck, swapped or shifted colour bit
// shows up as the wrong bar instead of as a plausible-looking block.
//
// `pll_locked` comes from the MMCM and is asynchronous to `pclk`, so it is
// synchronised and used as the reset release: the counters then always start from
// a known state instead of whatever the flops powered up with.
//
// Two sources feed the pixel output:
//   fb_en = 0 -> the colour bars below (the P2 behaviour, bit for bit)
//   fb_en = 1 -> `src_rgb`, a pixel supplied by the caller from the DDR framebuffer
//
// `px_nx` is the *next* cycle's visible-column index. The framebuffer path needs
// it rather than the current `x`: a block RAM answers one cycle after its address,
// so presenting `px_nx` now puts the wanted pixel on `src_rgb` at exactly the edge
// where this module registers `de` for that pixel. Registering `de`, `hs`, `vs` and
// `rgb` together is what keeps them aligned, so the extra lookahead stays hidden.
module lcd_timing #(
    parameter H_ACTIVE = 800,
    parameter H_FP     = 40,
    parameter H_SYNC   = 128,
    parameter H_BP     = 88,
    parameter V_ACTIVE = 480,
    parameter V_TOP_BORDER    = 8,
    parameter V_FP     = 2,
    parameter V_SYNC   = 2,
    parameter V_BP     = 25,
    parameter V_BOTTOM_BORDER = 8
) (
    input  wire        pclk,
    input  wire        pll_locked,
    input  wire        fb_en,
    input  wire [15:0] src_rgb,
    output wire [10:0] px_nx,
    output wire        vactive,    // this line is a visible one
    output wire        vfirst,     // this line is the first visible line of a frame
    output reg         de,
    output reg         hs,
    output reg         vs,
    output reg  [15:0] rgb,
    output wire        bl
);
    localparam [10:0] H_TOTAL = H_ACTIVE + H_FP + H_SYNC + H_BP;   // 1056
    localparam [10:0] H_START = H_SYNC + H_BP;                     // 216
    localparam [10:0] H_END   = H_START + H_ACTIVE;
    localparam [10:0] H_BAR   = H_ACTIVE / 8;                      // 100
    // The vendor's V_Total_Time is 525, and it *includes* an 8-line border above and
    // below the picture. Leaving them out gives 509 lines, i.e. 62.0 Hz instead of the
    // specified 59.9 Hz -- the panel tolerates it, but the comment and the code must
    // agree, and 525 is what the vendor's disp_parameter_cfg.v actually says.
    localparam [9:0]  V_TOTAL = V_ACTIVE + V_TOP_BORDER + V_FP + V_SYNC + V_BP
                              + V_BOTTOM_BORDER;                   // 525
    // The borders are part of blanking, so the visible window starts *after* the top
    // border (V_BP is the last blank line, the border lines follow it) and is still
    // exactly V_ACTIVE lines tall -- the picture does not move or stretch.
    localparam [9:0]  V_START = V_SYNC + V_BP + V_TOP_BORDER;      // 35
    localparam [9:0]  V_END   = V_START + V_ACTIVE;                // 515

    (* ASYNC_REG = "TRUE" *) reg lock_s0, lock_s1;
    always @(posedge pclk) begin
        lock_s0 <= pll_locked;
        lock_s1 <= lock_s0;
    end
    wire rst_n = lock_s1;

    reg [10:0] hcnt;
    reg [9:0]  vcnt;
    wire h_last = (hcnt == H_TOTAL - 11'd1);
    wire v_last = (vcnt == V_TOTAL - 10'd1);

    always @(posedge pclk or negedge rst_n)
        if (!rst_n)      hcnt <= 11'd0;
        else if (h_last) hcnt <= 11'd0;
        else             hcnt <= hcnt + 11'd1;

    always @(posedge pclk or negedge rst_n)
        if (!rst_n)       vcnt <= 10'd0;
        else if (h_last)  vcnt <= v_last ? 10'd0 : vcnt + 10'd1;

    // ---- colour bars ----
    wire [10:0] x   = hcnt - H_START;

    // One column ahead, for the framebuffer path's block-RAM address and lane
    // select. Wraps harmlessly into blanking, where `de` is low anyway.
    assign px_nx = hcnt + 11'd1 - H_START;

    // The framebuffer path consumes one line buffer per *visible* line, so it has
    // to know which line boundaries those are. Switching on every line boundary
    // would consume 525 lines per frame while the reader only has 480 to give,
    // and the extra 45 (during blanking) walk the row index every frame -- the
    // image rolls.
    assign vactive = (vcnt >= V_START) && (vcnt < V_END);
    assign vfirst  = (vcnt == V_START);

    wire [2:0]  bar = (x >= H_BAR * 7) ? 3'd7 :
                      (x >= H_BAR * 6) ? 3'd6 :
                      (x >= H_BAR * 5) ? 3'd5 :
                      (x >= H_BAR * 4) ? 3'd4 :
                      (x >= H_BAR * 3) ? 3'd3 :
                      (x >= H_BAR * 2) ? 3'd2 :
                      (x >= H_BAR * 1) ? 3'd1 : 3'd0;

    reg [15:0] bar_rgb;
    always @(*) begin
        case (bar)
            3'd0:    bar_rgb = 16'hFFFF;   // white
            3'd1:    bar_rgb = 16'hFFE0;   // yellow
            3'd2:    bar_rgb = 16'h07FF;   // cyan
            3'd3:    bar_rgb = 16'h07E0;   // green
            3'd4:    bar_rgb = 16'hF81F;   // magenta
            3'd5:    bar_rgb = 16'hF800;   // red
            3'd6:    bar_rgb = 16'h001F;   // blue
            default: bar_rgb = 16'h0000;   // black
        endcase
    end

    // Outputs are registered on the same edge as the decode that produces them, so
    // de, hs, vs and rgb stay aligned with each other (the whole group is one cycle
    // behind the counters, which is harmless).
    always @(posedge pclk or negedge rst_n)
        if (!rst_n) begin
            de  <= 1'b0;
            hs  <= 1'b1;
            vs  <= 1'b1;
            rgb <= 16'h0000;
        end else begin
            de  <= (hcnt >= H_START) && (hcnt < H_END) &&
                   (vcnt >= V_START) && (vcnt < V_END);
            hs  <= (hcnt >= H_SYNC);
            vs  <= (vcnt >= V_SYNC);
            rgb <= fb_en ? src_rgb : bar_rgb;
        end

    // U19 is a plain active-high backlight enable on this carrier (the vendor's
    // top level ties it to 1'b1 as well).
    assign bl = 1'b1;
endmodule
