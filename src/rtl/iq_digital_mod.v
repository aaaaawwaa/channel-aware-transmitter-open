`timescale 1ns / 1ps
module iq_digital_mod (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire [2:0] mode,
    input wire [6:0] prbs_seed,
    input wire [31:0] sym_phase_step,
    // 用户 bit 源控制(来自 reg_bridge, 经 user_bit_source 消费)
    input wire source_user,
    input wire user_loop,
    input wire user_commit_epoch,
    input wire [12:0] user_length,
    input wire user_active_bank,
    output wire [7:0] bram_b_addr,
    output wire bram_b_en,
    input wire [31:0] bram_b_rdata,
    input wire user_stream_enable,
    input wire [31:0] user_stream_length,
    input wire [31:0] user_stream_tdata,
    input wire [3:0] user_stream_tkeep,
    input wire user_stream_tvalid,
    output wire user_stream_tready,
    input wire user_stream_tlast,
    output wire user_done,
    output wire user_underflow,
    output wire user_ready,
    output reg signed [7:0] i_sample,
    output reg signed [7:0] q_sample,
    output reg symbol_valid
);
    reg [31:0] phase_acc;
    reg active;
    wire [5:0] prbs_symbol_bits;
    wire [5:0] user_symbol_bits;
    wire [5:0] stream_symbol_bits;
    wire user_bram_ready, user_bram_done, user_bram_underflow;
    wire stream_axis_ready, stream_ready, stream_done, stream_underflow;
    wire stream_frame_done;
    wire use_stream = source_user && (user_stream_enable === 1'b1);
    wire use_bram = source_user && !use_stream;
    wire signed [7:0] mapped_i;
    wire signed [7:0] mapped_q;
    wire [2:0] consume_bits;
    wire [32:0] phase_sum = {1'b0, phase_acc} + {1'b0, sym_phase_step};
    wire symbol_tick = active && phase_sum[32];
    wire emit_ok = !source_user || user_ready;
    wire emit_symbol = enable && (!active || symbol_tick) && emit_ok;

    prbs7_source u_prbs (
        .clk(clk), .rst_n(rst_n), .reset_seed(!enable),
        .seed_in(prbs_seed),
        .advance(emit_symbol && !source_user), .advance_bits(consume_bits),
        .symbol_bits(prbs_symbol_bits)
    );

    user_bit_source u_user (
        .clk(clk), .rst_n(rst_n),
        .active(enable && use_bram),
        .loop_en(user_loop),
        .commit_toggle(user_commit_epoch),
        .bit_length(user_length),
        .active_bank(user_active_bank),
        .advance(emit_symbol && use_bram), .advance_bits(consume_bits),
        .symbol_bits(user_symbol_bits),
        .ready(user_bram_ready), .done(user_bram_done), .underflow(user_bram_underflow),
        .b_addr(bram_b_addr), .b_en(bram_b_en), .b_rdata(bram_b_rdata)
    );

    user_stream_source u_stream (
        .clk(clk), .rst_n(rst_n),
        .active(enable && use_stream), .bit_length(user_stream_length),
        .advance(emit_symbol && use_stream), .advance_bits(consume_bits),
        .s_axis_tdata(user_stream_tdata), .s_axis_tkeep(user_stream_tkeep),
        .s_axis_tvalid(user_stream_tvalid), .s_axis_tready(stream_axis_ready),
        .s_axis_tlast(user_stream_tlast), .symbol_bits(stream_symbol_bits),
        .ready(stream_ready), .done(stream_done), .underflow(stream_underflow),
        .frame_done_pulse(stream_frame_done)
    );

    assign user_ready = use_stream ? stream_ready : user_bram_ready;
    assign user_done = use_stream ? stream_done : user_bram_done;
    assign user_underflow = use_stream ? stream_underflow : user_bram_underflow;
    assign user_stream_tready = use_stream ? stream_axis_ready : 1'b0;
    wire [5:0] symbol_bits = source_user ?
                              (use_stream ? stream_symbol_bits : user_symbol_bits) :
                              prbs_symbol_bits;

    iq_constellation_mapper u_mapper (
        .mode(mode), .symbol_bits(symbol_bits),
        .i_symbol(mapped_i), .q_symbol(mapped_q),
        .consume_bits(consume_bits)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || !enable) begin
            phase_acc <= 32'd0;
            active <= 1'b0;
            i_sample <= 8'sd0;
            q_sample <= 8'sd0;
            symbol_valid <= 1'b0;
        end else begin
            symbol_valid <= 1'b0;
            if (!active) begin
                if (source_user && !user_ready) begin
                    // 等待用户 bit 源预取就绪
                end else begin
                    active <= 1'b1;
                    phase_acc <= 32'd0;
                    i_sample <= mapped_i;
                    q_sample <= mapped_q;
                    symbol_valid <= 1'b1;
                end
            end else if (source_user && !user_ready) begin
                // 数据耗尽(stall): 保持相位, 等 user_bit_source 重启/回绕
                phase_acc <= phase_acc;
            end else begin
                phase_acc <= phase_sum[31:0];
                if (phase_sum[32]) begin
                    i_sample <= mapped_i;
                    q_sample <= mapped_q;
                    symbol_valid <= 1'b1;
                end
            end
        end
    end
endmodule
