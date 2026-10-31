`timescale 1ns / 1ps
module iq_digital_mod_symrate_tb;
    localparam [31:0] STEP_10K = 32'd858993;
    localparam [31:0] STEP_1M = 32'd85899345;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = 3'd1;
    reg [6:0] prbs_seed = 7'h7f;
    reg [31:0] sym_phase_step = STEP_10K;
    reg source_user = 1'b0;
    reg user_loop = 1'b0;
    reg user_commit_epoch = 1'b0;
    reg [12:0] user_length = 13'd0;
    reg user_active_bank = 1'b0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    reg [31:0] bram_b_rdata = 32'd0;
    wire user_done, user_underflow;
    wire signed [7:0] i_sample;
    wire signed [7:0] q_sample;
    wire symbol_valid;

    iq_digital_mod uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed),
        .sym_phase_step(sym_phase_step),
        .source_user(source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank),
        .bram_b_addr(bram_b_addr), .bram_b_en(bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_done(user_done), .user_underflow(user_underflow),
        .i_sample(i_sample), .q_sample(q_sample), .symbol_valid(symbol_valid)
    );

    always #10 clk = ~clk;

    integer errors = 0;
    integer ticks = 0;
    integer previous_tick = -1;
    integer interval;
    integer boundaries = 0;

    always @(posedge clk) begin
        #1;
        ticks = ticks + 1;
        if (symbol_valid) begin
            if (previous_tick >= 0) begin
                interval = ticks - previous_tick;
                if (interval != 5000 && interval != 5001) begin
                    $display("FAIL 10k interval=%0d", interval);
                    errors = errors + 1;
                end
            end
            previous_tick = ticks;
            boundaries = boundaries + 1;
        end
    end

    initial begin
        repeat (2) @(posedge clk);
        rst_n = 1'b1;
        enable = 1'b1;
        repeat (10020) @(posedge clk);
        if (boundaries < 3) begin
            $display("FAIL expected three 10k boundaries, got %0d", boundaries);
            errors = errors + 1;
        end
        if (errors == 0) $display("PASS iq_digital_mod_symrate");
        else $fatal(1, "iq_digital_mod_symrate failures=%0d", errors);
        $finish;
    end
endmodule
