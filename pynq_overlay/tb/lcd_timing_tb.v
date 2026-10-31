`timescale 1ns / 1ps
// Timing acceptance for lcd_timing: capture exactly one frame (VS falling edge to
// VS falling edge) and check its geometry against the panel's numbers, including
// that every visible pixel carries the colour bar its x position calls for.
//
// The frame is measured as a whole rather than by re-implementing the counter
// chain, so the checks are independent of how the DUT produces the timing.
module lcd_timing_tb;
    localparam PCLK_PERIOD = 30;   // 33.333 MHz

    localparam H_ACTIVE = 800, H_FP = 40, H_SYNC = 128, H_BP = 88;
    localparam V_ACTIVE = 480, V_FP = 2,  V_SYNC = 2,  V_BP = 25;
    localparam H_TOTAL  = H_ACTIVE + H_FP + H_SYNC + H_BP;   // 1056
    // ⚠ V_TOTAL is the vendor's *literal* 525, not recomputed from the porch values.
    //   The old version of this file wrote the same formula as the DUT
    //   (V_ACTIVE + V_FP + V_SYNC + V_BP) with the same "// 525" comment, so both sides
    //   were wrong together and the check passed while the panel ran at 62.0 Hz instead
    //   of 59.9 Hz. The vendor's disp_parameter_cfg.v (ch24, Resolution_800x480) says
    //   V_Total_Time 525 and additionally defines V_Top_Border 8 / V_Bottom_Border 8.
    //   Pinning the number here means the DUT cannot drift from the specification
    //   without this failing.
    localparam V_TOTAL  = 525;
    localparam V_BORDER = 8;                                 // each, top and bottom
    // First visible line, by the *vertical counter*: VSYNC 2 + VBP 25 + top border 8.
    localparam V_START  = V_SYNC + V_BP + V_BORDER;          // 35

    reg  pclk = 1'b0;
    always #(PCLK_PERIOD / 2) pclk = ~pclk;

    reg  locked = 1'b0;
    wire de, hs, vs, bl;
    wire [15:0] rgb;

    lcd_timing dut (
        .pclk       (pclk),
        .pll_locked (locked),
        .fb_en      (1'b0),        // colour-bar source: the P2 path, bit for bit
        .src_rgb    (16'h0000),
        .px_nx      (),
        .vactive    (),
        .vfirst     (),
        .de         (de),
        .hs         (hs),
        .vs         (vs),
        .rgb        (rgb),
        .bl         (bl)
    );

    integer errors = 0;

    task check;
        input [255:0] name;
        input integer got;
        input integer want;
        begin
            if (got === want) begin
                $display("PASS %0s", name);
            end else begin
                errors = errors + 1;
                $display("FAIL %0s got=%0d want=%0d", name, got, want);
            end
        end
    endtask

    function [15:0] bar_of;
        input integer px;
        begin
            case (px / 100)
                0:       bar_of = 16'hFFFF;   // white
                1:       bar_of = 16'hFFE0;   // yellow
                2:       bar_of = 16'h07FF;   // cyan
                3:       bar_of = 16'h07E0;   // green
                4:       bar_of = 16'hF81F;   // magenta
                5:       bar_of = 16'hF800;   // red
                6:       bar_of = 16'h001F;   // blue
                default: bar_of = 16'h0000;   // black
            endcase
        end
    endfunction

    reg     in_frame    = 1'b0;
    reg     frame_ready = 1'b0;
    reg     hs_p = 1'b1, vs_p = 1'b1;
    integer fp, fhs_low, fvs_low, fde, flines, fde_runs, fde_run_bad, frgb_err, fx;
    integer bl_low = 0;
    // Which line index the first and last visible run started on. Counting the runs
    // alone cannot tell "8 blank lines before the picture" from "8 after it" -- both
    // give 480 runs. These two pin the borders' position.
    integer first_de_line = -1;
    integer last_de_line  = -1;

    always @(posedge pclk) begin
        hs_p <= hs;
        vs_p <= vs;

        if (!bl) bl_low = bl_low + 1;

        if (vs_p && !vs) begin                 // VS falling edge = frame boundary
            if (!in_frame) begin
                in_frame = 1'b1;
                fp = 0; fhs_low = 0; fvs_low = 0; fde = 0;
                flines = 0; fde_runs = 0; fde_run_bad = 0; frgb_err = 0; fx = 0;
                first_de_line = -1; last_de_line = -1;
            end else begin
                in_frame = 1'b0;
                frame_ready = 1'b1;
            end
        end

        if (in_frame) begin
            fp = fp + 1;
            if (!hs) fhs_low = fhs_low + 1;
            if (!vs) fvs_low = fvs_low + 1;

            if (de) begin
                if (first_de_line < 0) first_de_line = flines;
                last_de_line = flines;
                fde = fde + 1;
                if (rgb !== bar_of(fx)) frgb_err = frgb_err + 1;
                fx = fx + 1;
            end else if (fx != 0) begin         // a completed visible run
                fde_runs = fde_runs + 1;
                if (fx !== H_ACTIVE) fde_run_bad = fde_run_bad + 1;
                fx = 0;
            end

            if (hs_p && !hs) flines = flines + 1;
        end
    end

    initial begin
        $display("== lcd_timing_tb: 800x480 RGB565 colour bars ==");
        repeat (10) @(posedge pclk);
        locked = 1'b1;                          // MMCM locks

        wait (frame_ready == 1'b1);             // one full frame captured
        repeat (4) @(posedge pclk);

        check("frame_pclk",         fp,          H_TOTAL * V_TOTAL);    // 554400
        check("frame_lines",        flines,      V_TOTAL);              // 525
        check("frame_hs_low_pclk",  fhs_low,     V_TOTAL * H_SYNC);     // 67200
        check("frame_vs_low_pclk",  fvs_low,     V_SYNC * H_TOTAL);     // 2112
        check("frame_de_pclk",      fde,         H_ACTIVE * V_ACTIVE);  // 384000
        check("frame_de_lines",     fde_runs,    V_ACTIVE);             // 480
        // The picture sits after VSYNC(2) + VBP(25) + top border(8) = vertical counter 35,
        // and the bottom border(8) + VFP(2) follow the last visible line. If the borders
        // were dropped the frame would be 16 lines short and start at counter 27.
        // `flines` counts HS edges within the frame, so it runs one ahead of the vertical
        // counter: add 1. Measuring both ends this way pins the borders' *position*,
        // which the line count alone cannot ("8 blank lines before" and "8 after" both
        // still give 480 visible lines).
        check("first_de_line",      first_de_line, V_START + 1);           // 36
        check("last_de_line",       last_de_line,  V_START + V_ACTIVE);    // 515
        check("de_run_length_bad",  fde_run_bad, 0);
        check("rgb_bar_errors",     frgb_err,    0);
        check("backlight_low_pclk", bl_low,      0);

        if (errors == 0) $display("PASS lcd_timing_tb");
        else             $display("FAIL lcd_timing_tb (%0d errors)", errors);
        $finish;
    end

    initial begin
        #100000000;                             // 100 ms watchdog
        $display("FAIL lcd_timing_tb timeout");
        $finish;
    end
endmodule
