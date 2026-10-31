`timescale 1ns / 1ps
module rrc_filter_tb;
    localparam signed [15:0] TB_RRC_COEFF_00 = 16'sh0021;
    localparam signed [15:0] TB_RRC_COEFF_01 = 16'sh00da;
    localparam signed [15:0] TB_RRC_COEFF_02 = 16'sh009d;
    localparam signed [15:0] TB_RRC_COEFF_03 = 16'shff64;
    localparam signed [15:0] TB_RRC_COEFF_04 = 16'shfe5f;
    localparam signed [15:0] TB_RRC_COEFF_05 = 16'shff0e;
    localparam signed [15:0] TB_RRC_COEFF_06 = 16'sh01a4;
    localparam signed [15:0] TB_RRC_COEFF_07 = 16'sh042f;
    localparam signed [15:0] TB_RRC_COEFF_08 = 16'sh03a8;
    localparam signed [15:0] TB_RRC_COEFF_09 = 16'shfe96;
    localparam signed [15:0] TB_RRC_COEFF_10 = 16'shf759;
    localparam signed [15:0] TB_RRC_COEFF_11 = 16'shf3ed;
    localparam signed [15:0] TB_RRC_COEFF_12 = 16'shfa94;
    localparam signed [15:0] TB_RRC_COEFF_13 = 16'sh0d3e;
    localparam signed [15:0] TB_RRC_COEFF_14 = 16'sh26e7;
    localparam signed [15:0] TB_RRC_COEFF_15 = 16'sh3d43;
    localparam signed [15:0] TB_RRC_COEFF_16 = 16'sh4621;
    localparam signed [15:0] TB_RRC_COEFF_17 = 16'sh3d43;
    localparam signed [15:0] TB_RRC_COEFF_18 = 16'sh26e7;
    localparam signed [15:0] TB_RRC_COEFF_19 = 16'sh0d3e;
    localparam signed [15:0] TB_RRC_COEFF_20 = 16'shfa94;
    localparam signed [15:0] TB_RRC_COEFF_21 = 16'shf3ed;
    localparam signed [15:0] TB_RRC_COEFF_22 = 16'shf759;
    localparam signed [15:0] TB_RRC_COEFF_23 = 16'shfe96;
    localparam signed [15:0] TB_RRC_COEFF_24 = 16'sh03a8;
    localparam signed [15:0] TB_RRC_COEFF_25 = 16'sh042f;
    localparam signed [15:0] TB_RRC_COEFF_26 = 16'sh01a4;
    localparam signed [15:0] TB_RRC_COEFF_27 = 16'shff0e;
    localparam signed [15:0] TB_RRC_COEFF_28 = 16'shfe5f;
    localparam signed [15:0] TB_RRC_COEFF_29 = 16'shff64;
    localparam signed [15:0] TB_RRC_COEFF_30 = 16'sh009d;
    localparam signed [15:0] TB_RRC_COEFF_31 = 16'sh00da;
    localparam signed [15:0] TB_RRC_COEFF_32 = 16'sh0021;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg sample_tick = 1'b0;
    reg [1:0] phase = 2'd0;
    reg load_symbol = 1'b0;
    reg signed [15:0] new_i = 16'sd0;
    reg signed [15:0] new_q = 16'sd0;
    wire signed [15:0] i_sample;
    wire signed [15:0] q_sample;
    wire sample_valid;

    rrc_filter uut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .sample_tick(sample_tick), .phase(phase),
        .load_symbol(load_symbol), .rolloff(2'd0), .new_i(new_i), .new_q(new_q),
        .i_sample(i_sample), .q_sample(q_sample),
        .sample_valid(sample_valid)
    );

    always #5 clk = ~clk;

    integer failures = 0;
    integer phase_count[0:3];
    integer k;
    reg signed [15:0] held_i;
    reg signed [15:0] held_q;
    reg [31:0] gold_mem [0:95];
    reg signed [15:0] gold_i;
    reg signed [15:0] gold_q;

    function signed [15:0] round_acc;
        input signed [39:0] acc;
        reg signed [39:0] magnitude;
        reg signed [39:0] rounded;
        begin
            if (acc >= 0)
                rounded = (acc + 40'sd16384) >>> 15;
            else begin
                magnitude = -acc;
                rounded = -((magnitude + 40'sd16384) >>> 15);
            end
            if (rounded > 40'sd32767)
                round_acc = 16'sh7fff;
            else if (rounded < -40'sd32768)
                round_acc = 16'sh8000;
            else
                round_acc = rounded[15:0];
        end
    endfunction

    function signed [15:0] impulse_value;
        input integer index;
        reg signed [15:0] coeff;
        reg signed [39:0] acc;
        begin
            case (index)
                0: coeff = TB_RRC_COEFF_00;
                1: coeff = TB_RRC_COEFF_01;
                2: coeff = TB_RRC_COEFF_02;
                3: coeff = TB_RRC_COEFF_03;
                4: coeff = TB_RRC_COEFF_04;
                5: coeff = TB_RRC_COEFF_05;
                6: coeff = TB_RRC_COEFF_06;
                7: coeff = TB_RRC_COEFF_07;
                8: coeff = TB_RRC_COEFF_08;
                9: coeff = TB_RRC_COEFF_09;
                10: coeff = TB_RRC_COEFF_10;
                11: coeff = TB_RRC_COEFF_11;
                12: coeff = TB_RRC_COEFF_12;
                13: coeff = TB_RRC_COEFF_13;
                14: coeff = TB_RRC_COEFF_14;
                15: coeff = TB_RRC_COEFF_15;
                16: coeff = TB_RRC_COEFF_16;
                17: coeff = TB_RRC_COEFF_17;
                18: coeff = TB_RRC_COEFF_18;
                19: coeff = TB_RRC_COEFF_19;
                20: coeff = TB_RRC_COEFF_20;
                21: coeff = TB_RRC_COEFF_21;
                22: coeff = TB_RRC_COEFF_22;
                23: coeff = TB_RRC_COEFF_23;
                24: coeff = TB_RRC_COEFF_24;
                25: coeff = TB_RRC_COEFF_25;
                26: coeff = TB_RRC_COEFF_26;
                27: coeff = TB_RRC_COEFF_27;
                28: coeff = TB_RRC_COEFF_28;
                29: coeff = TB_RRC_COEFF_29;
                30: coeff = TB_RRC_COEFF_30;
                31: coeff = TB_RRC_COEFF_31;
                32: coeff = TB_RRC_COEFF_32;
                default: coeff = 16'sd0;
            endcase
            acc = $signed(16'sd32512) * coeff;
            impulse_value = round_acc(acc);
        end
    endfunction

    task sample_and_check;
        input [1:0] p;
        input load;
        input signed [15:0] vi;
        input signed [15:0] vq;
        input signed [15:0] expected_i;
        input signed [15:0] expected_q;
        input [255:0] label;
        begin
            @(negedge clk);
            phase = p;
            load_symbol = load;
            new_i = vi;
            new_q = vq;
            sample_tick = 1'b1;
            @(posedge clk);
            #1;
            phase_count[p] = phase_count[p] + 1;
            if (!sample_valid) begin
                $display("FAIL %0s sample_valid=0", label);
                failures = failures + 1;
            end
            if (i_sample !== expected_i || q_sample !== expected_q) begin
                $display("FAIL %0s i=%0d/%0d q=%0d/%0d", label,
                         i_sample, expected_i, q_sample, expected_q);
                failures = failures + 1;
            end
            @(negedge clk);
            sample_tick = 1'b0;
            load_symbol = 1'b0;
        end
    endtask

    task sample_no_check;
        input [1:0] p;
        input load;
        input signed [15:0] vi;
        input signed [15:0] vq;
        begin
            @(negedge clk);
            phase = p;
            load_symbol = load;
            new_i = vi;
            new_q = vq;
            sample_tick = 1'b1;
            @(posedge clk);
            #1;
            @(negedge clk);
            sample_tick = 1'b0;
            load_symbol = 1'b0;
        end
    endtask

    task check_idle_holds_output;
        begin
            held_i = i_sample;
            held_q = q_sample;
            @(posedge clk);
            #1;
            if (sample_valid !== 1'b0 || i_sample !== held_i || q_sample !== held_q) begin
                $display("FAIL sample_tick=0 must hold output and clear valid");
                failures = failures + 1;
            end
        end
    endtask

    task load_symbol_four_phases;
        input signed [15:0] value_i;
        input signed [15:0] value_q;
        begin
            sample_no_check(2'd0, 1'b1, value_i, value_q);
            sample_no_check(2'd1, 1'b0, 16'sd0, 16'sd0);
            sample_no_check(2'd2, 1'b0, 16'sd0, 16'sd0);
            sample_no_check(2'd3, 1'b0, 16'sd0, 16'sd0);
        end
    endtask

    task check_boundary;
        input signed [15:0] expected_value;
        input [255:0] label;
        begin
            sample_and_check(2'd0, 1'b0, 16'sd0, 16'sd0,
                             expected_value, 16'sd0, label);
        end
    endtask

    task check_round_acc_literals;
        begin
            if (round_acc(40'sd16384) !== 16'sd1) begin
                $display("FAIL round_acc(+16384) literal expected +1");
                failures = failures + 1;
            end
            if (round_acc(-40'sd16384) !== -16'sd1) begin
                $display("FAIL round_acc(-16384) literal expected -1");
                failures = failures + 1;
            end
            if (round_acc(40'sd32768) !== 16'sd1) begin
                $display("FAIL round_acc(+32768) literal expected +1");
                failures = failures + 1;
            end
            if (round_acc(-40'sd32768) !== -16'sd1) begin
                $display("FAIL round_acc(-32768) literal expected -1");
                failures = failures + 1;
            end
            if (round_acc(40'sd49152) !== 16'sd2) begin
                $display("FAIL round_acc(+49152) literal expected +2");
                failures = failures + 1;
            end
            if (round_acc(-40'sd49152) !== -16'sd2) begin
                $display("FAIL round_acc(-49152) literal expected -2");
                failures = failures + 1;
            end
        end
    endtask

    task check_nonzero_load_ignored;
        input [1:0] p;
        input signed [15:0] vi;
        input signed [15:0] vq;
        input [255:0] label;
        reg signed [15:0] phase0_i;
        reg signed [15:0] phase0_q;
        reg signed [15:0] phase_i;
        reg signed [15:0] phase_q;
        begin
            sample_no_check(2'd0, 1'b0, 16'sd0, 16'sd0);
            phase0_i = i_sample;
            phase0_q = q_sample;
            sample_no_check(p, 1'b0, 16'sd0, 16'sd0);
            phase_i = i_sample;
            phase_q = q_sample;
            sample_and_check(p, 1'b1, vi, vq, phase_i, phase_q, label);
            sample_and_check(2'd0, 1'b0, 16'sd0, 16'sd0,
                             phase0_i, phase0_q,
                             "phase load history unchanged");
        end
    endtask

    task vector_symbol;
        input integer symbol_index;
        output reg signed [15:0] value_i;
        output reg signed [15:0] value_q;
        begin
            case (symbol_index)
                0: begin value_i = -16'sd32512; value_q = -16'sd32512; end
                1: begin value_i = -16'sd32512; value_q = -16'sd32512; end
                2: begin value_i = -16'sd32512; value_q = -16'sd32512; end
                3: begin value_i = -16'sd32512; value_q = 16'sd32512; end
                4: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                5: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                6: begin value_i = 16'sd32512; value_q = -16'sd32512; end
                7: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                8: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                9: begin value_i = 16'sd32512; value_q = -16'sd32512; end
                10: begin value_i = -16'sd32512; value_q = 16'sd32512; end
                11: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                12: begin value_i = 16'sd32512; value_q = -16'sd32512; end
                13: begin value_i = 16'sd32512; value_q = -16'sd32512; end
                14: begin value_i = 16'sd32512; value_q = 16'sd32512; end
                15: begin value_i = 16'sd32512; value_q = -16'sd32512; end
                default: begin value_i = 16'sd0; value_q = 16'sd0; end
            endcase
        end
    endtask

    task check_gold_vector;
        integer index;
        reg signed [15:0] symbol_i;
        reg signed [15:0] symbol_q;
        reg [1:0] gold_phase;
        reg gold_load;
        begin
            $readmemh("src/tb/rrc_expected.mem", gold_mem);
            if (gold_mem[0] !== 32'hffdfffdf) begin
                $display("FAIL rrc_expected.mem first sample=%h", gold_mem[0]);
                failures = failures + 1;
            end
            if (gold_mem[95] !== 32'h00000000) begin
                $display("FAIL rrc_expected.mem final sample=%h", gold_mem[95]);
                failures = failures + 1;
            end
            rst_n = 1'b0;
            #1;
            rst_n = 1'b1;
            enable = 1'b1;
            for (index = 0; index < 96; index = index + 1) begin
                gold_phase = index % 4;
                gold_load = (index % 4) == 0;
                symbol_i = 16'sd0;
                symbol_q = 16'sd0;
                if (gold_load && index < 64)
                    vector_symbol(index / 4, symbol_i, symbol_q);
                gold_i = gold_mem[index][31:16];
                gold_q = gold_mem[index][15:0];
                sample_and_check(gold_phase, gold_load, symbol_i, symbol_q,
                                 gold_i, gold_q, "rrc_expected.mem sample");
                if (i_sample !== gold_i || q_sample !== gold_q)
                    $display("GOLD mismatch index=%0d phase=%0d load=%0d", index,
                             gold_phase, gold_load);
            end
        end
    endtask

    reg signed [15:0] boundary_x;
    reg signed [15:0] boundary_y;


    initial begin
        for (k = 0; k < 4; k = k + 1)
            phase_count[k] = 0;

        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        enable = 1'b1;
        check_round_acc_literals();

        // A single symbol impulse must expose every one of the 33 taps.
        sample_and_check(2'd0, 1'b1, 16'sd32512, -16'sd32512,
                         impulse_value(0), -impulse_value(0), "impulse tap 0");
        for (k = 1; k < 33; k = k + 1) begin
            sample_and_check(k[1:0], (k % 4) == 0, 16'sd0, 16'sd0,
                             impulse_value(k), -impulse_value(k), "impulse tap");
        end
        if (phase_count[0] != 9 || phase_count[1] != 8 ||
            phase_count[2] != 8 || phase_count[3] != 8) begin
            $display("FAIL phase tap schedule counts=%0d/%0d/%0d/%0d",
                     phase_count[0], phase_count[1], phase_count[2], phase_count[3]);
            failures = failures + 1;
        end
        check_idle_holds_output();

        // Loading a new symbol shifts the old symbol into hist[1].
        rst_n = 1'b0;
        #1;
        if (i_sample !== 16'sd0) begin
            $display("FAIL reset must clear i_sample");
            failures = failures + 1;
        end
        if (q_sample !== 16'sd0) begin
            $display("FAIL reset must clear q_sample");
            failures = failures + 1;
        end
        if (sample_valid !== 1'b0) begin
            $display("FAIL reset must clear sample_valid");
            failures = failures + 1;
        end
        rst_n = 1'b1;
        enable = 1'b1;
        @(posedge clk);
        sample_and_check(2'd0, 1'b1, 16'sd1000, 16'sd0,
                         round_acc(40'sd33000), 16'sd0, "history first symbol");
        sample_no_check(2'd1, 1'b0, 16'sd0, 16'sd0);
        sample_no_check(2'd2, 1'b0, 16'sd0, 16'sd0);
        sample_no_check(2'd3, 1'b0, 16'sd0, 16'sd0);
        sample_and_check(2'd0, 1'b1, 16'sd2000, 16'sd0,
                         round_acc(40'sd66000 - 40'sd417000), 16'sd0,
                         "history shifted symbol");

        // Nonzero symbols on phases 1/2/3 must not alter output or history.
        check_nonzero_load_ignored(2'd1, 16'sd1234, -16'sd2345,
                                   "phase 1 load ignored");
        check_nonzero_load_ignored(2'd2, -16'sd3456, 16'sd4567,
                                   "phase 2 load ignored");
        check_nonzero_load_ignored(2'd3, 16'sd5678, -16'sd6789,
                                   "phase 3 load ignored");

        // Cross-check the RTL sample sequence against the independent Python GOLD file.
        check_gold_vector();

        // Construct exact +/-16384, +/-32768 and +/-49152 MACs.
        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        boundary_y = 16'sd25;
        boundary_x = 16'sd1548;
        load_symbol_four_phases(boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(boundary_x, 16'sd0);
        check_boundary(16'sd1, "+16384 rounds away");
        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        load_symbol_four_phases(-boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(-boundary_x, 16'sd0);
        check_boundary(-16'sd1, "-16384 rounds away");

        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        boundary_y = 16'sd17;
        boundary_x = 16'sd1708;
        load_symbol_four_phases(boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(boundary_x, 16'sd0);
        check_boundary(16'sd1, "+32768 rounds");
        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        load_symbol_four_phases(-boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(-boundary_x, 16'sd0);
        check_boundary(-16'sd1, "-32768 rounds");

        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        boundary_y = 16'sd9;
        boundary_x = 16'sd1868;
        load_symbol_four_phases(boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(boundary_x, 16'sd0);
        check_boundary(16'sd2, "+49152 rounds");
        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        load_symbol_four_phases(-boundary_y, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(16'sd0, 16'sd0);
        load_symbol_four_phases(-boundary_x, 16'sd0);
        check_boundary(-16'sd2, "-49152 rounds");

        // Saturation must clamp an intentionally over-range polyphase sum.
        rst_n = 1'b0;
        #1;
        rst_n = 1'b1;
        enable = 1'b1;
        force uut.acc_i = 40'sd1073741824;
        sample_and_check(2'd0, 1'b0, 16'sd0, 16'sd0,
                         16'sd32767, 16'sd0, "positive saturation");
        release uut.acc_i;
        force uut.acc_i = -40'sd1073741824;
        sample_and_check(2'd0, 1'b0, 16'sd0, 16'sd0,
                         -16'sd32768, 16'sd0, "negative saturation");
        release uut.acc_i;

        // Disable clears valid, output and history; re-enable starts clean.
        enable = 1'b0;
        @(posedge clk);
        #1;
        if (i_sample !== 16'sd0) begin
            $display("FAIL disable must clear i_sample");
            failures = failures + 1;
        end
        if (q_sample !== 16'sd0) begin
            $display("FAIL disable must clear q_sample");
            failures = failures + 1;
        end
        if (sample_valid !== 1'b0) begin
            $display("FAIL disable must clear sample_valid");
            failures = failures + 1;
        end
        enable = 1'b1;
        sample_and_check(2'd0, 1'b1, 16'sd32512, 16'sd0,
                         impulse_value(0), 16'sd0, "re-enable seed history");
        if (failures == 0)
            $display("PASS rrc_filter");
        else
            $display("FAILURES=%0d", failures);
        $finish;
    end
endmodule
