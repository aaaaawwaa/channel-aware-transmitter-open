`timescale 1ns / 1ps
module rrc_filter (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire sample_tick,
    input wire [1:0] phase,
    input wire load_symbol,
    input wire [1:0] rolloff,
    input wire signed [15:0] new_i,
    input wire signed [15:0] new_q,
    output reg signed [15:0] i_sample,
    output reg signed [15:0] q_sample,
    output reg sample_valid
);
    `include "rrc_coeffs.vh"

    reg signed [15:0] i_hist [0:8];
    reg signed [15:0] q_hist [0:8];
    reg signed [39:0] acc_i;
    reg signed [39:0] acc_q;
    reg signed [15:0] i_head;
    reg signed [15:0] q_head;
    reg signed [15:0] p0_i [0:8];
    reg signed [15:0] p0_q [0:8];
    reg signed [15:0] c [0:32];
    // 乘积与平衡加法树中间项: 40-bit 防止求和溢出, 且显式树形分组
    // 阻止 Vivado 把 8 路 DSP48 串成 PCOUT 级联(单周期长路径)。
    reg signed [39:0] pr [0:8];
    reg signed [39:0] qr [0:8];
    integer m;
    integer n;
    integer k;

    function signed [15:0] rrc_coeff(input integer idx, input [1:0] alpha);
        begin
            case (alpha)
                2'd1: begin
                    case (idx)
                        0: rrc_coeff = RRC_COEFF_00_A20;
                        1: rrc_coeff = RRC_COEFF_01_A20;
                        2: rrc_coeff = RRC_COEFF_02_A20;
                        3: rrc_coeff = RRC_COEFF_03_A20;
                        4: rrc_coeff = RRC_COEFF_04_A20;
                        5: rrc_coeff = RRC_COEFF_05_A20;
                        6: rrc_coeff = RRC_COEFF_06_A20;
                        7: rrc_coeff = RRC_COEFF_07_A20;
                        8: rrc_coeff = RRC_COEFF_08_A20;
                        9: rrc_coeff = RRC_COEFF_09_A20;
                        10: rrc_coeff = RRC_COEFF_10_A20;
                        11: rrc_coeff = RRC_COEFF_11_A20;
                        12: rrc_coeff = RRC_COEFF_12_A20;
                        13: rrc_coeff = RRC_COEFF_13_A20;
                        14: rrc_coeff = RRC_COEFF_14_A20;
                        15: rrc_coeff = RRC_COEFF_15_A20;
                        16: rrc_coeff = RRC_COEFF_16_A20;
                        17: rrc_coeff = RRC_COEFF_17_A20;
                        18: rrc_coeff = RRC_COEFF_18_A20;
                        19: rrc_coeff = RRC_COEFF_19_A20;
                        20: rrc_coeff = RRC_COEFF_20_A20;
                        21: rrc_coeff = RRC_COEFF_21_A20;
                        22: rrc_coeff = RRC_COEFF_22_A20;
                        23: rrc_coeff = RRC_COEFF_23_A20;
                        24: rrc_coeff = RRC_COEFF_24_A20;
                        25: rrc_coeff = RRC_COEFF_25_A20;
                        26: rrc_coeff = RRC_COEFF_26_A20;
                        27: rrc_coeff = RRC_COEFF_27_A20;
                        28: rrc_coeff = RRC_COEFF_28_A20;
                        29: rrc_coeff = RRC_COEFF_29_A20;
                        30: rrc_coeff = RRC_COEFF_30_A20;
                        31: rrc_coeff = RRC_COEFF_31_A20;
                        32: rrc_coeff = RRC_COEFF_32_A20;
                        default: rrc_coeff = 16'sd0;
                    endcase
                end
                2'd2: begin
                    case (idx)
                        0: rrc_coeff = RRC_COEFF_00_A50;
                        1: rrc_coeff = RRC_COEFF_01_A50;
                        2: rrc_coeff = RRC_COEFF_02_A50;
                        3: rrc_coeff = RRC_COEFF_03_A50;
                        4: rrc_coeff = RRC_COEFF_04_A50;
                        5: rrc_coeff = RRC_COEFF_05_A50;
                        6: rrc_coeff = RRC_COEFF_06_A50;
                        7: rrc_coeff = RRC_COEFF_07_A50;
                        8: rrc_coeff = RRC_COEFF_08_A50;
                        9: rrc_coeff = RRC_COEFF_09_A50;
                        10: rrc_coeff = RRC_COEFF_10_A50;
                        11: rrc_coeff = RRC_COEFF_11_A50;
                        12: rrc_coeff = RRC_COEFF_12_A50;
                        13: rrc_coeff = RRC_COEFF_13_A50;
                        14: rrc_coeff = RRC_COEFF_14_A50;
                        15: rrc_coeff = RRC_COEFF_15_A50;
                        16: rrc_coeff = RRC_COEFF_16_A50;
                        17: rrc_coeff = RRC_COEFF_17_A50;
                        18: rrc_coeff = RRC_COEFF_18_A50;
                        19: rrc_coeff = RRC_COEFF_19_A50;
                        20: rrc_coeff = RRC_COEFF_20_A50;
                        21: rrc_coeff = RRC_COEFF_21_A50;
                        22: rrc_coeff = RRC_COEFF_22_A50;
                        23: rrc_coeff = RRC_COEFF_23_A50;
                        24: rrc_coeff = RRC_COEFF_24_A50;
                        25: rrc_coeff = RRC_COEFF_25_A50;
                        26: rrc_coeff = RRC_COEFF_26_A50;
                        27: rrc_coeff = RRC_COEFF_27_A50;
                        28: rrc_coeff = RRC_COEFF_28_A50;
                        29: rrc_coeff = RRC_COEFF_29_A50;
                        30: rrc_coeff = RRC_COEFF_30_A50;
                        31: rrc_coeff = RRC_COEFF_31_A50;
                        32: rrc_coeff = RRC_COEFF_32_A50;
                        default: rrc_coeff = 16'sd0;
                    endcase
                end
                default: begin
                    case (idx)
                        0: rrc_coeff = RRC_COEFF_00_A35;
                        1: rrc_coeff = RRC_COEFF_01_A35;
                        2: rrc_coeff = RRC_COEFF_02_A35;
                        3: rrc_coeff = RRC_COEFF_03_A35;
                        4: rrc_coeff = RRC_COEFF_04_A35;
                        5: rrc_coeff = RRC_COEFF_05_A35;
                        6: rrc_coeff = RRC_COEFF_06_A35;
                        7: rrc_coeff = RRC_COEFF_07_A35;
                        8: rrc_coeff = RRC_COEFF_08_A35;
                        9: rrc_coeff = RRC_COEFF_09_A35;
                        10: rrc_coeff = RRC_COEFF_10_A35;
                        11: rrc_coeff = RRC_COEFF_11_A35;
                        12: rrc_coeff = RRC_COEFF_12_A35;
                        13: rrc_coeff = RRC_COEFF_13_A35;
                        14: rrc_coeff = RRC_COEFF_14_A35;
                        15: rrc_coeff = RRC_COEFF_15_A35;
                        16: rrc_coeff = RRC_COEFF_16_A35;
                        17: rrc_coeff = RRC_COEFF_17_A35;
                        18: rrc_coeff = RRC_COEFF_18_A35;
                        19: rrc_coeff = RRC_COEFF_19_A35;
                        20: rrc_coeff = RRC_COEFF_20_A35;
                        21: rrc_coeff = RRC_COEFF_21_A35;
                        22: rrc_coeff = RRC_COEFF_22_A35;
                        23: rrc_coeff = RRC_COEFF_23_A35;
                        24: rrc_coeff = RRC_COEFF_24_A35;
                        25: rrc_coeff = RRC_COEFF_25_A35;
                        26: rrc_coeff = RRC_COEFF_26_A35;
                        27: rrc_coeff = RRC_COEFF_27_A35;
                        28: rrc_coeff = RRC_COEFF_28_A35;
                        29: rrc_coeff = RRC_COEFF_29_A35;
                        30: rrc_coeff = RRC_COEFF_30_A35;
                        31: rrc_coeff = RRC_COEFF_31_A35;
                        32: rrc_coeff = RRC_COEFF_32_A35;
                        default: rrc_coeff = 16'sd0;
                    endcase
                end
            endcase
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (k = 0; k < 33; k = k + 1)
                c[k] <= 16'sd0;
        end else begin
            for (k = 0; k < 33; k = k + 1)
                c[k] <= rrc_coeff(k, rolloff);
        end
    end

    function signed [31:0] mult16;
        input signed [15:0] lhs;
        input signed [15:0] rhs;
        reg signed [31:0] product;
        begin
            product = lhs * rhs;
            mult16 = product;
        end
    endfunction

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

    always @* begin
        i_head = i_hist[0];
        q_head = q_hist[0];
        if (phase == 2'd0 && load_symbol) begin
            i_head = new_i;
            q_head = new_q;
        end
        p0_i[0] = i_head;
        p0_q[0] = q_head;
        for (m = 1; m < 9; m = m + 1) begin
            if (phase == 2'd0 && load_symbol) begin
                p0_i[m] = i_hist[m - 1];
                p0_q[m] = q_hist[m - 1];
            end else begin
                p0_i[m] = i_hist[m];
                p0_q[m] = q_hist[m];
            end
        end

        acc_i = 40'sd0;
        acc_q = 40'sd0;
        case (phase)
            2'd0: begin
                pr[0] = mult16(p0_i[0], c[0]);
                pr[1] = mult16(p0_i[1], c[4]);
                pr[2] = mult16(p0_i[2], c[8]);
                pr[3] = mult16(p0_i[3], c[12]);
                pr[4] = mult16(p0_i[4], c[16]);
                pr[5] = mult16(p0_i[5], c[20]);
                pr[6] = mult16(p0_i[6], c[24]);
                pr[7] = mult16(p0_i[7], c[28]);
                pr[8] = mult16(p0_i[8], c[32]);
                qr[0] = mult16(p0_q[0], c[0]);
                qr[1] = mult16(p0_q[1], c[4]);
                qr[2] = mult16(p0_q[2], c[8]);
                qr[3] = mult16(p0_q[3], c[12]);
                qr[4] = mult16(p0_q[4], c[16]);
                qr[5] = mult16(p0_q[5], c[20]);
                qr[6] = mult16(p0_q[6], c[24]);
                qr[7] = mult16(p0_q[7], c[28]);
                qr[8] = mult16(p0_q[8], c[32]);
                acc_i = ((pr[0] + pr[1]) + (pr[2] + pr[3])) +
                        ((pr[4] + pr[5]) + (pr[6] + pr[7])) + pr[8];
                acc_q = ((qr[0] + qr[1]) + (qr[2] + qr[3])) +
                        ((qr[4] + qr[5]) + (qr[6] + qr[7])) + qr[8];
            end
            2'd1: begin
                pr[0] = mult16(i_hist[0], c[1]);
                pr[1] = mult16(i_hist[1], c[5]);
                pr[2] = mult16(i_hist[2], c[9]);
                pr[3] = mult16(i_hist[3], c[13]);
                pr[4] = mult16(i_hist[4], c[17]);
                pr[5] = mult16(i_hist[5], c[21]);
                pr[6] = mult16(i_hist[6], c[25]);
                pr[7] = mult16(i_hist[7], c[29]);
                qr[0] = mult16(q_hist[0], c[1]);
                qr[1] = mult16(q_hist[1], c[5]);
                qr[2] = mult16(q_hist[2], c[9]);
                qr[3] = mult16(q_hist[3], c[13]);
                qr[4] = mult16(q_hist[4], c[17]);
                qr[5] = mult16(q_hist[5], c[21]);
                qr[6] = mult16(q_hist[6], c[25]);
                qr[7] = mult16(q_hist[7], c[29]);
                acc_i = ((pr[0] + pr[1]) + (pr[2] + pr[3])) +
                        ((pr[4] + pr[5]) + (pr[6] + pr[7]));
                acc_q = ((qr[0] + qr[1]) + (qr[2] + qr[3])) +
                        ((qr[4] + qr[5]) + (qr[6] + qr[7]));
            end
            2'd2: begin
                pr[0] = mult16(i_hist[0], c[2]);
                pr[1] = mult16(i_hist[1], c[6]);
                pr[2] = mult16(i_hist[2], c[10]);
                pr[3] = mult16(i_hist[3], c[14]);
                pr[4] = mult16(i_hist[4], c[18]);
                pr[5] = mult16(i_hist[5], c[22]);
                pr[6] = mult16(i_hist[6], c[26]);
                pr[7] = mult16(i_hist[7], c[30]);
                qr[0] = mult16(q_hist[0], c[2]);
                qr[1] = mult16(q_hist[1], c[6]);
                qr[2] = mult16(q_hist[2], c[10]);
                qr[3] = mult16(q_hist[3], c[14]);
                qr[4] = mult16(q_hist[4], c[18]);
                qr[5] = mult16(q_hist[5], c[22]);
                qr[6] = mult16(q_hist[6], c[26]);
                qr[7] = mult16(q_hist[7], c[30]);
                acc_i = ((pr[0] + pr[1]) + (pr[2] + pr[3])) +
                        ((pr[4] + pr[5]) + (pr[6] + pr[7]));
                acc_q = ((qr[0] + qr[1]) + (qr[2] + qr[3])) +
                        ((qr[4] + qr[5]) + (qr[6] + qr[7]));
            end
            default: begin
                pr[0] = mult16(i_hist[0], c[3]);
                pr[1] = mult16(i_hist[1], c[7]);
                pr[2] = mult16(i_hist[2], c[11]);
                pr[3] = mult16(i_hist[3], c[15]);
                pr[4] = mult16(i_hist[4], c[19]);
                pr[5] = mult16(i_hist[5], c[23]);
                pr[6] = mult16(i_hist[6], c[27]);
                pr[7] = mult16(i_hist[7], c[31]);
                qr[0] = mult16(q_hist[0], c[3]);
                qr[1] = mult16(q_hist[1], c[7]);
                qr[2] = mult16(q_hist[2], c[11]);
                qr[3] = mult16(q_hist[3], c[15]);
                qr[4] = mult16(q_hist[4], c[19]);
                qr[5] = mult16(q_hist[5], c[23]);
                qr[6] = mult16(q_hist[6], c[27]);
                qr[7] = mult16(q_hist[7], c[31]);
                acc_i = ((pr[0] + pr[1]) + (pr[2] + pr[3])) +
                        ((pr[4] + pr[5]) + (pr[6] + pr[7]));
                acc_q = ((qr[0] + qr[1]) + (qr[2] + qr[3])) +
                        ((qr[4] + qr[5]) + (qr[6] + qr[7]));
            end
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            i_sample <= 16'sd0;
            q_sample <= 16'sd0;
            sample_valid <= 1'b0;
            for (n = 0; n < 9; n = n + 1) begin
                i_hist[n] <= 16'sd0;
                q_hist[n] <= 16'sd0;
            end
        end else if (!enable) begin
            i_sample <= 16'sd0;
            q_sample <= 16'sd0;
            sample_valid <= 1'b0;
            for (n = 0; n < 9; n = n + 1) begin
                i_hist[n] <= 16'sd0;
                q_hist[n] <= 16'sd0;
            end
        end else begin
            sample_valid <= 1'b0;
            if (sample_tick) begin
                i_sample <= round_acc(acc_i);
                q_sample <= round_acc(acc_q);
                sample_valid <= 1'b1;
            end
            if (sample_tick && phase == 2'd0 && load_symbol) begin
                for (n = 8; n > 0; n = n - 1) begin
                    i_hist[n] <= i_hist[n - 1];
                    q_hist[n] <= q_hist[n - 1];
                end
                i_hist[0] <= new_i;
                q_hist[0] <= new_q;
            end
        end
    end
endmodule
