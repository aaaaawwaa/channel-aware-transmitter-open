`timescale 1ns / 1ps

module rrc_digital_mod (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire [2:0] mode,
    input wire [6:0] prbs_seed,
    input wire [1:0] rolloff,
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
    output wire frame_done_pulse,
    output wire drain_active,
    output reg signed [15:0] i_sample,
    output reg signed [15:0] q_sample,
    output reg sample_valid,
    output wire symbol_valid
);
    // mapper 编码 BPSK=0..QAM64=4; OFF/非法编码 5-7 禁用 RRC
    wire rrc_enable = enable && (mode <= 3'd4);
    reg active;
    reg [31:0] sample_phase_acc;
    reg [1:0] poly_phase;
    reg symbol_valid_c;
    reg user_done_d;
    reg [5:0] drain_remaining;
    reg frame_done_pulse_r;
    wire user_done_w, user_underflow_w;
    wire user_bram_ready, user_bram_done, user_bram_underflow;
    wire [5:0] stream_symbol_bits;
    wire stream_axis_ready, stream_ready, stream_done, stream_underflow, stream_frame_done;
    wire use_stream = source_user && (user_stream_enable === 1'b1);
    wire use_bram = source_user && !use_stream;

    // 输出级流水: sample_tick/load_symbol/phase/new_i/new_q 统一寄存一拍再喂入 rrc_filter,
    // 切断 sample_phase_acc 33-bit 进位链 → FIR 乘加树的组合长路径; 所有信号等延迟一拍,
    // symbol_valid 同步延迟以与 sample_valid 对齐(保持 symbol_valid 只在 sample_valid 边界为高)。
    reg sample_tick_r;
    reg load_symbol_r;
    reg [1:0] phase_r;
    reg signed [15:0] new_i_r;
    reg signed [15:0] new_q_r;
    reg symbol_valid_r;

    wire [31:0] sample_phase_step = sym_phase_step << 2;
    wire [32:0] sample_sum = {1'b0, sample_phase_acc} +
                             {1'b0, sample_phase_step};
    wire first_sample = rrc_enable && !active;
    wire user_draining = source_user && !user_underflow_w && (drain_remaining != 6'd0);
    wire user_stall = rrc_enable && source_user && !user_ready && !user_draining;
    wire sample_tick = !user_stall && (first_sample || (active && sample_sum[32]));
    wire load_symbol = first_sample ||
                       (active && sample_tick && poly_phase == 2'd0);
    wire advance_prbs = active && sample_tick && poly_phase == 2'd3 && !user_draining;

    wire [5:0] prbs_symbol_bits;
    wire [5:0] user_symbol_bits;
    wire signed [7:0] mapped_i;
    wire signed [7:0] mapped_q;
    wire [2:0] consume_bits;
    wire signed [15:0] scaled_i = user_draining ? 16'sd0 : {mapped_i, 8'h00};
    wire signed [15:0] scaled_q = user_draining ? 16'sd0 : {mapped_q, 8'h00};
    wire signed [15:0] filter_i_sample;
    wire signed [15:0] filter_q_sample;
    wire filter_sample_valid;

    prbs7_source u_prbs (
        .clk(clk), .rst_n(rst_n), .reset_seed(!rrc_enable),
        .seed_in(prbs_seed),
        .advance(advance_prbs && !source_user), .advance_bits(consume_bits),
        .symbol_bits(prbs_symbol_bits)
    );

    user_bit_source u_user (
        .clk(clk), .rst_n(rst_n),
        .active(rrc_enable && use_bram),
        .loop_en(user_loop),
        .commit_toggle(user_commit_epoch),
        .bit_length(user_length),
        .active_bank(user_active_bank),
        .advance(advance_prbs && use_bram), .advance_bits(consume_bits),
        .symbol_bits(user_symbol_bits),
        .ready(user_bram_ready), .done(user_bram_done), .underflow(user_bram_underflow),
        .b_addr(bram_b_addr), .b_en(bram_b_en), .b_rdata(bram_b_rdata)
    );

    user_stream_source u_stream (
        .clk(clk), .rst_n(rst_n),
        .active(rrc_enable && use_stream), .bit_length(user_stream_length),
        .advance(advance_prbs && use_stream), .advance_bits(consume_bits),
        .s_axis_tdata(user_stream_tdata), .s_axis_tkeep(user_stream_tkeep),
        .s_axis_tvalid(user_stream_tvalid), .s_axis_tready(stream_axis_ready),
        .s_axis_tlast(user_stream_tlast), .symbol_bits(stream_symbol_bits),
        .ready(stream_ready), .done(stream_done), .underflow(stream_underflow),
        .frame_done_pulse(stream_frame_done)
    );

    assign user_ready = use_stream ? stream_ready : user_bram_ready;
    assign user_done_w = use_stream ? stream_done : user_bram_done;
    assign user_underflow_w = use_stream ? stream_underflow : user_bram_underflow;
    assign user_stream_tready = use_stream ? stream_axis_ready : 1'b0;
    wire [5:0] symbol_bits = source_user ?
                              (use_stream ? stream_symbol_bits : user_symbol_bits) :
                              prbs_symbol_bits;

    iq_constellation_mapper u_mapper (
        .mode(mode), .symbol_bits(symbol_bits),
        .i_symbol(mapped_i), .q_symbol(mapped_q),
        .consume_bits(consume_bits)
    );

    rrc_filter u_filter (
        .clk(clk), .rst_n(rst_n), .enable(rrc_enable),
        .sample_tick(sample_tick_r), .phase(phase_r),
        .load_symbol(load_symbol_r), .rolloff(rolloff),
        .new_i(new_i_r), .new_q(new_q_r),
        .i_sample(filter_i_sample), .q_sample(filter_q_sample),
        .sample_valid(filter_sample_valid)
    );

    always @* begin
        i_sample = filter_i_sample;
        q_sample = filter_q_sample;
        sample_valid = filter_sample_valid;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || !rrc_enable) begin
            active <= 1'b0;
            sample_phase_acc <= 32'd0;
            poly_phase <= 2'd0;
            symbol_valid_c <= 1'b0;
            user_done_d <= 1'b0;
            drain_remaining <= 6'd0;
            frame_done_pulse_r <= 1'b0;
        end else begin
            frame_done_pulse_r <= user_draining && sample_tick && (drain_remaining == 6'd1);
            user_done_d <= user_done_w;
            if (!source_user || user_underflow_w)
                drain_remaining <= 6'd0;
            else if (user_done_w && !user_done_d)
                drain_remaining <= 6'd32;
            else if (user_draining && sample_tick && (drain_remaining != 6'd0))
                drain_remaining <= drain_remaining - 1'b1;
            symbol_valid_c <= 1'b0;
            if (!active) begin
                if (source_user && !user_ready) begin
                    // 等待用户 bit 源预取就绪
                end else begin
                    active <= 1'b1;
                    sample_phase_acc <= 32'd0;
                    poly_phase <= 2'd1;
                    symbol_valid_c <= 1'b1;
                end
            end else if (source_user && !user_ready && !user_draining) begin
                // 数据耗尽(stall): 保持相位, 等 user_bit_source 重启/回绕
                sample_phase_acc <= sample_phase_acc;
            end else begin
                sample_phase_acc <= sample_sum[31:0];
                if (sample_tick) begin
                    poly_phase <= poly_phase + 2'd1;
                    if ((poly_phase == 2'd0) && !user_draining)
                        symbol_valid_c <= 1'b1;
                end
            end
        end
    end

    // 输出级流水寄存器: 与 rrc_filter 消耗同沿对齐, 全部相对组合输入滞后一拍。
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || !rrc_enable) begin
            sample_tick_r <= 1'b0;
            load_symbol_r <= 1'b0;
            phase_r <= 2'd0;
            new_i_r <= 16'sd0;
            new_q_r <= 16'sd0;
            symbol_valid_r <= 1'b0;
        end else begin
            sample_tick_r <= sample_tick;
            load_symbol_r <= load_symbol;
            phase_r <= poly_phase;
            new_i_r <= scaled_i;
            new_q_r <= scaled_q;
            symbol_valid_r <= symbol_valid_c;
        end
    end

    assign symbol_valid = symbol_valid_r;
    assign user_done = user_done_w;
    assign user_underflow = user_underflow_w;
    assign drain_active = user_draining ||
                          (source_user && !user_underflow_w &&
                           user_done_w && !user_done_d);
    assign frame_done_pulse = frame_done_pulse_r;
endmodule
