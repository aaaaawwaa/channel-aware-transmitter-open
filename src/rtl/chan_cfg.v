`timescale 1ns / 1ps
// 信道抽头表读取/锁存 FSM。
// 每通道 6 个 32-bit 字, 字布局:
//   抽头字 (0-3 / 4-7): gain[8:0] | delay[17:9] (gain 9-bit 有符号 Q8, delay 9-bit 无符号);
//   tone 字 (12 / 14): fword[23:0] | ampl[30:24] (ampl 7-bit 无符号);
//   impulse 字 (13 / 15): period[15:0] | width[23:16] | ampl[31:24] (ampl 8-bit 有符号)。
//
// 另加 **3 个通道无关的字**(I/Q 复数级): iq_impair 是通道对级**单实例**, 字 8-10
// 不属于任何单通道, 故任一通道 commit 都会一并排空(幂等, 无害)。这样避免"只有 CH1
// 的 commit 才能改 I/Q 参数"这种隐式耦合。
//   字 8 : iq_coef_ab = A[15:0] | B[31:16] (各 Q2.14 有符号)
//   字 9 : iq_coef_cd = C[15:0] | D[31:16]
//   字 10: iq_fword[31:0] (频偏相位增量)。字 11 仍预留。
// 直接输出**打包后的字**(与 iq_impair 的 coef_ab/coef_cd 端口同布局), 不在顶层再拆再拼。
// CH1 base=0, CH2 base=4。
//
// chX_commit 是 PS 侧同步后的 epoch 翻转位: 观察到翻转(边沿)即置该通道 pending。
// 两通道 pending 独立维护, 逐个排空(两路同时到达时 CH1 优先), 因此同时到达的
// CH2 commit 不会被丢弃; 排空完成的那一拍若又出现新边沿, pending 保持。
// 首个 commit 之前所有 chX_gain/delay 保持 0(FIR 未配置即全零)。
//
// BRAM 端口 B 为 **同步** 读(R12): 第 N 拍给出 tap_b_addr/tap_b_en, BRAM 于
// 第 N+1 拍采样地址, 数据第 N+1 拍才出现在 tap_b_rdata, 故第 N+2 拍锁存。
// 状态机: IDLE -给出地址-> WAIT(地址保持, BRAM 采样) -> CAP(锁存数据)。
module chan_cfg (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        ch1_commit,
    input  wire        ch2_commit,
    output reg  [4:0]  tap_b_addr,
    output reg         tap_b_en,
    input  wire [31:0] tap_b_rdata,
    output reg  [35:0] ch1_gain,
    output reg  [35:0] ch1_delay,
    output reg  [35:0] ch2_gain,
    output reg  [35:0] ch2_delay,
    output reg  [23:0] ch1_tone_fword, ch2_tone_fword,
    output reg  [6:0]  ch1_tone_ampl,  ch2_tone_ampl,
    output reg  [15:0] ch1_imp_period, ch2_imp_period,
    output reg  [7:0]  ch1_imp_width,  ch2_imp_width,
    output reg  signed [7:0] ch1_imp_ampl, ch2_imp_ampl,
    output reg  [31:0] iq_coef_ab, iq_coef_cd, iq_fword
);
    localparam S_IDLE = 2'd0, S_WAIT = 2'd1, S_CAP = 2'd2;

    // 每通道 9 步: {0,1,2,3,12,13} + 通道无关的 {8,9,10}
    localparam STEPS = 9;

    reg [1:0] state;
    reg       cur_ch;          // 0 = CH1, 1 = CH2
    reg [3:0] step;            // 0..8
    reg       commit1_d, commit2_d;
    reg       pend1, pend2;    // 每通道 commit pending

    wire edge1 = (ch1_commit != commit1_d);
    wire edge2 = (ch2_commit != commit2_d);

    // 字地址表: 用函数表达, 避免 base+step 在跨段时出现断点。
    function [4:0] word_sel;
        input       ch;        // 0 = CH1, 1 = CH2
        input [3:0] s;
        begin
            if (s >= 4'd6)       word_sel = 5'd8 + (s - 4'd6);        // 8,9,10 通道无关
            else if (ch == 1'b0) word_sel = (s < 4'd4) ? {1'b0, s}          // 0..3
                                                       : (5'd8 + {1'b0, s});  // 12,13
            else                 word_sel = (s < 4'd4) ? (5'd4 + {1'b0, s})  // 4..7
                                                       : (5'd10 + {1'b0, s}); // 14,15
        end
    endfunction

    wire [4:0] cur_word  = word_sel(cur_ch, step);
    wire [4:0] next_word = word_sel(cur_ch, step + 4'd1);

    wire       done_ch  = (state == S_CAP) && (step == STEPS - 1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            cur_ch     <= 1'b0;
            step       <= 4'd0;
            commit1_d  <= 1'b0;
            commit2_d  <= 1'b0;
            pend1      <= 1'b0;
            pend2      <= 1'b0;
            tap_b_addr <= 5'd0;
            tap_b_en   <= 1'b0;
            ch1_gain   <= 36'd0;
            ch1_delay  <= 36'd0;
            ch2_gain   <= 36'd0;
            ch2_delay  <= 36'd0;
            ch1_tone_fword <= 24'd0;
            ch2_tone_fword <= 24'd0;
            ch1_tone_ampl  <= 7'd0;
            ch2_tone_ampl  <= 7'd0;
            ch1_imp_period <= 16'd0;
            ch2_imp_period <= 16'd0;
            ch1_imp_width  <= 8'd0;
            ch2_imp_width  <= 8'd0;
            ch1_imp_ampl   <= 8'sd0;
            ch2_imp_ampl   <= 8'sd0;
            iq_coef_ab     <= 32'd0;
            iq_coef_cd     <= 32'd0;
            iq_fword       <= 32'd0;
        end else begin
            commit1_d <= ch1_commit;
            commit2_d <= ch2_commit;

            // pending: 边沿优先置位; 仅当本通道排空完成且同一拍无新边沿才清除,
            // 保证同时/紧邻到达的另一字节不被静默丢弃。
            if (edge1) pend1 <= 1'b1;
            else if (done_ch && cur_ch == 1'b0) pend1 <= 1'b0;
            if (edge2) pend2 <= 1'b1;
            else if (done_ch && cur_ch == 1'b1) pend2 <= 1'b0;

            case (state)
                S_IDLE: begin
                    tap_b_en <= 1'b0;
                    if (pend1 || pend2) begin
                        cur_ch     <= pend1 ? 1'b0 : 1'b1;   // CH1 优先
                        step       <= 3'd0;
                        // 保持 pend1 ? 0 : 4 不动: 此处 cur_ch 由非阻塞赋值更新,
                        // 用 word_sel(cur_ch,0) 会读到旧通道, CH1->CH2 时排空错字。
                        tap_b_addr <= pend1 ? 5'd0 : 5'd4;
                        tap_b_en   <= 1'b1;
                        state      <= S_WAIT;
                    end
                end
                S_WAIT: begin
                    // 地址/使能保持本拍, BRAM 在本拍末采样, 数据下拍有效
                    tap_b_addr <= cur_word;
                    state      <= S_CAP;
                end
                S_CAP: begin
                    case (step)
                        4'd0, 4'd1, 4'd2, 4'd3: begin
                            if (cur_ch == 1'b0) begin
                                ch1_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch1_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end else begin
                                ch2_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch2_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end
                        end
                        4'd4: begin   // tone 字
                            if (cur_ch == 1'b0) begin
                                ch1_tone_fword <= tap_b_rdata[23:0];
                                ch1_tone_ampl  <= tap_b_rdata[30:24];
                            end else begin
                                ch2_tone_fword <= tap_b_rdata[23:0];
                                ch2_tone_ampl  <= tap_b_rdata[30:24];
                            end
                        end
                        4'd5: begin   // impulse 字
                            if (cur_ch == 1'b0) begin
                                ch1_imp_period <= tap_b_rdata[15:0];
                                ch1_imp_width  <= tap_b_rdata[23:16];
                                ch1_imp_ampl   <= tap_b_rdata[31:24];
                            end else begin
                                ch2_imp_period <= tap_b_rdata[15:0];
                                ch2_imp_width  <= tap_b_rdata[23:16];
                                ch2_imp_ampl   <= tap_b_rdata[31:24];
                            end
                        end
                        // 字 8/9/10 通道无关: 整字锁存, 保持与 iq_impair 端口同布局
                        4'd6: iq_coef_ab <= tap_b_rdata;   // A[15:0] | B[31:16]
                        4'd7: iq_coef_cd <= tap_b_rdata;   // C[15:0] | D[31:16]
                        4'd8: iq_fword   <= tap_b_rdata;
                        default: ;    // step 9..15 不可达(step 到 8 即结束), 显式留空
                    endcase
                    if (step == STEPS - 1) begin
                        tap_b_en <= 1'b0;
                        state    <= S_IDLE;
                    end else begin
                        step       <= step + 4'd1;
                        tap_b_addr <= next_word;
                        tap_b_en   <= 1'b1;
                        state      <= S_WAIT;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
