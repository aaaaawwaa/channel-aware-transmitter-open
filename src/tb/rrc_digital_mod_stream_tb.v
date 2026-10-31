`timescale 1ns / 1ps

module rrc_digital_mod_stream_tb;
    localparam [2:0] MODE_BPSK = 3'd0;
    localparam [31:0] STEP_1M = 32'd85899345;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = MODE_BPSK;
    reg [6:0] prbs_seed = 7'h7f;
    reg [1:0] rolloff = 2'd0;
    reg [31:0] sym_phase_step = STEP_1M;
    reg source_user = 1'b1;
    reg user_loop = 1'b0;
    reg user_commit_epoch = 1'b0;
    reg [12:0] user_length = 13'd0;
    reg user_active_bank = 1'b0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    reg [31:0] bram_b_rdata = 32'd0;
    reg user_stream_enable = 1'b1;
    reg [31:0] user_stream_length = 32'd8;
    reg [31:0] user_stream_tdata = 32'h000000C0;
    reg [3:0] user_stream_tkeep = 4'b0001;
    reg user_stream_tvalid = 1'b0;
    wire user_stream_tready;
    reg user_stream_tlast = 1'b1;
    wire user_done, user_underflow, user_ready;
    wire frame_done_pulse, drain_active;
    wire signed [15:0] i_sample, q_sample;
    wire sample_valid, symbol_valid;

    integer errors = 0;
    integer samples = 0;
    integer symbols = 0;
    integer drain_samples = 0;
    integer guard;

    rrc_digital_mod dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
        .prbs_seed(prbs_seed), .rolloff(rolloff),
        .sym_phase_step(sym_phase_step), .source_user(source_user),
        .user_loop(user_loop), .user_commit_epoch(user_commit_epoch),
        .user_length(user_length), .user_active_bank(user_active_bank),
        .bram_b_addr(bram_b_addr), .bram_b_en(bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_stream_enable(user_stream_enable),
        .user_stream_length(user_stream_length),
        .user_stream_tdata(user_stream_tdata),
        .user_stream_tkeep(user_stream_tkeep),
        .user_stream_tvalid(user_stream_tvalid),
        .user_stream_tready(user_stream_tready),
        .user_stream_tlast(user_stream_tlast),
        .user_done(user_done), .user_underflow(user_underflow),
        .user_ready(user_ready), .frame_done_pulse(frame_done_pulse),
        .drain_active(drain_active), .i_sample(i_sample), .q_sample(q_sample),
        .sample_valid(sample_valid), .symbol_valid(symbol_valid)
    );

    always #5 clk = ~clk;

    task check;
        input [255:0] name;
        input condition;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL %0s", name);
                errors = errors + 1;
            end
        end
    endtask

    task send_word;
        begin
            user_stream_tvalid = 1'b1;
            guard = 0;
            while (!user_stream_tready && guard < 100) begin
                @(posedge clk);
                guard = guard + 1;
            end
            check("stream word accepted", guard < 100);
            @(posedge clk);
            #1 user_stream_tvalid = 1'b0;
        end
    endtask

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        enable = 1'b1;
        send_word();

        guard = 0;
        while (!frame_done_pulse && guard < 20000) begin
            @(posedge clk);
            #1;
            guard = guard + 1;
            if (sample_valid) begin
                samples = samples + 1;
                if (drain_active)
                    drain_samples = drain_samples + 1;
            end
            if (symbol_valid)
                symbols = symbols + 1;
        end

        check("stream payload done", user_done === 1'b1);
        check("stream payload no underflow", user_underflow === 1'b0);
        check("eight input symbols emitted", symbols == 8);
        check("RRC drain active before frame done", drain_samples == 32);
        check("frame done pulse", frame_done_pulse === 1'b1);
        check("stream not ready after frame", user_ready === 1'b0);
        check("stream frame guard", guard < 20000);

        enable = 1'b0;
        @(posedge clk);
        #1;
        user_stream_length = 32'd16;
        user_stream_tdata = 32'h000000C0;
        user_stream_tkeep = 4'b0001;
        user_stream_tlast = 1'b1;
        enable = 1'b1;
        send_word();
        guard = 0;
        while (!user_underflow && guard < 2000) begin
            @(posedge clk);
            #1;
            guard = guard + 1;
        end
        check("short stream underflow", user_underflow === 1'b1);
        check("short stream not done", user_done === 1'b0);
        check("short stream frame not completed", frame_done_pulse === 1'b0);
        check("short stream no drain", drain_active === 1'b0);

        if (errors == 0)
            $display("PASS rrc_digital_mod_stream_tb samples=%0d symbols=%0d drain=%0d",
                     samples, symbols, drain_samples);
        else
            $display("FAIL rrc_digital_mod_stream_tb: %0d errors", errors);
        $finish;
    end
endmodule
