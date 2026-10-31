`timescale 1ns / 1ps
module iq_digital_mod_stream_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg enable = 0;
    reg [2:0] mode = 3'd0;
    reg [6:0] prbs_seed = 7'h7f;
    reg [31:0] sym_phase_step = 32'h4000_0000;
    reg source_user = 1;
    reg user_loop = 0;
    reg user_commit_epoch = 0;
    reg [12:0] user_length = 0;
    reg user_active_bank = 0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    reg [31:0] bram_b_rdata = 0;
    reg user_stream_enable = 1;
    reg [31:0] user_stream_length = 8;
    reg [31:0] user_stream_tdata = 0;
    reg [3:0] user_stream_tkeep = 0;
    reg user_stream_tvalid = 0;
    wire user_stream_tready;
    reg user_stream_tlast = 0;
    wire user_done, user_underflow, user_ready;
    wire signed [7:0] i_sample, q_sample;
    wire symbol_valid;
    wire [7:0] bram_ref_addr;
    wire bram_ref_en;
    reg [31:0] bram_ref_rdata = 32'd0;
    wire signed [7:0] bram_i_sample, bram_q_sample;
    wire bram_symbol_valid;
    wire bram_done, bram_underflow, bram_ready;
    integer errors = 0;
    integer symbols = 0;
    integer bram_symbols = 0;

    always @(posedge clk)
        if (bram_ref_en)
            bram_ref_rdata <= 32'h000000C0;

    iq_digital_mod bram_ref (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed), .sym_phase_step(sym_phase_step),
        .source_user(source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(13'd8),
        .user_active_bank(user_active_bank), .bram_b_addr(bram_ref_addr),
        .bram_b_en(bram_ref_en), .bram_b_rdata(bram_ref_rdata),
        .user_stream_enable(1'b0), .user_stream_length(32'd0),
        .user_stream_tdata(32'd0), .user_stream_tkeep(4'd0),
        .user_stream_tvalid(1'b0), .user_stream_tready(),
        .user_stream_tlast(1'b0), .user_done(bram_done),
        .user_underflow(bram_underflow), .user_ready(bram_ready),
        .i_sample(bram_i_sample), .q_sample(bram_q_sample),
        .symbol_valid(bram_symbol_valid)
    );

    iq_digital_mod uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed), .sym_phase_step(sym_phase_step),
        .source_user(source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank), .bram_b_addr(bram_b_addr),
        .bram_b_en(bram_b_en), .bram_b_rdata(bram_b_rdata),
        .user_stream_enable(user_stream_enable),
        .user_stream_length(user_stream_length),
        .user_stream_tdata(user_stream_tdata),
        .user_stream_tkeep(user_stream_tkeep),
        .user_stream_tvalid(user_stream_tvalid),
        .user_stream_tready(user_stream_tready),
        .user_stream_tlast(user_stream_tlast),
        .user_done(user_done), .user_underflow(user_underflow),
        .user_ready(user_ready), .i_sample(i_sample), .q_sample(q_sample),
        .symbol_valid(symbol_valid)
    );

    always #10 clk = ~clk;

    task check;
        input string name;
        input condition;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL %0s", name);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        #20;
        rst_n = 1'b1;
        user_stream_enable = 1'b0;
        enable = 1'b1;
        #1;
        check("BRAM selection blocks AXIS", user_stream_tready === 1'b0);
        enable = 1'b0;
        @(posedge clk);
        user_stream_enable = 1'b1;
        enable = 1'b1;
        #1;
        user_stream_tdata = 32'h000000C0;
        user_stream_tkeep = 4'b0001;
        user_stream_tlast = 1'b1;
        user_stream_tvalid = 1'b1;
        while (!user_stream_tready) @(posedge clk);
        @(posedge clk);
        #1;
        user_stream_tvalid = 1'b0;
        user_stream_tkeep = 4'b0000;
        user_stream_tlast = 1'b0;

        repeat (80) begin
            @(posedge clk);
            #1;
            if (symbol_valid) begin
                if (symbols < 2)
                    check("BPSK stream negative symbol", i_sample === -8'sd127 && q_sample === 8'sd0);
                else
                    check("BPSK stream positive symbol", i_sample === 8'sd127 && q_sample === 8'sd0);
                symbols = symbols + 1;
            end
            if (bram_symbol_valid) begin
                if (bram_symbols < 2)
                    check("BPSK BRAM negative symbol", bram_i_sample === -8'sd127 && bram_q_sample === 8'sd0);
                else
                    check("BPSK BRAM positive symbol", bram_i_sample === 8'sd127 && bram_q_sample === 8'sd0);
                bram_symbols = bram_symbols + 1;
            end
        end
        check("stream emitted eight symbols", symbols == 8);
        check("stream source done", user_done === 1'b1);
        check("stream source no underflow", user_underflow === 1'b0);
        check("stream source not ready after done", user_ready === 1'b0);
        check("BRAM emitted eight symbols", bram_symbols == 8);
        check("BRAM source done", bram_done === 1'b1);
        check("BRAM source no underflow", bram_underflow === 1'b0);
        check("BRAM source not ready after done", bram_ready === 1'b0);
        if (errors == 0) $display("PASS iq_digital_mod_stream_tb symbols=%0d", symbols);
        else $display("FAIL iq_digital_mod_stream_tb: %0d errors", errors);
        $finish;
    end
endmodule
