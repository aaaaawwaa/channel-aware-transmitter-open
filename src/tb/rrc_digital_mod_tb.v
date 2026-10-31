`timescale 1ns / 1ps

module rrc_digital_mod_tb;
    localparam [2:0] MODE_BPSK = 3'd0;
    localparam [2:0] MODE_QPSK = 3'd1;
    localparam [2:0] MODE_QAM16 = 3'd3;
    localparam [2:0] MODE_QAM64 = 3'd4;
    localparam [31:0] STEP_10K = 32'd858993;
    localparam [31:0] STEP_100K = 32'd8589934;
    localparam [31:0] STEP_1M = 32'd85899345;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = MODE_QPSK;
    reg [6:0] prbs_seed = 7'h7f;
    reg [1:0] rolloff = 2'd0;
    reg [31:0] sym_phase_step = STEP_1M;
    reg source_user = 1'b0;
    reg user_loop = 1'b0;
    reg user_commit_epoch = 1'b0;
    reg [12:0] user_length = 13'd0;
    reg user_active_bank = 1'b0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    reg [31:0] bram_b_rdata = 32'd0;
    wire user_done, user_underflow;
    wire signed [15:0] i_sample;
    wire signed [15:0] q_sample;
    wire sample_valid;
    wire symbol_valid;

    rrc_digital_mod dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed),
        .rolloff(rolloff),
        .sym_phase_step(sym_phase_step),
        .source_user(source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank),
        .bram_b_addr(bram_b_addr), .bram_b_en(bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_done(user_done), .user_underflow(user_underflow),
        .i_sample(i_sample),
        .q_sample(q_sample), .sample_valid(sample_valid),
        .symbol_valid(symbol_valid)
    );

    always #5 clk = ~clk;

    integer failures = 0;
    integer cycle_count = 0;
    integer sample_count = 0;
    integer k;
    integer interval;
    integer min_interval;
    integer max_interval;
    integer interval_count;
    integer interval_sum;
    integer last_valid_cycle;
    integer seen_first;
    reg [31:0] gold_mem [0:95];
    reg signed [15:0] gold_i;
    reg signed [15:0] gold_q;
    reg signed [15:0] held_i;
    reg signed [15:0] held_q;
    reg [63:0] phase_step_ext;
    reg [63:0] average_lhs;
    reg [63:0] average_rhs;
    reg [63:0] average_diff;
    integer expected_floor;
    integer expected_ceil;
    integer expected_samples;

    always @(posedge clk) begin
        cycle_count = cycle_count + 1;
    end

    task fail;
        input [255:0] label;
        begin
            $display("FAIL %0s at cycle %0d", label, cycle_count);
            failures = failures + 1;
        end
    endtask

    task reset_dut;
        begin
            enable = 1'b0;
            mode = MODE_QPSK;
            rst_n = 1'b0;
            repeat (2) @(negedge clk);
            rst_n = 1'b1;
            @(negedge clk);
        end
    endtask

    task check_idle;
        begin
            held_i = i_sample;
            held_q = q_sample;
            @(posedge clk);
            #1;
            if (sample_valid !== 1'b0 || symbol_valid !== 1'b0)
                fail("idle valid must clear");
            if (i_sample !== held_i || q_sample !== held_q)
                fail("idle output must hold");
        end
    endtask

    task check_gold;
        input integer target_samples;
        input [31:0] step;
        integer guard;
        begin
            reset_dut();
            $readmemh("src/tb/rrc_expected.mem", gold_mem);
            if (gold_mem[0] !== 32'hffdfffdf)
                fail("GOLD first sample changed");
            if (gold_mem[63] !== 32'h42cce179)
                fail("GOLD sample 63 changed");
            sym_phase_step = step;
            mode = MODE_QPSK;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < target_samples && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (symbol_valid && !sample_valid)
                    fail("symbol_valid requires sample_valid");
                if (sample_valid) begin
                    if (sample_count < 64) begin
                        gold_i = gold_mem[sample_count][31:16];
                        gold_q = gold_mem[sample_count][15:0];
                        if (i_sample !== gold_i || q_sample !== gold_q) begin
                            $display("FAIL GOLD index=%0d got=%h/%h expected=%h/%h",
                                     sample_count, i_sample, q_sample, gold_i, gold_q);
                            failures = failures + 1;
                        end
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("symbol_valid phase0 position");
                    if (dut.poly_phase !== ((sample_count + 1) % 4))
                        fail("polyphase order");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != target_samples)
                fail("GOLD sample timeout");
            check_idle();
        end
    endtask

    task check_gold_rolloff;
        input [1:0] rf;
        input [8*128-1:0] memfile;
        integer guard;
        begin
            reset_dut();
            $readmemh(memfile, gold_mem);
            rolloff = rf;
            sym_phase_step = STEP_100K;
            mode = MODE_QPSK;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 64 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (symbol_valid && !sample_valid)
                    fail("rolloff symbol_valid requires sample_valid");
                if (sample_valid) begin
                    gold_i = gold_mem[sample_count][31:16];
                    gold_q = gold_mem[sample_count][15:0];
                    if (i_sample !== gold_i || q_sample !== gold_q) begin
                        $display("FAIL RRC rolloff=%0d GOLD index=%0d got=%h/%h expected=%h/%h",
                                 rf, sample_count, i_sample, q_sample, gold_i, gold_q);
                        failures = failures + 1;
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("rolloff symbol_valid phase0 position");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != 64)
                fail("rolloff GOLD sample timeout");
            rolloff = 2'd0;
            check_idle();
        end
    endtask

    task check_gold_qam16;
        integer guard;
        begin
            reset_dut();
            $readmemh("src/tb/rrc_expected_qam16.mem", gold_mem);
            sym_phase_step = STEP_100K;
            mode = MODE_QAM16;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 64 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    gold_i = gold_mem[sample_count][31:16];
                    gold_q = gold_mem[sample_count][15:0];
                    if (i_sample !== gold_i || q_sample !== gold_q) begin
                        $display("FAIL QAM16 GOLD index=%0d got=%h/%h expected=%h/%h",
                                 sample_count, i_sample, q_sample, gold_i, gold_q);
                        failures = failures + 1;
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("QAM16 symbol_valid phase0 position");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != 64)
                fail("QAM16 GOLD sample timeout");
            check_idle();
        end
    endtask

    task check_gold_qam64;
        integer guard;
        begin
            reset_dut();
            $readmemh("src/tb/rrc_expected_qam64.mem", gold_mem);
            sym_phase_step = STEP_100K;
            mode = MODE_QAM64;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 64 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    gold_i = gold_mem[sample_count][31:16];
                    gold_q = gold_mem[sample_count][15:0];
                    if (i_sample !== gold_i || q_sample !== gold_q) begin
                        $display("FAIL QAM64 GOLD index=%0d got=%h/%h expected=%h/%h",
                                 sample_count, i_sample, q_sample, gold_i, gold_q);
                        failures = failures + 1;
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("QAM64 symbol_valid phase0 position");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != 64)
                fail("QAM64 GOLD sample timeout");
            check_idle();
        end
    endtask

    task check_gold_seed;
        integer guard;
        begin
            prbs_seed = 7'd5;
            reset_dut();
            $readmemh("src/tb/rrc_expected_seed005_qpsk.mem", gold_mem);
            sym_phase_step = STEP_100K;
            mode = MODE_QPSK;
            rolloff = 2'd0;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 64 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    gold_i = gold_mem[sample_count][31:16];
                    gold_q = gold_mem[sample_count][15:0];
                    if (i_sample !== gold_i || q_sample !== gold_q) begin
                        $display("FAIL seed GOLD index=%0d got=%h/%h expected=%h/%h",
                                 sample_count, i_sample, q_sample, gold_i, gold_q);
                        failures = failures + 1;
                    end
                    if (symbol_valid !== (sample_count % 4 == 0))
                        fail("seed GOLD symbol_valid phase0 position");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count != 64)
                fail("seed GOLD sample timeout");
            prbs_seed = 7'h7f;
            check_idle();
        end
    endtask

    task check_mode_reset;
        integer guard;
        begin
            enable = 1'b0;
            @(posedge clk);
            #1;
            enable = 1'b1;
            mode = MODE_BPSK;
            sample_count = 0;
            guard = 0;
            while (sample_count < 1 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    if (i_sample === 16'sd0 || q_sample !== 16'sd0)
                        fail("BPSK RRC must emit I-only symbols");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count == 0)
                fail("BPSK RRC sample_valid timeout");
            enable = 1'b0;
            @(posedge clk);
            #1;
            mode = MODE_QAM16;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count < 1 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    if (i_sample === 16'sd0 || q_sample === 16'sd0)
                        fail("QAM16 RRC must emit non-zero I/Q symbols");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count == 0)
                fail("QAM16 RRC sample_valid timeout");
            // check_gold_qam16/64 已覆盖 gold_mem; 重新载入 QPSK 期望供 seed 恢复断言比较。
            $readmemh("src/tb/rrc_expected.mem", gold_mem);
            enable = 1'b0;
            @(posedge clk);
            #1;
            mode = MODE_QPSK;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count == 0 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    if (i_sample !== $signed(gold_mem[0][31:16]) ||
                        q_sample !== $signed(gold_mem[0][15:0]))
                        fail("mode re-enable must return to seed GOLD");
                    if (!symbol_valid)
                        fail("mode re-enable first sample symbol_valid");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count == 0)
                fail("mode re-enable sample_valid timeout");
            enable = 1'b0;
            @(posedge clk);
            #1;
            if (i_sample !== 16'sd0 || q_sample !== 16'sd0 ||
                sample_valid !== 1'b0 || symbol_valid !== 1'b0)
                fail("disable must clear RRC path");
            mode = MODE_QPSK;
            enable = 1'b1;
            sample_count = 0;
            guard = 0;
            while (sample_count == 0 && guard < 200000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    if (i_sample !== $signed(gold_mem[0][31:16]) ||
                        q_sample !== $signed(gold_mem[0][15:0]))
                        fail("disable re-enable must return to seed GOLD");
                    if (!symbol_valid)
                        fail("disable re-enable first sample symbol_valid");
                    sample_count = sample_count + 1;
                end
            end
            if (sample_count == 0)
                fail("disable re-enable sample_valid timeout");
        end
    endtask

    task check_rate;
        input [31:0] step;
        input integer label_rate;
        integer guard;
        begin
            reset_dut();
            sym_phase_step = step;
            mode = MODE_QPSK;
            enable = 1'b1;
            phase_step_ext = {32'd0, step} << 2;
            expected_floor = 64'h100000000 / phase_step_ext;
            expected_ceil = (64'h100000000 + phase_step_ext - 1) / phase_step_ext;
            expected_samples = 128;
            interval_count = 0;
            interval_sum = 0;
            min_interval = 1000000000;
            max_interval = 0;
            seen_first = 0;
            last_valid_cycle = 0;
            guard = 0;
            while (interval_count < expected_samples && guard < 300000) begin
                @(posedge clk);
                #1;
                guard = guard + 1;
                if (sample_valid) begin
                    if (seen_first != 0) begin
                        interval = cycle_count - last_valid_cycle;
                        if (interval < expected_floor || interval > expected_ceil) begin
                            $display("FAIL rate=%0d interval=%0d expected=%0d..%0d",
                                     label_rate, interval, expected_floor, expected_ceil);
                            failures = failures + 1;
                        end
                        if (interval < min_interval)
                            min_interval = interval;
                        if (interval > max_interval)
                            max_interval = interval;
                        interval_sum = interval_sum + interval;
                        interval_count = interval_count + 1;
                    end
                    last_valid_cycle = cycle_count;
                    seen_first = 1;
                end
            end
            if (interval_count != expected_samples)
                fail("rate statistics timeout");
            average_lhs = interval_sum * phase_step_ext;
            average_rhs = 64'h100000000 * interval_count;
            if (average_lhs >= average_rhs)
                average_diff = average_lhs - average_rhs;
            else
                average_diff = average_rhs - average_lhs;
            if (average_diff > phase_step_ext)
                fail("rate long-term average error exceeds one cycle");
            $display("RATE %0dHz intervals min=%0d max=%0d average=%0f expected=%0d..%0d",
                     label_rate, min_interval, max_interval,
                     interval_sum * 1.0 / interval_count,
                     expected_floor, expected_ceil);
        end
    endtask

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        check_gold(64, STEP_1M);
        check_gold_rolloff(2'd1, "src/tb/rrc_expected_qpsk_a020.mem");
        check_gold_rolloff(2'd2, "src/tb/rrc_expected_qpsk_a050.mem");
        check_gold_qam16();
        check_gold_qam64();
        check_gold_seed();
        check_mode_reset();
        check_rate(STEP_10K, 10000);
        check_rate(STEP_100K, 100000);
        check_rate(STEP_1M, 1000000);
        if (failures == 0)
            $display("PASS rrc_digital_mod");
        else
            $display("FAILURES=%0d", failures);
        $finish;
    end
endmodule
