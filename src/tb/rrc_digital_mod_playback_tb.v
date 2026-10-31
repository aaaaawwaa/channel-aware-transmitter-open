`timescale 1ns/1ps
module rrc_digital_mod_playback_tb;
    localparam [2:0] QPSK = 3'd1;
    localparam [31:0] SYM_STEP = 32'h1000_0000;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = QPSK;
    reg [6:0] prbs_seed = 7'h7f;
    reg [1:0] rolloff = 2'd0;
    reg [31:0] sym_phase_step = SYM_STEP;
    reg source_user = 1'b0;
    reg user_loop = 1'b0;
    reg user_commit_epoch = 1'b0;
    reg [12:0] user_length = 13'd0;
    reg user_active_bank = 1'b0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    reg [31:0] bram_b_rdata = 32'd0;
    wire user_done, user_underflow, user_ready;
    wire frame_done_pulse, drain_active;
    wire signed [15:0] i_sample, q_sample;
    wire sample_valid, symbol_valid;

    reg [7:0] mem [0:1023];
    reg [31:0] bram_b_rdata_q = 32'd0;
    wire [9:0] byte_addr = {bram_b_addr[7], bram_b_addr[6:0], 2'b00};
    integer k;
    always @(posedge clk) begin
        if (bram_b_en)
            bram_b_rdata_q <= {mem[byte_addr + 10'd3], mem[byte_addr + 10'd2],
                               mem[byte_addr + 10'd1], mem[byte_addr]};
    end
    always @* bram_b_rdata = bram_b_rdata_q;

    rrc_digital_mod dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed), .rolloff(rolloff),
        .sym_phase_step(sym_phase_step), .source_user(source_user),
        .user_loop(user_loop), .user_commit_epoch(user_commit_epoch),
        .user_length(user_length), .user_active_bank(user_active_bank),
        .bram_b_addr(bram_b_addr), .bram_b_en(bram_b_en),
        .bram_b_rdata(bram_b_rdata), .user_done(user_done),
        .user_underflow(user_underflow), .user_ready(user_ready),
        .frame_done_pulse(frame_done_pulse), .drain_active(drain_active),
        .i_sample(i_sample), .q_sample(q_sample),
        .sample_valid(sample_valid), .symbol_valid(symbol_valid)
    );

    always #5 clk = ~clk;

    integer failures = 0;
    integer cycles = 0;
    integer sample_count = 0;
    integer symbol_count = 0;
    integer drain_samples = 0;
    integer guard;
    reg seen_user_done = 1'b0;
    reg seen_frame_done = 1'b0;

    initial begin
        for (k = 0; k < 1024; k = k + 1)
            mem[k] = 8'd0;
        // 0xB2 0x71, eight QPSK symbols; MSB-first in byte stream.
        mem[0] = 8'hB2;
        mem[1] = 8'h71;
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        source_user = 1'b1;
        user_length = 13'd16;
        enable = 1'b1;
        guard = 0;
        while (!seen_frame_done && guard < 20000) begin
            @(posedge clk); #1;
            cycles = cycles + 1;
            guard = guard + 1;
            if (sample_valid)
                sample_count = sample_count + 1;
            if (symbol_valid) begin
                symbol_count = symbol_count + 1;
                if (drain_active)
                    failures = failures + 1;
            end
            if (user_done)
                seen_user_done = 1'b1;
            if (drain_active)
                drain_samples = drain_samples + sample_valid;
            if (frame_done_pulse) begin
                seen_frame_done = 1'b1;
                if (!seen_user_done)
                    failures = failures + 1;
                if (drain_active)
                    failures = failures + 1;
                if (user_underflow)
                    failures = failures + 1;
            end
        end
        if (!seen_frame_done) begin
            $display("FAIL RRC frame_done timeout");
            $finish;
        end
        // frame_done_pulse is registered one cycle after the final drain sample.
        @(posedge clk); #1;
        if (sample_valid)
            sample_count = sample_count + 1;
        if (frame_done_pulse)
            failures = failures + 1;
        if (sample_count != 64)
            failures = failures + 1;
        if (symbol_count != 8)
            failures = failures + 1;
        if (drain_samples != 32)
            failures = failures + 1;
        if (failures == 0)
            $display("PASS rrc_digital_mod_playback_tb samples=%0d symbols=%0d drain=%0d",
                     sample_count, symbol_count, drain_samples);
        else
            $display("FAIL rrc_digital_mod_playback_tb failures=%0d samples=%0d symbols=%0d drain=%0d",
                     failures, sample_count, symbol_count, drain_samples);
        $finish;
    end
endmodule
